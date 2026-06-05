import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api/api_exceptions.dart';
import '../models/expense.dart';
import '../models/project.dart';
import '../repositories/budgets_repository.dart';
import 'auth_provider.dart';

// ── Repository provider ────────────────────────────────────────────────────

final budgetsRepositoryProvider = Provider<BudgetsRepository>((ref) {
  return BudgetsRepository(DioBudgetsTransport(ref.watch(apiClientProvider)));
});

// ── State ──────────────────────────────────────────────────────────────────

class BudgetsState {
  final List<Project> projects;
  final List<Expense> expenses;
  final bool isLoadingProjects;
  final bool isLoadingExpenses;
  final bool isSubmitting;
  final String? error;

  const BudgetsState({
    this.projects = const [],
    this.expenses = const [],
    this.isLoadingProjects = false,
    this.isLoadingExpenses = false,
    this.isSubmitting = false,
    this.error,
  });

  bool get isLoading => isLoadingProjects || isLoadingExpenses;

  BudgetsState copyWith({
    List<Project>? projects,
    List<Expense>? expenses,
    bool? isLoadingProjects,
    bool? isLoadingExpenses,
    bool? isSubmitting,
    String? error,
    bool clearError = false,
  }) =>
      BudgetsState(
        projects: projects ?? this.projects,
        expenses: expenses ?? this.expenses,
        isLoadingProjects: isLoadingProjects ?? this.isLoadingProjects,
        isLoadingExpenses: isLoadingExpenses ?? this.isLoadingExpenses,
        isSubmitting: isSubmitting ?? this.isSubmitting,
        error: clearError ? null : (error ?? this.error),
      );
}

// ── Notifier ───────────────────────────────────────────────────────────────

class BudgetsNotifier extends StateNotifier<BudgetsState> {
  final BudgetsRepository _repo;
  BudgetsNotifier(this._repo) : super(const BudgetsState());

  Future<void> loadProjects() async {
    state = state.copyWith(isLoadingProjects: true, clearError: true);
    try {
      final projects = await _repo.listProjects(status: 'active');
      state = state.copyWith(projects: projects, isLoadingProjects: false);
    } on ApiError catch (e) {
      state = state.copyWith(isLoadingProjects: false, error: e.message);
    } catch (e) {
      state = state.copyWith(isLoadingProjects: false, error: e.toString());
    }
  }

  Future<void> loadExpenses() async {
    state = state.copyWith(isLoadingExpenses: true, clearError: true);
    try {
      final expenses = await _repo.listExpenses();
      state = state.copyWith(expenses: expenses, isLoadingExpenses: false);
    } on ApiError catch (e) {
      state = state.copyWith(isLoadingExpenses: false, error: e.message);
    } catch (e) {
      state = state.copyWith(isLoadingExpenses: false, error: e.toString());
    }
  }

  /// Load both projects and expenses in parallel.
  Future<void> load() async {
    await Future.wait([loadProjects(), loadExpenses()]);
  }

  Future<bool> addExpense(
    String projectId,
    double amount,
    String description, {
    String? vendor,
  }) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      final expense = await _repo.createExpense(
        projectId,
        amount,
        description,
        vendor: vendor,
      );
      // Immutable prepend — do not mutate existing list.
      state = state.copyWith(
        expenses: [expense, ...state.expenses],
        isSubmitting: false,
      );
      // Reload projects so budget bars update with new spent totals.
      await loadProjects();
      return true;
    } on ApiError catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.message);
      return false;
    } catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.toString());
      return false;
    }
  }

  Future<void> removeExpense(String id) async {
    // Optimistic remove — do not mutate existing list.
    final without = state.expenses.where((e) => e.id != id).toList();
    state = state.copyWith(expenses: without);

    try {
      await _repo.deleteExpense(id);
      // Reload projects so budget bars reflect the removal.
      await loadProjects();
    } on ApiError catch (e) {
      state = state.copyWith(error: e.message);
      await load(); // Restore from server.
    } catch (e) {
      state = state.copyWith(error: e.toString());
      await load();
    }
  }

  Future<bool> addProject(String name, {double? budget}) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      final project = await _repo.createProject(name, budget: budget);
      state = state.copyWith(
        projects: [project, ...state.projects],
        isSubmitting: false,
      );
      return true;
    } on ApiError catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.message);
      return false;
    } catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.toString());
      return false;
    }
  }

  void clearError() => state = state.copyWith(clearError: true);
}

// ── Provider ───────────────────────────────────────────────────────────────

final budgetsProvider =
    StateNotifierProvider<BudgetsNotifier, BudgetsState>((ref) {
  return BudgetsNotifier(ref.watch(budgetsRepositoryProvider));
});
