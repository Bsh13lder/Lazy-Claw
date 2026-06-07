import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/core/smart_add_parser.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// A polished add-task bottom sheet with Todoist-style "smart add": as the user
/// types, recognized natural-language tokens (due date, priority, `#project`)
/// are parsed on-device and surfaced as chips. Parsed values pre-select the
/// priority / due-date controls; manual taps always win.
///
/// Returns the data to the caller via [Navigator.pop] so the screen can invoke
/// the provider without knowing about UI internals.
class AddTaskSheet extends StatefulWidget {
  const AddTaskSheet({super.key, this.initialDueDate});

  /// When provided (e.g. tapping a day in the calendar view), the due date is
  /// pre-selected to this day so the new task lands on the chosen date.
  final DateTime? initialDueDate;

  @override
  State<AddTaskSheet> createState() => _AddTaskSheetState();
}

class _AddTaskSheetState extends State<AddTaskSheet> {
  final _titleController = TextEditingController();

  /// Live parse of the current title text. Drives the detected-token chips and
  /// the default selections for priority / due date.
  ParsedTask _parsed = const ParsedTask(cleanTitle: '');

  /// Manual overrides. When set, they win over the parsed values.
  String? _manualPriority;
  bool _dueDateTouched = false;
  String? _manualDueDate;

  bool _submitting = false;

  static const _priorities = ['low', 'medium', 'high', 'urgent'];

  @override
  void initState() {
    super.initState();
    // A caller-supplied date (calendar day-tap) pre-selects the due date. We
    // mark the field as touched so it wins over any parsed default and stays
    // put until the user explicitly changes it.
    final initial = widget.initialDueDate;
    if (initial != null) {
      _dueDateTouched = true;
      _manualDueDate = _isoFor(initial);
    }
  }

  @override
  void dispose() {
    _titleController.dispose();
    super.dispose();
  }

  // ── Effective (override-aware) values ───────────────────────────────────────

  String get _effectivePriority =>
      _manualPriority ?? _parsed.priority ?? 'medium';

  String? get _effectiveDueDate =>
      _dueDateTouched ? _manualDueDate : _parsed.dueDate;

  bool get _hasDetection =>
      _parsed.dueDate != null ||
      _parsed.priority != null ||
      _parsed.project != null;

  void _onTitleChanged(String value) {
    setState(() => _parsed = parseSmartAdd(value));
  }

  Future<void> _submit() async {
    // Re-parse from the live controller so a submit-via-keyboard can't race the
    // onChanged callback.
    final parsed = parseSmartAdd(_titleController.text);
    final clean = parsed.cleanTitle.trim();
    final title = clean.isNotEmpty ? clean : _titleController.text.trim();
    if (title.isEmpty) return;

    final priority = _manualPriority ?? parsed.priority ?? 'medium';
    final dueDate = _dueDateTouched ? _manualDueDate : parsed.dueDate;

    setState(() => _submitting = true);
    Navigator.of(context).pop(_AddTaskResult(
      title: title,
      priority: priority,
      dueDate: dueDate,
      category: parsed.project,
    ));
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
    final effDue = _effectiveDueDate;
    final effPriority = _effectivePriority;

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // ── Title input ────────────────────────────────────────────────
        LzTextField(
          controller: _titleController,
          hint: 'e.g. "Pay rent tomorrow !p1 #home"',
          prefixIcon: Icons.task_alt_outlined,
          textInputAction: TextInputAction.done,
          onChanged: _onTitleChanged,
          onSubmitted: (_) => _submit(),
          autofocus: true,
        ),

        // ── Smart-detected tokens (live) ───────────────────────────────
        if (_hasDetection) ...[
          const SizedBox(height: AppSpacing.md),
          Row(
            children: [
              Icon(Icons.auto_awesome_outlined,
                  size: 13, color: AppColors.accent),
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
                  label: _parsed.dueDate!,
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
              if (_parsed.project != null)
                LzChip(
                  label: '#${_parsed.project!}',
                  icon: Icons.folder_outlined,
                  selected: true,
                  color: AppColors.info,
                  dense: true,
                ),
            ],
          ),
        ],

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
              selected: effDue == _isoToday(),
              color: AppColors.warn,
              onTap: () => _setDueDate(
                  effDue == _isoToday() ? null : _isoToday()),
            ),
            const SizedBox(width: AppSpacing.sm),
            LzChip(
              label: 'Tomorrow',
              icon: Icons.event_outlined,
              selected: effDue == _isoTomorrow(),
              color: AppColors.accent,
              onTap: () => _setDueDate(
                  effDue == _isoTomorrow() ? null : _isoTomorrow()),
            ),
            const SizedBox(width: AppSpacing.sm),
            LzChip(
              label: 'Pick…',
              icon: Icons.calendar_month_outlined,
              selected: effDue != null &&
                  effDue != _isoToday() &&
                  effDue != _isoTomorrow(),
              color: AppColors.info,
              onTap: _pickDate,
            ),
          ],
        ),

        if (effDue != null) ...[
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              Icon(Icons.event_available_outlined,
                  size: 14, color: AppColors.textMuted),
              const SizedBox(width: AppSpacing.xs),
              Text(
                'Due $effDue',
                style: AppText.caption.copyWith(color: AppColors.textSecondary),
              ),
              const Spacer(),
              GestureDetector(
                onTap: () => _setDueDate(null),
                child: Icon(Icons.close,
                    size: 14, color: AppColors.textMuted),
              ),
            ],
          ),
        ],

        const SizedBox(height: AppSpacing.xl),

        // ── Submit button ──────────────────────────────────────────────
        LzButton.primary(
          label: 'Add Task',
          icon: Icons.add,
          loading: _submitting,
          expand: true,
          onPressed: _submit,
        ),
      ],
    );
  }

  /// Apply a manual due-date selection (or clear). Marks the field as touched
  /// so the parsed default no longer applies.
  void _setDueDate(String? iso) {
    setState(() {
      _dueDateTouched = true;
      _manualDueDate = iso;
    });
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

  String _isoFor(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  String _isoToday() => _isoFor(DateTime.now());

  String _isoTomorrow() => _isoFor(DateTime.now().add(const Duration(days: 1)));
}

/// Data returned by [AddTaskSheet] when the user taps "Add Task".
class _AddTaskResult {
  const _AddTaskResult({
    required this.title,
    required this.priority,
    this.dueDate,
    this.category,
  });
  final String title;
  final String priority;
  final String? dueDate;
  final String? category;
}

// ── Public helper ─────────────────────────────────────────────────────────────

/// Show the add-task sheet and return the submitted result, or null if
/// dismissed. Callers invoke the provider directly with the returned data.
///
/// Pass [initialDueDate] (e.g. from a calendar day-tap) to pre-select the due
/// date so the new task lands on that day. Omitting it preserves the original
/// behavior (no date pre-selected).
Future<({String title, String priority, String? dueDate, String? category})?>
    showAddTaskSheet(
  BuildContext context, {
  DateTime? initialDueDate,
}) async {
  final result = await LzBottomSheet.show<_AddTaskResult>(
    context,
    title: 'New Task',
    builder: (_) => AddTaskSheet(initialDueDate: initialDueDate),
  );
  if (result == null) return null;
  return (
    title: result.title,
    priority: result.priority,
    dueDate: result.dueDate,
    category: result.category,
  );
}
