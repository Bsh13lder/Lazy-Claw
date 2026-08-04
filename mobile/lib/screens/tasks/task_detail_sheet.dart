import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/core/autosave.dart';
import 'package:lazyclaw_mobile/core/due_date.dart';
import 'package:lazyclaw_mobile/core/recurrence.dart';
import 'package:lazyclaw_mobile/core/reminder_lead.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/project.dart';
import '../../models/subtask.dart';
import '../../models/task.dart';
import '../../providers/budgets_provider.dart';
import '../../providers/tasks_provider.dart';
import '../../widgets/autosave_indicator.dart';
import '../settings/settings_prefs.dart';
import 'chip_edit.dart';
import 'task_attribute_chips.dart';
import 'task_budget_control.dart';
import 'task_budget_edit_state.dart';
import 'task_comments_section.dart';
import 'task_detail_actions.dart';
import 'task_detail_live_data.dart';
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
/// the user change the title, notes, priority, project and due date.
///
/// It AUTO-SAVES: text is committed a beat after typing stops, discrete
/// controls (chips, pickers, the repeat menu) the moment they change, and
/// anything still pending is flushed when the sheet is dismissed or the app is
/// backgrounded. The floating check no longer means "make this real" — it means
/// "commit now and close". Delete stays explicit (confirm, then
/// [TasksNotifier.deleteTask]).
///
/// The write itself is unchanged: one [TasksNotifier.updateTask] carrying the
/// patch [buildTaskDetailPatch] builds, whose three-way (untouched / clear /
/// set) rules are what make auto-save safe to run repeatedly — see [_commit].
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
  /// preview. Seeded so empty notes open straight in the editor (today's
  /// behavior); non-empty notes start collapsed and switch on tap.
  late bool _editingNotes;
  late final FocusNode _notesFocus;

  /// Working copy of the task's tags. [_originalTagsJson] is the SAVED
  /// serialization — the baseline that decides whether `tags` rides in the
  /// patch at all. It advances on every successful write (see [_rebase]).
  late List<String> _tags;
  late String _originalTagsJson;
  final TextEditingController _tagController = TextEditingController();

  /// The BUDGET block's whole state (saved baseline, working draft, open
  /// editor, inline rejection) as ONE immutable value.
  late TaskBudgetEditState _budget;
  final FocusNode _budgetFocus = FocusNode();

  /// The due date, split into a date-only day and a separate time-of-day.
  String? _dueDay;
  TimeOfDay? _dueTime;
  bool _deleting = false;

  /// The inline rejection under the TITLE. Non-null blocks every write: an
  /// auto-save that wrote a half-deleted title over a good one would be the
  /// exact data loss this feature exists to prevent.
  String? _titleError;

  /// The user's explicit reminder-lead choice. Null until they touch it, so
  /// the global default applies once a due time is present.
  ReminderLead? _explicitLead;

  /// The task's PERSISTED `reminderAt` (null / '' = none). A reminder is
  /// modelled as `due − lead`, so for a DATE-ONLY due date the lead — and the
  /// composed reminder — is not derivable at all; this absolute value is the
  /// only record of it. It tells Save "untouched" (preserve) from "cleared"
  /// (send the `''` sentinel), and advances with every write.
  String? _originalReminderAt;

  /// Set once the user touches the REMIND control. Only then may a write send
  /// the clear sentinel. Reset by [_rebase] — after a write the persisted
  /// value IS the baseline again.
  bool _reminderTouched = false;

  /// Set once the user changes the due day or time. Gates re-anchoring a
  /// date-only task's reminder onto the new day. Reset by [_rebase].
  bool _dueTouched = false;

  /// Working copy of the sub-tasks; [_originalSteps] is the saved baseline.
  late List<Subtask> _subtasks;
  String? _originalSteps;

  /// The selected project (`category`). [_categoryTouched] gates whether a
  /// write touches the column at all.
  String? _category;
  bool _categoryTouched = false;

  /// The selected recurrence, seeded from the stored cron. [_recurrenceTouched]
  /// keeps a non-editable "custom" cron intact until the user picks a kind.
  late Recurrence _recurrence;
  bool _recurrenceTouched = false;

  /// The series' end day (`yyyy-MM-dd`), null = "Never".
  String? _recurUntil;
  bool _recurUntilTouched = false;

  /// Auto-save scheduling. Owns the debounce, the coalescing and the
  /// background flush; [_commit] owns every decision about WHAT to write.
  late final AutosaveController _autosave;

  /// Fingerprint of the state that is currently PERSISTED. Auto-save writes
  /// only when the live state differs — see [taskEditSignature].
  late String _savedSignature;

  /// Mirrors [_autosave.status] into the tree for the indicator.
  AutosaveStatus _status = AutosaveStatus.idle;

  /// Set at the top of [dispose] so the dismiss-time flush can update fields
  /// without calling `setState` on a State that is going away.
  bool _disposing = false;

  /// The notifier, captured while this widget is definitely alive. The
  /// dismiss-time flush runs from [dispose], where `ref` is no longer legal to
  /// use — and that flush is precisely the write this feature exists for.
  late final TasksNotifier _tasks;

  @override
  void initState() {
    super.initState();
    final t = widget.task;
    _tasks = ref.read(tasksProvider.notifier);
    _titleController = TextEditingController(text: t.title);
    _notesController = TextEditingController(text: t.description ?? '');
    _editingNotes = t.description?.trim().isNotEmpty != true;
    _notesFocus = FocusNode();
    _tags = parseTaskTags(t.tags);
    _originalTagsJson = jsonEncode(_tags);
    _budget = TaskBudgetEditState.forTask(t.allocatedBudget);
    _budgetController = TextEditingController(
      text: t.allocatedBudget == null
          ? ''
          : formatTaskAllocation(t.allocatedBudget!),
    );
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

    _savedSignature = _signature(_titleController.text.trim());
    _autosave = AutosaveController(onCommit: _commit)
      ..addListener(_onAutosaveStatus)
      ..bindText(_titleController)
      ..bindText(_notesController)
      ..bindText(_budgetController);
  }

  /// The due/reminder derivations, rebuilt from the current fields on every
  /// read. Cheap and always consistent — there is no cached copy to
  /// invalidate. See `task_due_model.dart` for the rules themselves.
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
    // ORDER IS LOAD-BEARING. `_disposing` first so a flush-triggered field
    // update doesn't call setState on a dying State; then detach the status
    // listener; then flush — which runs [_commit] synchronously as far as the
    // `await` on the write, so the whole payload is captured off the
    // controllers BEFORE they are disposed two lines below. Awaiting is not an
    // option in dispose, and is not needed: the notifier outlives this sheet.
    _disposing = true;
    _autosave.removeListener(_onAutosaveStatus);
    _autosave.flush();
    _autosave.dispose();
    _titleController.dispose();
    _notesController.dispose();
    _budgetController.dispose();
    _tagController.dispose();
    _notesFocus.dispose();
    _budgetFocus.dispose();
    super.dispose();
  }

  void _onAutosaveStatus() {
    if (!mounted || _disposing) return;
    setState(() => _status = _autosave.status);
  }

  /// `setState` while the sheet is alive, a plain assignment once it isn't.
  /// The dismiss-time flush legitimately updates baselines after the element
  /// is gone; there is simply nothing left to rebuild.
  void _apply(VoidCallback change) {
    if (!mounted || _disposing) {
      change();
      return;
    }
    setState(change);
  }

  // ── Auto-save ───────────────────────────────────────────────────────────

  /// Fingerprint of the RESULTING RECORD (never of the patch) — see
  /// [taskEditSignature] for why that distinction decides correctness.
  String _signature(String title) {
    final due = _due;
    return taskEditSignature(
      title: title,
      description: _notesController.text.trim(),
      priority: _priority,
      composedDue: due.composedDue,
      tags: _tags,
      budgetText: _budgetController.text.trim(),
      steps: serializeSubtasks(_subtasks),
      category: _category,
      recurring: recurrenceToCron(
        _recurrence,
        dueAnchor: recurrenceAnchorFromDue(due.composedDue),
      ),
      recurUntil: _recurUntil,
      // The reminder that SURVIVES this edit, not `reminderArg` — the latter
      // collapses to null once the value becomes the baseline, which would
      // leave the sheet permanently dirty and loop the writer.
      effectiveReminderAt: dueDateHasTime(due.composedDue)
          ? due.composedReminderAt
          : (due.survivingReminderAt ?? ''),
    );
  }

  /// Persist the sheet — the dirty gate, the validity gate and the writer, in
  /// that order. Called by [AutosaveController]; never directly.
  Future<AutosaveOutcome> _commit() async {
    // A queued write must never resurrect columns on a row being deleted.
    if (_deleting) return AutosaveOutcome.unchanged;

    final title = _titleController.text.trim();
    if (title.isEmpty) {
      _apply(() => _titleError = kTaskTitleRequiredError);
      return AutosaveOutcome.blocked;
    }
    // Junk in the allocation used to be dropped silently by the patch builder
    // (it parses to null = "untouched"), indistinguishable from a save.
    final budgetError = taskAllocationError(_budgetController.text);
    if (budgetError != null) {
      _apply(() => _budget = _budget.rejected(budgetError));
      return AutosaveOutcome.blocked;
    }
    if (_titleError != null) _apply(() => _titleError = null);

    // Fold un-submitted tag text before fingerprinting, so a tag typed but
    // never committed is part of what gets saved (matches the old Save).
    _foldPendingTag();
    final signature = _signature(title);
    if (signature == _savedSignature) return AutosaveOutcome.unchanged;

    // Every three-way (untouched / clear / set) rule lives in the pure builder
    // — see task_detail_patch.dart for why it is not inlined here.
    final patch = buildTaskDetailPatch(
      title: title,
      description: _notesController.text.trim(),
      priority: _priority,
      composedDue: _due.composedDue,
      tags: _tags,
      originalTagsJson: _originalTagsJson,
      budgetText: _budgetController.text,
      originalBudget: _budget.original,
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
    await _tasks.updateTask(
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
    _apply(() => _rebase(patch));
    _savedSignature = signature;
    return AutosaveOutcome.written;
  }

  /// Advance every three-way baseline onto what was just persisted.
  ///
  /// WITHOUT this, a value the user auto-saves and then REVERTS is read as
  /// "untouched" and never written back: clear an auto-saved allocation and
  /// the number survives in the database; delete an auto-saved tag and it
  /// comes back on reload. The one-shot Save this sheet used to have could
  /// freeze its baselines at open precisely because there was only ever one
  /// write.
  void _rebase(TaskDetailPatch patch) {
    _originalTagsJson = jsonEncode(_tags);
    _originalSteps = serializeSubtasks(_subtasks);
    _categoryTouched = false;
    _recurrenceTouched = false;
    _recurUntilTouched = false;
    // A null reminderAt means the column was left alone, so the baseline
    // stands; '' means it was cleared.
    final written = patch.reminderAt;
    if (written != null) {
      _originalReminderAt = written.isEmpty ? null : written;
    }
    _reminderTouched = false;
    _dueTouched = false;
    final text = _budgetController.text.trim();
    _budget = _budget.savedAs(text.isEmpty ? null : double.tryParse(text));
  }

  /// The floating check: commit whatever is outstanding, then close. It is no
  /// longer the only path to persistence, so a blocked write keeps the sheet
  /// open with its error visible rather than closing over the problem.
  Future<void> _submit() async {
    if (_deleting) return;
    await _autosave.flush();
    if (!mounted) return;
    if (_autosave.status == AutosaveStatus.blocked) return;
    Navigator.of(context).pop();
  }

  // ── Editing ─────────────────────────────────────────────────────────────

  /// Open the tags popup. The text controller stays owned by THIS state, not
  /// by the popup, so a tag typed but never submitted survives the popup
  /// closing and is still folded in by [_foldPendingTag].
  Future<void> _openTags() async {
    await showTaskTagsSheet(
      context,
      tags: _tags,
      controller: _tagController,
      onChanged: (next) {
        setState(() => _tags = next);
        _autosave.markDirtyNow();
      },
    );
  }

  /// Commit any un-submitted text in the tag field into [_tags].
  void _foldPendingTag() {
    if (_tagController.text.trim().isEmpty) return;
    _tags = tagsWithAdded(_tags, _tagController.text);
    _tagController.clear();
  }

  /// Switches the Notes block from the read-only preview to the editable
  /// field and focuses it.
  void _beginEditNotes() {
    setState(() => _editingNotes = true);
    _notesFocus.requestFocus();
  }

  Future<void> _addLink() async {
    final inserted = await insertLinkIntoNotes(context, _notesController);
    // The controller binding has already scheduled the save; this rebuild is
    // only so the field re-renders with the spliced text.
    if (inserted && mounted) setState(() {});
  }

  /// Edit the allocation in place. Nothing needs seeding — the figure the user
  /// tapped IS the controller's text (empty = no allocation yet).
  void _editAllocated() {
    setState(() => _budget = _budget.editing());
    _budgetFocus.requestFocus();
  }

  /// Commit a validated top-up: [total] is the NEW allocation (summed,
  /// validated and rounded by [previewTaskTopUp]). Writing it into the very
  /// controller the save path parses keeps a top-up "the allocation, edited"
  /// rather than a second concept — and it is a direct adjustment, NOT a
  /// `budget_entries` credit row (see `task_budget_math.dart`).
  void _commitTopUp(double total) {
    setState(() {
      _budgetController.text = formatTaskAllocation(total);
      _budget = _budget.toppedUp(total);
    });
    // A top-up is a decision, not a keystroke — persist it now rather than
    // making the user hope the debounce ran.
    _autosave.markDirtyNow();
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
    _autosave.markDirtyNow();
  }

  /// Hand off to the Smart Fast Reschedule sheet. Flushes first: that sheet
  /// pops this one, and everything typed here would otherwise be discarded.
  Future<void> _reschedule() async {
    await _autosave.flush();
    if (!mounted) return;
    await handOffToReschedule(context, ref, widget.task);
  }

  Future<void> _delete() async {
    if (_deleting) return;
    final confirmed = await LzConfirm.show(
      context,
      title: 'Delete task?',
      message: widget.task.title,
      confirmLabel: 'Delete',
      danger: true,
    );
    if (!confirmed || !mounted) return;
    // Drop anything queued BEFORE flagging: a debounced field write landing
    // after the delete would re-create columns on a row on its way out.
    _autosave.cancelPending();
    setState(() => _deleting = true);
    await _tasks.deleteTask(widget.task.id);
    if (!mounted) return;
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    // Every derivation from live provider state (the fresh task, the saved
    // sub-task ids comments/expenses may reference, the spend rollups, the
    // destination project) — see task_detail_live_data.dart for the rules.
    final data = TaskDetailLiveData.resolve(
      fallback: widget.task,
      liveTasks: ref.watch(tasksProvider).tasks,
      budgets: ref.watch(budgetsProvider),
      pickerProjects: widget.projects,
      category: _category,
    );
    final live = data.task;
    final project = data.project;
    return LzFloatingSubmitLayout(
      // Square, always-on-screen submit. The old footer button was anchored to
      // the END of this (very long) column, so on a short viewport — or with
      // the keyboard up — it was simply off screen. It now means "commit and
      // close": the sheet is already saving as the user works.
      submit: LzFloatingSubmit(
        key: const Key('task-detail-save'),
        tooltip: 'Save and close',
        loading: _status == AutosaveStatus.saving,
        onPressed: _deleting ? null : _submit,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ── Save state, then the destructive action ────────────────────
          //
          // The indicator sits at the TOP because that is where the eye
          // starts, and it is the only remaining evidence that typing here
          // does anything. Delete keeps the far corner from the submit: this
          // sheet is thumb-driven and "one mis-tap deletes the task" is not
          // an acceptable failure mode. It is still behind a confirm.
          Row(
            children: [
              AutosaveIndicator(status: _status),
              const Spacer(),
              TaskDeleteAction(
                deleting: _deleting,
                onPressed: _status == AutosaveStatus.saving ? null : _delete,
              ),
            ],
          ),

          // ── Title ──────────────────────────────────────────────────────
          LzTextField(
            controller: _titleController,
            fieldKey: const Key('task-detail-title'),
            hint: 'What needs to be done?',
            prefixIcon: Icons.task_alt_outlined,
            textInputAction: TextInputAction.next,
            errorText: _titleError,
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
                count: taskLevelComments(data.task.taskComments).length,
                onTap: () => openTaskCommentsSheet(
                  context,
                  ref,
                  live: live,
                  taskId: widget.task.id,
                ),
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
            onChanged: (p) {
              setState(() => _priority = p);
              _autosave.markDirtyNow();
            },
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
            // The DRAFT, not the saved figure: a pending edit or a committed
            // top-up shows in the readout before the write lands.
            allocated: _budget.draft,
            spent: data.spent,
            currency: data.currency,
            canAddExpense: project != null,
            editor: _budget.editor,
            allocatedController: _budgetController,
            allocatedFocusNode: _budgetFocus,
            allocatedError: _budget.error,
            // Debounced, not immediate: the controller binding already
            // scheduled the write, and committing per keystroke would fight
            // nextDraftAllocation's deliberate tolerance of half-typed input.
            onAllocatedChanged: (text) =>
                setState(() => _budget = _budget.typed(text)),
            onEditAllocated: _editAllocated,
            onTopUp: () => setState(() => _budget = _budget.toppingUp()),
            onTopUpCommitted: _commitTopUp,
            onCancelTopUp: () => setState(() => _budget = _budget.closed()),
            // Unreachable while `canAddExpense` is false (the row is disabled),
            // but a non-null callback keeps the widget's contract simple.
            onAddExpense: () => openTaskExpenseSheet(
              context,
              ref,
              project: project,
              taskId: widget.task.id,
              contextLabel: live.title,
            ),
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Due date + reminder (extracted: see TaskDueSection) ────────
          TaskDueSection(
            dueDay: _dueDay,
            dueTime: _dueTime,
            composedDue: _due.composedDue,
            survivingReminderAt: _due.survivingReminderAt,
            effectiveLead: _due.effectiveLead,
            todayIso: isoToday(),
            tomorrowIso: isoTomorrow(),
            onReschedule: _reschedule,
            onToggleDay: (iso) => _editDue(() {
              _dueDay = _dueDay == iso ? null : iso;
            }),
            onPickDay: _pickDate,
            onPickTime: _pickTime,
            onClearTime: () => _editDue(() => _dueTime = null),
            onClearDue: () => _editDue(() {
              _dueDay = null;
              _dueTime = null;
            }),
            onLeadChanged: (lead) => _editNow(() {
              _reminderTouched = true;
              _explicitLead = lead;
            }),
            onClearReminder: () => _editNow(() => _reminderTouched = true),
          ),

          // ── Repeat + series end (extracted: see TaskRepeatSection) ────
          const SizedBox(height: AppSpacing.xl),
          TaskRepeatSection(
            recurrence: _recurrence,
            recurUntil: _recurUntil,
            anchorWeekday: _due.composedDue == null
                ? null
                : DateTime.tryParse(_due.composedDue!)?.weekday,
            onRecurrenceChanged: (r) => _editNow(() {
              _recurrenceTouched = true;
              _recurrence = r;
            }),
            onPickUntil: _pickRecurUntil,
            onClearUntil: () => _editNow(() {
              _recurUntilTouched = true;
              _recurUntil = null;
            }),
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Sub-tasks (extracted: see TaskSubtasksSection) ─────────────
          TaskSubtasksSection(
            subtasks: _subtasks,
            onChanged: (next) => _editNow(() => _subtasks = next),
            commentCounts: data.commentCounts,
            expenseTotals: data.expenseTotals,
            expenseCurrency: data.expenseCurrency,
            savedSubtaskIds: data.savedSubtaskIds,
            // Always wired, even with no project: the money sign stays
            // visible and EXPLAINS itself (snackbar) instead of vanishing,
            // which would read as "this task can't have expenses".
            onAddExpense: (sid) => openTaskExpenseSheet(
              context,
              ref,
              project: project,
              taskId: widget.task.id,
              subtaskId: sid,
              contextLabel: 'Sub-task: ${subtaskTitleById(_subtasks, sid)}',
            ),
            onOpenComments: (sid) => openSubtaskCommentsSheet(
              context,
              ref,
              live: live,
              taskId: widget.task.id,
              subtaskId: sid,
              title: subtaskTitleById(_subtasks, sid),
            ),
          ),
        ],
      ),
    );
  }

  /// Apply a DISCRETE edit and persist it at once. Chips, pickers, menus and
  /// toggles carry a finished decision — there is nothing to debounce, and a
  /// user who taps "high" and closes the sheet expects "high".
  void _editNow(VoidCallback change) {
    setState(change);
    _autosave.markDirtyNow();
  }

  /// [_editNow] for the due controls, which additionally flag that the due
  /// date moved (which is what lets a date-only task's reminder re-anchor).
  void _editDue(VoidCallback change) => _editNow(() {
    _dueTouched = true;
    change();
  });

  Future<void> _pickDate() async {
    final picked = await pickTaskDueDay(context, currentDay: _dueDay);
    if (picked == null || !mounted) return;
    _editDue(() => _dueDay = picked);
  }

  Future<void> _pickRecurUntil() async {
    final picked = await pickTaskRecurUntilDay(context, currentDay: _recurUntil);
    if (picked == null || !mounted) return;
    _editNow(() {
      _recurUntilTouched = true;
      _recurUntil = picked;
    });
  }

  Future<void> _pickTime() async {
    final picked = await showTaskTimePicker(context, initial: _dueTime);
    if (picked == null || !mounted) return;
    _editDue(() => _dueTime = picked);
  }
}

// ── Public helper ─────────────────────────────────────────────────────────────

/// Open the task detail/edit sheet for [task]. The sheet reads
/// [tasksProvider] for its writes; [ref] is accepted so the call site (the
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
