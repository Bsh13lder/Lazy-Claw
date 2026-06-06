import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../local/budgets_dao.dart';
import '../models/expense.dart';
import '../models/project.dart';
import '../repositories/budgets_repository.dart';
import '../sync/budgets_sync.dart';
import 'auth_provider.dart';
// Reuse the shared infra providers (DB handle + reachability) defined alongside
// the Tasks offline-first stack — they are domain-agnostic.
import 'tasks_provider.dart' show appDatabaseProvider, reachableProvider;

// ── Infrastructure providers ────────────────────────────────────────────────

/// Local budgets store (projects + expenses) backed by the encrypted DB.
final budgetsDaoProvider = Provider<BudgetsDao>((ref) {
  return BudgetsDao(ref.watch(appDatabaseProvider));
});

/// Remote seam (Dio-backed). Now only used by the sync engine, never directly
/// by the UI (which reads/writes the local cache first).
final budgetsRepositoryProvider = Provider<BudgetsRepository>((ref) {
  return BudgetsRepository(DioBudgetsTransport(ref.watch(apiClientProvider)));
});

/// The offline-first budgets sync engine (projects + expenses, shared cursor).
final budgetsSyncProvider = Provider<BudgetsSync>((ref) {
  return BudgetsSync(
      ref.watch(budgetsDaoProvider), ref.watch(budgetsRepositoryProvider));
});

// ── State ──────────────────────────────────────────────────────────────────

class BudgetsState {
  final List<Project> projects;
  final List<Expense> expenses;

  /// Project ids with un-pushed local edits — the UI shows a cloud-off badge.
  final Set<String> dirtyProjectIds;

  /// Expense ids with un-pushed local edits.
  final Set<String> dirtyExpenseIds;

  final bool isLoading;
  final bool isSubmitting;
  final String? error;

  const BudgetsState({
    this.projects = const [],
    this.expenses = const [],
    this.dirtyProjectIds = const {},
    this.dirtyExpenseIds = const {},
    this.isLoading = false,
    this.isSubmitting = false,
    this.error,
  });

  BudgetsState copyWith({
    List<Project>? projects,
    List<Expense>? expenses,
    Set<String>? dirtyProjectIds,
    Set<String>? dirtyExpenseIds,
    bool? isLoading,
    bool? isSubmitting,
    String? error,
    bool clearError = false,
  }) =>
      BudgetsState(
        projects: projects ?? this.projects,
        expenses: expenses ?? this.expenses,
        dirtyProjectIds: dirtyProjectIds ?? this.dirtyProjectIds,
        dirtyExpenseIds: dirtyExpenseIds ?? this.dirtyExpenseIds,
        isLoading: isLoading ?? this.isLoading,
        isSubmitting: isSubmitting ?? this.isSubmitting,
        error: clearError ? null : (error ?? this.error),
      );
}

// ── Notifier ───────────────────────────────────────────────────────────────

/// Offline-first budgets notifier.
///
/// Reads come from the local DAO (instant, works offline). Writes go to the
/// DAO + outbox first (optimistic), then a best-effort [BudgetsSync.sync]
/// pushes them when the backend is reachable. The UI never blocks on the
/// network.
class BudgetsNotifier extends StateNotifier<BudgetsState> {
  final BudgetsDao _dao;
  final BudgetsSync _sync;

  BudgetsNotifier(this._dao, this._sync) : super(const BudgetsState());

  /// Load from the local cache immediately, then kick a background sync and
  /// refresh from cache again when it settles.
  Future<void> load() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      await _refreshFromCache(loading: false);
    } catch (e) {
      // A degraded/corrupt cache must never strand the screen on the loading
      // skeleton — surface the error and let the UI recover.
      state = state.copyWith(isLoading: false, error: e.toString());
    } finally {
      if (state.isLoading) state = state.copyWith(isLoading: false);
    }
    // Best-effort sync; failures are silent (offline is a normal state).
    unawaited(_syncThenRefresh());
  }

  /// Pull-to-refresh: force a sync then re-read the cache.
  Future<void> refresh() => _syncThenRefresh();

  /// Trigger a sync (e.g. when reachability flips to true) then refresh.
  Future<void> syncNow() => _syncThenRefresh();

  Future<bool> addProject(String name, {double? budget}) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      await _dao.applyLocalProjectCreate(name, budget: budget);
      await _refreshFromCache();
      state = state.copyWith(isSubmitting: false);
      unawaited(_syncThenRefresh());
      return true;
    } catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.toString());
      return false;
    }
  }

  Future<bool> addExpense(
    String projectId,
    double amount,
    String description, {
    String? vendor,
  }) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      // Stamp the project name so the new local row renders before the next
      // pull rehydrates it from the server.
      final match =
          state.projects.where((p) => p.id == projectId).firstOrNull;
      final projectName = match?.name;
      await _dao.applyLocalExpenseCreate(
        projectId,
        amount,
        description,
        vendor: vendor,
        projectName: projectName,
      );
      await _refreshFromCache();
      state = state.copyWith(isSubmitting: false);
      unawaited(_syncThenRefresh());
      return true;
    } catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.toString());
      return false;
    }
  }

  /// Patch an existing expense locally (amount/description/vendor/project/notes/
  /// date). Only the supplied fields change; the rest are preserved. Lands
  /// optimistically in the cache + outbox, then best-effort syncs. Returns true
  /// on success, false (with `state.error` set) when the local write throws.
  Future<bool> updateExpense(
    String id, {
    double? amount,
    String? description,
    String? vendor,
    String? projectId,
    String? notes,
    String? spentAt,
  }) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      await _dao.applyLocalExpenseUpdate(
        id,
        amount: amount,
        description: description,
        vendor: vendor,
        projectId: projectId,
        notes: notes,
        spentAt: spentAt,
      );
      await _refreshFromCache();
      state = state.copyWith(isSubmitting: false);
      unawaited(_syncThenRefresh());
      return true;
    } catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.toString());
      return false;
    }
  }

  Future<void> removeExpense(String id) async {
    try {
      await _dao.applyLocalExpenseDelete(id);
      await _refreshFromCache();
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> removeProject(String id) async {
    try {
      await _dao.applyLocalProjectDelete(id);
      await _refreshFromCache();
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  void clearError() => state = state.copyWith(clearError: true);

  // ── internals ──────────────────────────────────────────────────────────

  Future<void> _refreshFromCache({bool loading = false}) async {
    final projects = await _dao.listProjects();
    final expenses = await _dao.listExpenses();
    final dirtyProjects = await _dao.dirtyProjectIds();
    final dirtyExpenses = await _dao.dirtyExpenseIds();
    state = state.copyWith(
      projects: projects,
      expenses: expenses,
      dirtyProjectIds: dirtyProjects,
      dirtyExpenseIds: dirtyExpenses,
      isLoading: loading,
    );
  }

  Future<void> _syncThenRefresh() async {
    try {
      await _sync.sync();
    } catch (_) {
      // Offline / server down — the local cache already holds the truth.
    }
    if (!mounted) return;
    try {
      await _refreshFromCache();
    } catch (e) {
      // A cache-read throw here must not escape as an unhandled async error.
      state = state.copyWith(error: e.toString());
    }
  }
}

// ── Provider ───────────────────────────────────────────────────────────────

final budgetsProvider =
    StateNotifierProvider<BudgetsNotifier, BudgetsState>((ref) {
  final notifier = BudgetsNotifier(
    ref.watch(budgetsDaoProvider),
    ref.watch(budgetsSyncProvider),
  );

  // When the backend comes back online, drain the outbox + pull deltas.
  ref.listen<bool>(reachableProvider, (prev, next) {
    if (next == true && prev != true) {
      notifier.syncNow();
    }
  });

  return notifier;
});
