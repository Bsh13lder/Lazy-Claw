import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../core/actions/app_actions.dart';
import '../core/reminder_lead.dart';
import '../models/project.dart';
import '../models/subtask.dart';
import '../models/task.dart';
import '../providers/budgets_provider.dart';
import '../providers/tasks_provider.dart';
import '../providers/ui_prefs_provider.dart';
import 'expenses/add_expense_sheet.dart';
import 'notes/notes_body.dart';
import 'settings/settings_prefs.dart';
import 'storage_banners.dart';
import 'tasks/add_task_sheet.dart';
import 'tasks/ai_task_badge.dart';
import 'tasks/connected_task_row.dart';
import 'tasks/overdue_view.dart';
import 'tasks/reschedule_sheet.dart';
import 'tasks/task_calendar_view.dart';
import 'tasks/task_detail_sheet.dart';
import 'tasks/task_owner_filter.dart';
import 'tasks/tasks_project_view.dart';

// ── Top segment ─────────────────────────────────────────────────────────────

/// The two top-level segments of this tab. Notes was promoted out of the bottom
/// nav into this segment; the freed slot now hosts the Documents tab.
enum _Segment { tasks, notes }

// ── View mode ─────────────────────────────────────────────────────────────────

/// The ways to view tasks (nested under the Tasks segment). Overdue is a
/// first-class peer of List/Calendar/Projects (full control over the overdue
/// set, incl. "Reschedule all").
enum _TasksView { list, overdue, calendar, projects }

// ── Sections ──────────────────────────────────────────────────────────────────

/// The four section buckets rendered by [TasksScreen]. Public so widget
/// tests can pump [TaskSection] directly without going through the full
/// screen.
enum Section { overdue, today, upcoming, done }

/// The section's default collapse state before any persisted preference has
/// loaded: Done starts collapsed, everything else starts expanded.
bool defaultSectionCollapsed(Section section) => section == Section.done;

extension _SectionLabel on Section {
  String get label {
    switch (this) {
      case Section.overdue:
        return 'Overdue';
      case Section.today:
        return 'Today';
      case Section.upcoming:
        return 'Upcoming';
      case Section.done:
        return 'Done';
    }
  }

  IconData get emptyIcon {
    switch (this) {
      case Section.overdue:
        return Icons.warning_amber_rounded;
      case Section.today:
        return Icons.today_outlined;
      case Section.upcoming:
        return Icons.event_outlined;
      case Section.done:
        return Icons.check_circle_outline;
    }
  }
}

// ── Grouping helper ───────────────────────────────────────────────────────────

/// True when [task] is overdue: it has a parseable due date whose calendar day
/// is strictly before today, and it isn't done. The single source of truth for
/// "overdue" — shared by [_groupTasks] (the inline section) and the dedicated
/// Overdue view ([isOverdueTask] re-exports it).
bool _isOverdueOn(Task task, DateTime today) {
  if (task.isDone || task.dueDate == null) return false;
  final DateTime dueDay;
  try {
    final d = DateTime.parse(task.dueDate!);
    dueDay = DateTime(d.year, d.month, d.day);
  } catch (_) {
    return false;
  }
  return dueDay.isBefore(today);
}

/// Whether [task] is overdue as of [now] (defaults to the wall clock). The
/// dedicated Overdue view filters with this so its rule matches [_groupTasks]
/// exactly.
bool isOverdueTask(Task task, {DateTime? now}) {
  final n = now ?? DateTime.now();
  return _isOverdueOn(task, DateTime(n.year, n.month, n.day));
}

/// Splits [tasks] into the four section buckets.
Map<Section, List<Task>> _groupTasks(List<Task> tasks) {
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);

  final overdue = <Task>[];
  final todayList = <Task>[];
  final upcoming = <Task>[];
  final done = <Task>[];

  for (final task in tasks) {
    if (task.isDone) {
      done.add(task);
      continue;
    }
    if (task.dueDate == null) {
      upcoming.add(task);
      continue;
    }
    final DateTime dueDay;
    try {
      final d = DateTime.parse(task.dueDate!);
      dueDay = DateTime(d.year, d.month, d.day);
    } catch (_) {
      upcoming.add(task);
      continue;
    }
    if (dueDay.isBefore(today)) {
      overdue.add(task);
    } else if (dueDay == today) {
      todayList.add(task);
    } else {
      upcoming.add(task);
    }
  }

  return {
    Section.overdue: overdue,
    Section.today: todayList,
    Section.upcoming: upcoming,
    Section.done: done,
  };
}

// ── Screen ────────────────────────────────────────────────────────────────────

class TasksScreen extends ConsumerStatefulWidget {
  const TasksScreen({super.key});

  @override
  ConsumerState<TasksScreen> createState() => _TasksScreenState();
}

class _TasksScreenState extends ConsumerState<TasksScreen> {
  /// Tasks vs Notes — the top segment. Notes lives here now (it was a bottom-nav
  /// tab); selecting it renders the shared [NotesBody].
  _Segment _segment = _Segment.tasks;

  /// List vs Calendar. Local-only — resets on tab rebuild, which is fine.
  _TasksView _view = _TasksView.list;

  /// Owner filter (All · Mine · AI) — separates self-created from AI-created
  /// tasks across all three views. Local-only; defaults to All.
  TaskOwnerFilter _ownerFilter = TaskOwnerFilter.all;

  /// Calendar focus (the visible month) and the selected day. Seeded to today
  /// so the calendar opens on the current month with today highlighted and the
  /// FAB pre-fills today.
  late DateTime _focusedDay;
  late DateTime _selectedDay;

  /// The calendar colors tasks by their project, so it needs the budgets store.
  /// We load it lazily on the first switch to Calendar (kept out of the
  /// list-only path so a list-only screen never builds the budgets provider).
  bool _budgetsRequested = false;

  /// The Projects view's persisted expanded-bucket names. Empty (all
  /// collapsed) until the async pref load below completes — the view renders
  /// fine meanwhile since collapsed-by-default is the pre-existing behavior.
  Set<String> _projectsExpanded = const <String>{};

  /// The Projects view's persisted "hide completed" toggle.
  bool _projectsHideCompleted = false;

  /// The List view's per-section collapsed state, keyed by [Section]. Seeded
  /// from [defaultSectionCollapsed] (Done collapsed, others expanded) so the
  /// correct defaults render on the very first frame, before the persisted
  /// values below have loaded.
  Map<Section, bool> _sectionCollapsed = {
    for (final section in Section.values)
      section: defaultSectionCollapsed(section),
  };

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _focusedDay = DateTime(now.year, now.month, now.day);
    _selectedDay = _focusedDay;
    // Load tasks from the local cache on first render (offline-first).
    Future.microtask(() => ref.read(tasksProvider.notifier).load());
    // The list (tap-the-chip project picker + Projects view) all need the
    // project list, so load the budgets store up front (cheap, offline-first).
    _ensureBudgetsLoaded();
    // Restore the Projects view's persisted expansion + hide-completed state,
    // and the List view's per-section collapsed state (client-local, see
    // UiPrefsDao) — a single async read, applied once.
    unawaited(_loadProjectsPrefs());
    unawaited(_loadSectionCollapsedPrefs());
    // NOTE: the cold-start deep-link replay lives in [build] via
    // [drainPendingAction] (not a one-shot here) so it survives whichever frame
    // this screen first becomes visible on — see _myActions usage below.
  }

  /// One-shot restore of the Projects view's persisted UI state. Best-effort:
  /// this is client-local convenience state, not user data, so a failure here
  /// (a DB hiccup, or a test host that never overrode [appDatabaseProvider])
  /// must never block the rest of the Tasks screen from rendering — it just
  /// falls back to the collapsed/visible defaults already set above.
  Future<void> _loadProjectsPrefs() async {
    try {
      final prefs = ref.read(uiPrefsDaoProvider);
      final expanded = await prefs.getStringSet(kPrefProjectsExpanded);
      final hideCompleted = await prefs.getBool(kPrefProjectsHideCompleted);
      if (!mounted) return;
      setState(() {
        _projectsExpanded = expanded;
        _projectsHideCompleted = hideCompleted;
      });
    } catch (e) {
      debugPrint('TasksScreen._loadProjectsPrefs failed: $e');
    }
  }

  /// One-shot restore of the List view's per-section collapsed state.
  /// Best-effort, mirroring [_loadProjectsPrefs]: a failure here just leaves
  /// the [defaultSectionCollapsed] seed in place.
  Future<void> _loadSectionCollapsedPrefs() async {
    try {
      final prefs = ref.read(uiPrefsDaoProvider);
      final loaded = <Section, bool>{};
      for (final section in Section.values) {
        loaded[section] = await prefs.getBool(
          kPrefListSectionCollapsed(section.name),
          fallback: defaultSectionCollapsed(section),
        );
      }
      if (!mounted) return;
      setState(() => _sectionCollapsed = loaded);
    } catch (e) {
      debugPrint('TasksScreen._loadSectionCollapsedPrefs failed: $e');
    }
  }

  /// Persists the Projects view's expanded-bucket set as it changes, and
  /// keeps the local copy in sync so a view remount (leaving and returning to
  /// Projects) restores it without waiting on another async DB read.
  void _onProjectsExpandedChanged(Set<String> expanded) {
    setState(() => _projectsExpanded = expanded);
    unawaited(
      _persistUiPref(
        () => ref
            .read(uiPrefsDaoProvider)
            .setStringSet(kPrefProjectsExpanded, expanded),
      ),
    );
  }

  /// Persists the Projects view's hide-completed toggle, mirroring
  /// [_onProjectsExpandedChanged].
  void _onProjectsHideCompletedChanged(bool value) {
    setState(() => _projectsHideCompleted = value);
    unawaited(
      _persistUiPref(
        () => ref
            .read(uiPrefsDaoProvider)
            .setBool(kPrefProjectsHideCompleted, value),
      ),
    );
  }

  /// Persists a single List-view section's collapsed flag as it changes, and
  /// keeps the local copy in sync so a rebuild doesn't wait on another async
  /// DB read. Mirrors [_onProjectsExpandedChanged].
  void _onSectionCollapsedChanged(Section section, bool collapsed) {
    setState(
      () => _sectionCollapsed = {..._sectionCollapsed, section: collapsed},
    );
    unawaited(
      _persistUiPref(
        () => ref
            .read(uiPrefsDaoProvider)
            .setBool(kPrefListSectionCollapsed(section.name), collapsed),
      ),
    );
  }

  /// Runs a fire-and-forget prefs write, swallowing (and logging) any
  /// failure — see [_loadProjectsPrefs] for why this stays best-effort.
  Future<void> _persistUiPref(Future<void> Function() write) async {
    try {
      await write();
    } catch (e) {
      debugPrint('TasksScreen: persisting a Projects-view pref failed: $e');
    }
  }

  /// The deep-link actions this screen owns (Tasks + the nested Notes segment).
  static const Set<AppAction> _myActions = {
    AppAction.addTask,
    AppAction.newNote,
  };

  /// Replay a consumed deep-link action: open the add-task sheet, or switch to
  /// the Notes segment and open the create-note flow.
  void _handlePendingAction(AppAction action) {
    switch (action) {
      case AppAction.addTask:
        if (_segment != _Segment.tasks) {
          setState(() => _segment = _Segment.tasks);
        }
        _openAddSheet(initialDueDate: _contextualAddDate);
        break;
      case AppAction.newNote:
        if (_segment != _Segment.notes) {
          setState(() => _segment = _Segment.notes);
        }
        showCreateNoteFlow(context, ref);
        break;
      case AppAction.addExpense:
      case AppAction.chat:
      case AppAction.openTasks:
      case AppAction.assistant:
        break; // not owned by this screen (openTasks/assistant self-clear in main.dart)
    }
  }

  /// Ensure the budgets store is loaded once (for per-project colors). Cheap,
  /// offline-first, idempotent.
  void _ensureBudgetsLoaded() {
    if (_budgetsRequested) return;
    _budgetsRequested = true;
    Future.microtask(() => ref.read(budgetsProvider.notifier).load());
  }

  Future<void> _refresh() => ref.read(tasksProvider.notifier).refresh();

  /// The date the add-sheet should pre-select: the selected calendar day while
  /// in Calendar view, otherwise none (list view = no implied date).
  DateTime? get _contextualAddDate =>
      _view == _TasksView.calendar ? _selectedDay : null;

  /// The global default reminder lead from settings (falls back to the built-in
  /// default until the prefs have loaded).
  ReminderLead get _defaultLead =>
      ref.read(settingsPrefsProvider).valueOrNull?.reminderLeadDefault ??
      kDefaultReminderLead;

  Future<void> _openAddSheet({DateTime? initialDueDate}) async {
    // Tactile tick when summoning the add sheet (FAB + app-bar + button +
    // calendar day-tap all route through here).
    HapticFeedback.selectionClick();
    final result = await showAddTaskSheet(
      context,
      initialDueDate: initialDueDate,
      defaultLead: _defaultLead,
    );
    if (result == null || !mounted) return;
    await ref
        .read(tasksProvider.notifier)
        .addTask(
          result.title,
          priority: result.priority,
          dueDate: result.dueDate,
          category: result.category,
          reminderAt: result.reminderAt,
          recurring: result.recurring,
          recurUntil: result.recurUntil,
          description: result.description,
          steps: result.steps,
        );
  }

  /// Open the "New Project" sheet. Reuses the same [AddProjectSheet] the Money
  /// tab uses — projects are shared across Tasks and Money — so a project can be
  /// created directly from the Tasks → Projects view instead of only from Money.
  void _showAddProject() {
    HapticFeedback.selectionClick();
    LzBottomSheet.show<void>(
      context,
      title: 'New Project',
      builder: (_) => AddProjectSheet(
        onSubmit: (name, budget, color, startDate, dueDate) => ref
            .read(budgetsProvider.notifier)
            .createProject(
              name,
              budget: budget,
              color: color,
              startDate: startDate,
              dueDate: dueDate,
            ),
      ),
    );
  }

  // ── Tap-the-chip quick-edit commits (route through the provider) ───────────

  void _commitPriority(String id, String priority) =>
      ref.read(tasksProvider.notifier).updateTask(id, priority: priority);

  void _commitDueDate(String id, String dueDate) =>
      ref.read(tasksProvider.notifier).updateTask(id, dueDate: dueDate);

  void _commitCategory(String id, String category) =>
      ref.read(tasksProvider.notifier).updateTask(id, category: category);

  void _commitSubtasks(String id, List<Subtask> subtasks) =>
      ref.read(tasksProvider.notifier).setSubtasks(id, subtasks);

  void _commitTitle(String id, String title) =>
      ref.read(tasksProvider.notifier).updateTask(id, title: title);

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(tasksProvider);
    final projects = ref.watch(budgetsProvider).projects;
    final reachable = ref.watch(reachableProvider);
    final degraded = ref.watch(dbHealthProvider).isDegraded;
    final notesMode = _segment == _Segment.notes;

    // Deep-link replay (cold start AND warm tap), made frame-order-proof: a
    // `+ Task` / `+ Note` shortcut/widget may set the pending action BEFORE this
    // screen mounts (cold) or WHILE it's already alive in the indexedStack
    // (warm). A `ref.listen` only catches changes that happen after this screen
    // subscribes, which misses the cold-start case where the action was set
    // first. [drainPendingAction] re-arms on every build and consumes our
    // action whenever this screen becomes visible — so it never strands.
    drainPendingAction(
      ref,
      mine: _myActions,
      isMounted: () => mounted,
      onDrained: _handlePendingAction,
    );
    // Also react to a change that lands WHILE we're the visible tab — opening
    // the sheet promptly without waiting for the next unrelated rebuild.
    ref.listen<AppAction?>(pendingActionProvider, (_, next) {
      drainPendingAction(
        ref,
        mine: _myActions,
        isMounted: () => mounted,
        onDrained: _handlePendingAction,
      );
    });

    // Show error snackbar on new task errors.
    ref.listen<TasksState>(tasksProvider, (prev, next) {
      if (next.error != null && next.error != prev?.error) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bgSurfaceElevated,
            content: Text(next.error!, style: AppText.body),
            action: SnackBarAction(
              label: 'Dismiss',
              textColor: AppColors.accent,
              onPressed: () => ref.read(tasksProvider.notifier).clearError(),
            ),
          ),
        );
      }
    });

    return LzScaffold(
      appBar: LzAppBar(
        title: notesMode ? 'Notes' : 'Tasks',
        large: true,
        gradientTitle: true,
        actions: [
          // In the Projects view, expose a direct "New project" action so
          // projects aren't only creatable from the Money tab.
          if (!notesMode && _view == _TasksView.projects)
            LzIconButton(
              icon: Icons.create_new_folder_outlined,
              tooltip: 'New project',
              onPressed: _showAddProject,
            ),
          LzIconButton(
            icon: Icons.add,
            tooltip: notesMode ? 'New note' : 'New task',
            onPressed: notesMode
                ? () => showCreateNoteFlow(context, ref)
                : () => _openAddSheet(initialDueDate: _contextualAddDate),
          ),
        ],
      ),
      // Offline / degraded banners are global (shared infra) — they apply to
      // both Tasks and Notes.
      banner: buildStorageBanners(
        context,
        offline: !reachable,
        degraded: degraded,
        onRetry: () => ref.read(tasksProvider.notifier).load(),
      ),
      floatingActionButton: FloatingActionButton(
        backgroundColor: AppColors.accent,
        foregroundColor: AppColors.onAccent,
        tooltip: notesMode ? 'New note' : 'New task',
        onPressed: notesMode
            ? () => showCreateNoteFlow(context, ref)
            : () => _openAddSheet(initialDueDate: _contextualAddDate),
        child: Icon(notesMode ? Icons.note_add_rounded : Icons.add),
      ),
      body: Column(
        children: [
          // Tasks ⇄ Notes top segment — always visible.
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.lg,
              AppSpacing.md,
              AppSpacing.lg,
              0,
            ),
            child: _SegmentToggle(
              segment: _segment,
              onChanged: (s) {
                if (s == _segment) return;
                HapticFeedback.selectionClick();
                setState(() => _segment = s);
              },
            ),
          ),
          Expanded(
            child: notesMode
                ? const NotesBody()
                : _buildTasksContent(state, projects),
          ),
        ],
      ),
    );
  }

  /// The Tasks segment body: the owner filter (All · Mine · AI) + the
  /// List · Calendar · Projects toggle (nested) + content.
  Widget _buildTasksContent(TasksState state, List<Project> projects) {
    // The owner filter is applied ONCE here; every view downstream renders the
    // already-filtered list so List/Calendar/Projects stay in lockstep.
    final visibleTasks = filterByOwner(state.tasks, _ownerFilter);
    // The AI-chip badge counts the unfiltered set so it always reflects how
    // many AI tasks exist, regardless of the active filter.
    final aiCount = countAgentTasks(state.tasks);

    return Column(
      children: [
        // Owner filter (All · Mine · AI) — always visible so the user can
        // separate self-created from AI-created tasks from any state.
        Padding(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.lg,
            AppSpacing.md,
            AppSpacing.lg,
            0,
          ),
          child: _OwnerFilterRow(
            filter: _ownerFilter,
            aiCount: aiCount,
            onChanged: (f) {
              if (f == _ownerFilter) return;
              HapticFeedback.selectionClick();
              setState(() => _ownerFilter = f);
            },
          ),
        ),
        // List · Calendar · Projects toggle — always visible so the user can
        // switch even from an empty/error state.
        Padding(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.lg,
            AppSpacing.sm,
            AppSpacing.lg,
            0,
          ),
          child: _ViewToggle(
            view: _view,
            onChanged: (v) {
              if (v == _view) return;
              HapticFeedback.selectionClick();
              _ensureBudgetsLoaded();
              setState(() => _view = v);
            },
          ),
        ),
        Expanded(child: _buildViewBody(state, visibleTasks, projects)),
      ],
    );
  }

  /// Routes to the active view's body. [visibleTasks] is [TasksState.tasks]
  /// already narrowed by the active owner filter.
  Widget _buildViewBody(
    TasksState state,
    List<Task> visibleTasks,
    List<Project> projects,
  ) {
    switch (_view) {
      case _TasksView.list:
        return _buildBody(state, visibleTasks, projects);
      case _TasksView.overdue:
        return _buildOverdueBody(state, visibleTasks, projects);
      case _TasksView.calendar:
        return _buildCalendarBody(state, visibleTasks, projects);
      case _TasksView.projects:
        return _buildProjectsBody(state, visibleTasks, projects);
    }
  }

  /// The dedicated Overdue view: an error/skeleton guard around [OverdueView]
  /// (which owns its own filter/sort + "Reschedule all"). Reuses the same
  /// per-task callbacks as the List view so every row control still works.
  Widget _buildOverdueBody(
    TasksState state,
    List<Task> visibleTasks,
    List<Project> projects,
  ) {
    if (state.isLoading && state.tasks.isEmpty && state.error == null) {
      return LzSkeleton.list(
        count: 4,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
      );
    }
    if (state.tasks.isEmpty && state.error != null) {
      return LzErrorState(
        message: state.error!,
        onRetry: () => ref.read(tasksProvider.notifier).load(),
      );
    }

    return OverdueView(
      tasks: visibleTasks,
      projects: projects,
      dirtyIds: state.dirtyIds,
      onRefresh: _refresh,
      onComplete: (id) => ref.read(tasksProvider.notifier).completeTask(id),
      onDelete: (id) => ref.read(tasksProvider.notifier).deleteTask(id),
      onOpen: (task) => _openDetail(task, projects),
      onReschedule: _openReschedule,
      onRenameTitle: _commitTitle,
      onPriorityChanged: _commitPriority,
      onDueDateChanged: _commitDueDate,
      onCategoryChanged: _commitCategory,
      onSubtasksChanged: _commitSubtasks,
    );
  }

  /// Calendar body: an error/skeleton guard around [TaskCalendarView]. The
  /// calendar itself is useful even with zero tasks, so we only short-circuit
  /// on the first instant load and on a hard error with nothing cached.
  Widget _buildCalendarBody(
    TasksState state,
    List<Task> visibleTasks,
    List<Project> projects,
  ) {
    if (state.isLoading && state.tasks.isEmpty && state.error == null) {
      return LzSkeleton.list(
        count: 4,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
      );
    }
    if (state.tasks.isEmpty && state.error != null) {
      return LzErrorState(
        message: state.error!,
        onRetry: () => ref.read(tasksProvider.notifier).load(),
      );
    }

    return LzRefresh(
      onRefresh: _refresh,
      child: TaskCalendarView(
        tasks: visibleTasks,
        projects: projects,
        dirtyIds: state.dirtyIds,
        focusedDay: _focusedDay,
        selectedDay: _selectedDay,
        onDaySelected: (selected, focused) => setState(() {
          _selectedDay = DateTime(selected.year, selected.month, selected.day);
          _focusedDay = focused;
        }),
        onPageChanged: (focused) => setState(() => _focusedDay = focused),
        onComplete: (id) => ref.read(tasksProvider.notifier).completeTask(id),
        onDelete: (id) => ref.read(tasksProvider.notifier).deleteTask(id),
        onOpen: (task) => _openDetail(task, projects),
        onAddOnDay: (day) => _openAddSheet(initialDueDate: day),
      ),
    );
  }

  /// Open the Smart Fast Reschedule sheet for [task] (overdue cards route here).
  void _openReschedule(Task task) => showRescheduleSheet(context, ref, task);

  /// Open the full detail sheet for [task], handing it the project list so its
  /// project picker is populated.
  void _openDetail(Task task, List<Project> projects) => showTaskDetailSheet(
    context,
    ref,
    task,
    projects: projects,
    defaultLead: _defaultLead,
  );

  /// The Projects view: tasks grouped under their project (Money-tab projects +
  /// a first-class Inbox bucket), each bucket expandable to its tasks.
  Widget _buildProjectsBody(
    TasksState state,
    List<Task> visibleTasks,
    List<Project> projects,
  ) {
    if (state.isLoading && state.tasks.isEmpty && state.error == null) {
      return LzSkeleton.list(
        count: 5,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
      );
    }
    if (state.tasks.isEmpty && state.error != null) {
      return LzErrorState(
        message: state.error!,
        onRetry: () => ref.read(tasksProvider.notifier).load(),
      );
    }

    return LzRefresh(
      onRefresh: _refresh,
      child: TasksProjectView(
        // Stable across rebuilds (never derived from mutable state) so this
        // view's internal expand-state survives an unrelated screen rebuild
        // while Projects stays the active view. Losing it only on a genuine
        // remount (switching away and back) is fine — that's restored from
        // [_projectsExpanded] below.
        key: const ValueKey('tasks-project-view'),
        tasks: visibleTasks,
        projects: projects,
        dirtyIds: state.dirtyIds,
        onComplete: (id) => ref.read(tasksProvider.notifier).completeTask(id),
        onDelete: (id) => ref.read(tasksProvider.notifier).deleteTask(id),
        onOpen: (task) => _openDetail(task, projects),
        onRenameTitle: _commitTitle,
        onPriorityChanged: _commitPriority,
        onDueDateChanged: _commitDueDate,
        onCategoryChanged: _commitCategory,
        onSubtasksChanged: _commitSubtasks,
        onAddProject: _showAddProject,
        initialExpanded: _projectsExpanded,
        onExpandedChanged: _onProjectsExpandedChanged,
        hideCompleted: _projectsHideCompleted,
        onHideCompletedChanged: _onProjectsHideCompletedChanged,
      ),
    );
  }

  Widget _buildBody(
    TasksState state,
    List<Task> visibleTasks,
    List<Project> projects,
  ) {
    // ── Loading skeleton ─────────────────────────────────────────────────────
    // Only on the first instant cache read (no items yet, nothing errored).
    if (state.isLoading && state.tasks.isEmpty && state.error == null) {
      return LzSkeleton.list(
        count: 6,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
      );
    }

    // ── Error state ──────────────────────────────────────────────────────────
    // Nothing cached to show + a load error → offer a real Retry instead of
    // a misleading "empty" or an infinite skeleton.
    if (state.tasks.isEmpty && state.error != null) {
      return LzErrorState(
        message: state.error!,
        onRetry: () => ref.read(tasksProvider.notifier).load(),
      );
    }

    // ── Overall empty state ──────────────────────────────────────────────────
    if (!state.isLoading && state.tasks.isEmpty && state.error == null) {
      return LzEmptyState(
        icon: Icons.task_alt_outlined,
        title: 'No tasks yet',
        hint: 'Tap + to add your first task.',
        actionLabel: 'Add task',
        actionIcon: Icons.add,
        onAction: _openAddSheet,
      );
    }

    // ── Filtered-empty state ─────────────────────────────────────────────────
    // There ARE tasks, but the active owner filter excludes them all (e.g. the
    // "AI" filter with no AI-created tasks). Show a filter-specific empty state
    // with a quick way back to "All" rather than an unexplained blank list.
    if (visibleTasks.isEmpty) {
      return LzEmptyState(
        icon: _ownerFilter == TaskOwnerFilter.ai
            ? Icons.auto_awesome_outlined
            : Icons.person_outline,
        title: _ownerFilter == TaskOwnerFilter.ai
            ? 'No AI-created tasks'
            : 'No tasks you created',
        hint: 'Switch back to All to see every task.',
        actionLabel: 'Show all',
        actionIcon: Icons.clear_all,
        onAction: () => setState(() => _ownerFilter = TaskOwnerFilter.all),
      );
    }

    // ── Sectioned list ───────────────────────────────────────────────────────
    final grouped = _groupTasks(visibleTasks);

    return LzRefresh(
      onRefresh: _refresh,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg,
          AppSpacing.md,
          AppSpacing.lg,
          AppSpacing.xxxl, // leave room above the FAB
        ),
        children: [
          for (final section in Section.values)
            TaskSection(
              section: section,
              tasks: grouped[section] ?? const [],
              dirtyIds: state.dirtyIds,
              projects: projects,
              initialCollapsed:
                  _sectionCollapsed[section] ??
                  defaultSectionCollapsed(section),
              onCollapsedChanged: (collapsed) =>
                  _onSectionCollapsedChanged(section, collapsed),
              onComplete: (id) =>
                  ref.read(tasksProvider.notifier).completeTask(id),
              onDelete: (id) => ref.read(tasksProvider.notifier).deleteTask(id),
              onOpen: (task) => _openDetail(task, projects),
              onRenameTitle: _commitTitle,
              onPriorityChanged: _commitPriority,
              onDueDateChanged: _commitDueDate,
              onCategoryChanged: _commitCategory,
              onSubtasksChanged: _commitSubtasks,
              onReschedule: _openReschedule,
            ),
        ],
      ),
    );
  }
}

// ── Section widget ─────────────────────────────────────────────────────────────

class TaskSection extends StatefulWidget {
  const TaskSection({
    super.key,
    required this.section,
    required this.tasks,
    required this.dirtyIds,
    required this.projects,
    required this.initialCollapsed,
    this.onCollapsedChanged,
    required this.onComplete,
    required this.onDelete,
    required this.onOpen,
    required this.onRenameTitle,
    required this.onPriorityChanged,
    required this.onDueDateChanged,
    required this.onCategoryChanged,
    required this.onSubtasksChanged,
    required this.onReschedule,
  });

  final Section section;
  final List<Task> tasks;
  final Set<String> dirtyIds;
  final List<Project> projects;

  /// The section's collapsed state on first build — seeded by the caller
  /// from its persisted preference (see [defaultSectionCollapsed] for the
  /// fallback used before that preference has loaded).
  final bool initialCollapsed;

  /// Fired with the new collapsed state whenever the chevron is tapped, so
  /// the caller can persist it. Optional — tests that only care about the
  /// visual toggle can omit it.
  final ValueChanged<bool>? onCollapsedChanged;

  final void Function(String id) onComplete;
  final void Function(String id) onDelete;
  final void Function(Task task) onOpen;
  final void Function(String id, String title) onRenameTitle;
  final void Function(String id, String priority) onPriorityChanged;
  final void Function(String id, String dueDate) onDueDateChanged;
  final void Function(String id, String category) onCategoryChanged;
  final void Function(String id, List<Subtask> subtasks) onSubtasksChanged;
  final void Function(Task task) onReschedule;

  @override
  State<TaskSection> createState() => _TaskSectionState();
}

class _TaskSectionState extends State<TaskSection> {
  // Seeded from the caller's persisted preference (or the built-in default —
  // Done collapsed, others expanded) — see [TaskSection.initialCollapsed].
  late bool _collapsed;

  // The staggered fade+slide entrance plays exactly once, on first mount. Once
  // the last row finishes we drop the Animate wrappers so subsequent rebuilds
  // (sync refresh, complete, delete, scroll) render plain rows — no replay.
  bool _entered = false;

  @override
  void initState() {
    super.initState();
    _collapsed = widget.initialCollapsed;
  }

  @override
  void didUpdateWidget(TaskSection oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Resync when the persisted pref arrives after first mount: the List
    // view renders its sections synchronously on the very first build
    // (before the screen's async pref load resolves), so this State is
    // already mounted with the seeded default by the time the parent
    // rebuilds with the real persisted value — initState won't re-run
    // (same widget type/slot, no key), so without this the section would be
    // silently stuck on the default forever. A user toggle also flows back
    // down through the parent's map into `initialCollapsed`, so this stays
    // consistent with interactive changes too.
    if (widget.initialCollapsed != oldWidget.initialCollapsed) {
      _collapsed = widget.initialCollapsed;
    }
  }

  void _markEntered() {
    if (!_entered && mounted) setState(() => _entered = true);
  }

  /// Wrap [child] in a subtle, index-staggered fade+slide entrance — but only
  /// until [_entered] flips. The flag is flipped by the LAST row's completion
  /// so earlier rows are never cut short mid-animation.
  Widget _entrance({
    required int index,
    required int last,
    required Widget child,
  }) {
    if (_entered) return child;
    return child
        .animate(onComplete: index == last ? (_) => _markEntered() : null)
        .fadeIn(
          duration: AppMotion.base,
          delay: Duration(milliseconds: 28 * index),
          curve: AppMotion.curveEmphasized,
        )
        .slideY(
          begin: 0.04,
          end: 0,
          duration: AppMotion.base,
          delay: Duration(milliseconds: 28 * index),
          curve: AppMotion.curveEmphasized,
        );
  }

  @override
  Widget build(BuildContext context) {
    // Never render the section header if there's nothing to show (and it's not
    // the upcoming section which always appears as the catch-all bucket).
    if (widget.tasks.isEmpty &&
        widget.section != Section.upcoming &&
        widget.section != Section.today) {
      return const SizedBox.shrink();
    }

    final count = widget.tasks.length;
    final countBadge = count > 0
        ? Text(
            count.toString(),
            style: AppText.caption.copyWith(color: AppColors.textMuted),
          )
        : null;

    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.lg),
      child: LzSection(
        title: widget.section.label,
        action: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (countBadge != null) ...[
              countBadge,
              const SizedBox(width: AppSpacing.sm),
            ],
            GestureDetector(
              onTap: () => setState(() {
                _collapsed = !_collapsed;
                widget.onCollapsedChanged?.call(_collapsed);
              }),
              child: Icon(
                _collapsed
                    ? Icons.keyboard_arrow_down
                    : Icons.keyboard_arrow_up,
                size: 18,
                color: AppColors.textMuted,
              ),
            ),
          ],
        ),
        child: _buildContent(),
      ),
    );
  }

  Widget _buildContent() {
    if (_collapsed) return const SizedBox.shrink();

    if (widget.tasks.isEmpty) {
      return LzEmptyState(
        icon: widget.section.emptyIcon,
        title: _emptyTitle(widget.section),
      );
    }

    final last = widget.tasks.length - 1;
    return Column(
      children: [
        for (int i = 0; i < widget.tasks.length; i++) ...[
          _entrance(
            index: i,
            last: last,
            child: AgentTaskBadged(
              task: widget.tasks[i],
              child: ConnectedTaskRow(
                task: widget.tasks[i],
                pendingSync: widget.dirtyIds.contains(widget.tasks[i].id),
                projects: widget.projects,
                onComplete: widget.onComplete,
                onDelete: widget.onDelete,
                onOpen: widget.onOpen,
                onRenameTitle: widget.onRenameTitle,
                onPriorityChanged: widget.onPriorityChanged,
                onDueDateChanged: widget.onDueDateChanged,
                onCategoryChanged: widget.onCategoryChanged,
                onSubtasksChanged: widget.onSubtasksChanged,
                onReschedule: widget.onReschedule,
              ),
            ),
          ),
          if (i < widget.tasks.length - 1)
            const SizedBox(height: AppSpacing.sm),
        ],
      ],
    );
  }

  String _emptyTitle(Section section) {
    switch (section) {
      case Section.today:
        return 'Nothing due today';
      case Section.upcoming:
        return 'No upcoming tasks';
      default:
        return 'All clear';
    }
  }
}

// ── Segment toggle (Tasks | Notes) ──────────────────────────────────────────

/// The top-level Tasks ⇄ Notes toggle. Built from two [LzChip]s for kit
/// consistency.
class _SegmentToggle extends StatelessWidget {
  const _SegmentToggle({required this.segment, required this.onChanged});

  final _Segment segment;
  final void Function(_Segment) onChanged;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        LzChip(
          label: 'Tasks',
          icon: Icons.check_circle_outline,
          selected: segment == _Segment.tasks,
          onTap: () => onChanged(_Segment.tasks),
        ),
        const SizedBox(width: AppSpacing.sm),
        LzChip(
          label: 'Notes',
          icon: Icons.notes_outlined,
          selected: segment == _Segment.notes,
          onTap: () => onChanged(_Segment.notes),
        ),
      ],
    );
  }
}

// ── Owner filter (All | Mine | AI) ──────────────────────────────────────────

/// A three-chip filter row that separates self-created from AI-created tasks
/// (All · Mine · AI). The AI chip carries a small count badge when the agent
/// has queued tasks. Built from [LzChip]s + [LzBadge] for kit consistency.
class _OwnerFilterRow extends StatelessWidget {
  const _OwnerFilterRow({
    required this.filter,
    required this.aiCount,
    required this.onChanged,
  });

  final TaskOwnerFilter filter;

  /// Number of AI-created tasks (badges the "AI" chip; hidden when zero).
  final int aiCount;
  final void Function(TaskOwnerFilter) onChanged;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        LzChip(
          label: TaskOwnerFilter.all.label,
          icon: Icons.all_inclusive,
          selected: filter == TaskOwnerFilter.all,
          onTap: () => onChanged(TaskOwnerFilter.all),
        ),
        const SizedBox(width: AppSpacing.sm),
        LzChip(
          label: TaskOwnerFilter.mine.label,
          icon: Icons.person_outline,
          selected: filter == TaskOwnerFilter.mine,
          onTap: () => onChanged(TaskOwnerFilter.mine),
        ),
        const SizedBox(width: AppSpacing.sm),
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            LzChip(
              label: TaskOwnerFilter.ai.label,
              icon: Icons.auto_awesome,
              color: AppColors.info,
              selected: filter == TaskOwnerFilter.ai,
              onTap: () => onChanged(TaskOwnerFilter.ai),
            ),
            if (aiCount > 0) ...[
              const SizedBox(width: AppSpacing.xs),
              LzBadge(count: aiCount, color: AppColors.info),
            ],
          ],
        ),
      ],
    );
  }
}

// ── View toggle ────────────────────────────────────────────────────────────────

/// A compact segment toggle that swaps the Tasks body between the sectioned
/// List, the dedicated Overdue view, the month Calendar, and the Projects
/// breakdown. Built from [LzChip]s so it inherits the kit styling (no bespoke
/// colors). Horizontally scrollable so the four segments never overflow on
/// narrow screens.
class _ViewToggle extends StatelessWidget {
  const _ViewToggle({required this.view, required this.onChanged});

  final _TasksView view;
  final void Function(_TasksView) onChanged;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          LzChip(
            label: 'List',
            icon: Icons.view_agenda_outlined,
            selected: view == _TasksView.list,
            onTap: () => onChanged(_TasksView.list),
          ),
          const SizedBox(width: AppSpacing.sm),
          LzChip(
            label: 'Overdue',
            icon: Icons.warning_amber_rounded,
            color: AppColors.error,
            selected: view == _TasksView.overdue,
            onTap: () => onChanged(_TasksView.overdue),
          ),
          const SizedBox(width: AppSpacing.sm),
          LzChip(
            label: 'Calendar',
            icon: Icons.calendar_month_outlined,
            selected: view == _TasksView.calendar,
            onTap: () => onChanged(_TasksView.calendar),
          ),
          const SizedBox(width: AppSpacing.sm),
          LzChip(
            label: 'Projects',
            icon: Icons.folder_outlined,
            selected: view == _TasksView.projects,
            onTap: () => onChanged(_TasksView.projects),
          ),
        ],
      ),
    );
  }
}
