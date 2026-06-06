import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/task.dart';
import '../../providers/tasks_provider.dart';

/// A task detail/edit bottom sheet. Pre-fills every field from [task] and lets
/// the user change the title, notes, priority and due date, then Save (patch
/// via [TasksNotifier.updateTask]) or Delete (confirm, then
/// [TasksNotifier.deleteTask]). Mirrors the add-task sheet's look so the two
/// surfaces feel like one family.
class TaskDetailSheet extends ConsumerStatefulWidget {
  const TaskDetailSheet({super.key, required this.task});

  final Task task;

  @override
  ConsumerState<TaskDetailSheet> createState() => _TaskDetailSheetState();
}

class _TaskDetailSheetState extends ConsumerState<TaskDetailSheet> {
  late final TextEditingController _titleController;
  late final TextEditingController _notesController;
  late String _priority;
  String? _dueDate;
  bool _saving = false;
  bool _deleting = false;

  static const _priorities = ['low', 'medium', 'high', 'urgent'];

  @override
  void initState() {
    super.initState();
    final t = widget.task;
    _titleController = TextEditingController(text: t.title);
    _notesController = TextEditingController(text: t.description ?? '');
    _priority = _priorities.contains(t.priority) ? t.priority : 'medium';
    _dueDate = t.dueDate;
  }

  @override
  void dispose() {
    _titleController.dispose();
    _notesController.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final title = _titleController.text.trim();
    if (title.isEmpty || _saving || _deleting) return;
    setState(() => _saving = true);
    await ref.read(tasksProvider.notifier).updateTask(
          widget.task.id,
          title: title,
          description: _notesController.text.trim(),
          priority: _priority,
          dueDate: _dueDate,
        );
    if (!mounted) return;
    Navigator.of(context).pop();
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

  @override
  Widget build(BuildContext context) {
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
          LzTextField(
            controller: _notesController,
            fieldKey: const Key('task-detail-notes'),
            hint: 'Notes (optional)',
            prefixIcon: Icons.notes_outlined,
            minLines: 2,
            maxLines: 5,
            keyboardType: TextInputType.multiline,
          ),

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

          // ── Due date quick-pick ────────────────────────────────────────
          _SectionLabel('DUE DATE'),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              LzChip(
                label: 'Today',
                icon: Icons.today_outlined,
                selected: _dueDate == _isoToday(),
                color: AppColors.warn,
                onTap: () => setState(() {
                  _dueDate = _dueDate == _isoToday() ? null : _isoToday();
                }),
              ),
              const SizedBox(width: AppSpacing.sm),
              LzChip(
                label: 'Tomorrow',
                icon: Icons.event_outlined,
                selected: _dueDate == _isoTomorrow(),
                color: AppColors.accent,
                onTap: () => setState(() {
                  _dueDate = _dueDate == _isoTomorrow() ? null : _isoTomorrow();
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
                  style: AppText.caption
                      .copyWith(color: AppColors.textSecondary),
                ),
                const Spacer(),
                GestureDetector(
                  onTap: () => setState(() => _dueDate = null),
                  child:
                      Icon(Icons.close, size: 14, color: AppColors.textMuted),
                ),
              ],
            ),
          ],

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
    if (_dueDate != null) {
      try {
        final parsed = DateTime.parse(_dueDate!);
        if (!parsed.isBefore(now)) initial = parsed;
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
      setState(() => _dueDate = _isoFor(picked));
    }
  }

  String _isoFor(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  String _isoToday() => _isoFor(DateTime.now());

  String _isoTomorrow() => _isoFor(DateTime.now().add(const Duration(days: 1)));
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

// ── Public helper ─────────────────────────────────────────────────────────────

/// Open the task detail/edit sheet for [task]. The sheet reads
/// [tasksProvider] for Save/Delete; [ref] is accepted so the call site (the
/// Tasks screen, which already holds a [WidgetRef]) owns the invocation.
Future<void> showTaskDetailSheet(
  BuildContext context,
  WidgetRef ref,
  Task task,
) {
  return LzBottomSheet.show<void>(
    context,
    title: 'Edit Task',
    builder: (_) => TaskDetailSheet(task: task),
  );
}
