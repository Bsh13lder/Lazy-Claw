import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/core/due_date.dart';
import 'package:lazyclaw_mobile/core/project_resolver.dart';
import 'package:lazyclaw_mobile/core/recurrence.dart';
import 'package:lazyclaw_mobile/core/reminder_lead.dart';
import 'package:lazyclaw_mobile/core/smart_add_parser.dart';
import 'package:lazyclaw_mobile/core/smart_add_task_expense.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/subtask.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/screens/expenses/add_expense_sheet.dart'
    show AddProjectSheet;
import 'package:lazyclaw_mobile/screens/settings/settings_prefs.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_task_expense_chip.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_task_result.dart';
import 'package:lazyclaw_mobile/screens/tasks/chip_edit.dart';
import 'package:lazyclaw_mobile/screens/tasks/recurrence_picker.dart';
import 'package:lazyclaw_mobile/screens/tasks/reminder_lead_picker.dart';
import 'package:lazyclaw_mobile/screens/tasks/smart_add_controller.dart';
import 'package:lazyclaw_mobile/screens/tasks/subtask_editor.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_project_suggestion_strip.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// Key for the floating square save, shared with the tests (the button carries
/// no visible label, so there is no text to find it by).
const Key kAddTaskSubmitKey = Key('add-task-submit');

/// A polished add-task bottom sheet with Todoist-style "smart add": as the user
/// types, recognized natural-language tokens (due date, priority, `#project`)
/// are parsed on-device and surfaced as chips. Parsed values pre-select the
/// priority / due-date controls; manual taps always win.
///
/// The title field ALSO recognizes a money amount (`buy paint 40 eur #home
/// tomorrow`) and offers a confirmation chip to file it as an expense linked
/// to the new task. That path is opt-in by construction — see
/// [_expenseArmed] and `add_task_expense_chip.dart` for why a bare number
/// never arms it.
///
/// Returns the data to the caller via [Navigator.pop] so the screen can invoke
/// the provider without knowing about UI internals.
class AddTaskSheet extends ConsumerStatefulWidget {
  const AddTaskSheet({
    super.key,
    this.initialDueDate,
    this.defaultLead = kDefaultReminderLead,
    this.projects = const [],
  });

  /// When provided (e.g. tapping a day in the calendar view), the due date is
  /// pre-selected to this day so the new task lands on the chosen date.
  final DateTime? initialDueDate;

  /// The global default reminder lead, applied automatically once the task
  /// gains a due time and the user hasn't picked a lead explicitly.
  final ReminderLead defaultLead;

  /// The user's projects, for the PROJECT chip + picker (and its "＋ New
  /// project" create-new affordance). Empty when the caller hasn't loaded the
  /// budgets store yet — the chip still works, just with nothing to pick from
  /// besides "No project" / "＋ New project".
  final List<Project> projects;

  @override
  ConsumerState<AddTaskSheet> createState() => _AddTaskSheetState();
}

class _AddTaskSheetState extends ConsumerState<AddTaskSheet> {
  final _titleController = SmartAddController();

  /// Drives the `/` project-suggestion strip: it only shows while the title
  /// field is actually focused (so it doesn't linger after the user moves on
  /// to another field). [FocusNode] is a [ChangeNotifier], so a listener
  /// rebuild is enough — nothing else needs to watch it.
  final _titleFocusNode = FocusNode();

  /// Free-form notes → the task's `description`. Mirrors the edit sheet's Notes
  /// field so the two surfaces feel like one family.
  final _notesController = TextEditingController();

  /// The sub-tasks (checklist) being authored for the new task. Edited in-sheet
  /// via [SubtaskEditor] and carried to the caller (serialized to `steps`) so
  /// the create lands them in ONE round-trip — no post-create edit needed.
  List<Subtask> _subtasks = const [];

  /// Live parse of the current title text. Drives the detected-token chips and
  /// the default selections for priority / due date.
  ParsedTask _parsed = const ParsedTask(cleanTitle: '');

  /// The money amount recognized in the title, or null. Detected AFTER
  /// [_parsed] and only from the characters the task grammar did not claim —
  /// see `core/smart_add_task_expense.dart` for why the two parsers are
  /// composed rather than merged.
  TaskExpenseMatch? _expenseMatch;

  /// Whether the user has confirmed (or refused) filing that amount as an
  /// expense. Sticky once touched — see [AddTaskExpenseArmState].
  AddTaskExpenseArmState _expenseArm = const AddTaskExpenseArmState();

  /// Manual overrides. When set, they win over the parsed values. The due date
  /// is split into a date-only day string (`_manualDueDate`) and a separate
  /// time-of-day (`_manualTime`) so the two pickers are independent.
  String? _manualPriority;

  /// The manually-picked project (via the PROJECT chip's picker), and whether
  /// the user has touched it. Until touched, the chip displays (and submit
  /// uses) the live-parsed `/project` token instead.
  String? _category;
  bool _categoryTouched = false;

  bool _dueDateTouched = false;
  String? _manualDueDate;
  bool _timeTouched = false;
  TimeOfDay? _manualTime;

  /// The user's explicit reminder-lead choice. Null until they touch the
  /// picker, so the global default applies automatically once a time is set.
  ReminderLead? _explicitLead;

  /// Manual recurrence override. Null until the user taps the repeat picker, so
  /// the parsed recurrence (from "every day" etc.) applies until then.
  Recurrence? _manualRecurrence;
  bool _recurrenceTouched = false;

  /// The series' end day (`yyyy-MM-dd`), or null = repeats forever ("Never").
  /// Only meaningful (and only shown) while a recurrence is selected.
  String? _recurUntil;

  bool _submitting = false;

  /// The projects offered by the PROJECT chip's picker and the `/` suggestion
  /// strip. Seeded from [widget.projects] (a construction-time snapshot) but
  /// then kept live: a project created FROM INSIDE this sheet (via "＋ New
  /// project" or the strip's "Create project" row) is refreshed into this
  /// list from [budgetsProvider] right after the create succeeds, so a
  /// re-opened picker (or a re-typed `/token`) sees it immediately instead of
  /// the sheet acting on stale data and inviting a duplicate create.
  late List<Project> _projects;

  static const _priorities = ['low', 'medium', 'high', 'urgent'];

  @override
  void initState() {
    super.initState();
    _projects = widget.projects;
    // A caller-supplied date (calendar day-tap) pre-selects the due date. We
    // mark the field as touched so it wins over any parsed default and stays
    // put until the user explicitly changes it.
    final initial = widget.initialDueDate;
    if (initial != null) {
      _dueDateTouched = true;
      _manualDueDate = _isoFor(initial);
    }
    _titleFocusNode.addListener(_handleTitleFocusChange);
  }

  @override
  void dispose() {
    _titleFocusNode
      ..removeListener(_handleTitleFocusChange)
      ..dispose();
    _titleController.dispose();
    _notesController.dispose();
    super.dispose();
  }

  /// Rebuilds so the suggestion strip appears/disappears with focus (its
  /// visibility isn't otherwise driven by anything in [setState]).
  void _handleTitleFocusChange() {
    if (mounted) setState(() {});
  }

  // ── Effective (override-aware) values ───────────────────────────────────────

  String get _effectivePriority =>
      _manualPriority ?? _parsed.priority ?? 'medium';

  /// The effective project (manual pick wins over the live-parsed `/token`).
  /// Read live in [build] so typing `/gro` updates the chip immediately, and
  /// re-derived fresh in [_submit] so a keyboard submit can't race the
  /// `onChanged` callback.
  String? get _effectiveCategory =>
      _categoryTouched ? _category : _parsed.project;

  /// The parsed due date's date-only day part (`yyyy-MM-dd`), or null.
  String? get _parsedDay {
    final due = _parsed.dueDate;
    return due == null ? null : dueDateDayPart(due);
  }

  /// The parsed due date's time-of-day, or null when it's date-only.
  TimeOfDay? get _parsedTime {
    final parts = dueTimeParts(_parsed.dueDate);
    return parts == null
        ? null
        : TimeOfDay(hour: parts.hour, minute: parts.minute);
  }

  /// The effective day (manual override wins over the live parse).
  String? get _effectiveDay => _dueDateTouched ? _manualDueDate : _parsedDay;

  /// The effective time (manual override wins over the live parse).
  TimeOfDay? get _effectiveTime => _timeTouched ? _manualTime : _parsedTime;

  /// The effective reminder lead (explicit choice wins over the global default).
  ReminderLead get _effectiveLead => _explicitLead ?? widget.defaultLead;

  /// The effective recurrence (manual pick wins over the live parse; never
  /// null — defaults to "does not repeat").
  Recurrence get _effectiveRecurrence => _recurrenceTouched
      ? (_manualRecurrence ?? Recurrence.none)
      : (_parsed.recurrence ?? Recurrence.none);

  // ── Expense chip ────────────────────────────────────────────────────────────

  /// Whether the task names a project at all. An expense MUST land in one, so
  /// this gates the chip: no project → visibly disabled with a reason, rather
  /// than a submit-time failure the user can't see coming.
  bool get _expenseHasProject => (_effectiveCategory ?? '').trim().isNotEmpty;

  /// Whether an expense will actually be filed on submit.
  bool get _expenseArmed =>
      _expenseArm.isArmed(_expenseMatch, hasProject: _expenseHasProject);

  /// The existing project the expense would land in, or null when the task
  /// names one that doesn't exist yet (it's created during submit). Only
  /// supplies the currency the chip DISPLAYS — see [AddTaskExpenseChip].
  Project? get _expenseProject {
    final name = _effectiveCategory?.trim();
    if (name == null || name.isEmpty) return null;
    return resolveProjectMatch(name, _projects);
  }

  // The project term is excluded once `_categoryTouched` (its chip is
  // suppressed below — see the SMART DETECTED row) so this doesn't show an
  // empty "SMART DETECTED" header when the parsed project is the only
  // detection.
  bool get _hasDetection =>
      _parsed.dueDate != null ||
      _parsed.priority != null ||
      (_parsed.project != null && !_categoryTouched) ||
      _parsed.recurrence != null;

  /// Combine a date-only [day] string with an optional [time] into the final
  /// `dueDate` payload: a datetime when a time is set, a date-only string when
  /// only a day is set, today+time when only a time is set, else null.
  String? _compose(String? day, TimeOfDay? time) {
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
    if (d == null) return day; // non-ISO fallback: leave as-is
    return composeDueDate(
      DateTime(d.year, d.month, d.day),
      hour: time?.hour,
      minute: time?.minute,
    );
  }

  void _onTitleChanged(String value) {
    final parsed = parseSmartAdd(value);
    setState(() {
      _parsed = parsed;
      _expenseMatch = detectTaskExpense(parsed, value);
    });
    // After the state swap, so the highlight reflects the FRESH parse and the
    // arm state it implies.
    _syncTitleHighlight();
  }

  /// Toggle the expense chip. The user's choice is sticky from here on — a
  /// later re-parse can never flip it back (in either direction).
  void _toggleExpense() {
    // Read the CURRENT armed value before the swap, so the toggle inverts what
    // the user actually sees rather than the default it would resolve to.
    final next = _expenseArm.toggled(_expenseArmed);
    setState(() => _expenseArm = next);
    _syncTitleHighlight();
  }

  /// Push the fresh spans into the controller so the field highlights the
  /// recognized tokens live; the chips below echo the resolved values.
  ///
  /// The amount span is included ONLY while the expense is armed. That keeps
  /// this field's existing contract intact — a highlighted token is a consumed
  /// token is a token stripped from the saved title — instead of painting a
  /// number as "recognized" and then leaving it in the title anyway. Tapping
  /// the chip is therefore visible in the field itself, which is the point.
  void _syncTitleHighlight() {
    final match = _expenseMatch;
    _titleController.tokens = match != null && _expenseArmed
        ? mergeExpenseToken(_parsed.tokens, match.token)
        : _parsed.tokens;
  }

  Future<void> _submit() async {
    // Re-parse from the live controller so a submit-via-keyboard can't race the
    // onChanged callback.
    final raw = _titleController.text;
    final parsed = parseSmartAdd(raw);

    // Effective category: a manual PROJECT-chip pick always wins over the
    // live-parsed `/token`, re-derived from the fresh parse (not `_parsed`)
    // for the same submit-can't-race-onChanged reason as priority/due date.
    // Hoisted above the title because the expense decision depends on it.
    final category = _categoryTouched ? _category : parsed.project;

    // Re-derive the expense decision from the FRESH parse too. `_expenseArm`
    // is the only piece carried over from the live state — it holds the user's
    // explicit tap, which by definition can't be re-derived from text.
    final expense = detectTaskExpense(parsed, raw);
    final fileExpense = _expenseArm.isArmed(
      expense,
      hasProject: (category ?? '').trim().isNotEmpty,
    );

    // An armed amount is a CONSUMED token, so it comes out of the title —
    // same rule every other recognized token in this field follows. An
    // un-armed one is not consumed, so the digits stay put.
    final clean = fileExpense && expense != null
        ? titleWithoutExpenseToken(raw, parsed, expense.token)
        : parsed.cleanTitle.trim();
    final title = clean.trim().isNotEmpty ? clean.trim() : raw.trim();
    if (title.isEmpty) return;

    final priority = _manualPriority ?? parsed.priority ?? 'medium';

    // Re-derive the effective day/time from this fresh parse so a keyboard
    // submit can't race the onChanged callback, then compose them.
    final parsedDay = parsed.dueDate == null
        ? null
        : dueDateDayPart(parsed.dueDate!);
    final pt = dueTimeParts(parsed.dueDate);
    final parsedTime = pt == null
        ? null
        : TimeOfDay(hour: pt.hour, minute: pt.minute);
    final day = _dueDateTouched ? _manualDueDate : parsedDay;
    final time = _timeTouched ? _manualTime : parsedTime;
    final dueDate = _compose(day, time);

    // Compute the absolute reminderAt from the effective lead. Empty when there
    // is no due time or the lead is None — addTask normalises that to "no
    // reminder".
    final reminderAt = resolveReminderAt(
      dueDate: dueDate,
      explicitLead: _explicitLead,
      defaultLead: widget.defaultLead,
    );

    // Resolve the recurrence (manual pick wins over the fresh parse) and convert
    // it to a standard 5-field cron, anchored to the composed due date so the
    // clock + day-of-month / weekday come from when the task is actually due.
    final recurrence = _recurrenceTouched
        ? (_manualRecurrence ?? Recurrence.none)
        : (parsed.recurrence ?? Recurrence.none);
    final recurring = recurrenceToCron(
      recurrence,
      dueAnchor: recurrenceAnchorFromDue(dueDate),
    );
    // The series end only rides along when the task actually repeats.
    final recurUntil = recurring == null ? null : _recurUntil;

    // Notes → description (null when blank so we don't persist an empty column)
    // and the checklist → serialized `steps` (null for an empty list).
    final notes = _notesController.text.trim();
    final steps = serializeSubtasks(_subtasks);

    setState(() => _submitting = true);
    Navigator.of(context).pop(
      AddTaskResult(
        title: title,
        priority: priority,
        dueDate: dueDate,
        category: category,
        reminderAt: reminderAt.isEmpty ? null : reminderAt,
        recurring: recurring,
        recurUntil: recurUntil,
        description: notes.isEmpty ? null : notes,
        steps: steps,
        // The single gate: null here means no money is ever spent downstream.
        expenseAmount: fileExpense ? expense?.amount : null,
      ),
    );
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

  @override
  Widget build(BuildContext context) {
    final effDay = _effectiveDay;
    final effTime = _effectiveTime;
    final effPriority = _effectivePriority;
    final composed = _compose(effDay, effTime);

    // The configured time-of-day at which DATE-ONLY tasks remind (falls back to
    // the built-in default until the prefs have loaded). Used only to LABEL the
    // muted "Reminds at …" hint below — the actual scheduling reads the same
    // pref in the reminder service.
    final defaultReminderMinutes =
        ref.watch(settingsPrefsProvider).valueOrNull?.defaultReminderMinutes ??
        kDefaultReminderMinutes;

    return LzFloatingSubmitLayout(
      submit: LzFloatingSubmit(
        key: kAddTaskSubmitKey,
        tooltip: 'Add Task',
        icon: Icons.add_rounded,
        loading: _submitting,
        onPressed: _submit,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ── Title input ────────────────────────────────────────────────
          LzTextField(
            controller: _titleController,
            focusNode: _titleFocusNode,
            hint: 'e.g. "Pay rent tomorrow !p1 #home"',
            prefixIcon: Icons.task_alt_outlined,
            textInputAction: TextInputAction.done,
            onChanged: _onTitleChanged,
            onSubmitted: (_) => _submit(),
            autofocus: true,
          ),

          // ── `/` project suggestion strip (live, focus-gated) ────────────
          if (_parsed.project != null && _titleFocusNode.hasFocus)
            TaskProjectSuggestionStrip(
              token: _parsed.project!,
              projects: _projects,
              onSelect: _applyProjectSuggestion,
              onCreate: _createProjectFromSuggestion,
            ),

          // ── Syntax legend (discoverability) ────────────────────────────
          const SizedBox(height: AppSpacing.xs),
          Text(
            'tom · fri 9am · in 2h · morning · !p1 · #project · 40 eur',
            style: AppText.caption.copyWith(color: AppColors.textMuted),
          ),

          // ── Expense confirmation (live, opt-in) ────────────────────────
          // Sits directly under the field, above the passive "SMART
          // DETECTED" echo, because it is a DECISION and not a readout: it
          // is the only thing here that can spend money.
          AddTaskExpenseChip(
            match: _expenseMatch,
            armed: _expenseArmed,
            project: _expenseProject,
            hasProject: _expenseHasProject,
            onToggle: _toggleExpense,
          ),

          // ── Smart-detected tokens (live) ───────────────────────────────
          if (_hasDetection) ...[
            const SizedBox(height: AppSpacing.md),
            Row(
              children: [
                Icon(
                  Icons.auto_awesome_outlined,
                  size: 13,
                  color: AppColors.accent,
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  'SMART DETECTED',
                  style: AppText.caption.copyWith(
                    color: AppColors.accent,
                    letterSpacing: 0.8,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.sm),
            Wrap(
              spacing: AppSpacing.sm,
              runSpacing: AppSpacing.sm,
              children: [
                if (_parsed.dueDate != null)
                  LzChip(
                    label: dueDateDisplay(_parsed.dueDate!),
                    icon: Icons.event_outlined,
                    selected: true,
                    color: AppColors.accent,
                    dense: true,
                  ),
                if (_parsed.priority != null)
                  LzChip(
                    label: _parsed.priority!,
                    icon: Icons.flag_outlined,
                    selected: true,
                    color: _priorityColor(_parsed.priority!),
                    dense: true,
                  ),
                // Suppressed once a manual PROJECT-chip pick has touched the
                // category — otherwise a picked "Casa" sits next to a stale
                // `#Groceries` parsed from a since-superseded `/token`, and
                // the manual pick (which is what actually wins on submit)
                // reads as contradicted.
                if (_parsed.project != null && !_categoryTouched)
                  LzChip(
                    label: '#${_parsed.project!}',
                    icon: Icons.folder_outlined,
                    selected: true,
                    color: AppColors.info,
                    dense: true,
                  ),
                if (_parsed.recurrence != null)
                  LzChip(
                    label: recurrenceLabel(_parsed.recurrence!),
                    icon: Icons.repeat,
                    selected: true,
                    color: AppColors.accent,
                    dense: true,
                  ),
              ],
            ),
          ],

          const SizedBox(height: AppSpacing.lg),

          // ── Notes ──────────────────────────────────────────────────────
          LzTextField(
            controller: _notesController,
            fieldKey: const Key('add-task-notes'),
            hint: 'Notes (optional)',
            prefixIcon: Icons.notes_outlined,
            minLines: 2,
            maxLines: 5,
            keyboardType: TextInputType.multiline,
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Priority selector ──────────────────────────────────────────
          Text(
            'PRIORITY',
            style: AppText.caption.copyWith(
              color: AppColors.textMuted,
              letterSpacing: 0.8,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: _priorities.map((p) {
              final selected = p == effPriority;
              return Padding(
                padding: const EdgeInsets.only(right: AppSpacing.sm),
                child: LzChip(
                  label: p,
                  selected: selected,
                  color: _priorityColor(p),
                  onTap: () => setState(() => _manualPriority = p),
                ),
              );
            }).toList(),
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Project ─────────────────────────────────────────────────────
          Text(
            'PROJECT',
            style: AppText.caption.copyWith(
              color: AppColors.textMuted,
              letterSpacing: 0.8,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          ProjectChip(
            fieldKey: const Key('add-task-project'),
            projects: _projects,
            category: _effectiveCategory,
            onTap: _openProjectPicker,
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Due date quick-pick ────────────────────────────────────────
          Text(
            'DUE DATE',
            style: AppText.caption.copyWith(
              color: AppColors.textMuted,
              letterSpacing: 0.8,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              LzChip(
                label: 'Today',
                icon: Icons.today_outlined,
                selected: effDay == _isoToday(),
                color: AppColors.warn,
                onTap: () =>
                    _setDueDate(effDay == _isoToday() ? null : _isoToday()),
              ),
              const SizedBox(width: AppSpacing.sm),
              LzChip(
                label: 'Tomorrow',
                icon: Icons.event_outlined,
                selected: effDay == _isoTomorrow(),
                color: AppColors.accent,
                onTap: () => _setDueDate(
                  effDay == _isoTomorrow() ? null : _isoTomorrow(),
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              LzChip(
                label: 'Pick…',
                icon: Icons.calendar_month_outlined,
                selected:
                    effDay != null &&
                    effDay != _isoToday() &&
                    effDay != _isoTomorrow(),
                color: AppColors.info,
                onTap: _pickDate,
              ),
            ],
          ),

          // ── Time-of-day chip ───────────────────────────────────────────
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              LzChip(
                label: effTime != null
                    ? formatClock12(effTime.hour, effTime.minute)
                    : 'Add time',
                icon: Icons.schedule_outlined,
                selected: effTime != null,
                color: AppColors.accent,
                onTap: _pickTime,
              ),
              if (effTime != null) ...[
                const SizedBox(width: AppSpacing.sm),
                GestureDetector(
                  onTap: () => _setTime(null),
                  child: Icon(
                    Icons.close,
                    size: 16,
                    color: AppColors.textMuted,
                  ),
                ),
              ],
            ],
          ),

          if (composed != null) ...[
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
                  'Due ${dueDateDisplay(composed)}',
                  style: AppText.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
                const Spacer(),
                GestureDetector(
                  onTap: _clearDue,
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
          if (dueDateHasTime(composed)) ...[
            const SizedBox(height: AppSpacing.xl),
            Text(
              'REMIND',
              style: AppText.caption.copyWith(
                color: AppColors.textMuted,
                letterSpacing: 0.8,
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
            ReminderLeadPicker(
              value: _effectiveLead,
              onChanged: (lead) => setState(() => _explicitLead = lead),
            ),
          ]
          // ── Date-only due → reminds at the default time-of-day ─────────
          // No clock time was set, so this task still gets a reminder (at the
          // configured default time on its due date). Surface that so the user
          // knows the reminder isn't silently skipped.
          else if (composed != null) ...[
            const SizedBox(height: AppSpacing.sm),
            Row(
              children: [
                Icon(
                  Icons.notifications_active_outlined,
                  size: 13,
                  color: AppColors.textMuted,
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  'Reminds at '
                  '${formatClock12(defaultReminderMinutes ~/ 60, defaultReminderMinutes % 60)}'
                  ' on the due date',
                  style: AppText.caption.copyWith(color: AppColors.textMuted),
                ),
              ],
            ),
          ],

          // ── Recurrence (repeat) ────────────────────────────────────────
          const SizedBox(height: AppSpacing.xl),
          Text(
            'REPEAT',
            style: AppText.caption.copyWith(
              color: AppColors.textMuted,
              letterSpacing: 0.8,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          RecurrencePicker(
            value: _effectiveRecurrence,
            anchorWeekday: composed == null
                ? null
                : DateTime.tryParse(composed)?.weekday,
            onChanged: (r) => setState(() {
              _recurrenceTouched = true;
              _manualRecurrence = r;
            }),
          ),

          // ── Series end (only when a recurrence is selected) ────────────
          if (_effectiveRecurrence.repeats) ...[
            const SizedBox(height: AppSpacing.md),
            Text(
              'ENDS',
              style: AppText.caption.copyWith(
                color: AppColors.textMuted,
                letterSpacing: 0.8,
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
            Row(
              children: [
                LzChip(
                  key: const Key('add-task-recur-until-never'),
                  label: 'Never',
                  icon: Icons.all_inclusive,
                  selected: _recurUntil == null,
                  color: AppColors.accent,
                  onTap: () => setState(() => _recurUntil = null),
                ),
                const SizedBox(width: AppSpacing.sm),
                LzChip(
                  key: const Key('add-task-recur-until-date'),
                  label: _recurUntil ?? 'On date…',
                  icon: Icons.event_outlined,
                  selected: _recurUntil != null,
                  color: AppColors.info,
                  onTap: _pickRecurUntil,
                ),
                if (_recurUntil != null) ...[
                  const SizedBox(width: AppSpacing.sm),
                  GestureDetector(
                    key: const Key('add-task-recur-until-clear'),
                    onTap: () => setState(() => _recurUntil = null),
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

          // ── Sub-tasks ──────────────────────────────────────────────────
          const SizedBox(height: AppSpacing.xl),
          Row(
            children: [
              Text(
                'SUBTASKS',
                style: AppText.caption.copyWith(
                  color: AppColors.textMuted,
                  letterSpacing: 0.8,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const Spacer(),
              if (subtaskProgressLabel(_subtasks) != null)
                Text(
                  subtaskProgressLabel(_subtasks)!,
                  key: const Key('add-task-subtask-progress'),
                  style: AppText.caption.copyWith(color: AppColors.accent),
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          SubtaskEditor(
            subtasks: _subtasks,
            onChanged: (next) => setState(() => _subtasks = next),
          ),
          // No trailing submit button: it moved to the floating square above,
          // which is anchored to the sheet's viewport instead of the end of
          // this column (see [LzFloatingSubmit]'s "WHY this exists"). The
          // bottom gap the button used to need is reserved by
          // [LzFloatingSubmitLayout].
        ],
      ),
    );
  }

  /// Open the project picker anchored to the PROJECT chip. A normal pick (incl.
  /// explicit "No project" → null) marks the field touched so it wins over any
  /// live-parsed `/token`; the "＋ New project" row opens [_openCreateProject]
  /// instead.
  Future<void> _openProjectPicker() async {
    final result = await showProjectPicker(
      context,
      projects: _projects,
      current: _effectiveCategory,
      allowCreate: true,
    );
    if (result == null || !mounted) return;
    if (result.createNew) {
      await _openCreateProject();
      return;
    }
    setState(() {
      _category = result.category;
      _categoryTouched = true;
    });
    // Gaining (or losing) a project can flip the expense chip between armed
    // and disabled, and the field highlight has to follow it — otherwise the
    // "highlighted == will be filed" invariant holds only until the next
    // keystroke.
    _syncTitleHighlight();
  }

  /// Open the shared "New Project" sheet (same one Tasks → Projects and Money
  /// use). On a successful create, the new project becomes this task's
  /// (touched) category.
  Future<void> _openCreateProject() async {
    await LzBottomSheet.show<void>(
      context,
      title: 'New Project',
      builder: (_) => AddProjectSheet(
        onSubmit: (name, budget, color, startDate, dueDate) async {
          final ok = await ref.read(budgetsProvider.notifier).createProject(
                name,
                budget: budget,
                color: color,
                startDate: startDate,
                dueDate: dueDate,
              );
          if (ok && mounted) {
            setState(() {
              _category = name;
              _categoryTouched = true;
              // Refresh the live list so a re-opened picker (or the `/`
              // strip) sees the project we just created instead of acting on
              // the construction-time snapshot and offering to create it
              // again.
              _projects = ref.read(budgetsProvider).projects;
            });
            // Same reason as [_openProjectPicker]: the task just gained a
            // project, which can arm the expense chip.
            _syncTitleHighlight();
          }
          return ok;
        },
      ),
    );
  }

  /// Apply a `/`-suggestion-strip pick: strips the raw token from the title
  /// text and sets [projectName] as the (touched) category — the same
  /// override shape a manual PROJECT-chip pick produces. Re-parses the
  /// stripped text so the title highlight + "SMART DETECTED" chips and the
  /// strip's own visibility (which reads `_parsed.project`) all stay in sync.
  void _applyProjectSuggestion(String projectName) {
    final next = removeProjectToken(_titleController.text);
    final reparsed = parseSmartAdd(next);
    setState(() {
      // Assign via `.value` with an explicit collapsed selection rather than
      // the bare `.text =` setter: the latter forces
      // `selection: TextSelection.collapsed(offset: -1)`, an invalid caret
      // that (absent Flutter's focus-based self-heal) leaves the next
      // keystroke landing at index 0. Same pattern as
      // task_comments_section.dart's `_addLink`.
      _titleController.value = TextEditingValue(
        text: next,
        selection: TextSelection.collapsed(offset: next.length),
      );
      _parsed = reparsed;
      // This path REWRITES the title, so the amount span must be re-derived
      // against the new text like every other token. Carrying the old match
      // forward would leave the chip quoting an amount from characters that
      // have shifted (the `/token` was just deleted out of the middle of the
      // string) and hand a stale span to the highlighter.
      _expenseMatch = detectTaskExpense(reparsed, next);
      _category = projectName;
      _categoryTouched = true;
    });
    // Outside setState so it reads the freshly-swapped `_parsed`/`_expenseMatch`
    // — same ordering as [_onTitleChanged].
    _syncTitleHighlight();
  }

  /// The suggestion strip's "Create project '{token}'" row. Mirrors
  /// [_openCreateProject]'s error-handling shape (no-ops on failure — the
  /// notifier already surfaces `state.error` to anyone watching it), then
  /// applies the newly created project the same way a match-row tap does.
  Future<void> _createProjectFromSuggestion(String token) async {
    final ok = await ref.read(budgetsProvider.notifier).createProject(token);
    if (ok && mounted) {
      // Refresh the live list first so a re-opened picker (or a re-typed
      // `/token`) sees the project we just created — [_applyProjectSuggestion]
      // below fires the `setState` that rebuilds with it.
      _projects = ref.read(budgetsProvider).projects;
      _applyProjectSuggestion(token);
    }
  }

  /// Apply a manual due-date (day) selection (or clear). Marks the field as
  /// touched so the parsed default no longer applies.
  void _setDueDate(String? iso) {
    setState(() {
      _dueDateTouched = true;
      _manualDueDate = iso;
    });
  }

  /// Apply a manual time-of-day selection (or clear). Marks the field as
  /// touched so the parsed default no longer applies.
  void _setTime(TimeOfDay? time) {
    setState(() {
      _timeTouched = true;
      _manualTime = time;
    });
  }

  /// Clear both the day and the time in one tap (the "Due …" summary's ✕).
  void _clearDue() {
    setState(() {
      _dueDateTouched = true;
      _manualDueDate = null;
      _timeTouched = true;
      _manualTime = null;
    });
  }

  Future<void> _pickTime() async {
    final picked = await showTimePicker(
      context: context,
      initialTime: _effectiveTime ?? const TimeOfDay(hour: 9, minute: 0),
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
    if (picked != null) _setTime(picked);
  }

  Future<void> _pickDate() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: now.add(const Duration(days: 1)),
      firstDate: now,
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
      _setDueDate(
        '${picked.year.toString().padLeft(4, '0')}-${picked.month.toString().padLeft(2, '0')}-${picked.day.toString().padLeft(2, '0')}',
      );
    }
  }

  /// Pick the series' end day. A long horizon (10 years) so a yearly recurrence
  /// can still be given a meaningful end date.
  Future<void> _pickRecurUntil() async {
    final now = DateTime.now();
    DateTime initial = now.add(const Duration(days: 30));
    final existing = _recurUntil == null ? null : DateTime.tryParse(_recurUntil!);
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
      setState(() => _recurUntil = _isoFor(picked));
    }
  }

  String _isoFor(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  String _isoToday() => _isoFor(DateTime.now());

  String _isoTomorrow() => _isoFor(DateTime.now().add(const Duration(days: 1)));
}

// ── Public helper ─────────────────────────────────────────────────────────────

/// Show the add-task sheet and return the submitted result, or null if
/// dismissed. Callers invoke the provider directly with the returned data.
///
/// Pass [initialDueDate] (e.g. from a calendar day-tap) to pre-select the due
/// date so the new task lands on that day. Omitting it preserves the original
/// behavior (no date pre-selected). [defaultLead] is the global reminder-lead
/// default applied once a due time is set without an explicit pick. [projects]
/// feeds the PROJECT chip's picker (and highlights the caller's existing
/// projects) — pass `ref.read(budgetsProvider).projects`; omitting it just
/// leaves the picker with nothing besides "No project" / "＋ New project".
///
/// Returns an [AddTaskResult] (previously an equivalent 9-field anonymous
/// record, re-typed when the result grew an `expenseAmount` and had to be
/// consumed OUTSIDE this file by `add_task_submit.dart`). Field access is
/// unchanged for callers. Feed the result to `submitAddTaskResultWithRef` —
/// it owns the "create the task, then file the linked expense" ordering that
/// `expenseAmount` depends on.
Future<AddTaskResult?> showAddTaskSheet(
  BuildContext context, {
  DateTime? initialDueDate,
  ReminderLead defaultLead = kDefaultReminderLead,
  List<Project> projects = const [],
}) {
  return LzBottomSheet.show<AddTaskResult>(
    context,
    title: 'New Task',
    builder: (_) => AddTaskSheet(
      initialDueDate: initialDueDate,
      defaultLead: defaultLead,
      projects: projects,
    ),
  );
}
