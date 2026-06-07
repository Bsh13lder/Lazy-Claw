import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../core/actions/app_actions.dart';
import '../models/task.dart';
import '../providers/budgets_provider.dart';
import '../providers/tasks_provider.dart';
import 'notes/notes_body.dart';
import 'storage_banners.dart';
import 'tasks/add_task_sheet.dart';
import 'tasks/task_calendar_view.dart';
import 'tasks/task_detail_sheet.dart';
import 'tasks/task_row.dart';

// ── Top segment ─────────────────────────────────────────────────────────────

/// The two top-level segments of this tab. Notes was promoted out of the bottom
/// nav into this segment; the freed slot now hosts the Documents tab.
enum _Segment { tasks, notes }

// ── View mode ─────────────────────────────────────────────────────────────────

/// The two ways to view tasks (nested under the Tasks segment).
enum _TasksView { list, calendar }

// ── Sections ──────────────────────────────────────────────────────────────────

/// The four section buckets rendered by [TasksScreen].
enum _Section { overdue, today, upcoming, done }

extension _SectionLabel on _Section {
  String get label {
    switch (this) {
      case _Section.overdue:
        return 'Overdue';
      case _Section.today:
        return 'Today';
      case _Section.upcoming:
        return 'Upcoming';
      case _Section.done:
        return 'Done';
    }
  }

  IconData get emptyIcon {
    switch (this) {
      case _Section.overdue:
        return Icons.warning_amber_rounded;
      case _Section.today:
        return Icons.today_outlined;
      case _Section.upcoming:
        return Icons.event_outlined;
      case _Section.done:
        return Icons.check_circle_outline;
    }
  }
}

// ── Grouping helper ───────────────────────────────────────────────────────────

/// Splits [tasks] into the four section buckets.
Map<_Section, List<Task>> _groupTasks(List<Task> tasks) {
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
    _Section.overdue: overdue,
    _Section.today: todayList,
    _Section.upcoming: upcoming,
    _Section.done: done,
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

  /// Calendar focus (the visible month) and the selected day. Seeded to today
  /// so the calendar opens on the current month with today highlighted and the
  /// FAB pre-fills today.
  late DateTime _focusedDay;
  late DateTime _selectedDay;

  /// The calendar colors tasks by their project, so it needs the budgets store.
  /// We load it lazily on the first switch to Calendar (kept out of the
  /// list-only path so a list-only screen never builds the budgets provider).
  bool _budgetsRequested = false;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _focusedDay = DateTime(now.year, now.month, now.day);
    _selectedDay = _focusedDay;
    // Load tasks from the local cache on first render (offline-first).
    Future.microtask(() => ref.read(tasksProvider.notifier).load());

    // Cold-start deep link: a `+ Task` / `+ Note` shortcut or widget button
    // may have set a pending action BEFORE this screen mounted. ref.listen
    // (in build) only fires on CHANGE, so replay any pre-existing one here.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final taken = takePendingAction(ref, _myActions);
      if (taken != null && mounted) _handlePendingAction(taken);
    });
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
        break; // not owned by this screen
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

  Future<void> _openAddSheet({DateTime? initialDueDate}) async {
    // Tactile tick when summoning the add sheet (FAB + app-bar + button +
    // calendar day-tap all route through here).
    HapticFeedback.selectionClick();
    final result = await showAddTaskSheet(context, initialDueDate: initialDueDate);
    if (result == null || !mounted) return;
    await ref.read(tasksProvider.notifier).addTask(
          result.title,
          priority: result.priority,
          dueDate: result.dueDate,
          category: result.category,
        );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(tasksProvider);
    final reachable = ref.watch(reachableProvider);
    final degraded = ref.watch(dbHealthProvider).isDegraded;
    final notesMode = _segment == _Segment.notes;

    // Warm deep link: a `+ Task` / `+ Note` shortcut/widget fired while this
    // screen was already alive (indexedStack keeps it mounted). Consume + open.
    ref.listen<AppAction?>(pendingActionProvider, (_, next) {
      if (next == null) return;
      final taken = takePendingAction(ref, _myActions);
      if (taken != null) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) _handlePendingAction(taken);
        });
      }
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
              onPressed: () =>
                  ref.read(tasksProvider.notifier).clearError(),
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
            child: notesMode ? const NotesBody() : _buildTasksContent(state),
          ),
        ],
      ),
    );
  }

  /// The Tasks segment body: the List ⇄ Calendar toggle (nested) + content.
  Widget _buildTasksContent(TasksState state) {
    return Column(
      children: [
        // List ⇄ Calendar toggle — always visible so the user can switch
        // even from an empty/error state.
        Padding(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.lg,
            AppSpacing.md,
            AppSpacing.lg,
            0,
          ),
          child: _ViewToggle(
            view: _view,
            onChanged: (v) {
              if (v == _view) return;
              HapticFeedback.selectionClick();
              if (v == _TasksView.calendar) _ensureBudgetsLoaded();
              setState(() => _view = v);
            },
          ),
        ),
        Expanded(
          child: _view == _TasksView.list
              ? _buildBody(state)
              : _buildCalendarBody(state),
        ),
      ],
    );
  }

  /// Calendar body: an error/skeleton guard around [TaskCalendarView]. The
  /// calendar itself is useful even with zero tasks, so we only short-circuit
  /// on the first instant load and on a hard error with nothing cached.
  Widget _buildCalendarBody(TasksState state) {
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

    final projects = ref.watch(budgetsProvider).projects;

    return LzRefresh(
      onRefresh: _refresh,
      child: TaskCalendarView(
        tasks: state.tasks,
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
        onOpen: (task) => showTaskDetailSheet(context, ref, task),
        onAddOnDay: (day) => _openAddSheet(initialDueDate: day),
      ),
    );
  }

  Widget _buildBody(TasksState state) {
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

    // ── Sectioned list ───────────────────────────────────────────────────────
    final grouped = _groupTasks(state.tasks);

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
          for (final section in _Section.values)
            _TaskSection(
              section: section,
              tasks: grouped[section] ?? const [],
              dirtyIds: state.dirtyIds,
              onComplete: (id) =>
                  ref.read(tasksProvider.notifier).completeTask(id),
              onDelete: (id) =>
                  ref.read(tasksProvider.notifier).deleteTask(id),
              onOpen: (task) => showTaskDetailSheet(context, ref, task),
            ),
        ],
      ),
    );
  }
}

// ── Section widget ─────────────────────────────────────────────────────────────

class _TaskSection extends StatefulWidget {
  const _TaskSection({
    required this.section,
    required this.tasks,
    required this.dirtyIds,
    required this.onComplete,
    required this.onDelete,
    required this.onOpen,
  });

  final _Section section;
  final List<Task> tasks;
  final Set<String> dirtyIds;
  final void Function(String id) onComplete;
  final void Function(String id) onDelete;
  final void Function(Task task) onOpen;

  @override
  State<_TaskSection> createState() => _TaskSectionState();
}

class _TaskSectionState extends State<_TaskSection> {
  // "Done" section starts collapsed.
  bool _collapsed = false;

  // The staggered fade+slide entrance plays exactly once, on first mount. Once
  // the last row finishes we drop the Animate wrappers so subsequent rebuilds
  // (sync refresh, complete, delete, scroll) render plain rows — no replay.
  bool _entered = false;

  @override
  void initState() {
    super.initState();
    _collapsed = widget.section == _Section.done;
  }

  void _markEntered() {
    if (!_entered && mounted) setState(() => _entered = true);
  }

  /// Wrap [child] in a subtle, index-staggered fade+slide entrance — but only
  /// until [_entered] flips. The flag is flipped by the LAST row's completion
  /// so earlier rows are never cut short mid-animation.
  Widget _entrance({required int index, required int last, required Widget child}) {
    if (_entered) return child;
    return child
        .animate(
          onComplete: index == last ? (_) => _markEntered() : null,
        )
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
        widget.section != _Section.upcoming &&
        widget.section != _Section.today) {
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
      padding: const EdgeInsets.only(bottom: AppSpacing.xl),
      child: LzSection(
        title: widget.section.label,
        action: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (countBadge != null) ...[
              countBadge,
              const SizedBox(width: AppSpacing.sm),
            ],
            if (widget.section == _Section.done)
              GestureDetector(
                onTap: () => setState(() => _collapsed = !_collapsed),
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
            child: TaskRow(
              task: widget.tasks[i],
              pendingSync:
                  widget.dirtyIds.contains(widget.tasks[i].id),
              onComplete: () => widget.onComplete(widget.tasks[i].id),
              onDelete: () => widget.onDelete(widget.tasks[i].id),
              onTap: () => widget.onOpen(widget.tasks[i]),
            ),
          ),
          if (i < widget.tasks.length - 1)
            const SizedBox(height: AppSpacing.sm),
        ],
      ],
    );
  }

  String _emptyTitle(_Section section) {
    switch (section) {
      case _Section.today:
        return 'Nothing due today';
      case _Section.upcoming:
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

// ── View toggle ────────────────────────────────────────────────────────────────

/// A compact two-segment toggle that swaps the Tasks body between the sectioned
/// List and the month Calendar. Built from two [LzChip]s so it inherits the kit
/// styling (no bespoke colors).
class _ViewToggle extends StatelessWidget {
  const _ViewToggle({required this.view, required this.onChanged});

  final _TasksView view;
  final void Function(_TasksView) onChanged;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        LzChip(
          label: 'List',
          icon: Icons.view_agenda_outlined,
          selected: view == _TasksView.list,
          onTap: () => onChanged(_TasksView.list),
        ),
        const SizedBox(width: AppSpacing.sm),
        LzChip(
          label: 'Calendar',
          icon: Icons.calendar_month_outlined,
          selected: view == _TasksView.calendar,
          onTap: () => onChanged(_TasksView.calendar),
        ),
      ],
    );
  }
}
