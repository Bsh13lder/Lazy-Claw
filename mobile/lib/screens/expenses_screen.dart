import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/actions/app_actions.dart';
import '../models/expense.dart';
import '../models/project.dart';
import '../providers/budgets_provider.dart';
import '../providers/tasks_provider.dart'
    show reachableProvider, dbHealthProvider;
import '../ui/ui.dart';
import 'expenses/add_expense_sheet.dart';
import 'expenses/budget_log_sheet.dart';
import 'expenses/budget_math.dart';
import 'expenses/budget_summary_card.dart';
import 'expenses/edit_project_sheet.dart';
import 'expenses/expense_detail_sheet.dart';
import 'expenses/expense_row.dart';
import 'expenses/money_helpers.dart';
import 'expenses/project_card.dart';
import 'storage_banners.dart';

/// Sort order for the expense ledger.
enum _LedgerSort { newest, oldest, amount }

/// Sentinel filter value for expenses that no longer map to a live project
/// (e.g. their project was deleted locally before the sync caught up).
const String _kUncategorizedFilter = '__uncategorized__';

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

    // Cold-start deep link: a `+ Expense` shortcut/widget button may have set a
    // pending action before this screen mounted. ref.listen only fires on
    // change, so replay any pre-existing pending action once after first build.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final taken = takePendingAction(ref, const {AppAction.addExpense});
      if (taken != null && mounted) {
        _showAddExpense(ref.read(budgetsProvider));
      }
    });
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
        onSubmit: (name, budget, color) => ref
            .read(budgetsProvider.notifier)
            .createProject(name, budget: budget, color: color),
      ),
    );
  }

  void _showEditProject(Project project) {
    final notifier = ref.read(budgetsProvider.notifier);
    LzBottomSheet.show<void>(
      context,
      title: 'Edit Project',
      builder: (_) => EditProjectSheet(
        project: project,
        onRename: (name) => notifier.renameProject(project.id, name),
        onSetBudget: (budget) => notifier.setProjectBudget(project.id, budget),
        onSetColor: (color) => notifier.setProjectColor(project.id, color),
        onDelete: () async {
          await notifier.deleteProject(project.id);
          return true;
        },
        onOpenBudgetLog: () => showBudgetLogSheet(
          context,
          ref,
          projectId: project.id,
          currency: project.currency,
        ),
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

    // Warm deep link: a `+ Expense` shortcut/widget fired while this screen was
    // already alive (indexedStack keeps it mounted). Consume + open the sheet.
    ref.listen<AppAction?>(pendingActionProvider, (_, next) {
      if (next == null) return;
      final taken = takePendingAction(ref, const {AppAction.addExpense});
      if (taken != null) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) _showAddExpense(ref.read(budgetsProvider));
        });
      }
    });

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
      title: Text('Expenses', style: AppText.titleL),
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
              ref.read(budgetsProvider.notifier).deleteProject(id),
          onEditProject: _showEditProject,
          onToggleFavorite: (id) =>
              ref.read(budgetsProvider.notifier).toggleFavorite(id),
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
    required this.onEditProject,
    required this.onToggleFavorite,
    required this.onRefresh,
  });

  final BudgetsState state;
  final void Function(String id) onDeleteProject;
  final void Function(Project project) onEditProject;
  final void Function(String id) onToggleFavorite;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    // Aggregate totals — derived from the live expense set (not a stale/absent
    // server rollup) and currency-aware so mixed currencies aren't summed.
    final totals = BudgetTotals.from(state.projects, state.expenses);

    return LzRefresh(
      onRefresh: onRefresh,
      child: ListView(
        padding: EdgeInsets.zero,
        children: [
          // Hero summary card.
          BudgetSummaryCard(totals: totals),
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
                onEdit: () => onEditProject(p),
                onToggleFavorite: () => onToggleFavorite(p.id),
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

class _LedgerTab extends StatefulWidget {
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
  State<_LedgerTab> createState() => _LedgerTabState();
}

class _LedgerTabState extends State<_LedgerTab> {
  /// null = all projects; a project id; or [_kUncategorizedFilter].
  String? _projectFilter;
  _LedgerSort _sort = _LedgerSort.newest;

  /// Whether any visible expense no longer maps to a live project.
  bool _hasUncategorized(List<Expense> visible, Set<String> liveIds) =>
      visible.any((e) => !liveIds.contains(e.projectId));

  List<Expense> _applyFilter(List<Expense> visible, Set<String> liveIds) {
    final filter = _projectFilter;
    if (filter == null) return visible;
    if (filter == _kUncategorizedFilter) {
      return visible.where((e) => !liveIds.contains(e.projectId)).toList();
    }
    return visible.where((e) => e.projectId == filter).toList();
  }

  @override
  Widget build(BuildContext context) {
    final state = widget.state;
    final visible = state.expenses.where((e) => !e.isVoid).toList();

    if (visible.isEmpty) {
      return LzEmptyState(
        icon: Icons.receipt_long_outlined,
        title: 'No expenses yet',
        hint: 'Tap + Expense to log your first one.',
      );
    }

    final liveIds = state.projects.map((p) => p.id).toSet();
    // A stale filter (its project was just deleted) collapses back to "All".
    if (_projectFilter != null &&
        _projectFilter != _kUncategorizedFilter &&
        !liveIds.contains(_projectFilter)) {
      _projectFilter = null;
    }

    final filtered = _applyFilter(visible, liveIds);
    final controls = _LedgerControls(
      projects: state.projects,
      selectedProjectId: _projectFilter,
      showUncategorized: _hasUncategorized(visible, liveIds),
      sort: _sort,
      onProjectChanged: (id) => setState(() => _projectFilter = id),
      onSortChanged: (s) => setState(() => _sort = s),
    );

    return LzRefresh(
      onRefresh: widget.onRefresh,
      child: CustomScrollView(
        slivers: [
          SliverToBoxAdapter(child: controls),
          if (filtered.isEmpty)
            SliverFillRemaining(
              hasScrollBody: false,
              child: LzEmptyState(
                icon: Icons.filter_alt_off_outlined,
                title: 'No expenses match',
                hint: 'Try a different project or clear the filter.',
              ),
            )
          else if (_sort == _LedgerSort.amount)
            _amountSliver(filtered)
          else
            _dateGroupedSliver(filtered, newestFirst: _sort == _LedgerSort.newest),
        ],
      ),
    );
  }

  /// A flat, single-card list sorted by amount (largest first).
  Widget _amountSliver(List<Expense> expenses) {
    final sorted = [...expenses]..sort((a, b) => b.amount.compareTo(a.amount));
    final total = sorted.fold<double>(0, (s, e) => s + e.amount);
    final currency = sorted.isNotEmpty ? sorted.first.currency : 'USD';
    return SliverPadding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.lg,
        AppSpacing.lg,
        AppSpacing.xxxl,
      ),
      sliver: SliverToBoxAdapter(
        child: LzSection(
          title: 'By amount',
          action: Text(
            fmtMoney(currency, total),
            style: AppText.caption.copyWith(
              color: AppColors.textSecondary,
              fontWeight: FontWeight.w700,
            ),
          ),
          child: _expenseCard(sorted),
        ),
      ),
    );
  }

  /// Date-grouped sections, ordered [newestFirst] (or oldest-first).
  Widget _dateGroupedSliver(List<Expense> expenses, {required bool newestFirst}) {
    final byDate = groupBy<Expense, String>(expenses, (e) => dateLabel(e.spentAt));
    final sortedDates = byDate.keys.toList()
      ..sort((a, b) => newestFirst ? b.compareTo(a) : a.compareTo(b));

    return SliverPadding(
      padding: const EdgeInsets.only(bottom: AppSpacing.xxxl),
      sliver: SliverList(
        delegate: SliverChildBuilderDelegate(
          (context, i) {
            final date = sortedDates[i];
            final dayExpenses = byDate[date]!;
            final dayTotal = dayExpenses.fold<double>(0, (s, e) => s + e.amount);
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
                child: _expenseCard(dayExpenses),
              ),
            );
          },
          childCount: sortedDates.length,
        ),
      ),
    );
  }

  Widget _expenseCard(List<Expense> expenses) {
    return LzCard(
      padding: EdgeInsets.zero,
      child: Column(
        children: [
          for (int j = 0; j < expenses.length; j++) ...[
            ExpenseRow(
              expense: expenses[j],
              projects: widget.state.projects,
              pendingSync: widget.state.dirtyExpenseIds.contains(expenses[j].id),
              onDelete: () => widget.onDeleteExpense(expenses[j].id),
              onTap: () => widget.onTapExpense(expenses[j]),
              showProject: true,
            ),
            if (j < expenses.length - 1)
              Divider(
                height: 0.5,
                thickness: 0.5,
                color: AppColors.borderSubtle,
                indent: AppSpacing.lg + 40 + AppSpacing.md,
              ),
          ],
        ],
      ),
    );
  }
}

/// Filter + sort controls for the ledger: a horizontally scrollable project
/// filter row (All · each project · Uncategorized) and a sort selector
/// (Newest · Oldest · Largest).
class _LedgerControls extends StatelessWidget {
  const _LedgerControls({
    required this.projects,
    required this.selectedProjectId,
    required this.showUncategorized,
    required this.sort,
    required this.onProjectChanged,
    required this.onSortChanged,
  });

  final List<Project> projects;
  final String? selectedProjectId;
  final bool showUncategorized;
  final _LedgerSort sort;
  final ValueChanged<String?> onProjectChanged;
  final ValueChanged<_LedgerSort> onSortChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Project filter.
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.lg,
            AppSpacing.md,
            AppSpacing.lg,
            AppSpacing.xs,
          ),
          child: Row(
            children: [
              LzChip(
                label: 'All',
                dense: true,
                selected: selectedProjectId == null,
                color: AppColors.accent,
                onTap: () => onProjectChanged(null),
              ),
              for (final p in projects) ...[
                const SizedBox(width: AppSpacing.sm),
                LzChip(
                  label: p.name,
                  dense: true,
                  selected: selectedProjectId == p.id,
                  color: AppColors.accent,
                  onTap: () => onProjectChanged(p.id),
                ),
              ],
              if (showUncategorized) ...[
                const SizedBox(width: AppSpacing.sm),
                LzChip(
                  label: 'Uncategorized',
                  dense: true,
                  selected: selectedProjectId == _kUncategorizedFilter,
                  color: AppColors.textMuted,
                  onTap: () => onProjectChanged(_kUncategorizedFilter),
                ),
              ],
            ],
          ),
        ),
        // Sort selector.
        Padding(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.lg,
            AppSpacing.xs,
            AppSpacing.lg,
            AppSpacing.xs,
          ),
          child: Row(
            children: [
              Icon(Icons.swap_vert_rounded,
                  size: 16, color: AppColors.textMuted),
              const SizedBox(width: AppSpacing.sm),
              _SortChip(
                label: 'Newest',
                value: _LedgerSort.newest,
                current: sort,
                onTap: onSortChanged,
              ),
              const SizedBox(width: AppSpacing.sm),
              _SortChip(
                label: 'Oldest',
                value: _LedgerSort.oldest,
                current: sort,
                onTap: onSortChanged,
              ),
              const SizedBox(width: AppSpacing.sm),
              _SortChip(
                label: 'Largest',
                value: _LedgerSort.amount,
                current: sort,
                onTap: onSortChanged,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _SortChip extends StatelessWidget {
  const _SortChip({
    required this.label,
    required this.value,
    required this.current,
    required this.onTap,
  });

  final String label;
  final _LedgerSort value;
  final _LedgerSort current;
  final ValueChanged<_LedgerSort> onTap;

  @override
  Widget build(BuildContext context) {
    return LzChip(
      label: label,
      dense: true,
      selected: current == value,
      color: AppColors.info,
      onTap: () => onTap(value),
    );
  }
}
