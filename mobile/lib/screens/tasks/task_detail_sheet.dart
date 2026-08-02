import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/core/due_date.dart';
import 'package:lazyclaw_mobile/core/recurrence.dart';
import 'package:lazyclaw_mobile/core/reminder_lead.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/project.dart';
import '../../models/subtask.dart';
import '../../models/task.dart';
import '../../providers/tasks_provider.dart';
import '../../widgets/link_text.dart';
import '../settings/settings_prefs.dart';
import 'add_link_dialog.dart';
import 'chip_edit.dart';
import 'recurrence_picker.dart';
import 'reminder_lead_picker.dart';
import 'reschedule_sheet.dart';
import 'subtask_editor.dart';
import 'task_comments_section.dart';

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

  static const int _maxTagLength = 40;

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

  static const _priorities = ['low', 'medium', 'high', 'urgent'];

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
    _priority = _priorities.contains(t.priority) ? t.priority : 'medium';
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

  /// The effective reminder lead (explicit choice wins over the global default).
  ReminderLead get _effectiveLead => _explicitLead ?? widget.defaultLead;

  /// The reminderAt string to persist: `''` (clear) when there's no due time or
  /// the lead is None, else the absolute `due − lead` instant.
  String get _composedReminderAt => resolveReminderAt(
    dueDate: _composedDue,
    explicitLead: _explicitLead,
    defaultLead: widget.defaultLead,
  );

  /// The reminder instant that SURVIVES this edit when the due date has no
  /// time-of-day, or null when nothing survives. This is the only path that can
  /// see a date-only task's reminder, since `due − lead` degenerates for it.
  ///
  /// * user touched the REMIND control → null (an explicit clear)
  /// * no reminder to begin with → null
  /// * due date removed entirely → null (a reminder with nothing to remind
  ///   about is an orphan that would still nag)
  /// * due day untouched → the stored instant, verbatim
  /// * due day moved → the SAME clock time re-anchored onto the new day (the
  ///   Smart-Reschedule shape, cf. [cardDueTimeLabel]) rather than a reminder
  ///   stranded on the old date
  String? get _survivingReminderAt {
    if (_reminderTouched) return null;
    final original = _originalReminderAt;
    if (original == null || original.isEmpty) return null;
    final due = _composedDue;
    if (due == null) return null;
    if (!_dueTouched) return original;
    final parts = dueTimeParts(original);
    final day = DateTime.tryParse(due);
    if (parts == null || day == null) return original;
    return composeDueDate(day, hour: parts.hour, minute: parts.minute);
  }

  /// The `reminderAt` argument for [TasksNotifier.updateTask]: `null` leaves the
  /// column untouched, `''` force-clears it, anything else sets it.
  ///
  /// WHY this is not simply [_composedReminderAt]: `resolveReminderAt` can only
  /// answer `''` for a due date with no time-of-day, and EVERY backend-respawned
  /// recurring occurrence (plus every Smart-Rescheduled task) has exactly that
  /// shape — a date-only due plus a real timed `reminder_at`. Sending the composed
  /// value unconditionally meant opening a repeating task to fix a typo and
  /// hitting Save permanently deleted its reminder, its server reminder job and
  /// its advance nags.
  String? get _reminderArg {
    // A timed due makes the reminder fully derivable, and the lead picker IS the
    // user-visible control for it — so it stays authoritative (status quo).
    if (dueDateHasTime(_composedDue)) return _composedReminderAt;
    final surviving = _survivingReminderAt;
    if (surviving == null) return '';
    // Unchanged → leave the column absent from the patch, matching how this sheet
    // already avoids churning category / recurring / tags / steps.
    if (surviving == _originalReminderAt) return null;
    return surviving;
  }

  /// Combine the day + time into the persisted `dueDate` string: a datetime when
  /// a time is set, a date-only string when only a day is set, today+time when
  /// only a time is set, else null.
  String? get _composedDue {
    final day = _dueDay;
    final time = _dueTime;
    if (day == null) {
      if (time == null) return null;
      final n = DateTime.now();
      return composeDueDate(
        DateTime(n.year, n.month, n.day),
        hour: time.hour,
        minute: time.minute,
      );
    }
    final d = DateTime.tryParse(day);
    if (d == null) return day;
    return composeDueDate(
      DateTime(d.year, d.month, d.day),
      hour: time?.hour,
      minute: time?.minute,
    );
  }

  @override
  void dispose() {
    _titleController.dispose();
    _notesController.dispose();
    _budgetController.dispose();
    _tagController.dispose();
    _notesFocus.dispose();
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

  void _addTag(String raw) {
    final tag = raw.trim();
    if (tag.isEmpty) return;
    final clamped = tag.length > _maxTagLength
        ? tag.substring(0, _maxTagLength)
        : tag;
    if (_tags.contains(clamped)) {
      _tagController.clear();
      return;
    }
    setState(() => _tags = [..._tags, clamped]);
    _tagController.clear();
  }

  void _removeTag(String tag) {
    setState(() => _tags = _tags.where((t) => t != tag).toList());
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
    if (_tagController.text.trim().isNotEmpty) _addTag(_tagController.text);
    setState(() => _saving = true);
    // Only write `tags` when they changed. Serialized as the JSON-array string
    // the DAO/cache carry; task_sync decodes it to a list for the PATCH. An
    // empty list ('[]') is a deliberate clear (differs from the on-open snapshot
    // only when the task actually had tags).
    final nextTagsJson = jsonEncode(_tags);
    final tagsArg = nextTagsJson == _originalTagsJson ? null : nextTagsJson;
    // Budget: empty field clears a previously-set budget; a parsed number that
    // differs sets it; otherwise leave untouched.
    final budgetText = _budgetController.text.trim();
    final parsedBudget = budgetText.isEmpty
        ? null
        : double.tryParse(budgetText);
    double? budgetArg;
    bool clearBudget = false;
    if (budgetText.isEmpty && _originalBudget != null) {
      clearBudget = true;
    } else if (parsedBudget != null && parsedBudget != _originalBudget) {
      budgetArg = parsedBudget;
    }
    // Only write `steps` when the checklist changed. An empty string (not null)
    // force-clears the column when every sub-task was removed.
    final nextSteps = serializeSubtasks(_subtasks);
    final stepsArg = nextSteps == _originalSteps ? null : (nextSteps ?? '');
    // Only write `category` when the user touched the project. An empty string
    // (not null) force-clears the column when "No project" was chosen.
    final categoryArg = !_categoryTouched ? null : (_category ?? '');
    // Only write `recurring` when the user touched the repeat picker, so an
    // untouched custom/known cron is preserved. A "does not repeat" selection
    // sends an empty string to force-clear the column; otherwise the computed
    // cron (anchored to the due date) is sent.
    final recurringArg = !_recurrenceTouched
        ? null
        : (recurrenceToCron(
                _recurrence,
                dueAnchor: recurrenceAnchorFromDue(_composedDue),
              ) ??
              '');
    // Only write `recur_until` when the user touched the Ends control (the ''
    // sentinel clears back to "Never" — mirrors the recurring convention). When
    // the recurrence itself was just cleared, an end date is an orphan: clear it
    // too (but only when there is something to clear, so a plain edit doesn't
    // churn the column).
    String? recurUntilArg;
    if (recurringArg != null && recurringArg.isEmpty) {
      recurUntilArg = (_recurUntil != null || _recurUntilTouched) ? '' : null;
    } else if (_recurUntilTouched) {
      recurUntilArg = _recurUntil ?? '';
    }
    await ref
        .read(tasksProvider.notifier)
        .updateTask(
          widget.task.id,
          title: title,
          description: _notesController.text.trim(),
          priority: _priority,
          category: categoryArg,
          // Send the `''` clear sentinel (not null) when the due date was
          // removed, so the clear reaches the cache + outbox and syncs — a null
          // is read as "field untouched" and would silently no-op the clear.
          dueDate: _composedDue ?? '',
          steps: stepsArg,
          // Untouched reminders ride as null (absent) — see [_reminderArg].
          reminderAt: _reminderArg,
          recurring: recurringArg,
          recurUntil: recurUntilArg,
          tags: tagsArg,
          allocatedBudget: budgetArg,
          clearAllocatedBudget: clearBudget,
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

  Color _priorityColor(String p) {
    switch (p) {
      case 'urgent':
        return AppColors.error;
      case 'high':
        return AppColors.warn;
      case 'medium':
        return AppColors.info;
      default:
        return AppColors.textMuted;
    }
  }

  /// The Notes block: a read-only [LinkText] preview when there's non-empty,
  /// un-touched content (tapping anywhere but a link switches to the editor),
  /// else the editable field with a trailing "Add link" affordance.
  Widget _buildNotes() {
    if (!_editingNotes) {
      return GestureDetector(
        key: const Key('task-detail-notes-preview'),
        behavior: HitTestBehavior.opaque,
        onTap: _beginEditNotes,
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.all(AppSpacing.md),
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rMd,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: LinkText(_notesController.text, style: AppText.body),
        ),
      );
    }
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: LzTextField(
            controller: _notesController,
            fieldKey: const Key('task-detail-notes'),
            focusNode: _notesFocus,
            hint: 'Notes (optional)',
            prefixIcon: Icons.notes_outlined,
            minLines: 2,
            maxLines: 5,
            keyboardType: TextInputType.multiline,
          ),
        ),
        IconButton(
          key: const Key('task-detail-notes-add-link'),
          icon: const Icon(Icons.add_link),
          color: AppColors.textMuted,
          tooltip: 'Add link',
          onPressed: _addLink,
        ),
      ],
    );
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
    return SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ── Title ──────────────────────────────────────────────────────
          LzTextField(
            controller: _titleController,
            fieldKey: const Key('task-detail-title'),
            hint: 'What needs to be done?',
            prefixIcon: Icons.task_alt_outlined,
            textInputAction: TextInputAction.next,
          ),

          const SizedBox(height: AppSpacing.lg),

          // ── Notes ──────────────────────────────────────────────────────
          _buildNotes(),

          const SizedBox(height: AppSpacing.xl),

          // ── Priority selector ──────────────────────────────────────────
          _SectionLabel('PRIORITY'),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: _priorities.map((p) {
              return Padding(
                padding: const EdgeInsets.only(right: AppSpacing.sm),
                child: LzChip(
                  label: p,
                  selected: p == _priority,
                  color: _priorityColor(p),
                  onTap: () => setState(() => _priority = p),
                ),
              );
            }).toList(),
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Project selector ───────────────────────────────────────────
          _SectionLabel('PROJECT'),
          const SizedBox(height: AppSpacing.sm),
          Align(
            alignment: Alignment.centerLeft,
            child: ProjectChip(
              fieldKey: const Key('task-detail-project'),
              projects: widget.projects,
              category: _category,
              onTap: _pickProject,
            ),
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Tags ───────────────────────────────────────────────────────
          _SectionLabel('TAGS'),
          const SizedBox(height: AppSpacing.sm),
          if (_tags.isNotEmpty) ...[
            Wrap(
              spacing: AppSpacing.sm,
              runSpacing: AppSpacing.sm,
              children: [
                for (final tag in _tags)
                  _TaskTagChip(tag: tag, onDelete: () => _removeTag(tag)),
              ],
            ),
            const SizedBox(height: AppSpacing.sm),
          ],
          LzTextField(
            controller: _tagController,
            fieldKey: const Key('task-detail-tag-input'),
            hint: 'Add a tag',
            prefixIcon: Icons.label_outline,
            textInputAction: TextInputAction.done,
            onSubmitted: _addTag,
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Allocated budget ───────────────────────────────────────────
          _SectionLabel('BUDGET'),
          const SizedBox(height: AppSpacing.sm),
          LzTextField(
            controller: _budgetController,
            fieldKey: const Key('task-detail-budget'),
            hint: 'Allocated budget (optional)',
            prefixIcon: Icons.account_balance_wallet_outlined,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            textInputAction: TextInputAction.done,
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Due date quick-pick ────────────────────────────────────────
          Row(
            children: [
              _SectionLabel('DUE DATE'),
              const Spacer(),
              LzButton.ghost(
                key: const Key('task-detail-reschedule'),
                label: 'Reschedule',
                icon: Icons.event_repeat_outlined,
                onPressed: _reschedule,
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              LzChip(
                label: 'Today',
                icon: Icons.today_outlined,
                selected: _dueDay == _isoToday(),
                color: AppColors.warn,
                onTap: () => setState(() {
                  _dueTouched = true;
                  _dueDay = _dueDay == _isoToday() ? null : _isoToday();
                }),
              ),
              const SizedBox(width: AppSpacing.sm),
              LzChip(
                label: 'Tomorrow',
                icon: Icons.event_outlined,
                selected: _dueDay == _isoTomorrow(),
                color: AppColors.accent,
                onTap: () => setState(() {
                  _dueTouched = true;
                  _dueDay = _dueDay == _isoTomorrow() ? null : _isoTomorrow();
                }),
              ),
              const SizedBox(width: AppSpacing.sm),
              LzChip(
                label: 'Pick…',
                icon: Icons.calendar_month_outlined,
                selected:
                    _dueDay != null &&
                    _dueDay != _isoToday() &&
                    _dueDay != _isoTomorrow(),
                color: AppColors.info,
                onTap: _pickDate,
              ),
            ],
          ),

          // ── Time-of-day chip ──────────────────────────────────────────
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              LzChip(
                label: _dueTime != null
                    ? formatClock12(_dueTime!.hour, _dueTime!.minute)
                    : 'Add time',
                icon: Icons.schedule_outlined,
                selected: _dueTime != null,
                color: AppColors.accent,
                onTap: _pickTime,
              ),
              if (_dueTime != null) ...[
                const SizedBox(width: AppSpacing.sm),
                GestureDetector(
                  onTap: () => setState(() {
                    _dueTouched = true;
                    _dueTime = null;
                  }),
                  child: Icon(
                    Icons.close,
                    size: 16,
                    color: AppColors.textMuted,
                  ),
                ),
              ],
            ],
          ),

          if (_composedDue != null) ...[
            const SizedBox(height: AppSpacing.sm),
            Row(
              children: [
                Icon(
                  Icons.event_available_outlined,
                  size: 14,
                  color: AppColors.textMuted,
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  'Due ${dueDateDisplay(_composedDue!)}',
                  style: AppText.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
                const Spacer(),
                GestureDetector(
                  key: const Key('task-detail-due-clear'),
                  onTap: () => setState(() {
                    _dueTouched = true;
                    _dueDay = null;
                    _dueTime = null;
                  }),
                  child: Icon(
                    Icons.close,
                    size: 14,
                    color: AppColors.textMuted,
                  ),
                ),
              ],
            ),
          ],

          // ── Reminder lead-time (only when a due TIME exists) ───────────
          if (dueDateHasTime(_composedDue)) ...[
            const SizedBox(height: AppSpacing.xl),
            _SectionLabel('REMIND'),
            const SizedBox(height: AppSpacing.sm),
            ReminderLeadPicker(
              value: _effectiveLead,
              onChanged: (lead) => setState(() {
                _reminderTouched = true;
                _explicitLead = lead;
              }),
            ),
          ]
          // A DATE-ONLY due can't express a lead (`due − lead` degenerates), but
          // the task may still carry a real timed reminder — every respawned
          // recurring occurrence does. Show it read-only with a ✕, so the user
          // can SEE the reminder instead of the sheet silently implying "None".
          else if (_survivingReminderAt != null) ...[
            const SizedBox(height: AppSpacing.xl),
            _SectionLabel('REMIND'),
            const SizedBox(height: AppSpacing.sm),
            Row(
              children: [
                Icon(
                  Icons.notifications_active_outlined,
                  size: 14,
                  color: AppColors.textMuted,
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  dueDateDisplay(_survivingReminderAt!),
                  key: const Key('task-detail-reminder'),
                  style: AppText.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
                const Spacer(),
                GestureDetector(
                  key: const Key('task-detail-reminder-clear'),
                  onTap: () => setState(() => _reminderTouched = true),
                  child: Icon(
                    Icons.close,
                    size: 14,
                    color: AppColors.textMuted,
                  ),
                ),
              ],
            ),
          ],

          // ── Recurrence (repeat) ────────────────────────────────────────
          const SizedBox(height: AppSpacing.xl),
          _SectionLabel('REPEAT'),
          const SizedBox(height: AppSpacing.sm),
          RecurrencePicker(
            value: _recurrence,
            anchorWeekday: _composedDue == null
                ? null
                : DateTime.tryParse(_composedDue!)?.weekday,
            onChanged: (r) => setState(() {
              _recurrenceTouched = true;
              _recurrence = r;
            }),
          ),

          // ── Series end (only when a recurrence is selected) ────────────
          if (_recurrence.repeats) ...[
            const SizedBox(height: AppSpacing.md),
            _SectionLabel('ENDS'),
            const SizedBox(height: AppSpacing.sm),
            Row(
              children: [
                LzChip(
                  key: const Key('task-detail-recur-until-never'),
                  label: 'Never',
                  icon: Icons.all_inclusive,
                  selected: _recurUntil == null,
                  color: AppColors.accent,
                  onTap: () => setState(() {
                    _recurUntilTouched = true;
                    _recurUntil = null;
                  }),
                ),
                const SizedBox(width: AppSpacing.sm),
                LzChip(
                  key: const Key('task-detail-recur-until-date'),
                  label: _recurUntil ?? 'On date…',
                  icon: Icons.event_outlined,
                  selected: _recurUntil != null,
                  color: AppColors.info,
                  onTap: _pickRecurUntil,
                ),
                if (_recurUntil != null) ...[
                  const SizedBox(width: AppSpacing.sm),
                  GestureDetector(
                    key: const Key('task-detail-recur-until-clear'),
                    onTap: () => setState(() {
                      _recurUntilTouched = true;
                      _recurUntil = null;
                    }),
                    child: Icon(
                      Icons.close,
                      size: 16,
                      color: AppColors.textMuted,
                    ),
                  ),
                ],
              ],
            ),
          ],

          const SizedBox(height: AppSpacing.xl),

          // ── Sub-tasks ──────────────────────────────────────────────────
          Row(
            children: [
              _SectionLabel('SUBTASKS'),
              const Spacer(),
              if (subtaskProgressLabel(_subtasks) != null)
                Text(
                  subtaskProgressLabel(_subtasks)!,
                  key: const Key('task-detail-subtask-progress'),
                  style: AppText.caption.copyWith(color: AppColors.accent),
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          SubtaskEditor(
            subtasks: _subtasks,
            onChanged: (next) => setState(() => _subtasks = next),
            commentCounts: commentCounts,
            onOpenComments: (sid) => showSubtaskCommentsSheet(
              context,
              subtaskTitle: _subtaskTitle(sid),
              comments: live.taskComments
                  .where((c) => c.subtaskId == sid)
                  .toList(),
              onAdd: (text) => ref
                  .read(tasksProvider.notifier)
                  .addComment(widget.task.id, text, subtaskId: sid),
              onDelete: (cid) => ref
                  .read(tasksProvider.notifier)
                  .deleteComment(widget.task.id, cid),
              onAddLink: () => showAddLinkDialog(context),
            ),
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Comments ───────────────────────────────────────────────────
          _SectionLabel('COMMENTS'),
          const SizedBox(height: AppSpacing.sm),
          TaskCommentsSection(
            comments: live.taskComments,
            onAdd: (text) => ref
                .read(tasksProvider.notifier)
                .addComment(widget.task.id, text),
            onDelete: (cid) => ref
                .read(tasksProvider.notifier)
                .deleteComment(widget.task.id, cid),
            onAddLink: () => showAddLinkDialog(context),
          ),

          const SizedBox(height: AppSpacing.xxl),

          // ── Footer: Delete + Save ──────────────────────────────────────
          Row(
            children: [
              LzButton.danger(
                key: const Key('task-detail-delete'),
                label: 'Delete Task',
                icon: Icons.delete_outline,
                loading: _deleting,
                onPressed: _delete,
              ),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: LzButton.primary(
                  key: const Key('task-detail-save'),
                  label: 'Save',
                  icon: Icons.check,
                  loading: _saving,
                  expand: true,
                  onPressed: _save,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _pickDate() async {
    final now = DateTime.now();
    DateTime initial = now.add(const Duration(days: 1));
    if (_dueDay != null) {
      try {
        final parsed = DateTime.parse(_dueDay!);
        if (!parsed.isBefore(DateTime(now.year, now.month, now.day))) {
          initial = parsed;
        }
      } catch (_) {
        // Keep the default when the stored value isn't ISO-parseable.
      }
    }
    final picked = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: now.subtract(const Duration(days: 365)),
      lastDate: now.add(const Duration(days: 365)),
      builder: (ctx, child) => Theme(
        data: Theme.of(ctx).copyWith(
          colorScheme: ColorScheme.dark(
            primary: AppColors.accent,
            surface: AppColors.bgSurfaceElevated,
          ),
        ),
        child: child!,
      ),
    );
    if (picked != null) {
      setState(() {
        _dueTouched = true;
        _dueDay = _isoFor(picked);
      });
    }
  }

  /// Pick the series' end day. A long horizon (10 years) so a yearly recurrence
  /// can still be given a meaningful end date.
  Future<void> _pickRecurUntil() async {
    final now = DateTime.now();
    DateTime initial = now.add(const Duration(days: 30));
    final existing = _recurUntil == null
        ? null
        : DateTime.tryParse(_recurUntil!);
    if (existing != null && !existing.isBefore(now)) initial = existing;
    final picked = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: now,
      lastDate: now.add(const Duration(days: 3650)),
      builder: (ctx, child) => Theme(
        data: Theme.of(ctx).copyWith(
          colorScheme: ColorScheme.dark(
            primary: AppColors.accent,
            surface: AppColors.bgSurfaceElevated,
          ),
        ),
        child: child!,
      ),
    );
    if (picked != null) {
      setState(() {
        _recurUntilTouched = true;
        _recurUntil = _isoFor(picked);
      });
    }
  }

  Future<void> _pickTime() async {
    final picked = await showTimePicker(
      context: context,
      initialTime: _dueTime ?? const TimeOfDay(hour: 9, minute: 0),
      builder: (ctx, child) => Theme(
        data: Theme.of(ctx).copyWith(
          colorScheme: ColorScheme.dark(
            primary: AppColors.accent,
            surface: AppColors.bgSurfaceElevated,
          ),
        ),
        child: child!,
      ),
    );
    if (picked != null) {
      setState(() {
        _dueTouched = true;
        _dueTime = picked;
      });
    }
  }

  String _isoFor(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  String _isoToday() => _isoFor(DateTime.now());

  String _isoTomorrow() => _isoFor(DateTime.now().add(const Duration(days: 1)));

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

/// A small uppercase section label matching the add-task sheet's headers.
class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: AppText.caption.copyWith(
        color: AppColors.textMuted,
        letterSpacing: 0.8,
        fontWeight: FontWeight.w700,
      ),
    );
  }
}

/// A removable tag chip (tap to delete) shown in the detail sheet's TAGS row.
class _TaskTagChip extends StatelessWidget {
  const _TaskTagChip({required this.tag, required this.onDelete});

  final String tag;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      borderRadius: AppRadii.rPill,
      child: InkWell(
        key: ValueKey('task-detail-tag-$tag'),
        borderRadius: AppRadii.rPill,
        onTap: onDelete,
        child: Container(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.md,
            AppSpacing.xs,
            AppSpacing.xs,
            AppSpacing.xs,
          ),
          decoration: BoxDecoration(
            color: AppColors.accent.withValues(alpha: 0.14),
            borderRadius: AppRadii.rPill,
            border: Border.all(color: AppColors.accent.withValues(alpha: 0.35)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                tag,
                style: AppText.caption.copyWith(
                  color: AppColors.accent,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(width: AppSpacing.xs),
              Icon(
                Icons.close_rounded,
                size: 14,
                color: AppColors.accent.withValues(alpha: 0.7),
              ),
            ],
          ),
        ),
      ),
    );
  }
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
