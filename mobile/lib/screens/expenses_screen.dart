import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/actions/app_actions.dart';
import '../models/budget_entry.dart';
import '../models/expense.dart';
import '../models/inbox_suggestion.dart';
import '../models/project.dart';
import '../providers/budgets_provider.dart';
import '../providers/tasks_provider.dart'
    show reachableProvider, dbHealthProvider, tasksProvider;
import '../ui/ui.dart';
import 'expenses/add_expense_sheet.dart';
import 'expenses/budget_log_sheet.dart';
import 'expenses/budget_math.dart';
import 'expenses/budget_summary_card.dart';
import 'expenses/bulk_assign.dart';
import 'expenses/credit_row.dart';
import 'expenses/edit_project_sheet.dart';
import 'expenses/expense_detail_sheet.dart';
import 'expenses/expense_row.dart';
import 'expenses/money_helpers.dart';
import 'expenses/project_card.dart';
import 'expenses/selection_mode.dart';
import 'storage_banners.dart';

/// Sort order for the expense ledger.
enum _LedgerSort { newest, oldest, amount }

/// Sentinel filter value for expenses that no longer map to a live project
/// (e.g. their project was deleted locally before the sync caught up).
const String _kUncategorizedFilter = '__uncategorized__';

/// Sentinel filter value scoping the ledger to the starred (favorite)
/// projects — the same set the Home dashboard and the Overview tab lead with.
const String _kFavoritesFilter = '__favorites__';

/// Extra bottom padding reserved for the Ledger's scroll content while the
/// pinned bulk-action bar is showing (see `_LedgerTabState.build`), so the
/// bottom-anchored overlay can never cover the last list row or the balance /
/// connect-hint footer beneath it.
const double _kBulkBarReserve = 88;

class ExpensesScreen extends ConsumerStatefulWidget {
  const ExpensesScreen({super.key});

  @override
  ConsumerState<ExpensesScreen> createState() => _ExpensesScreenState();
}

class _ExpensesScreenState extends ConsumerState<ExpensesScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabController;
  String? _lastSelectedProjectId;

  /// Whether the Ledger's pinned bulk-action bar is currently showing.
  /// Reported up by `_LedgerTab` (see `onBulkBarVisibleChanged`) since the
  /// FAB is owned here but the selection-mode state lives in the tab's own
  /// state. The FAB sits bottom-right — exactly where the pinned bar's
  /// trailing controls (Auto, Cancel) live — so it must hide while the bar
  /// is on screen, or it visually covers those controls AND intercepts
  /// their taps.
  bool _bulkBarVisible = false;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this);
    Future.microtask(() => ref.read(budgetsProvider.notifier).load());
    // NOTE: the cold-start deep-link replay lives in [build] via
    // [drainPendingAction] (not a one-shot here) so it survives whichever frame
    // this screen first becomes visible on.
  }

  /// Callback handed to `_LedgerTab`; called (post-frame, from the tab's own
  /// build) whenever its pinned bulk-bar visibility changes. No-ops when the
  /// value hasn't actually changed so it doesn't schedule a needless rebuild
  /// of this whole screen on every Ledger tab build.
  void _handleBulkBarVisibleChanged(bool visible) {
    if (visible == _bulkBarVisible) return;
    setState(() => _bulkBarVisible = visible);
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
        onSubmit: (name, budget, color, startDate, dueDate) =>
            ref.read(budgetsProvider.notifier).createProject(
                  name,
                  budget: budget,
                  color: color,
                  startDate: startDate,
                  dueDate: dueDate,
                ),
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
        onSetDates: (startDate, dueDate) => notifier.setProjectDates(
          project.id,
          startDate: startDate,
          dueDate: dueDate,
        ),
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
      floatingActionButton: _bulkBarVisible ? null : _buildFAB(state),
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
          onToggleExpenseFavorite: (id) =>
              ref.read(budgetsProvider.notifier).toggleExpenseFavorite(id),
          onRefresh: _refresh,
          onBulkBarVisibleChanged: _handleBulkBarVisibleChanged,
        ),
      ],
    );
  }
}

// ── Overview Tab ─────────────────────────────────────────────────────────────

class _OverviewTab extends StatefulWidget {
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
  State<_OverviewTab> createState() => _OverviewTabState();
}

class _OverviewTabState extends State<_OverviewTab> {
  /// When true, the Overview narrows to just the projects that own a starred
  /// expense (each card scoped to its starred rows). Defaults to false so first
  /// use shows everything — the star subtotal alone is enough of a hint.
  bool _starredOnly = false;

  @override
  Widget build(BuildContext context) {
    final state = widget.state;

    // Hero "TOTAL SPENT" reflects the user's STARRED (favorite) projects when
    // any exist — the headline tracks the projects they pinned, not every
    // project summed. Falls back to all projects when nothing is starred.
    // Currency-aware so mixed currencies aren't summed.
    final totals = heroTotals(state.projects, state.expenses);
    final heroScopedToFavorites = hasFavoriteProjects(state.projects);

    // The "★ Starred only" subtotal — sum of just the pinned expenses, scoped
    // to the same headline currency the hero card uses.
    final starredTotal =
        starredExpenseTotal(state.expenses, currency: totals.currency);
    final starredExpenseIds = state.expenses
        .where((e) => e.isFavorite && !e.isVoid)
        .map((e) => e.projectId)
        .toSet();
    final hasStarred = starredExpenseIds.isNotEmpty;
    // A stale toggle collapses back to "show all" once the last star is gone.
    final starredOnly = _starredOnly && hasStarred;

    // Favorites lead — the same starred (project-level) set the Home dashboard
    // pins, so the projects the user actually budgets against sit on top instead
    // of being buried in a flat created-at list.
    final favorites = state.projects
        .where((p) => p.isFavorite && !p.isArchived)
        .toList();
    final favoriteIds = favorites.map((p) => p.id).toSet();
    final others =
        state.projects.where((p) => !favoriteIds.contains(p.id)).toList();

    // Starred-only mode: a flat list of just the projects with a starred
    // expense (each card is scoped to its starred rows below).
    final starredProjects =
        state.projects.where((p) => starredExpenseIds.contains(p.id)).toList();

    return LzRefresh(
      onRefresh: widget.onRefresh,
      child: ListView(
        padding: EdgeInsets.zero,
        children: [
          // Hero summary card.
          BudgetSummaryCard(
            totals: totals,
            scopedToFavorites: heroScopedToFavorites,
          ),
          // Visible starred subtotal + the show-all/starred-only toggle. Only
          // surfaces once at least one expense is starred.
          if (hasStarred)
            _StarredControl(
              starredOnly: starredOnly,
              subtotalText: fmtMoney(totals.currency, starredTotal),
              onChanged: (v) => setState(() => _starredOnly = v),
            ),
          if (starredOnly) ...[
            const _SectionLabel('★ STARRED EXPENSES'),
            ...starredProjects.map((p) => _projectCard(p, starredOnly: true)),
          ] else ...[
            if (favorites.isNotEmpty) ...[
              const _SectionLabel('★ FAVORITES'),
              ...favorites.map((p) => _projectCard(p)),
            ],
            if (others.isNotEmpty) ...[
              _SectionLabel(favorites.isEmpty ? 'PROJECTS' : 'OTHER PROJECTS'),
              // No favorites yet → teach the star (same affordance as Home).
              if (favorites.isEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(
                    AppSpacing.lg,
                    0,
                    AppSpacing.lg,
                    AppSpacing.sm,
                  ),
                  child: Text(
                    'Star a project to pin it here and on Home.',
                    style:
                        AppText.caption.copyWith(color: AppColors.textMuted),
                  ),
                ),
              ...others.map((p) => _projectCard(p)),
            ],
          ],
          if (state.projects.isNotEmpty)
            const SizedBox(height: AppSpacing.xxxl),
        ],
      ),
    );
  }

  Widget _projectCard(Project p, {bool starredOnly = false}) {
    final projectExpenses = widget.state.expenses
        .where((e) =>
            e.projectId == p.id &&
            !e.isVoid &&
            (!starredOnly || e.isFavorite))
        .toList();
    return ProjectCard(
      project: p,
      expenses: projectExpenses,
      pendingSync: widget.state.dirtyProjectIds.contains(p.id),
      onDelete: () => widget.onDeleteProject(p.id),
      onEdit: () => widget.onEditProject(p),
      onToggleFavorite: () => widget.onToggleFavorite(p.id),
    );
  }
}

/// The Overview's "★ Starred only" control: a toggle chip plus an always-visible
/// starred subtotal pill (sum of just the pinned expenses). Defaults to showing
/// everything — the toggle only narrows the view when the user opts in.
class _StarredControl extends StatelessWidget {
  const _StarredControl({
    required this.starredOnly,
    required this.subtotalText,
    required this.onChanged,
  });

  final bool starredOnly;
  final String subtotalText;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.xs,
        AppSpacing.lg,
        0,
      ),
      child: Row(
        children: [
          LzChip(
            label: '★ Starred only',
            dense: true,
            selected: starredOnly,
            color: AppColors.warn,
            onTap: () => onChanged(!starredOnly),
          ),
          const Spacer(),
          Container(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.sm,
              vertical: 2,
            ),
            decoration: BoxDecoration(
              color: AppColors.warn.withValues(alpha: 0.12),
              borderRadius: AppRadii.rPill,
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.star_rounded, size: 13, color: AppColors.warn),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  subtotalText,
                  style: AppText.caption.copyWith(
                    color: AppColors.warn,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Slim uppercase section label used above project card groups.
class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.md,
        AppSpacing.lg,
        AppSpacing.sm,
      ),
      child: Text(
        text,
        style: AppText.caption.copyWith(
          color: AppColors.textMuted,
          letterSpacing: 0.8,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

// ── Ledger Tab ────────────────────────────────────────────────────────────────

class _LedgerTab extends ConsumerStatefulWidget {
  const _LedgerTab({
    required this.state,
    required this.onDeleteExpense,
    required this.onTapExpense,
    required this.onToggleExpenseFavorite,
    required this.onRefresh,
    required this.onBulkBarVisibleChanged,
  });

  final BudgetsState state;
  final void Function(String id) onDeleteExpense;
  final void Function(Expense expense) onTapExpense;
  final void Function(String id) onToggleExpenseFavorite;
  final Future<void> Function() onRefresh;

  /// Reports whenever the pinned bulk-action bar's visibility changes, so
  /// the parent `ExpensesScreen` (which owns the FAB) can hide it while the
  /// bar is on screen — see `_ExpensesScreenState._handleBulkBarVisibleChanged`.
  final ValueChanged<bool> onBulkBarVisibleChanged;

  @override
  ConsumerState<_LedgerTab> createState() => _LedgerTabState();
}

class _LedgerTabState extends ConsumerState<_LedgerTab> {
  /// null = all projects; a project id; or [_kUncategorizedFilter].
  String? _projectFilter;
  _LedgerSort _sort = _LedgerSort.newest;

  /// Budget top-ups (credits) fetched from the ONLINE-only budget ledger, keyed
  /// nowhere — a flat list across the live projects. `null` means "not loaded"
  /// (offline / never fetched / failed): the ledger then shows expenses ONLY, no
  /// credit rows and no (misleading) balance. A non-null list (even empty) means
  /// the fetch succeeded, so credits + the balance summary are safe to show.
  List<BudgetEntry>? _entries;

  /// True while a budget-entries fetch is in flight (suppresses the offline
  /// hint from flashing during the initial online load).
  bool _entriesLoading = false;

  /// Monotonic token so a slow fetch that resolves after a newer one can't
  /// clobber the fresher result.
  int _loadGen = 0;

  /// Time window the ledger is scoped to. Defaults to All so the full history
  /// of budget top-ups (which are sparse — often one per month) is visible the
  /// moment the Ledger opens; the range chips + month stepper narrow it. A
  /// month default silently hid every top-up made in a prior month, which made
  /// budget tracking look broken ("only shows spends").
  ExpenseRange _range = ExpenseRange.all;

  /// Month displacement when [_range] is [ExpenseRange.month]: 0 = current
  /// month, -1 = previous, etc. Driven by the `‹ month ›` stepper. Clamped so it
  /// never steps past the current month (no empty future windows).
  int _monthOffset = 0;

  /// The picked window when [_range] is [ExpenseRange.custom] (null otherwise).
  DateTimeRange? _customRange;

  // ── Inbox bulk-select (Task 5) ─────────────────────────────────────────
  //
  // Only meaningful while filtered to the inbox project. Entered via a
  // long-press on a row (see `ExpenseRow.onLongPress`, wired only in that
  // filter), exited via the bulk bar's ✕ or by changing the project filter.

  /// True once a long-press has entered bulk-selection mode. Kept separate
  /// from `_selected.isEmpty` because Auto must still be tappable with
  /// nothing selected (it then scans the whole inbox) — deselecting the last
  /// row must NOT silently exit the mode out from under the user.
  bool _selectionMode = false;

  /// Ids of the expenses currently selected for a bulk action.
  Set<String> _selected = {};

  /// AI suggestions from the last Auto fetch, keyed by expense id. Cleared
  /// whenever the selection is applied, cancelled, or the filter leaves the
  /// inbox — a stale suggestion must never outlive the rows it describes.
  Map<String, InboxSuggestion> _suggestions = {};

  /// ONE shared busy lock across every bulk action — bulk-assign, the Auto
  /// suggestion fetch, and applying confident suggestions all PATCH the same
  /// rows, so only one may run at a time. Every bulk-bar button (and the
  /// cancel affordance) must gate on this, not a per-action flag.
  bool _bulkBusy = false;

  void _enterSelection(String expenseId) {
    setState(() {
      _selectionMode = true;
      _selected = {..._selected, expenseId};
    });
  }

  void _toggleSelected(String expenseId) {
    setState(() {
      final next = {..._selected};
      if (!next.remove(expenseId)) next.add(expenseId);
      _selected = next;
    });
  }

  void _cancelSelection() {
    setState(() {
      _selectionMode = false;
      _selected = {};
      _suggestions = {};
    });
  }

  /// A human label for a bulk-action failure summary — the expense's
  /// description, falling back to vendor, then the formatted amount — so two
  /// failures never render as identical, unattributable strings.
  String _expenseLabel(String expenseId) {
    for (final e in widget.state.expenses) {
      if (e.id == expenseId) {
        final d = e.displayDescription;
        return d.isNotEmpty ? d : fmtMoney(e.currency, e.amount);
      }
    }
    return expenseId;
  }

  Future<void> _openAssignSheet(
    List<Project> projects,
    String? inboxProjectId,
  ) async {
    if (_bulkBusy || _selected.isEmpty) return;
    final assignable =
        projects.where((p) => p.id != inboxProjectId).toList();
    if (assignable.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Create another project first.')),
      );
      return;
    }
    final allTasks = ref.read(tasksProvider).tasks;
    final result = await LzBottomSheet.show<(String, String?)>(
      context,
      title:
          'Assign ${_selected.length} expense${_selected.length == 1 ? '' : 's'}',
      builder: (_) => BulkAssignSheet(projects: assignable, allTasks: allTasks),
    );
    if (result == null || !mounted) return;
    await _runBulkAssign(result.$1, result.$2);
  }

  /// Sequential PATCH loop over a SNAPSHOT of the current selection (taken
  /// before the loop starts, so a mutation mid-flight — not currently
  /// possible since the bar disables while busy, but a safe habit regardless)
  /// can never change what's being moved out from under the loop. Each PATCH
  /// clears the expense's old task link (`taskIdSet: true` even when `taskId`
  /// is null) — a moved expense's old task belonged to the old project. The
  /// sub-task link is cleared alongside it (`subtaskIdSet: true`,
  /// `subtaskId: null`) for the same reason AND because the server rejects a
  /// `subtask_id` that survives a `task_id` change — a sub-task belongs to
  /// exactly one task, so a stale one would 400 the whole PATCH.
  Future<void> _runBulkAssign(String targetProjectId, String? taskId) async {
    if (_bulkBusy || _selected.isEmpty) return;
    final ids = _selected.toList();
    setState(() => _bulkBusy = true);
    var moved = 0;
    final failures = <String>[];
    for (final id in ids) {
      final ok = await ref.read(budgetsProvider.notifier).updateExpense(
            id,
            projectId: targetProjectId,
            taskId: taskId,
            taskIdSet: true,
            subtaskId: null,
            subtaskIdSet: true,
          );
      if (ok) {
        moved++;
      } else {
        failures.add(_expenseLabel(id));
      }
    }
    await widget.onRefresh();
    if (!mounted) return;
    setState(() {
      _bulkBusy = false;
      _selectionMode = false;
      _selected = {};
      _suggestions = {};
    });
    _showBulkSnack(moved: moved, total: ids.length, failures: failures);
  }

  /// Fetches AI suggestions for the current selection (or the whole inbox
  /// when nothing is selected — server caps that scan at 10) and opens the
  /// preview sheet. This is an ONLINE-only feature: any failure (including
  /// the `DioException(error: ApiError(...))` shape the interceptor throws)
  /// surfaces as a plain "needs connection" hint, never a raw error string.
  Future<void> _openAutoAssign() async {
    if (_bulkBusy) return;
    setState(() => _bulkBusy = true);
    final ids = _selected.isEmpty ? null : _selected.toList();
    ({List<InboxSuggestion> suggestions, int skipped})? result;
    var failureMessage = 'Auto-assign needs a connection. Try again online.';
    try {
      result = await ref
          .read(budgetsRepositoryProvider)
          .getInboxSuggestions(expenseIds: ids);
    } on DioException catch (_) {
      // The expected offline/API-failure shape (wraps ApiError) — the plain
      // "needs a connection" hint below covers it.
      result = null;
    } catch (e) {
      // Anything else is unexpected: surface loudly in debug so it can't hide
      // behind a generic snackbar, but fail soft in release — same
      // log-and-fall-back idiom as `budget_log_sheet._friendly` /
      // `ChatScreen._connect`.
      if (kDebugMode) rethrow;
      debugPrint('Auto-assign suggestions failed: $e');
      result = null;
      failureMessage = 'Something went wrong. Try again.';
    }
    if (!mounted) return;
    setState(() => _bulkBusy = false);
    if (result == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(failureMessage)),
      );
      return;
    }
    final suggestions = result.suggestions;
    setState(() {
      _suggestions = {for (final s in suggestions) s.expenseId: s};
    });
    final apply = await LzBottomSheet.show<bool>(
      context,
      title: 'Auto-assign suggestions',
      builder: (_) => AutoPreviewSheet(
        suggestions: suggestions,
        skipped: result!.skipped,
        labelFor: _expenseLabel,
      ),
    );
    if (apply == true && mounted) {
      await _applyConfidentSuggestions();
    }
  }

  /// Applies every high/medium-confidence, matched suggestion
  /// ([confidentSuggestions]) via the same clear-on-move PATCH semantics as
  /// [_runBulkAssign] (task AND sub-task link cleared, project set).
  Future<void> _applyConfidentSuggestions() async {
    if (_bulkBusy) return;
    final confident = confidentSuggestions(_suggestions.values.toList());
    if (confident.isEmpty) return;
    setState(() => _bulkBusy = true);
    var moved = 0;
    final failures = <String>[];
    for (final s in confident) {
      final ok = await ref.read(budgetsProvider.notifier).updateExpense(
            s.expenseId,
            projectId: s.projectId!,
            taskId: null,
            taskIdSet: true,
            subtaskId: null,
            subtaskIdSet: true,
          );
      if (ok) {
        moved++;
      } else {
        failures.add(_expenseLabel(s.expenseId));
      }
    }
    await widget.onRefresh();
    if (!mounted) return;
    setState(() {
      _bulkBusy = false;
      _selectionMode = false;
      _selected = {};
      _suggestions = {};
    });
    _showBulkSnack(moved: moved, total: confident.length, failures: failures);
  }

  void _showBulkSnack({
    required int moved,
    required int total,
    required List<String> failures,
  }) {
    final msg = failures.isEmpty
        ? 'Moved $moved of $total.'
        : 'Moved $moved of $total. Failed: ${failures.join('; ')}';
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  /// Whether any visible expense no longer maps to a live project.
  bool _hasUncategorized(List<Expense> visible, Set<String> liveIds) =>
      visible.any((e) => !liveIds.contains(e.projectId));

  /// The auto-created "Inbox" project (`Project.isInbox`), or null if none
  /// exists yet. A plain loop stands in for `firstWhereOrNull` — `collection`
  /// is only a transitive dep here, not one this app declares directly.
  Project? _findInboxProject(List<Project> projects) {
    for (final p in projects) {
      if (p.isInbox) return p;
    }
    return null;
  }

  @override
  void initState() {
    super.initState();
    // Post-frame so the first setState lands after mount (never during
    // initState/build). Fetch is best-effort; failure just leaves credits off.
    WidgetsBinding.instance.addPostFrameCallback((_) => _loadEntries());
  }

  @override
  void didUpdateWidget(covariant _LedgerTab oldWidget) {
    super.didUpdateWidget(oldWidget);
    // The online credit ledger is fetched imperatively (it isn't part of the
    // offline cache), so it can go stale when the underlying data moves. Re-
    // fetch when the project set OR any project's budget total changes:
    //  • projects loaded after our first build (initState saw an empty set),
    //  • a project was added / removed,
    //  • a budget top-up bumped projects.budget (a new credit likely exists —
    //    the Budget log sheet refreshes the provider after every add).
    // Without this the tab latched whatever it saw on first mount, so a top-up
    // added later only appeared after a manual pull-to-refresh.
    if (_budgetSignature(oldWidget.state.projects) !=
        _budgetSignature(widget.state.projects)) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _loadEntries();
      });
    }
  }

  /// A cheap signature of the projects whose change should trigger a credit
  /// re-fetch: the sorted `id:budget` pairs. The id set covers add/remove and
  /// late-load; the budget total covers a top-up (which bumps projects.budget).
  String _budgetSignature(List<Project> projects) {
    final parts = projects.map((p) => '${p.id}:${p.budget}').toList()..sort();
    return parts.join('|');
  }

  /// Fetch the budget top-up ledger for every live project in parallel and
  /// combine into [_entries]. ONLINE-only (budget entries aren't part of the
  /// offline cache): if we're offline OR any fetch throws we keep [_entries]
  /// null so the ledger stays expenses-only and never shows a misleading
  /// balance. The expense list itself never blocks on this.
  Future<void> _loadEntries() async {
    // NOTE: intentionally NOT gated on `reachableProvider`. That probe can read
    // "offline" on a network where the API is in fact reachable (self-heal /
    // remote-URL edge — the exact case that made top-ups vanish while cached
    // expenses still showed). This fetch uses the SAME ApiClient that syncs
    // expenses, so whenever the server is reachable at all it succeeds; a
    // genuine outage falls through to the all-failed branch below (→ hint).
    final projects =
        widget.state.projects.where((p) => p.id.isNotEmpty).toList();
    if (projects.isEmpty) return;
    final gen = ++_loadGen;
    if (mounted) setState(() => _entriesLoading = true);
    final repo = ref.read(budgetsRepositoryProvider);
    // Per-project so one project's transient failure can't hide another's
    // credits — fail-fast `Future.wait` used to null the WHOLE ledger if any
    // single request threw. Only an ALL-failed round (genuine offline) collapses
    // to the connect hint; a partial success still shows what loaded.
    var anyOk = false;
    final lists = await Future.wait(
      projects.map((p) => repo.listBudgetEntries(p.id).then(
            (l) {
              anyOk = true;
              return l;
            },
            onError: (_) => <BudgetEntry>[],
          )),
    );
    if (!mounted || gen != _loadGen) return;
    setState(() {
      // Online round succeeded → show fresh server credits. All-failed (genuine
      // offline) → fall back to the synced local cache so the Ledger tab still
      // shows top-ups offline, instead of nulling to the connect hint. Only when
      // the cache is ALSO empty do we fall through to null (no misleading zero).
      _entries = anyOk
          ? [for (final l in lists) ...l]
          : (widget.state.budgetEntries.isNotEmpty
              ? widget.state.budgetEntries
              : null);
      _entriesLoading = false;
    });
  }

  /// Pull-to-refresh: run the parent's sync-refresh, then re-fetch the credits.
  Future<void> _handleRefresh() async {
    await widget.onRefresh();
    await _loadEntries();
  }

  /// Apply the active project filter to the (already range-filtered) budget
  /// [entries], mirroring [_applyFilter] for expenses so credits obey the exact
  /// same project scoping.
  List<BudgetEntry> _applyEntryFilter(
    List<BudgetEntry> entries,
    Set<String> liveIds,
    Set<String> favoriteIds,
  ) {
    final filter = _projectFilter;
    if (filter == null) return entries;
    if (filter == _kUncategorizedFilter) {
      return entries.where((e) => !liveIds.contains(e.projectId)).toList();
    }
    if (filter == _kFavoritesFilter) {
      return entries.where((e) => favoriteIds.contains(e.projectId)).toList();
    }
    return entries.where((e) => e.projectId == filter).toList();
  }

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

  List<Expense> _applyFilter(
    List<Expense> visible,
    Set<String> liveIds,
    Set<String> favoriteIds,
  ) {
    final filter = _projectFilter;
    if (filter == null) return visible;
    if (filter == _kUncategorizedFilter) {
      return visible.where((e) => !liveIds.contains(e.projectId)).toList();
    }
    if (filter == _kFavoritesFilter) {
      return visible.where((e) => favoriteIds.contains(e.projectId)).toList();
    }
    return visible.where((e) => e.projectId == filter).toList();
  }

  @override
  Widget build(BuildContext context) {
    final state = widget.state;

    // When the backend returns, (re)fetch the ONLINE credit ledger so top-ups +
    // the balance appear without a manual pull-to-refresh.
    ref.listen<bool>(reachableProvider, (prev, next) {
      if (next == true && prev != true && _entries == null) {
        _loadEntries();
      }
    });

    final visible = state.expenses.where((e) => !e.isVoid).toList();

    if (visible.isEmpty) {
      return LzEmptyState(
        icon: Icons.receipt_long_outlined,
        title: 'No expenses yet',
        hint: 'Tap + Expense to log your first one.',
      );
    }

    final liveIds = state.projects.map((p) => p.id).toSet();
    final favoriteIds = state.projects
        .where((p) => p.isFavorite && !p.isArchived)
        .map((p) => p.id)
        .toSet();
    final inboxProject = _findInboxProject(state.projects);
    // A stale filter (its project was just deleted, or the last favorite was
    // un-starred) collapses back to "All".
    if (_projectFilter != null &&
        _projectFilter != _kUncategorizedFilter &&
        _projectFilter != _kFavoritesFilter &&
        !liveIds.contains(_projectFilter)) {
      _projectFilter = null;
    }
    if (_projectFilter == _kFavoritesFilter && favoriteIds.isEmpty) {
      _projectFilter = null;
    }
    final inboxFilterActive =
        inboxProject != null && _projectFilter == inboxProject.id;
    // Bulk selection / AI suggestions only make sense while filtered to the
    // inbox — clear them the moment the filter lands anywhere else (project
    // deleted, favorites collapse, a manual filter change that somehow
    // skipped the onProjectChanged reset below), so a stale selection can
    // never survive into a different project's view.
    //
    // Uses `shouldExitSelection` rather than comparing
    // `_projectFilter != inboxProject?.id` directly: that direct comparison
    // null-collapses when the inbox project is deleted — `_projectFilter`
    // resets to null (the stale-filter guard just above) AND `inboxProject`
    // is ALSO null, so `null != null` is false and the reset never fires.
    // That left every row across the whole Ledger stuck in selection mode
    // with no bulk bar (and no ✕ cancel) left to escape through.
    if (shouldExitSelection(
      selectionMode: _selectionMode,
      projectFilter: _projectFilter,
      inboxProjectId: inboxProject?.id,
    )) {
      _selectionMode = false;
      _selected = {};
      _suggestions = {};
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
    final filtered = _applyFilter(ranged, liveIds, favoriteIds);

    final currency = filtered.isNotEmpty
        ? filtered.first.currency
        : (visible.isNotEmpty ? visible.first.currency : 'USD');

    // Fold the ONLINE budget top-ups (credits) into the ledger once loaded.
    // Offline / not-yet-loaded → currency: null keeps EVERY expense row (no
    // debit ever vanishes); loaded → scope to the display currency so the
    // running balance never mixes currencies. Credits obey the same project +
    // range filters as the debits.
    final entriesLoaded = _entries != null;
    final filteredEntries = entriesLoaded
        ? _applyEntryFilter(
            filterEntriesByRange(
              _entries!,
              _range,
              monthOffset: _monthOffset,
              customStart: _customRange?.start,
              customEnd: _customRange?.end,
            ),
            liveIds,
            favoriteIds,
          )
        : const <BudgetEntry>[];
    final items = mergeLedger(
      filtered,
      filteredEntries,
      currency: entriesLoaded ? currency : null,
    );
    final balance = entriesLoaded ? ledgerBalance(items) : null;

    final inboxCount = inboxProject == null
        ? 0
        : ranged.where((e) => e.projectId == inboxProject.id).length;

    final controls = _LedgerControls(
      projects: state.projects,
      favoriteIds: favoriteIds,
      inboxProjectId: inboxProject?.id,
      inboxCount: inboxCount,
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
      onProjectChanged: (id) => setState(() {
        _projectFilter = id;
        // Leaving the inbox filter exits bulk-selection entirely — it has no
        // meaning against a different project's rows. Same
        // `shouldExitSelection` helper as the build-time guard above, for the
        // same null-collapsing reason (a direct `id != inboxProject?.id`
        // comparison would silently do nothing if this ever fires with both
        // sides null).
        if (shouldExitSelection(
          selectionMode: _selectionMode,
          projectFilter: id,
          inboxProjectId: inboxProject?.id,
        )) {
          _selectionMode = false;
          _selected = {};
          _suggestions = {};
        }
      }),
      onSortChanged: (s) => setState(() => _sort = s),
      onRangeChanged: _onRangeChanged,
      onMonthStep: _stepMonth,
    );

    final showBulkBar = _selectionMode && inboxFilterActive;

    // Tell the screen whether the pinned bar is showing so it can hide its
    // FloatingActionButton — the FAB docks bottom-right, exactly where the
    // bar's trailing controls (Auto, Cancel) sit, and would otherwise cover
    // them and steal their taps. Deferred to a post-frame callback: this
    // notifies a DIFFERENT widget's state (`_ExpensesScreenState`), and
    // calling its `setState` synchronously mid-build would hit "setState()
    // or markNeedsBuild() called during build".
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) widget.onBulkBarVisibleChanged(showBulkBar);
    });

    // The bulk bar is a screen-pinned overlay (bottom-anchored in a Stack)
    // rather than a sliver living inside the scroll content. It used to render
    // as the final sliver above the footer, which on a long inbox sat below
    // the fold — a long-press appeared to do nothing until the user scrolled
    // all the way down. Pinning it here means it's ALWAYS visible the instant
    // selection mode is entered, regardless of scroll position. The scroll
    // content reserves [_kBulkBarReserve] of extra bottom padding whenever the
    // bar is shown so the overlay can never cover the last list row or the
    // balance/connect-hint footer beneath it.
    return Stack(
      children: [
        LzRefresh(
          onRefresh: _handleRefresh,
          child: CustomScrollView(
            slivers: [
              SliverToBoxAdapter(child: controls),
              if (items.isEmpty)
                SliverFillRemaining(
                  hasScrollBody: false,
                  child: LzEmptyState(
                    icon: Icons.filter_alt_off_outlined,
                    title: 'No expenses match',
                    hint: 'Try a different project or time range.',
                  ),
                )
              else ...[
                if (_sort == _LedgerSort.amount)
                  _amountSliver(items, inboxFilterActive: inboxFilterActive)
                else
                  _dateGroupedSliver(
                    items,
                    newestFirst: _sort == _LedgerSort.newest,
                    inboxFilterActive: inboxFilterActive,
                  ),
                // Footer: the running balance (once credits loaded) and/or the
                // offline hint. Always present so it also carries the bottom
                // scroll room; its bottom padding grows by [_kBulkBarReserve]
                // while the pinned bulk bar is showing so the overlay never
                // covers it.
                SliverToBoxAdapter(
                  child: Padding(
                    padding: EdgeInsets.fromLTRB(
                      AppSpacing.lg,
                      AppSpacing.md,
                      AppSpacing.lg,
                      AppSpacing.xxxl + (showBulkBar ? _kBulkBarReserve : 0),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        if (balance != null)
                          _LedgerBalanceBar(
                              balance: balance, currency: currency),
                        if (!entriesLoaded && !_entriesLoading)
                          const _LedgerConnectHint(),
                      ],
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
        // Pinned bulk-action bar — bottom-anchored above the scroll view so it
        // stays on screen in selection mode no matter how far the list is
        // scrolled. Same AnimatedSwitcher + AppMotion cross-fade the old
        // sliver placement used.
        Positioned(
          left: 0,
          right: 0,
          bottom: 0,
          child: SafeArea(
            top: false,
            child: AnimatedSwitcher(
              duration: AppMotion.base,
              switchInCurve: AppMotion.curve,
              switchOutCurve: AppMotion.curve,
              child: showBulkBar
                  ? Padding(
                      key: const ValueKey('bulk-bar'),
                      padding: const EdgeInsets.fromLTRB(
                        AppSpacing.lg,
                        0,
                        AppSpacing.lg,
                        AppSpacing.md,
                      ),
                      child: BulkActionBar(
                        count: _selected.length,
                        busy: _bulkBusy,
                        onAssign: _selected.isEmpty
                            ? null
                            : () => _openAssignSheet(
                                  state.projects,
                                  inboxProject.id,
                                ),
                        onAuto: _openAutoAssign,
                        onCancel: _cancelSelection,
                      ),
                    )
                  : const SizedBox.shrink(key: ValueKey('bulk-bar-hidden')),
            ),
          ),
        ),
      ],
    );
  }

  /// A flat, single-card list sorted by magnitude (largest first), credits +
  /// debits interleaved. The section total is the money SPENT (debits) in view.
  Widget _amountSliver(List<LedgerItem> items,
      {required bool inboxFilterActive}) {
    final sorted = [...items]
      ..sort((a, b) => b.magnitude.compareTo(a.magnitude));
    final spent = sorted
        .where((i) => !i.isCredit)
        .fold<double>(0, (s, i) => s + i.magnitude);
    final currency = sorted.isNotEmpty ? sorted.first.currency : 'USD';
    return SliverPadding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.lg,
        AppSpacing.lg,
        0,
      ),
      sliver: SliverToBoxAdapter(
        child: LzSection(
          title: 'By amount',
          action: _SectionTotal(text: fmtMoney(currency, spent)),
          child: _ledgerCard(sorted, inboxFilterActive: inboxFilterActive),
        ),
      ),
    );
  }

  /// Date-grouped sections, ordered [newestFirst] (or oldest-first), with credit
  /// top-ups interleaved among the expenses of each day. The section total is
  /// the day's SPEND (debits) — the balance footer rolls credits up.
  Widget _dateGroupedSliver(
    List<LedgerItem> items, {
    required bool newestFirst,
    required bool inboxFilterActive,
  }) {
    final ordered = newestFirst ? items : items.reversed.toList();
    final byDate =
        groupBy<LedgerItem, String>(ordered, (i) => _ledgerDayKey(i.date));
    final sortedDates = byDate.keys.toList()
      ..sort((a, b) => newestFirst ? b.compareTo(a) : a.compareTo(b));

    return SliverList(
      delegate: SliverChildBuilderDelegate(
        (context, i) {
          final date = sortedDates[i];
          final dayItems = byDate[date]!;
          final daySpent = dayItems
              .where((it) => !it.isCredit)
              .fold<double>(0, (s, it) => s + it.magnitude);
          final currency =
              dayItems.isNotEmpty ? dayItems.first.currency : 'USD';

          return Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.lg,
              AppSpacing.lg,
              AppSpacing.lg,
              0,
            ),
            child: LzSection(
              title: friendlyDate(date),
              action: _SectionTotal(text: fmtMoney(currency, daySpent)),
              child: _ledgerCard(dayItems, inboxFilterActive: inboxFilterActive),
            ),
          );
        },
        childCount: sortedDates.length,
      ),
    );
  }

  /// A single card of ledger rows — expense (debit) rows via [ExpenseRow] and
  /// budget top-up (credit) rows via [CreditRow], separated by hairlines.
  Widget _ledgerCard(List<LedgerItem> items,
      {required bool inboxFilterActive}) {
    return LzCard(
      padding: EdgeInsets.zero,
      child: Column(
        children: [
          for (int j = 0; j < items.length; j++) ...[
            _ledgerRow(items[j], inboxFilterActive: inboxFilterActive),
            if (j < items.length - 1)
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

  Widget _ledgerRow(LedgerItem item, {required bool inboxFilterActive}) {
    if (item.isCredit) {
      return CreditRow(item: item);
    }
    final e = item.expense!;
    return ExpenseRow(
      expense: e,
      projects: widget.state.projects,
      pendingSync: widget.state.dirtyExpenseIds.contains(e.id),
      onDelete: () => widget.onDeleteExpense(e.id),
      onTap: () => widget.onTapExpense(e),
      onToggleFavorite: () => widget.onToggleExpenseFavorite(e.id),
      showProject: true,
      selectionMode: _selectionMode,
      selected: _selected.contains(e.id),
      onToggleSelect: () => _toggleSelected(e.id),
      onLongPress: inboxFilterActive ? () => _enterSelection(e.id) : null,
    );
  }
}

/// The `YYYY-MM-DD` key a ledger item is grouped under (its calendar day).
String _ledgerDayKey(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}-'
    '${d.month.toString().padLeft(2, '0')}-'
    '${d.day.toString().padLeft(2, '0')}';

/// The ledger's running-balance summary: Added (credits) · Spent (debits) ·
/// Balance (net). Only shown once the ONLINE budget top-ups have loaded, so the
/// ledger never presents a misleading balance while offline.
class _LedgerBalanceBar extends StatelessWidget {
  const _LedgerBalanceBar({required this.balance, required this.currency});

  final LedgerBalance balance;
  final String currency;

  @override
  Widget build(BuildContext context) {
    final balStr = balance.balance < 0
        ? '− ${fmtMoney(currency, balance.balance.abs())}'
        : fmtMoney(currency, balance.balance);
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      decoration: BoxDecoration(
        color: AppColors.bgSurfaceElevated,
        borderRadius: AppRadii.rMd,
        border: Border.all(color: AppColors.borderSubtle),
      ),
      child: Row(
        children: [
          Expanded(
            child: _figure(
              'Added',
              fmtMoney(currency, balance.added),
              AppColors.success,
            ),
          ),
          _dot(),
          Expanded(
            child: _figure(
              'Spent',
              fmtMoney(currency, balance.spent),
              AppColors.textSecondary,
            ),
          ),
          _dot(),
          Expanded(
            child: _figure(
              'Balance',
              balStr,
              balance.balance < 0 ? AppColors.error : AppColors.textPrimary,
            ),
          ),
        ],
      ),
    );
  }

  Widget _figure(String label, String value, Color color) => Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            label.toUpperCase(),
            style: AppText.caption.copyWith(
              color: AppColors.textMuted,
              letterSpacing: 0.6,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            value,
            style: AppText.body.copyWith(color: color, fontWeight: FontWeight.w800),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      );

  Widget _dot() => Padding(
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xs),
        child: Text('·', style: AppText.body.copyWith(color: AppColors.textMuted)),
      );
}

/// A slim muted hint shown when the ONLINE budget top-ups couldn't be loaded
/// (offline). The expense ledger still renders normally; only the credits + the
/// balance are withheld until reconnect.
class _LedgerConnectHint extends StatelessWidget {
  const _LedgerConnectHint();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: AppSpacing.sm),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.cloud_off_outlined,
              size: 13, color: AppColors.textMuted),
          const SizedBox(width: AppSpacing.xs),
          Text(
            'Connect to see budget entries',
            style: AppText.caption.copyWith(color: AppColors.textMuted),
          ),
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
/// 3. A horizontally-scrollable project filter (All · ★ Favorites · each
///    project, starred first · Uncategorized).
class _LedgerControls extends StatelessWidget {
  const _LedgerControls({
    required this.projects,
    required this.favoriteIds,
    required this.inboxProjectId,
    required this.inboxCount,
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
  final Set<String> favoriteIds;

  /// The id of the auto-created "Inbox" project (`Project.isInbox`), or null
  /// if none exists yet.
  final String? inboxProjectId;

  /// Count of the inbox project's expenses in the current (time-ranged) list.
  /// The inbox chip only renders when this is > 0.
  final int inboxCount;
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
              if (favoriteIds.isNotEmpty) ...[
                const SizedBox(width: AppSpacing.sm),
                LzChip(
                  label: '★ Favorites',
                  dense: true,
                  selected: selectedProjectId == _kFavoritesFilter,
                  color: AppColors.accent,
                  onTap: () => onProjectChanged(_kFavoritesFilter),
                ),
              ],
              // Pinned Inbox chip: only when the inbox project exists AND has
              // expenses in the current window, so it never clutters the row
              // once everything's been triaged out.
              if (inboxProjectId != null && inboxCount > 0) ...[
                const SizedBox(width: AppSpacing.sm),
                LzChip(
                  label: '📥 Inbox ($inboxCount)',
                  dense: true,
                  selected: selectedProjectId == inboxProjectId,
                  color: AppColors.warn,
                  onTap: () => onProjectChanged(inboxProjectId),
                ),
              ],
              // Starred projects lead the per-project chips so the ones the
              // user budgets against are one tap away, not a scroll away. The
              // inbox project gets its own pinned chip above, so it's excluded
              // here to avoid a duplicate.
              for (final p in [
                ...projects.where((p) =>
                    favoriteIds.contains(p.id) && p.id != inboxProjectId),
                ...projects.where((p) =>
                    !favoriteIds.contains(p.id) && p.id != inboxProjectId),
              ]) ...[
                const SizedBox(width: AppSpacing.sm),
                LzChip(
                  label: favoriteIds.contains(p.id) ? '★ ${p.name}' : p.name,
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
