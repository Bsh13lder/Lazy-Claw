import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/expense.dart';
import '../providers/budgets_provider.dart';
import '../providers/tasks_provider.dart'
    show reachableProvider, dbHealthProvider;
import '../ui/ui.dart';
import 'expenses/add_expense_sheet.dart';
import 'expenses/budget_summary_card.dart';
import 'expenses/expense_detail_sheet.dart';
import 'expenses/expense_row.dart';
import 'expenses/money_helpers.dart';
import 'expenses/project_card.dart';
import 'storage_banners.dart';

class ExpensesScreen extends ConsumerStatefulWidget {
  const ExpensesScreen({super.key});

  @override
  ConsumerState<ExpensesScreen> createState() => _ExpensesScreenState();
}

class _ExpensesScreenState extends ConsumerState<ExpensesScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabController;
  String? _lastSelectedProjectId;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this);
    Future.microtask(() => ref.read(budgetsProvider.notifier).load());
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  Future<void> _refresh() => ref.read(budgetsProvider.notifier).refresh();

  void _showAddExpense(BudgetsState state) {
    LzBottomSheet.show<void>(
      context,
      title: 'Add Expense',
      builder: (_) => AddExpenseSheet(
        projects: state.projects,
        initialProjectId: _lastSelectedProjectId ??
            (state.projects.isNotEmpty ? state.projects.first.id : null),
        onSubmit: (projectId, amount, description, vendor) async {
          // Remember last-used project for quick follow-up entry.
          _lastSelectedProjectId = projectId;
          return ref.read(budgetsProvider.notifier).addExpense(
                projectId,
                amount,
                description,
                vendor: vendor,
              );
        },
      ),
    );
  }

  void _showExpenseDetail(Expense expense) {
    showExpenseDetailSheet(context, ref, expense);
  }

  void _showAddProject() {
    LzBottomSheet.show<void>(
      context,
      title: 'New Project',
      builder: (_) => AddProjectSheet(
        onSubmit: (name, budget) =>
            ref.read(budgetsProvider.notifier).addProject(name, budget: budget),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(budgetsProvider);
    final reachable = ref.watch(reachableProvider);
    final degraded = ref.watch(dbHealthProvider).isDegraded;
    final banners = buildStorageBanners(
      context,
      offline: !reachable,
      degraded: degraded,
      onRetry: () => ref.read(budgetsProvider.notifier).load(),
    );

    // Show error snackbar on new errors.
    ref.listen<BudgetsState>(budgetsProvider, (prev, next) {
      if (next.error != null && next.error != prev?.error) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(next.error!),
            backgroundColor: AppColors.bgSurfaceElevated,
            action: SnackBarAction(
              label: 'Dismiss',
              textColor: AppColors.accent,
              onPressed: () =>
                  ref.read(budgetsProvider.notifier).clearError(),
            ),
          ),
        );
      }
    });

    return Scaffold(
      backgroundColor: AppColors.bgBase,
      appBar: _buildAppBar(state),
      body: Column(
        children: [
          ?banners,
          Expanded(child: _buildBody(state)),
        ],
      ),
      floatingActionButton: _buildFAB(state),
    );
  }

  PreferredSizeWidget _buildAppBar(BudgetsState state) {
    return AppBar(
      backgroundColor: AppColors.bgBase,
      surfaceTintColor: Colors.transparent,
      title: Text('Money', style: AppText.titleL),
      actions: [
        IconButton(
          icon: const Icon(Icons.create_new_folder_outlined),
          tooltip: 'New project',
          color: AppColors.textSecondary,
          onPressed: _showAddProject,
        ),
        const SizedBox(width: AppSpacing.sm),
      ],
      bottom: PreferredSize(
        preferredSize: const Size.fromHeight(48),
        child: TabBar(
          controller: _tabController,
          labelStyle: AppText.label,
          unselectedLabelStyle:
              AppText.label.copyWith(color: AppColors.textMuted),
          labelColor: AppColors.accent,
          unselectedLabelColor: AppColors.textMuted,
          indicatorColor: AppColors.accent,
          indicatorSize: TabBarIndicatorSize.label,
          dividerColor: AppColors.borderSubtle,
          tabs: const [
            Tab(text: 'Overview'),
            Tab(text: 'Ledger'),
          ],
        ),
      ),
    );
  }

  Widget _buildFAB(BudgetsState state) {
    return FloatingActionButton.extended(
      onPressed: state.isSubmitting ? null : () => _showAddExpense(state),
      backgroundColor: AppColors.accent,
      foregroundColor: AppColors.onAccent,
      icon: state.isSubmitting
          ? const SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: AppColors.onAccent,
              ),
            )
          : const Icon(Icons.add_rounded),
      label: Text('Expense', style: AppText.label.copyWith(color: AppColors.onAccent)),
    );
  }

  Widget _buildBody(BudgetsState state) {
    final nothingCached = state.projects.isEmpty && state.expenses.isEmpty;

    // Loading skeleton — only on the first instant cache read (nothing cached
    // yet, nothing errored).
    if (state.isLoading && nothingCached && state.error == null) {
      return LzSkeleton.list(
        count: 4,
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg,
          AppSpacing.lg,
          AppSpacing.lg,
          AppSpacing.md,
        ),
      );
    }

    // Error state — nothing cached to show + a load error → offer a real Retry
    // instead of a misleading empty/zeroed dashboard or an infinite skeleton.
    if (nothingCached && state.error != null) {
      return LzErrorState(
        message: state.error!,
        onRetry: () => ref.read(budgetsProvider.notifier).load(),
      );
    }

    // Empty state — no projects yet.
    if (!state.isLoading && state.projects.isEmpty && state.error == null) {
      return LzEmptyState(
        icon: Icons.receipt_long_outlined,
        title: 'No projects yet',
        hint: 'Create a project to start tracking expenses.',
        actionLabel: 'Create project',
        actionIcon: Icons.create_new_folder_outlined,
        onAction: _showAddProject,
      );
    }

    return TabBarView(
      controller: _tabController,
      children: [
        _OverviewTab(
          state: state,
          onDeleteProject: (id) =>
              ref.read(budgetsProvider.notifier).removeProject(id),
          onRefresh: _refresh,
        ),
        _LedgerTab(
          state: state,
          onDeleteExpense: (id) =>
              ref.read(budgetsProvider.notifier).removeExpense(id),
          onTapExpense: _showExpenseDetail,
          onRefresh: _refresh,
        ),
      ],
    );
  }
}

// ── Overview Tab ─────────────────────────────────────────────────────────────

class _OverviewTab extends StatelessWidget {
  const _OverviewTab({
    required this.state,
    required this.onDeleteProject,
    required this.onRefresh,
  });

  final BudgetsState state;
  final void Function(String id) onDeleteProject;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    // Aggregate totals.
    final totalSpent = state.projects.fold<double>(
      0,
      (sum, p) => sum + (p.spent ?? 0),
    );
    final totalBudget =
        state.projects.fold<double>(0, (sum, p) => sum + p.budget);
    final currency =
        state.projects.isNotEmpty ? state.projects.first.currency : 'USD';

    return LzRefresh(
      onRefresh: onRefresh,
      child: ListView(
        padding: EdgeInsets.zero,
        children: [
          // Hero summary card.
          BudgetSummaryCard(
            totalSpent: totalSpent,
            totalBudget: totalBudget,
            currency: currency,
          ),
          // Projects section.
          if (state.projects.isNotEmpty) ...[
            Padding(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.lg,
                AppSpacing.lg,
                AppSpacing.lg,
                AppSpacing.xs,
              ),
              child: LzSection(
                title: 'Projects',
                child: const SizedBox.shrink(),
              ),
            ),
            ...state.projects.map((p) {
              final projectExpenses = state.expenses
                  .where((e) => e.projectId == p.id && !e.isVoid)
                  .toList();
              return ProjectCard(
                project: p,
                expenses: projectExpenses,
                pendingSync: state.dirtyProjectIds.contains(p.id),
                onDelete: () => onDeleteProject(p.id),
              );
            }),
            const SizedBox(height: AppSpacing.xxxl),
          ],
        ],
      ),
    );
  }
}

// ── Ledger Tab ────────────────────────────────────────────────────────────────

class _LedgerTab extends StatelessWidget {
  const _LedgerTab({
    required this.state,
    required this.onDeleteExpense,
    required this.onTapExpense,
    required this.onRefresh,
  });

  final BudgetsState state;
  final void Function(String id) onDeleteExpense;
  final void Function(Expense expense) onTapExpense;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    final visible = state.expenses.where((e) => !e.isVoid).toList();

    if (visible.isEmpty) {
      return LzEmptyState(
        icon: Icons.receipt_long_outlined,
        title: 'No expenses yet',
        hint: 'Tap + Expense to log your first one.',
      );
    }

    // Group by date.
    final byDate = groupBy<Expense, String>(
      visible,
      (e) => dateLabel(e.spentAt),
    );

    // Sort dates: most recent first.
    final sortedDates = byDate.keys.toList()
      ..sort((a, b) => b.compareTo(a));

    return LzRefresh(
      onRefresh: onRefresh,
      child: ListView.builder(
        padding: const EdgeInsets.only(bottom: AppSpacing.xxxl),
        itemCount: sortedDates.length,
        itemBuilder: (context, i) {
          final date = sortedDates[i];
          final dayExpenses = byDate[date]!;
          final dayTotal = dayExpenses.fold<double>(
            0,
            (sum, e) => sum + e.amount,
          );
          final currency =
              dayExpenses.isNotEmpty ? dayExpenses.first.currency : 'USD';

          return Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.lg,
              AppSpacing.lg,
              AppSpacing.lg,
              0,
            ),
            child: LzSection(
              title: friendlyDate(date),
              action: Text(
                fmtMoney(currency, dayTotal),
                style: AppText.caption.copyWith(
                  color: AppColors.textSecondary,
                  fontWeight: FontWeight.w700,
                ),
              ),
              child: LzCard(
                padding: EdgeInsets.zero,
                child: Column(
                  children: [
                    for (int j = 0; j < dayExpenses.length; j++) ...[
                      ExpenseRow(
                        expense: dayExpenses[j],
                        projects: state.projects,
                        pendingSync:
                            state.dirtyExpenseIds.contains(dayExpenses[j].id),
                        onDelete: () => onDeleteExpense(dayExpenses[j].id),
                        onTap: () => onTapExpense(dayExpenses[j]),
                        showProject: true,
                      ),
                      if (j < dayExpenses.length - 1)
                        Divider(
                          height: 0.5,
                          thickness: 0.5,
                          color: AppColors.borderSubtle,
                          indent: AppSpacing.lg + 40 + AppSpacing.md,
                        ),
                    ],
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}
