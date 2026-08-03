import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/core/due_date.dart';
import 'package:lazyclaw_mobile/core/project_resolver.dart';
import 'package:lazyclaw_mobile/core/recurrence.dart';
import 'package:lazyclaw_mobile/core/reminder_lead.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/project.dart';
import '../../models/subtask.dart';
import '../../models/task.dart';
import '../../providers/budgets_provider.dart';
import '../../providers/tasks_provider.dart';
import '../../widgets/link_text.dart';
import '../expenses/add_expense_for_task.dart';
import '../settings/settings_prefs.dart';
import 'add_link_dialog.dart';
import 'chip_edit.dart';
import 'reschedule_sheet.dart';
import 'task_attribute_chips.dart';
import 'task_budget_control.dart';
import 'task_comments_section.dart';
import 'task_detail_patch.dart';
import 'task_detail_pickers.dart';
import 'task_due_model.dart';
import 'task_due_section.dart';
import 'task_expense_rollup.dart';
import 'task_notes_field.dart';
import 'task_repeat_section.dart';
import 'task_section_label.dart';
import 'task_subtasks_section.dart';
import 'task_tags_field.dart';

/// The pure expense rollups used to live at the bottom of this file. They now
/// have their own library (this file was already past the 800-line ceiling),
/// re-exported here so existing `import '.../task_detail_sheet.dart'` call
/// sites that reach for [subtaskExpenseTotals] keep resolving unchanged.
export 'task_expense_rollup.dart';

/// A task detail/edit bottom sheet. Pre-fills every field from [task] and lets
/// the user change the title, notes, priority, project and due date, then Save
/// (patch via [TasksNotifier.updateTask]) or Delete (confirm, then
/// [TasksNotifier.deleteTask]). Mirrors the add-task sheet's look so the two
/// surfaces feel like one family.
class TaskDetailSheet extends ConsumerStatefulWidget {
  const TaskDetailSheet({
    super.key,
    required this.task,
    this.projects = const [],
    this.defaultLead = kDefaultReminderLead,
  });

  final Task task;

  /// Known projects (name + color) for the project picker. Empty when the
  /// caller doesn't surface project editing.
  final List<Project> projects;

  /// Global default reminder lead, applied when the task has a due time but no
  /// explicit reminder yet (mirrors the add-task sheet).
  final ReminderLead defaultLead;

  @override
  ConsumerState<TaskDetailSheet> createState() => _TaskDetailSheetState();
}

class _TaskDetailSheetState extends ConsumerState<TaskDetailSheet> {
  late final TextEditingController _titleController;
  late final TextEditingController _notesController;
  late final TextEditingController _budgetController;
  late String _priority;

  /// Whether the Notes block shows the editable field vs. a read-only
  /// [LinkText] preview. Seeded so empty notes open straight in the editor
  /// (today's behavior); non-empty notes start collapsed into the preview and
  /// only switch to the editor when the user taps it.
  late bool _editingNotes;
  late final FocusNode _notesFocus;

  /// Working copy of the task's tags. Edited in-sheet (add/remove chips) and
  /// committed on Save. [_originalTagsJson] snapshots the on-open serialization
  /// so Save only writes `tags` when they actually changed (no churn on a
  /// title-only edit). The wire/cache shape is a JSON-array string.
  late List<String> _tags;
  late String _originalTagsJson;
  final TextEditingController _tagController = TextEditingController();

  /// The task's allocated budget on open (null = none). Save compares the
  /// parsed field against this to decide set / clear / untouched.
  double? _originalBudget;

  /// Whether the allocated-budget field is on screen. Seeded true only when
  /// the task already HAS an allocation — otherwise the field is a permanently
  /// empty box in a sheet that is already too long, and the money dropdown is
  /// the discoverable way in. Once revealed it stays revealed for the session
  /// (never auto-hidden mid-edit).
  late bool _showBudgetField;
  final FocusNode _budgetFocus = FocusNode();

  /// The due date is split into a date-only day string (`_dueDay`) and a
  /// separate time-of-day (`_dueTime`), pre-filled from the task's stored
  /// dueDate (which may be date-only or a full ISO datetime).
  String? _dueDay;
  TimeOfDay? _dueTime;
  bool _saving = false;
  bool _deleting = false;

  /// The user's explicit reminder-lead choice. Seeded from the task's existing
  /// reminderAt; null when the task has no reminder yet, so the global default
  /// applies once a due time is present.
  ReminderLead? _explicitLead;

  /// The task's `reminderAt` on open (null / '' = none). A reminder is modelled
  /// as `due − lead`, so for a DATE-ONLY due date the lead — and therefore the
  /// composed reminder — is not derivable at all; this absolute value is the only
  /// record of it. Kept so Save can tell "untouched" (preserve) from "cleared"
  /// (send the `''` sentinel), and so the sheet can still SHOW it.
  String? _originalReminderAt;

  /// Set once the user touches the REMIND control — a lead chip, or the ✕ on the
  /// read-only reminder row. Only then may Save send the clear sentinel.
  bool _reminderTouched = false;

  /// Set once the user changes the due day or the due time. Gates re-anchoring a
  /// date-only task's reminder onto the new day (and clearing it outright when
  /// the due date is removed), so an edit that never went near the date chips
  /// can't move or destroy the reminder.
  bool _dueTouched = false;

  /// Working copy of the task's sub-tasks. Edited in-sheet and committed as part
  /// of Save (one atomic [TasksNotifier.updateTask] call). [_originalSteps]
  /// snapshots the on-open serialization so Save only writes `steps` when the
  /// checklist actually changed (no churn on a title-only edit).
  late List<Subtask> _subtasks;
  String? _originalSteps;

  /// The selected project (`category`). Seeded from the task; null/blank means
  /// "No project". [_categoryTouched] gates whether Save writes the column, so a
  /// title-only edit never churns the category.
  String? _category;
  bool _categoryTouched = false;

  /// The selected recurrence. Seeded from the task's stored cron via
  /// [recurrenceFromCron]. [_recurrenceTouched] gates whether Save writes the
  /// `recurring` column, so a title-only edit never churns it (and a non-editable
  /// "custom" cron is preserved untouched until the user picks a known kind).
  late Recurrence _recurrence;
  bool _recurrenceTouched = false;

  /// The recurring series' end day (`yyyy-MM-dd`), or null = repeats forever
  /// ("Never"). Seeded from the task (the stored value may be a full ISO
  /// datetime — only the day part is edited here). [_recurUntilTouched] gates
  /// whether Save writes the column (sending the `''` sentinel to clear), so a
  /// title-only edit never churns it.
  String? _recurUntil;
  bool _recurUntilTouched = false;

  @override
  void initState() {
    super.initState();
    final t = widget.task;
    _titleController = TextEditingController(text: t.title);
    _notesController = TextEditingController(text: t.description ?? '');
    _editingNotes = t.description?.trim().isNotEmpty != true;
    _notesFocus = FocusNode();
    _tags = _parseTags(t.tags);
    _originalTagsJson = jsonEncode(_tags);
    _originalBudget = t.allocatedBudget;
    _budgetController = TextEditingController(
      text: _formatBudget(t.allocatedBudget),
    );
    _showBudgetField = t.allocatedBudget != null;
    // Fall back rather than trust a stored value the chips can't show.
    _priority = kTaskPriorities.contains(t.priority) ? t.priority : 'medium';
    final raw = t.dueDate;
    _dueDay = (raw == null || raw.isEmpty) ? null : dueDateDayPart(raw);
    final parts = dueTimeParts(raw);
    _dueTime = parts == null
        ? null
        : TimeOfDay(hour: parts.hour, minute: parts.minute);
    _originalReminderAt = t.reminderAt;
    final hasReminder = t.reminderAt != null && t.reminderAt!.isNotEmpty;
    // Only seed the picker when the lead is actually DERIVABLE (the stored due
    // carries a time-of-day). For a date-only due, leadFromReminderAt can only
    // answer "None" — seeding that both hid the real reminder AND suppressed the
    // global default if the user later added a due time.
    _explicitLead = (hasReminder && dueDateHasTime(raw))
        ? leadFromReminderAt(raw, t.reminderAt)
        : null;
    _subtasks = List.of(t.subtasks);
    _originalSteps = serializeSubtasks(_subtasks);
    _category = (t.category == null || t.category!.isEmpty) ? null : t.category;
    _recurrence = recurrenceFromCron(t.recurring);
    _recurUntil = (t.recurUntil == null || t.recurUntil!.isEmpty)
        ? null
        : dueDateDayPart(t.recurUntil!);
  }

  /// The due/reminder derivations, rebuilt from the current fields on every
  /// read. Cheap (a const-shaped value object over seven fields) and always
  /// consistent — there is no cached copy to invalidate. See
  /// `task_due_model.dart` for the rules themselves.
  TaskDueModel get _due => TaskDueModel(
    dueDay: _dueDay,
    dueTime: _dueTime,
    originalReminderAt: _originalReminderAt,
    explicitLead: _explicitLead,
    defaultLead: widget.defaultLead,
    reminderTouched: _reminderTouched,
    dueTouched: _dueTouched,
  );

  @override
  void dispose() {
    _titleController.dispose();
    _notesController.dispose();
    _budgetController.dispose();
    _tagController.dispose();
    _notesFocus.dispose();
    _budgetFocus.dispose();
    super.dispose();
  }

  /// Parse the task's stored `tags` (a JSON-array string) into a list. Tolerant:
  /// null / empty / malformed → `[]`.
  static List<String> _parseTags(String? raw) {
    if (raw == null || raw.trim().isEmpty) return [];
    try {
      final decoded = jsonDecode(raw);
      if (decoded is List) return decoded.map((e) => e.toString()).toList();
    } catch (_) {}
    return [];
  }

  /// Format a budget for the numeric field: null → empty; a whole number drops
  /// the trailing `.0` (`250.0` → `250`).
  static String _formatBudget(double? v) {
    if (v == null) return '';
    if (v == v.roundToDouble()) return v.toInt().toString();
    return v.toString();
  }

  /// Open the TASK-level comment thread (the same sheet a sub-task's 💬 badge
  /// opens, just scoped to the task).
  ///
  /// [live] is the freshly-watched task, not `widget.task`: comments write
  /// through the notifier immediately, so the snapshot the sheet was opened
  /// with goes stale the moment one is added.
  Future<void> _openTaskComments(Task live) async {
    await showCommentsSheet(
      context,
      title: 'Comments',
      comments: taskLevelComments(live.taskComments),
      onAdd: (text) =>
          ref.read(tasksProvider.notifier).addComment(widget.task.id, text),
      onDelete: (cid) =>
          ref.read(tasksProvider.notifier).deleteComment(widget.task.id, cid),
      onAddLink: () => showAddLinkDialog(context),
    );
  }

  /// Open ONE sub-task's comment thread — the same sheet as
  /// [_openTaskComments], scoped to [subtaskId] instead of to the task.
  Future<void> _openSubtaskComments(Task live, String subtaskId) async {
    await showCommentsSheet(
      context,
      title: _subtaskTitle(subtaskId),
      comments: [
        for (final c in live.taskComments)
          if (c.subtaskId == subtaskId) c,
      ],
      onAdd: (text) => ref
          .read(tasksProvider.notifier)
          .addComment(widget.task.id, text, subtaskId: subtaskId),
      onDelete: (cid) =>
          ref.read(tasksProvider.notifier).deleteComment(widget.task.id, cid),
      onAddLink: () => showAddLinkDialog(context),
    );
  }

  /// Open the tags popup. The text controller stays owned by THIS state, not
  /// by the popup, so a tag typed but never submitted survives the popup
  /// closing and is still folded in by [_foldPendingTag] on Save — the
  /// behavior the old always-on field had.
  Future<void> _openTags() async {
    await showTaskTagsSheet(
      context,
      tags: _tags,
      controller: _tagController,
      onChanged: (next) => setState(() => _tags = next),
    );
  }

  /// Commit any un-submitted text in the tag field into [_tags]. Called from
  /// Save, which pops immediately afterwards — no `setState` needed (and the
  /// `_saving` flag's own `setState` rebuilds anyway).
  void _foldPendingTag() {
    if (_tagController.text.trim().isEmpty) return;
    _tags = tagsWithAdded(_tags, _tagController.text);
    _tagController.clear();
  }

  /// Switches the Notes block from the read-only preview to the editable
  /// field and focuses it — mirrors the tap-to-edit pattern used by the
  /// title/sub-task inline editors elsewhere in this sheet.
  void _beginEditNotes() {
    setState(() => _editingNotes = true);
    _notesFocus.requestFocus();
  }

  /// Opens the "Add link" dialog and, when the user inserts a link, splices
  /// the returned `[text](url)` markdown into `_notesController` at the
  /// current cursor position. Falls back to appending at the end when the
  /// selection is invalid (e.g. the field hasn't been focused yet, so the
  /// controller's selection is still the default collapsed-at--1).
  Future<void> _addLink() async {
    final result = await showAddLinkDialog(context);
    if (result == null || !mounted) return;
    final text = _notesController.text;
    final selection = _notesController.selection;
    final start = selection.isValid ? selection.start : text.length;
    final end = selection.isValid ? selection.end : text.length;
    final nextText = text.replaceRange(start, end, result);
    setState(() {
      _notesController.value = TextEditingValue(
        text: nextText,
        selection: TextSelection.collapsed(offset: start + result.length),
      );
    });
  }

  Future<void> _save() async {
    final title = _titleController.text.trim();
    if (title.isEmpty || _saving || _deleting) return;
    // Fold any un-committed text in the tag field into the list before saving.
    _foldPendingTag();
    setState(() => _saving = true);
    // Every three-way (untouched / clear / set) rule lives in the pure
    // builder — see task_detail_patch.dart for why it is not inlined here.
    final patch = buildTaskDetailPatch(
      title: title,
      description: _notesController.text.trim(),
      priority: _priority,
      composedDue: _due.composedDue,
      tags: _tags,
      originalTagsJson: _originalTagsJson,
      budgetText: _budgetController.text,
      originalBudget: _originalBudget,
      nextSteps: serializeSubtasks(_subtasks),
      originalSteps: _originalSteps,
      categoryTouched: _categoryTouched,
      category: _category,
      recurrenceTouched: _recurrenceTouched,
      nextCron: recurrenceToCron(
        _recurrence,
        dueAnchor: recurrenceAnchorFromDue(_due.composedDue),
      ),
      recurUntilTouched: _recurUntilTouched,
      recurUntil: _recurUntil,
      // Untouched reminders ride as null (absent) — see TaskDueModel.reminderArg.
      reminderArg: _due.reminderArg,
    );
    await ref
        .read(tasksProvider.notifier)
        .updateTask(
          widget.task.id,
          title: patch.title,
          description: patch.description,
          priority: patch.priority,
          category: patch.category,
          dueDate: patch.dueDate,
          steps: patch.steps,
          reminderAt: patch.reminderAt,
          recurring: patch.recurring,
          recurUntil: patch.recurUntil,
          tags: patch.tags,
          allocatedBudget: patch.allocatedBudget,
          clearAllocatedBudget: patch.clearAllocatedBudget,
        );
    if (!mounted) return;
    Navigator.of(context).pop();
  }

  /// Close this sheet and open the Smart Fast Reschedule sheet for the task.
  /// The reschedule writes through [TasksNotifier.updateTask] itself, so we just
  /// hand off — any in-progress edits here are intentionally discarded (a quick
  /// reschedule is a deliberate "just move the date" action).
  Future<void> _reschedule() async {
    final navigator = Navigator.of(context);
    // Use the navigator's (still-mounted) context to present the next sheet —
    // this sheet's own context becomes defunct once we pop it below.
    final rootContext = navigator.context;
    final task = widget.task;
    navigator.pop();
    await showRescheduleSheet(rootContext, ref, task);
  }

  // ── Money ───────────────────────────────────────────────────────────────

  /// The task's destination project row, resolved from its `category` NAME
  /// (tasks store a project name, expenses need the project's id).
  ///
  /// Prefers the picker list the caller handed in; falls back to the budgets
  /// cache because some call sites open this sheet without surfacing project
  /// editing at all (`projects: const []`) — the task still HAS a project
  /// there, and "add expense" must not be dead just because the picker is.
  /// Returns null when the name is blank or doesn't resolve to exactly one
  /// project ([resolveProjectMatch] never guesses on an ambiguous match).
  Project? _resolveProject(List<Project> cached) {
    final name = _category;
    if (name == null || name.trim().isEmpty) return null;
    final pool = widget.projects.isNotEmpty ? widget.projects : cached;
    return resolveProjectMatch(name, pool);
  }

  /// Reveal (and focus) the allocated-budget field. Never hides it again — a
  /// second pick while it's already open just re-focuses, so the action can't
  /// destroy a half-typed number.
  void _revealAllocatedBudget() {
    setState(() => _showBudgetField = true);
    _budgetFocus.requestFocus();
  }

  /// Open the task-scoped Add Expense sheet for this task, optionally pinned
  /// to one of its sub-tasks.
  ///
  /// [subtaskId] must name a SAVED sub-task — the affordance that reaches this
  /// is hidden for in-sheet, not-yet-saved ones (see [SubtaskEditor]).
  ///
  /// No explicit refresh afterwards: `build` watches [budgetsProvider], and
  /// `addExpense` writes an optimistic row into that state, so the rollup and
  /// the sub-task chips re-render on the same frame the sheet stays open for.
  Future<void> _addExpense({
    required Project project,
    String? subtaskId,
    String? contextLabel,
  }) async {
    await showAddExpenseForTaskSheet(
      context,
      ref,
      projectId: project.id,
      taskId: widget.task.id,
      subtaskId: subtaskId,
      contextLabel: contextLabel,
    );
  }

  /// Explain, rather than silently do nothing, when a sub-task's money sign is
  /// tapped on a task with no (resolvable) project. The sheet deliberately
  /// still SHOWS the affordance — hiding it would make the feature look absent
  /// instead of blocked.
  void _warnNoProject() {
    ScaffoldMessenger.maybeOf(
      context,
    )?.showSnackBar(const SnackBar(content: Text(kTaskBudgetNoProjectReason)));
  }

  Future<void> _pickProject() async {
    final result = await showProjectPicker(
      context,
      projects: widget.projects,
      current: _category,
    );
    if (result == null || !mounted) return;
    setState(() {
      _categoryTouched = true;
      _category = (result.category == null || result.category!.isEmpty)
          ? null
          : result.category;
    });
  }

  Future<void> _delete() async {
    if (_saving || _deleting) return;
    final confirmed = await LzConfirm.show(
      context,
      title: 'Delete task?',
      message: widget.task.title,
      confirmLabel: 'Delete',
      danger: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => _deleting = true);
    await ref.read(tasksProvider.notifier).deleteTask(widget.task.id);
    if (!mounted) return;
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    // Comments are IMMEDIATE (they write straight through the notifier, not
    // save-gated like the rest of this sheet's fields) — so the sheet must
    // watch the fresh row rather than the snapshot `widget.task` it was opened
    // with, or a just-added comment wouldn't appear until Save/reopen.
    final live =
        ref
            .watch(tasksProvider)
            .tasks
            .where((t) => t.id == widget.task.id)
            .firstOrNull ??
        widget.task;
    // Keyed off `live.subtasks` (the SAVED task's parsed steps) rather than
    // `_subtasks` (this sheet's un-saved working list) — a comment writes
    // through the notifier IMMEDIATELY, but a locally-added sub-task doesn't
    // exist server-side until Save. Opening the comment sheet for one before
    // then would replay `comment_add` against an unknown `subtask_id` (a
    // definitive 400 the outbox then drains, silently erasing the comment).
    // A subtask id absent here — new/unsaved — gets no key, and
    // SubtaskEditor hides the 💬 affordance entirely for ids missing from
    // this map (see its `onOpenComments` doc).
    final commentCounts = <String, int>{
      for (final s in live.subtasks)
        s.id: live.taskComments.where((c) => c.subtaskId == s.id).length,
    };
    // Per-sub-task expense totals — "the money sign" SubtaskEditor renders.
    // Sourced from the SAME live/saved task id as commentCounts above (an
    // expense's subtask_id only ever points at a saved sub-task, exactly
    // like a comment's), so an in-sheet, not-yet-saved sub-task correctly
    // gets no chip either.
    final budgets = ref.watch(budgetsProvider);
    final expenses = budgets.expenses;
    final expenseTotals = subtaskExpenseTotals(expenses, widget.task.id);
    final expenseCurrency = subtaskExpenseCurrency(expenses, widget.task.id);
    // The destination project for any money added from this sheet. Null =
    // the task has no project (or names one that can't be resolved), which
    // disables the "Add expense" action with a stated reason.
    final project = _resolveProject(budgets.projects);
    // The SAVED sub-task ids — the only ones an expense's `subtask_id` may
    // point at, exactly like `commentCounts` above.
    final savedSubtaskIds = {for (final s in live.subtasks) s.id};
    return LzFloatingSubmitLayout(
      // Square, always-on-screen Save. The old footer button was anchored to
      // the END of this (very long) column, so on a short viewport — or with
      // the keyboard up — it was simply off screen: the user's report was
      // that there was no save button at all. NOTE: this layout brings its
      // own scroll view, so the column below must NOT be wrapped in another.
      submit: LzFloatingSubmit(
        key: const Key('task-detail-save'),
        tooltip: 'Save task',
        loading: _saving,
        onPressed: (_saving || _deleting) ? null : _save,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ── Destructive action, parked far from the thumb ──────────────
          //
          // Delete used to sit shoulder-to-shoulder with Save in a footer
          // Row. Now that Save is a bottom-right floating target this sheet
          // is thumb-driven, and "one mis-tap deletes the task" is not an
          // acceptable failure mode — so Delete lives at the TOP-RIGHT
          // (furthest reachable corner from the submit), rendered muted and
          // icon-only, and still behind a confirm dialog.
          Align(
            alignment: Alignment.centerRight,
            child: TaskDeleteAction(
              deleting: _deleting,
              onPressed: _saving ? null : _delete,
            ),
          ),

          // ── Title ──────────────────────────────────────────────────────
          LzTextField(
            controller: _titleController,
            fieldKey: const Key('task-detail-title'),
            hint: 'What needs to be done?',
            prefixIcon: Icons.task_alt_outlined,
            textInputAction: TextInputAction.next,
          ),

          const SizedBox(height: AppSpacing.lg),

          // ── Notes, with the task's comment thread one tap away ──────────
          //
          // The comments used to be a whole section pinned BELOW sub-tasks —
          // the far end of the app's longest sheet. As a badge here they cost
          // one row and are reachable the instant the sheet opens.
          Row(
            children: [
              TaskSectionLabel('NOTES'),
              const Spacer(),
              TaskCommentsBadge(
                count: taskLevelComments(live.taskComments).length,
                onTap: () => _openTaskComments(live),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          TaskNotesField(
            controller: _notesController,
            focusNode: _notesFocus,
            editing: _editingNotes,
            onBeginEdit: _beginEditNotes,
            onAddLink: _addLink,
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Priority + Project/Tags chips (see task_attribute_chips) ───
          TaskPriorityChips(
            priority: _priority,
            onChanged: (p) => setState(() => _priority = p),
          ),

          const SizedBox(height: AppSpacing.xl),

          TaskProjectTagsRow(
            projects: widget.projects,
            category: _category,
            tags: _tags,
            onPickProject: _pickProject,
            onOpenTags: _openTags,
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Budget (extracted: see TaskBudgetSection) ──────────────────
          TaskBudgetSection(
            allocated: _originalBudget,
            spent: taskExpenseTotal(expenses, widget.task.id),
            currency: taskExpenseCurrency(expenses, widget.task.id),
            canAddExpense: project != null,
            showAllocatedField: _showBudgetField,
            allocatedController: _budgetController,
            allocatedFocusNode: _budgetFocus,
            onEditAllocated: _revealAllocatedBudget,
            // Unreachable while `canAddExpense` is false (the row is disabled),
            // but a non-null callback keeps the widget's contract simple.
            onAddExpense: () => project == null
                ? _warnNoProject()
                : _addExpense(project: project, contextLabel: live.title),
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Due date + reminder (extracted: see TaskDueSection) ────────
          TaskDueSection(
            dueDay: _dueDay,
            dueTime: _dueTime,
            composedDue: _due.composedDue,
            survivingReminderAt: _due.survivingReminderAt,
            effectiveLead: _due.effectiveLead,
            todayIso: _isoToday(),
            tomorrowIso: _isoTomorrow(),
            onReschedule: _reschedule,
            onToggleDay: (iso) => setState(() {
              _dueTouched = true;
              _dueDay = _dueDay == iso ? null : iso;
            }),
            onPickDay: _pickDate,
            onPickTime: _pickTime,
            onClearTime: () => setState(() {
              _dueTouched = true;
              _dueTime = null;
            }),
            onClearDue: () => setState(() {
              _dueTouched = true;
              _dueDay = null;
              _dueTime = null;
            }),
            onLeadChanged: (lead) => setState(() {
              _reminderTouched = true;
              _explicitLead = lead;
            }),
            onClearReminder: () => setState(() => _reminderTouched = true),
          ),

          // ── Repeat + series end (extracted: see TaskRepeatSection) ────
          const SizedBox(height: AppSpacing.xl),
          TaskRepeatSection(
            recurrence: _recurrence,
            recurUntil: _recurUntil,
            anchorWeekday: _due.composedDue == null
                ? null
                : DateTime.tryParse(_due.composedDue!)?.weekday,
            onRecurrenceChanged: (r) => setState(() {
              _recurrenceTouched = true;
              _recurrence = r;
            }),
            onPickUntil: _pickRecurUntil,
            onClearUntil: () => setState(() {
              _recurUntilTouched = true;
              _recurUntil = null;
            }),
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Sub-tasks (extracted: see TaskSubtasksSection) ─────────────
          TaskSubtasksSection(
            subtasks: _subtasks,
            onChanged: (next) => setState(() => _subtasks = next),
            commentCounts: commentCounts,
            expenseTotals: expenseTotals,
            expenseCurrency: expenseCurrency,
            savedSubtaskIds: savedSubtaskIds,
            // Always wired, even with no project: the money sign stays
            // visible and EXPLAINS itself (snackbar) instead of vanishing,
            // which would read as "this task can't have expenses".
            onAddExpense: (sid) => project == null
                ? _warnNoProject()
                : _addExpense(
                    project: project,
                    subtaskId: sid,
                    contextLabel: 'Sub-task: ${_subtaskTitle(sid)}',
                  ),
            onOpenComments: (sid) => _openSubtaskComments(live, sid),
          ),
        ],
      ),
    );
  }

  Future<void> _pickDate() async {
    final now = DateTime.now();
    final picked = await showTaskDatePicker(
      context,
      initialDate: dueDayPickerSeed(_dueDay, now: now),
      firstDate: now.subtract(const Duration(days: 365)),
      lastDate: now.add(const Duration(days: 365)),
    );
    if (picked == null) return;
    setState(() {
      _dueTouched = true;
      _dueDay = isoDay(picked);
    });
  }

  /// Pick the series' end day. A long horizon (10 years) so a yearly
  /// recurrence can still be given a meaningful end date.
  Future<void> _pickRecurUntil() async {
    final now = DateTime.now();
    final picked = await showTaskDatePicker(
      context,
      initialDate: recurUntilPickerSeed(_recurUntil, now: now),
      firstDate: now,
      lastDate: now.add(const Duration(days: 3650)),
    );
    if (picked == null) return;
    setState(() {
      _recurUntilTouched = true;
      _recurUntil = isoDay(picked);
    });
  }

  Future<void> _pickTime() async {
    final picked = await showTaskTimePicker(context, initial: _dueTime);
    if (picked == null) return;
    setState(() {
      _dueTouched = true;
      _dueTime = picked;
    });
  }

  String _isoToday() => isoDay(DateTime.now());

  String _isoTomorrow() => isoDay(DateTime.now().add(const Duration(days: 1)));

  /// The sub-task's title for the comments sheet header, falling back to a
  /// generic label if the id somehow no longer matches (defensive only — the
  /// badge that opens this sheet is only ever rendered for a live sub-task).
  String _subtaskTitle(String id) => _subtasks
      .firstWhere(
        (s) => s.id == id,
        orElse: () => const Subtask(id: '', title: 'Sub-task', done: false),
      )
      .title;
}

// ── Public helper ─────────────────────────────────────────────────────────────

/// Open the task detail/edit sheet for [task]. The sheet reads
/// [tasksProvider] for Save/Delete; [ref] is accepted so the call site (the
/// Tasks screen, which already holds a [WidgetRef]) owns the invocation.
/// [projects] populates the project picker.
Future<void> showTaskDetailSheet(
  BuildContext context,
  WidgetRef ref,
  Task task, {
  List<Project> projects = const [],
  ReminderLead defaultLead = kDefaultReminderLead,
}) {
  return LzBottomSheet.show<void>(
    context,
    title: 'Edit Task',
    builder: (_) => TaskDetailSheet(
      task: task,
      projects: projects,
      defaultLead: defaultLead,
    ),
  );
}
