import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../local/budgets_dao.dart';
import '../models/budget_entry.dart';
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

  /// Budget-ledger entries (top-ups) across all projects, synced offline-first.
  /// The Log surfaces filter this by project id.
  final List<BudgetEntry> budgetEntries;

  /// Project ids with un-pushed local edits — the UI shows a cloud-off badge.
  final Set<String> dirtyProjectIds;

  /// Expense ids with un-pushed local edits.
  final Set<String> dirtyExpenseIds;

  /// Ledger-entry ids with un-pushed local edits.
  final Set<String> dirtyBudgetEntryIds;

  final bool isLoading;
  final bool isSubmitting;
  final String? error;

  const BudgetsState({
    this.projects = const [],
    this.expenses = const [],
    this.budgetEntries = const [],
    this.dirtyProjectIds = const {},
    this.dirtyExpenseIds = const {},
    this.dirtyBudgetEntryIds = const {},
    this.isLoading = false,
    this.isSubmitting = false,
    this.error,
  });

  BudgetsState copyWith({
    List<Project>? projects,
    List<Expense>? expenses,
    List<BudgetEntry>? budgetEntries,
    Set<String>? dirtyProjectIds,
    Set<String>? dirtyExpenseIds,
    Set<String>? dirtyBudgetEntryIds,
    bool? isLoading,
    bool? isSubmitting,
    String? error,
    bool clearError = false,
  }) =>
      BudgetsState(
        projects: projects ?? this.projects,
        expenses: expenses ?? this.expenses,
        budgetEntries: budgetEntries ?? this.budgetEntries,
        dirtyProjectIds: dirtyProjectIds ?? this.dirtyProjectIds,
        dirtyExpenseIds: dirtyExpenseIds ?? this.dirtyExpenseIds,
        dirtyBudgetEntryIds: dirtyBudgetEntryIds ?? this.dirtyBudgetEntryIds,
        isLoading: isLoading ?? this.isLoading,
        isSubmitting: isSubmitting ?? this.isSubmitting,
        error: clearError ? null : (error ?? this.error),
      );

  /// Ledger entries belonging to [projectId], newest first (already ordered by
  /// the DAO read).
  List<BudgetEntry> entriesForProject(String projectId) =>
      budgetEntries.where((e) => e.projectId == projectId).toList();
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

  /// Create a project locally (optimistic) with an optional [budget] and an
  /// optional [color] (a `"#RRGGBB"` hex string). Lands in the cache + outbox,
  /// then best-effort syncs. Returns true on success, false (with `state.error`
  /// set) when the local write throws.
  Future<bool> createProject(
    String name, {
    double? budget,
    String? color,
  }) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      await _dao.applyLocalProjectCreate(name, budget: budget, color: color);
      await _refreshFromCache();
      state = state.copyWith(isSubmitting: false);
      unawaited(_syncThenRefresh());
      return true;
    } catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.toString());
      return false;
    }
  }

  /// Rename an existing project (optimistic). Returns true on success, false
  /// (with `state.error` set) when the local write throws.
  Future<bool> renameProject(String id, String name) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      await _dao.applyLocalProjectUpdate(id, name: name);
      await _refreshFromCache();
      state = state.copyWith(isSubmitting: false);
      unawaited(_syncThenRefresh());
      return true;
    } catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.toString());
      return false;
    }
  }

  /// Set a project's accent [color] (a `"#RRGGBB"` hex string) (optimistic).
  /// Returns true on success, false (with `state.error` set) on a local throw.
  Future<bool> setProjectColor(String id, String color) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      await _dao.applyLocalProjectUpdate(id, color: color);
      await _refreshFromCache();
      state = state.copyWith(isSubmitting: false);
      unawaited(_syncThenRefresh());
      return true;
    } catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.toString());
      return false;
    }
  }

  /// Set a project's total [budget] amount (optimistic). The web equivalent of
  /// the project "edit budget" control — replays as a PATCH that overwrites the
  /// project total. Returns true on success, false (with `state.error` set) on
  /// a local throw.
  Future<bool> setProjectBudget(String id, double budget) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      await _dao.applyLocalProjectUpdate(id, budget: budget);
      await _refreshFromCache();
      state = state.copyWith(isSubmitting: false);
      unawaited(_syncThenRefresh());
      return true;
    } catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.toString());
      return false;
    }
  }

  /// Add [amount] to a project's budget OFFLINE-SAFELY — the fallback the
  /// "Add Budget" ledger uses when its online top-up POST can't reach the
  /// server. Reads the current (optimistically up-to-date) local budget and
  /// writes `budget = current + amount` through the already-synced project
  /// update path, so the top-up is never lost and survives outbox coalescing
  /// (each credit reads the running local total, so two queued credits stay
  /// additive instead of collapsing to just the last absolute value under LWW).
  /// Unlike the online ledger POST this creates NO `budget_entries` audit row —
  /// the money moves, the where-from note is simply skipped while offline.
  /// Returns true on success, false (with `state.error` set) on a local throw.
  Future<bool> creditProjectBudget(String id, double amount) async {
    final match = state.projects.where((p) => p.id == id).firstOrNull;
    if (match == null) return false;
    final next = match.budget + amount;
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      await _dao.applyLocalProjectUpdate(id, budget: next);
      await _refreshFromCache();
      state = state.copyWith(isSubmitting: false);
      unawaited(_syncThenRefresh());
      return true;
    } catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.toString());
      return false;
    }
  }

  /// Flip a project's favorite flag (optimistic). Reads the current value from
  /// the loaded state, writes the inverse through the DAO, and best-effort
  /// syncs. A favorited project surfaces in the Home "Favorites" section.
  /// Returns true on success, false (with `state.error` set) on a local throw.
  Future<bool> toggleFavorite(String id) async {
    final match = state.projects.where((p) => p.id == id).firstOrNull;
    if (match == null) return false;
    final next = !match.isFavorite;
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      await _dao.applyLocalProjectUpdate(id, isFavorite: next);
      await _refreshFromCache();
      state = state.copyWith(isSubmitting: false);
      unawaited(_syncThenRefresh());
      return true;
    } catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.toString());
      return false;
    }
  }

  /// Flip an expense's favorite (star) flag (optimistic). Reads the current
  /// value from the loaded state, writes the inverse through the DAO, and
  /// best-effort syncs. Starred expenses feed the Money "★ Starred only"
  /// subtotal. Returns true on success, false (with `state.error` set on a
  /// local throw, or silently for an unknown id) otherwise.
  Future<bool> toggleExpenseFavorite(String id) async {
    final match = state.expenses.where((e) => e.id == id).firstOrNull;
    if (match == null) return false;
    final next = !match.isFavorite;
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      await _dao.applyLocalExpenseFavorite(id, next);
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
      // Stamp the project name + currency so the new local row renders with the
      // right symbol BEFORE the next pull rehydrates it from the server.
      // Without inheriting the project's currency the DAO default ("USD") would
      // mislabel a non-USD project's expenses (and corrupt derived totals) until
      // a sync round-trip.
      final match =
          state.projects.where((p) => p.id == projectId).firstOrNull;
      final projectName = match?.name;
      final currency = match?.currency ?? 'USD';
      await _dao.applyLocalExpenseCreate(
        projectId,
        amount,
        description,
        vendor: vendor,
        projectName: projectName,
        currency: currency,
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
    // Drop the row from state SYNCHRONOUSLY — before any await — so a swipe's
    // Dismissible.onDismissed never leaves a dismissed tile in the tree. That
    // race throws "A dismissed Dismissible widget is still part of the tree"
    // and freezes the Money screen. The DB delete + sync follow;
    // _refreshFromCache re-reconciles from the source of truth. Mirrors
    // TasksNotifier.deleteTask.
    final prev = state.expenses;
    state = state.copyWith(
      expenses: prev.where((e) => e.id != id).toList(),
    );
    try {
      await _dao.applyLocalExpenseDelete(id);
      await _refreshFromCache();
      unawaited(_syncThenRefresh());
    } catch (e) {
      // Restore the optimistically-removed row on failure.
      state = state.copyWith(expenses: prev, error: e.toString());
    }
  }

  // ── Budget ledger (top-ups) — offline-first ────────────────────────────────

  /// Add money to a project's budget as a real ledger entry (optimistic,
  /// offline-first). Lands in the cache + outbox and bumps the project budget
  /// immediately (online OR offline), then best-effort syncs. Unlike the old
  /// online-only top-up this NEVER loses the money offline AND records the
  /// where-from note. Returns true on success, false (with `state.error` set)
  /// when the local write throws.
  Future<bool> addBudgetEntryLocal(
    String projectId,
    double amount, {
    String? source,
  }) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      // Inherit the project's currency so an offline top-up isn't mislabelled
      // USD until the next pull.
      final match =
          state.projects.where((p) => p.id == projectId).firstOrNull;
      final currency = match?.currency ?? 'USD';
      await _dao.applyLocalBudgetEntryCreate(
        projectId,
        amount,
        source: source,
        currency: currency,
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

  /// Delete a ledger entry (optimistic, offline-first). Drops it from state
  /// synchronously (Dismissible-safe), rolls back its budget bump, then queues
  /// the server delete + best-effort syncs.
  Future<void> removeBudgetEntry(String id) async {
    final prev = state.budgetEntries;
    state = state.copyWith(
      budgetEntries: prev.where((e) => e.id != id).toList(),
    );
    try {
      await _dao.applyLocalBudgetEntryDelete(id);
      await _refreshFromCache();
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(budgetEntries: prev, error: e.toString());
    }
  }

  Future<void> deleteProject(String id) async {
    // Same Dismissible-safe optimistic removal as removeExpense.
    final prev = state.projects;
    state = state.copyWith(
      projects: prev.where((p) => p.id != id).toList(),
    );
    try {
      await _dao.applyLocalProjectDelete(id);
      await _refreshFromCache();
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(projects: prev, error: e.toString());
    }
  }

  void clearError() => state = state.copyWith(clearError: true);

  // ── internals ──────────────────────────────────────────────────────────

  Future<void> _refreshFromCache({bool loading = false}) async {
    final projects = await _dao.listProjects();
    final expenses = await _dao.listExpenses();
    final budgetEntries = await _dao.listBudgetEntries();
    final dirtyProjects = await _dao.dirtyProjectIds();
    final dirtyExpenses = await _dao.dirtyExpenseIds();
    final dirtyEntries = await _dao.dirtyBudgetEntryIds();
    state = state.copyWith(
      projects: projects,
      expenses: expenses,
      budgetEntries: budgetEntries,
      dirtyProjectIds: dirtyProjects,
      dirtyExpenseIds: dirtyExpenses,
      dirtyBudgetEntryIds: dirtyEntries,
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
