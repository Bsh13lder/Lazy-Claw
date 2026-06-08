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
    // NOTE: the cold-start deep-link replay lives in [build] via
    // [drainPendingAction] (not a one-shot here) so it survives whichever frame
    // this screen first becomes visible on.
  }

  /// The deep-link actions this screen owns.
  static const Set<AppAction> _myActions = {AppAction.addExpense};

  void _openAddExpenseForAction(AppAction _) =>
      _showAddExpense(ref.read(budgetsProvider));

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

    // Deep-link replay (cold start AND warm tap), made frame-order-proof: a
    // `+ Expense` shortcut/widget may set the pending action BEFORE this screen
    // mounts (cold) or WHILE it's already alive in the indexedStack (warm).
    // [drainPendingAction] re-arms on every build and consumes our action
    // whenever this screen becomes visible — so it never strands.
    drainPendingAction(
      ref,
      mine: _myActions,
      isMounted: () => mounted,
      onDrained: _openAddExpenseForAction,
    );
    // Also react to a change that lands WHILE we're the visible tab.
    ref.listen<AppAction?>(pendingActionProvider, (_, next) {
      drainPendingAction(
        ref,
        mine: _myActions,
        isMounted: () => mounted,
        onDrained: _openAddExpenseForAction,
      );
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
          // Projects section header — a slim label sitting directly above the
          // cards (an empty LzSection here just wasted vertical space).
          if (state.projects.isNotEmpty) ...[
            Padding(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.lg,
                AppSpacing.md,
                AppSpacing.lg,
                AppSpacing.sm,
              ),
              child: Text(
                'PROJECTS',
                style: AppText.caption.copyWith(
                  color: AppColors.textMuted,
                  letterSpacing: 0.8,
                  fontWeight: FontWeight.w700,
                ),
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

  /// Time window the ledger is scoped to. Defaults to the current month.
  ExpenseRange _range = ExpenseRange.month;

  /// Month displacement when [_range] is [ExpenseRange.month]: 0 = current
  /// month, -1 = previous, etc. Driven by the `‹ month ›` stepper. Clamped so it
  /// never steps past the current month (no empty future windows).
  int _monthOffset = 0;

  /// The picked window when [_range] is [ExpenseRange.custom] (null otherwise).
  DateTimeRange? _customRange;

  /// Whether any visible expense no longer maps to a live project.
  bool _hasUncategorized(List<Expense> visible, Set<String> liveIds) =>
      visible.any((e) => !liveIds.contains(e.projectId));

  /// Switch the active time range. Today/Week/Month/All apply immediately;
  /// Custom opens a date-range picker and only switches once a range is chosen
  /// (or one already exists), so a cancelled picker never strands the ledger on
  /// an empty range. Selecting Month always re-anchors the stepper to the
  /// current month (offset 0).
  Future<void> _onRangeChanged(ExpenseRange range) async {
    if (range != ExpenseRange.custom) {
      setState(() {
        _range = range;
        if (range == ExpenseRange.month) _monthOffset = 0;
      });
      return;
    }
    final now = DateTime.now();
    final picked = await showDateRangePicker(
      context: context,
      firstDate: DateTime(now.year - 5),
      lastDate: DateTime(now.year + 1, 12, 31),
      initialDateRange: _customRange,
    );
    if (!mounted) return;
    if (picked != null) {
      setState(() {
        _customRange = picked;
        _range = ExpenseRange.custom;
      });
    } else if (_customRange != null) {
      // Re-selecting Custom without changing the dates: keep the prior window.
      setState(() => _range = ExpenseRange.custom);
    }
    // Otherwise (cancelled with no prior custom range) stay on the current range.
  }

  /// Step the month window by [delta] (−1 = older, +1 = newer). Forward steps
  /// clamp at the current month (offset 0) so the user can't land on an empty
  /// future window. Ensures Month is the active range.
  void _stepMonth(int delta) {
    setState(() {
      final next = _monthOffset + delta;
      _monthOffset = next > 0 ? 0 : next;
      _range = ExpenseRange.month;
    });
  }

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

    // Scope to the active time window first (offline, in-memory), then compose
    // with the project filter. Sort happens in the sliver builders below.
    final ranged = filterByRange(
      visible,
      _range,
      monthOffset: _monthOffset,
      customStart: _customRange?.start,
      customEnd: _customRange?.end,
    );
    final filtered = _applyFilter(ranged, liveIds);

    final currency = filtered.isNotEmpty
        ? filtered.first.currency
        : (visible.isNotEmpty ? visible.first.currency : 'USD');
    final controls = _LedgerControls(
      projects: state.projects,
      selectedProjectId: _projectFilter,
      showUncategorized: _hasUncategorized(ranged, liveIds),
      sort: _sort,
      range: _range,
      rangeLabel: expenseRangeLabel(
        _range,
        monthOffset: _monthOffset,
        customStart: _customRange?.start,
        customEnd: _customRange?.end,
      ),
      rangeTotalText: fmtMoney(currency, rangeTotal(filtered)),
      // Forward (newer) month step is disabled once we're on the current month.
      monthForwardEnabled: _monthOffset < 0,
      onProjectChanged: (id) => setState(() => _projectFilter = id),
      onSortChanged: (s) => setState(() => _sort = s),
      onRangeChanged: _onRangeChanged,
      onMonthStep: _stepMonth,
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
                hint: 'Try a different project or time range.',
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
          action: _SectionTotal(text: fmtMoney(currency, total)),
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
                action: _SectionTotal(text: fmtMoney(currency, dayTotal)),
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

/// Compact filter + sort controls for the ledger, tightened to three slim rows:
///
/// 1. A horizontally-scrollable range toggle (Today · Week · Month · All ·
///    Custom).
/// 2. A single resolved-window line: when Month is active it IS the
///    `‹ June 2026 ›` stepper (one-tap month selection), otherwise an icon +
///    range label; the window's running total is bold-right, with a compact sort
///    menu (Newest · Oldest · Largest) trailing.
/// 3. A horizontally-scrollable project filter (All · each project ·
///    Uncategorized).
class _LedgerControls extends StatelessWidget {
  const _LedgerControls({
    required this.projects,
    required this.selectedProjectId,
    required this.showUncategorized,
    required this.sort,
    required this.range,
    required this.rangeLabel,
    required this.rangeTotalText,
    required this.monthForwardEnabled,
    required this.onProjectChanged,
    required this.onSortChanged,
    required this.onRangeChanged,
    required this.onMonthStep,
  });

  final List<Project> projects;
  final String? selectedProjectId;
  final bool showUncategorized;
  final _LedgerSort sort;

  /// The active time window + its resolved label and running total (already
  /// scoped to the shown project filter).
  final ExpenseRange range;
  final String rangeLabel;
  final String rangeTotalText;

  /// Whether the `›` (newer) month step is available (false on the current
  /// month, where it dims to signal the clamp).
  final bool monthForwardEnabled;
  final ValueChanged<String?> onProjectChanged;
  final ValueChanged<_LedgerSort> onSortChanged;
  final ValueChanged<ExpenseRange> onRangeChanged;

  /// Step the month window by ±1 (the `‹ ›` chevrons).
  final ValueChanged<int> onMonthStep;

  @override
  Widget build(BuildContext context) {
    final isMonth = range == ExpenseRange.month;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Row 1 — range toggle (scrolls if it overflows narrow screens).
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.lg,
            AppSpacing.md,
            AppSpacing.lg,
            AppSpacing.sm,
          ),
          child: Row(
            children: [
              _RangeChip(
                label: 'Today',
                value: ExpenseRange.today,
                current: range,
                onTap: onRangeChanged,
              ),
              const SizedBox(width: AppSpacing.sm),
              _RangeChip(
                label: 'Week',
                value: ExpenseRange.week,
                current: range,
                onTap: onRangeChanged,
              ),
              const SizedBox(width: AppSpacing.sm),
              _RangeChip(
                label: 'Month',
                value: ExpenseRange.month,
                current: range,
                onTap: onRangeChanged,
              ),
              const SizedBox(width: AppSpacing.sm),
              _RangeChip(
                label: 'All',
                value: ExpenseRange.all,
                current: range,
                onTap: onRangeChanged,
              ),
              const SizedBox(width: AppSpacing.sm),
              _RangeChip(
                label: 'Custom',
                value: ExpenseRange.custom,
                current: range,
                icon: Icons.date_range_rounded,
                onTap: onRangeChanged,
              ),
            ],
          ),
        ),
        // Row 2 — resolved window (stepper when Month) + total + sort menu.
        Padding(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.lg,
            0,
            AppSpacing.sm,
            AppSpacing.xs,
          ),
          child: Row(
            children: [
              if (isMonth) ...[
                _MonthStepper(
                  label: rangeLabel,
                  forwardEnabled: monthForwardEnabled,
                  onStep: onMonthStep,
                ),
                const Spacer(),
              ] else
                Expanded(
                  child: Row(
                    children: [
                      Icon(Icons.event_note_outlined,
                          size: 14, color: AppColors.textMuted),
                      const SizedBox(width: AppSpacing.xs),
                      Flexible(
                        child: Text(
                          rangeLabel,
                          style: AppText.caption
                              .copyWith(color: AppColors.textMuted),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ),
                ),
              Text(
                rangeTotalText,
                style: AppText.body.copyWith(
                  color: AppColors.textPrimary,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(width: AppSpacing.xs),
              _SortMenu(sort: sort, onChanged: onSortChanged),
            ],
          ),
        ),
        // Row 3 — project filter.
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.lg,
            AppSpacing.xs,
            AppSpacing.lg,
            AppSpacing.sm,
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
      ],
    );
  }
}

/// A one-tap `‹ June 2026 ›` month stepper. The forward chevron dims when
/// [forwardEnabled] is false (we're on the current month — no future windows).
class _MonthStepper extends StatelessWidget {
  const _MonthStepper({
    required this.label,
    required this.forwardEnabled,
    required this.onStep,
  });

  final String label;
  final bool forwardEnabled;
  final ValueChanged<int> onStep;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        LzIconButton(
          icon: Icons.chevron_left_rounded,
          tooltip: 'Previous month',
          size: 20,
          color: AppColors.textSecondary,
          onPressed: () => onStep(-1),
        ),
        Text(
          label,
          style: AppText.label.copyWith(
            color: AppColors.textPrimary,
            fontWeight: FontWeight.w700,
          ),
        ),
        LzIconButton(
          icon: Icons.chevron_right_rounded,
          tooltip: 'Next month',
          size: 20,
          color: forwardEnabled ? AppColors.textSecondary : AppColors.textMuted,
          onPressed: forwardEnabled ? () => onStep(1) : null,
        ),
      ],
    );
  }
}

/// Compact trailing sort control: a popup menu (Newest · Oldest · Largest) that
/// shows the active option inline, replacing the old full sort-chip row.
class _SortMenu extends StatelessWidget {
  const _SortMenu({required this.sort, required this.onChanged});

  final _LedgerSort sort;
  final ValueChanged<_LedgerSort> onChanged;

  static String _label(_LedgerSort s) => switch (s) {
        _LedgerSort.newest => 'Newest',
        _LedgerSort.oldest => 'Oldest',
        _LedgerSort.amount => 'Largest',
      };

  @override
  Widget build(BuildContext context) {
    return PopupMenuButton<_LedgerSort>(
      initialValue: sort,
      tooltip: 'Sort',
      onSelected: onChanged,
      color: AppColors.bgSurfaceElevated,
      shape: const RoundedRectangleBorder(borderRadius: AppRadii.rMd),
      itemBuilder: (_) => [
        _item(_LedgerSort.newest),
        _item(_LedgerSort.oldest),
        _item(_LedgerSort.amount),
      ],
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.sm,
          vertical: AppSpacing.xs + 1,
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.swap_vert_rounded, size: 15, color: AppColors.textMuted),
            const SizedBox(width: AppSpacing.xs),
            Text(
              _label(sort),
              style: AppText.caption.copyWith(
                color: AppColors.textSecondary,
                fontWeight: FontWeight.w600,
              ),
            ),
            Icon(Icons.arrow_drop_down_rounded,
                size: 16, color: AppColors.textMuted),
          ],
        ),
      ),
    );
  }

  PopupMenuItem<_LedgerSort> _item(_LedgerSort value) {
    final selected = value == sort;
    return PopupMenuItem<_LedgerSort>(
      value: value,
      height: 42,
      child: Row(
        children: [
          SizedBox(
            width: 18,
            child: selected
                ? Icon(Icons.check_rounded, size: 16, color: AppColors.accent)
                : null,
          ),
          const SizedBox(width: AppSpacing.xs),
          Text(
            _label(value),
            style: AppText.body.copyWith(
              color: selected ? AppColors.accent : AppColors.textSecondary,
              fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }
}

/// A compact running-total badge for a ledger section header (a date group or
/// the by-amount list). A subtle accent-tinted pill keeps the figure legible
/// and consistent across sections without competing with the row amounts.
class _SectionTotal extends StatelessWidget {
  const _SectionTotal({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.sm,
        vertical: 2,
      ),
      decoration: BoxDecoration(
        color: AppColors.accent.withValues(alpha: 0.10),
        borderRadius: AppRadii.rPill,
      ),
      child: Text(
        text,
        style: AppText.caption.copyWith(
          color: AppColors.accent,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

/// A segmented time-range option chip (Today · Week · Month · All · Custom).
class _RangeChip extends StatelessWidget {
  const _RangeChip({
    required this.label,
    required this.value,
    required this.current,
    required this.onTap,
    this.icon,
  });

  final String label;
  final ExpenseRange value;
  final ExpenseRange current;
  final ValueChanged<ExpenseRange> onTap;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return LzChip(
      label: label,
      dense: true,
      icon: icon,
      selected: current == value,
      color: AppColors.accent,
      onTap: () => onTap(value),
    );
  }
}
