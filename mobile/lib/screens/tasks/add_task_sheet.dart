import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// A polished add-task bottom sheet: title field + optional due-date and
/// priority quick-selectors.  Returns the data to the caller via [Navigator.pop]
/// so the screen can invoke the provider without knowing about UI internals.
class AddTaskSheet extends StatefulWidget {
  const AddTaskSheet({super.key});

  @override
  State<AddTaskSheet> createState() => _AddTaskSheetState();
}

class _AddTaskSheetState extends State<AddTaskSheet> {
  final _titleController = TextEditingController();
  String _priority = 'medium';
  String? _dueDate;
  bool _submitting = false;

  static const _priorities = ['low', 'medium', 'high', 'urgent'];

  @override
  void dispose() {
    _titleController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final title = _titleController.text.trim();
    if (title.isEmpty) return;
    setState(() => _submitting = true);
    Navigator.of(context).pop(_AddTaskResult(
      title: title,
      priority: _priority,
      dueDate: _dueDate,
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
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // ── Title input ────────────────────────────────────────────────
        LzTextField(
          controller: _titleController,
          hint: 'What needs to be done?',
          prefixIcon: Icons.task_alt_outlined,
          textInputAction: TextInputAction.done,
          onSubmitted: (_) => _submit(),
          autofocus: true,
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
            final selected = p == _priority;
            return Padding(
              padding: const EdgeInsets.only(right: AppSpacing.sm),
              child: LzChip(
                label: p,
                selected: selected,
                color: _priorityColor(p),
                onTap: () => setState(() => _priority = p),
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
              selected: _dueDate == _isoToday(),
              color: AppColors.warn,
              onTap: () => setState(() {
                _dueDate =
                    _dueDate == _isoToday() ? null : _isoToday();
              }),
            ),
            const SizedBox(width: AppSpacing.sm),
            LzChip(
              label: 'Tomorrow',
              icon: Icons.event_outlined,
              selected: _dueDate == _isoTomorrow(),
              color: AppColors.accent,
              onTap: () => setState(() {
                _dueDate =
                    _dueDate == _isoTomorrow() ? null : _isoTomorrow();
              }),
            ),
            const SizedBox(width: AppSpacing.sm),
            LzChip(
              label: 'Pick…',
              icon: Icons.calendar_month_outlined,
              selected: _dueDate != null &&
                  _dueDate != _isoToday() &&
                  _dueDate != _isoTomorrow(),
              color: AppColors.info,
              onTap: _pickDate,
            ),
          ],
        ),

        if (_dueDate != null) ...[
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              Icon(Icons.event_available_outlined,
                  size: 14, color: AppColors.textMuted),
              const SizedBox(width: AppSpacing.xs),
              Text(
                'Due $_dueDate',
                style: AppText.caption.copyWith(color: AppColors.textSecondary),
              ),
              const Spacer(),
              GestureDetector(
                onTap: () => setState(() => _dueDate = null),
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
      setState(() {
        _dueDate =
            '${picked.year.toString().padLeft(4, '0')}-${picked.month.toString().padLeft(2, '0')}-${picked.day.toString().padLeft(2, '0')}';
      });
    }
  }

  String _isoToday() {
    final d = DateTime.now();
    return '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
  }

  String _isoTomorrow() {
    final d = DateTime.now().add(const Duration(days: 1));
    return '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
  }
}

/// Data returned by [AddTaskSheet] when the user taps "Add Task".
class _AddTaskResult {
  const _AddTaskResult({
    required this.title,
    required this.priority,
    this.dueDate,
  });
  final String title;
  final String priority;
  final String? dueDate;
}

// ── Public helper ─────────────────────────────────────────────────────────────

/// Show the add-task sheet and return the submitted result, or null if
/// dismissed. Callers invoke the provider directly with the returned data.
Future<({String title, String priority, String? dueDate})?> showAddTaskSheet(
  BuildContext context,
) async {
  final result = await LzBottomSheet.show<_AddTaskResult>(
    context,
    title: 'New Task',
    builder: (_) => const AddTaskSheet(),
  );
  if (result == null) return null;
  return (
    title: result.title,
    priority: result.priority,
    dueDate: result.dueDate,
  );
}
