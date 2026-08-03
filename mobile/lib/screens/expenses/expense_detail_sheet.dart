import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/expense.dart';
import '../../models/project.dart';
import '../../models/subtask.dart';
import '../../models/task.dart';
import '../../models/task_project_link.dart';
import '../../providers/budgets_provider.dart';
import '../../providers/tasks_provider.dart';
import 'money_helpers.dart';

/// An expense detail/edit bottom sheet. Pre-fills every field from [expense] and
/// lets the user change the amount, description, vendor, project and date, then
/// Save (patch via [BudgetsNotifier.updateExpense]) or Delete (confirm, then
/// [BudgetsNotifier.removeExpense]). Mirrors the task detail sheet + the
/// add-expense sheet so the surfaces feel like one family.
class ExpenseDetailSheet extends ConsumerStatefulWidget {
  const ExpenseDetailSheet({super.key, required this.expense});

  final Expense expense;

  @override
  ConsumerState<ExpenseDetailSheet> createState() => _ExpenseDetailSheetState();
}

class _ExpenseDetailSheetState extends ConsumerState<ExpenseDetailSheet> {
  late final TextEditingController _amountController;
  late final TextEditingController _descController;
  late final TextEditingController _vendorController;
  late String? _projectId;
  late String? _taskId;
  late String? _subtaskId;
  String? _spentAt;
  bool _saving = false;
  bool _deleting = false;
  String? _amountError;

  @override
  void initState() {
    super.initState();
    final e = widget.expense;
    _amountController = TextEditingController(text: _initialAmount(e.amount));
    _descController = TextEditingController(text: e.description ?? '');
    _vendorController = TextEditingController(text: e.vendor ?? '');
    _projectId = e.projectId.isEmpty ? null : e.projectId;
    _taskId = e.taskId;
    _subtaskId = e.subtaskId;
    _spentAt = _dateOnly(e.spentAt);
  }

  @override
  void dispose() {
    _amountController.dispose();
    _descController.dispose();
    _vendorController.dispose();
    super.dispose();
  }

  /// When this expense was recorded — server `created_at`, falling back to
  /// `spent_at`. Null when neither is set/parseable (render nothing).
  String? get _savedLabel =>
      formatSavedAt(widget.expense.createdAt) ??
      formatSavedAt(widget.expense.spentAt);

  Future<void> _save() async {
    if (_saving || _deleting) return;

    final amount = double.tryParse(_amountController.text.trim());
    if (amount == null || amount <= 0) {
      setState(() => _amountError = 'Enter a valid amount');
      return;
    }
    final desc = _descController.text.trim();
    if (desc.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Description is required')),
      );
      return;
    }

    setState(() {
      _saving = true;
      _amountError = null;
    });

    // Re-derive the SUBMITTABLE sub-task id from the current provider state
    // (never the raw `_subtaskId` field) — see [_effectiveSubtaskId]. A
    // sub-task deleted (locally or server-side) since this sheet's `initState`
    // ran must never be re-sent: the server validates `subtask_id` exists
    // among the task's current steps and 400s the WHOLE patch otherwise,
    // silently discarding this amount/description/etc edit along with it.
    final projects = ref.read(budgetsProvider).projects;
    final allTasks = ref.read(tasksProvider).tasks;
    final availableTasks = _tasksForCurrentProject(projects, allTasks);
    final effectiveSubtaskId = _effectiveSubtaskId(availableTasks);

    final vendor = _vendorController.text.trim();
    await ref.read(budgetsProvider.notifier).updateExpense(
          widget.expense.id,
          amount: amount,
          description: desc,
          vendor: vendor.isEmpty ? null : vendor,
          projectId: _projectId,
          taskId: _taskId,
          taskIdSet: true,
          subtaskId: effectiveSubtaskId,
          subtaskIdSet: true,
          spentAt: _spentAt,
        );

    if (!mounted) return;
    Navigator.of(context).pop();
  }

  Future<void> _delete() async {
    if (_saving || _deleting) return;
    final confirmed = await LzConfirm.show(
      context,
      title: 'Delete expense?',
      message: widget.expense.displayDescription.isEmpty
          ? 'This expense will be removed.'
          : widget.expense.displayDescription,
      confirmLabel: 'Delete',
      danger: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => _deleting = true);
    await ref.read(budgetsProvider.notifier).removeExpense(widget.expense.id);
    if (!mounted) return;
    Navigator.of(context).pop();
  }

  /// The tasks selectable for the currently-picked project — empty when no
  /// project is selected (or it matches no live project, e.g. it was
  /// deleted). A plain loop stands in for `firstWhereOrNull` — `collection`
  /// is only a transitive dep here, not one this app declares directly.
  ///
  /// Excludes done tasks by default (`tasksForProject`'s `includeCompleted:
  /// false`) so completed work doesn't clutter picking a NEW link — EXCEPT
  /// the task this expense is already linked to (`_taskId`). That one is
  /// always kept in the list even once it's marked done: `_TaskPicker`'s
  /// stale-selection guard can't distinguish "this id was never found" from
  /// "this id exists but got filtered out", so without this carve-out,
  /// marking a task done would silently sever every expense linked to it
  /// (and, transitively, any linked sub-task — see `_effectiveSubtaskId`)
  /// the next time that expense is opened for a completely unrelated edit.
  /// A done task that ISN'T the current link is still excluded as before.
  List<Task> _tasksForCurrentProject(List<Project> projects, List<Task> all) {
    for (final p in projects) {
      if (p.id != _projectId) continue;
      final visible = tasksForProject(all, p);
      if (_taskId == null || visible.any((t) => t.id == _taskId)) {
        return visible;
      }
      for (final t in tasksForProject(all, p, includeCompleted: true)) {
        if (t.id == _taskId) return [...visible, t];
      }
      return visible;
    }
    return const [];
  }

  /// The sub-tasks (checklist items) of the currently-selected task — empty
  /// when no task is selected or it matches none of [tasks] (e.g. it was
  /// deleted, mirrors [_tasksForCurrentProject]'s same defensive fallback).
  List<Subtask> _subtasksForSelectedTask(List<Task> tasks) {
    for (final t in tasks) {
      if (t.id == _taskId) return t.subtasks;
    }
    return const [];
  }

  /// [_subtaskId] once confirmed to still be among the currently-selected
  /// task's live sub-tasks, else null (a "ghost" link — its sub-task was
  /// deleted, whether locally just now or already server-side before this
  /// sheet was even opened). This is the ONLY value that may ever reach
  /// [_SubtaskPicker]'s display or [BudgetsNotifier.updateExpense]'s
  /// `subtaskId` argument — never the raw field. The picker's own stale-id
  /// guard is display-only (it just swaps in `null` for the DropdownButton's
  /// `value` so its assert doesn't fire); without also gating here, Save
  /// would still submit the raw ghost id, and the server's `subtask_id`
  /// existence check 400s the ENTIRE patch, silently dropping every other
  /// edit (amount, description, ...) in it too.
  String? _effectiveSubtaskId(List<Task> tasks) {
    final subtasks = _subtasksForSelectedTask(tasks);
    return subtasks.any((s) => s.id == _subtaskId) ? _subtaskId : null;
  }

  @override
  Widget build(BuildContext context) {
    final projects = ref.watch(budgetsProvider).projects;
    final allTasks = ref.watch(tasksProvider).tasks;
    final availableTasks = _tasksForCurrentProject(projects, allTasks);
    final effectiveSubtaskId = _effectiveSubtaskId(availableTasks);

    // Reconcile a detected ghost into STATE (not just this frame's display) —
    // once corrected, `_subtaskId` itself can never be resubmitted on a later
    // Save even if the user never touches the picker again. Scheduled for
    // after this frame (mutating state mid-build would throw); the
    // `_subtaskId != effectiveSubtaskId` re-check inside the callback (in
    // addition to the one already gating this block) makes it a strict
    // one-time correction, not a rebuild loop.
    if (effectiveSubtaskId != _subtaskId) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && _subtaskId != effectiveSubtaskId) {
          setState(() => _subtaskId = effectiveSubtaskId);
        }
      });
    }

    return SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ── Amount ─────────────────────────────────────────────────────
          LzTextField(
            controller: _amountController,
            fieldKey: const Key('expense-detail-amount'),
            label: 'Amount',
            hint: '0.00',
            prefixIcon: Icons.attach_money_rounded,
            keyboardType:
                const TextInputType.numberWithOptions(decimal: true),
            textInputAction: TextInputAction.next,
            errorText: _amountError,
            onChanged: (_) {
              if (_amountError != null) setState(() => _amountError = null);
            },
          ),

          const SizedBox(height: AppSpacing.lg),

          // ── Description ────────────────────────────────────────────────
          LzTextField(
            controller: _descController,
            fieldKey: const Key('expense-detail-desc'),
            label: 'Description',
            hint: 'What was this for?',
            prefixIcon: Icons.notes_rounded,
            textInputAction: TextInputAction.next,
          ),

          const SizedBox(height: AppSpacing.lg),

          // ── Vendor (optional) ──────────────────────────────────────────
          LzTextField(
            controller: _vendorController,
            fieldKey: const Key('expense-detail-vendor'),
            label: 'Vendor (optional)',
            hint: 'Merchant or source',
            prefixIcon: Icons.storefront_outlined,
            textInputAction: TextInputAction.done,
          ),

          const SizedBox(height: AppSpacing.lg),

          // ── Project picker ─────────────────────────────────────────────
          _ProjectPicker(
            projects: projects,
            selectedId: _projectId,
            // A task belongs to one project, so switching the expense's
            // project invalidates whatever task (and, transitively, sub-task)
            // was linked — reset both rather than carry them from the old
            // project forward.
            onChanged: (id) => setState(() {
              _projectId = id;
              _taskId = null;
              _subtaskId = null;
            }),
            // The picker's own stale-selectedId guard tripped (its render
            // fell back to "no project selected") — mirror that into the
            // sheet's own state so a subsequent Save can never resubmit the
            // id the UI just stopped showing.
            onStaleSelection: () {
              if (!mounted) return;
              setState(() => _projectId = null);
            },
          ),

          const SizedBox(height: AppSpacing.lg),

          // ── Task picker (optional) ─────────────────────────────────────
          _TaskPicker(
            tasks: availableTasks,
            selectedId: _taskId,
            // A sub-task belongs to exactly one task — switching (or
            // clearing) the task invalidates whatever sub-task was linked.
            onChanged: (id) => setState(() {
              _taskId = id;
              _subtaskId = null;
            }),
            // Same guard-trip reset as the project picker above — keeps
            // render and submitted state from ever diverging. Only fires
            // for a genuinely gone id (deleted, or its project changed) —
            // `_tasksForCurrentProject` keeps the CURRENTLY linked task in
            // `availableTasks` even once it's done, so completing a task
            // never trips this guard for its own already-saved link.
            onStaleSelection: () {
              if (!mounted) return;
              setState(() => _taskId = null);
            },
          ),

          const SizedBox(height: AppSpacing.lg),

          // ── Sub-task picker (optional; only meaningful with a task) ─────
          _SubtaskPicker(
            subtasks: _subtasksForSelectedTask(availableTasks),
            selectedId: effectiveSubtaskId,
            enabled: _taskId != null,
            onChanged: (id) => setState(() => _subtaskId = id),
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Date quick-pick ────────────────────────────────────────────
          _SectionLabel('DATE'),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              LzChip(
                label: 'Today',
                icon: Icons.today_outlined,
                selected: _spentAt == _isoToday(),
                color: AppColors.warn,
                onTap: () => setState(() => _spentAt = _isoToday()),
              ),
              const SizedBox(width: AppSpacing.sm),
              LzChip(
                label: 'Pick…',
                icon: Icons.calendar_month_outlined,
                selected: _spentAt != null && _spentAt != _isoToday(),
                color: AppColors.info,
                onTap: _pickDate,
              ),
            ],
          ),
          if (_spentAt != null) ...[
            const SizedBox(height: AppSpacing.sm),
            Row(
              children: [
                Icon(Icons.event_available_outlined,
                    size: 14, color: AppColors.textMuted),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  'Spent $_spentAt',
                  style:
                      AppText.caption.copyWith(color: AppColors.textSecondary),
                ),
              ],
            ),
          ],

          // ── Saved (created) time ───────────────────────────────────────
          if (_savedLabel != null) ...[
            const SizedBox(height: AppSpacing.md),
            Row(
              key: const Key('expense-detail-saved'),
              children: [
                const Icon(Icons.schedule_outlined,
                    size: 14, color: AppColors.textMuted),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  'Saved $_savedLabel',
                  style: AppText.caption.copyWith(color: AppColors.textMuted),
                ),
              ],
            ),
          ],

          const SizedBox(height: AppSpacing.xxl),

          // ── Footer: Delete + Save ──────────────────────────────────────
          Row(
            children: [
              LzButton.danger(
                key: const Key('expense-detail-delete'),
                label: 'Delete Expense',
                icon: Icons.delete_outline,
                loading: _deleting,
                onPressed: _delete,
              ),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: LzButton.primary(
                  key: const Key('expense-detail-save'),
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
    DateTime initial = now;
    if (_spentAt != null) {
      try {
        final parsed = DateTime.parse(_spentAt!);
        initial = parsed;
      } catch (_) {
        // Keep the default when the stored value isn't ISO-parseable.
      }
    }
    final picked = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: now.subtract(const Duration(days: 365 * 5)),
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
      setState(() => _spentAt = _isoFor(picked));
    }
  }

  /// A double rendered for an editable field: drop a trailing `.0` so a whole
  /// amount reads `12`, not `12.0`, while `12.5` stays `12.5`.
  static String _initialAmount(double v) {
    final s = v.toString();
    return s.endsWith('.0') ? s.substring(0, s.length - 2) : s;
  }

  /// The leading `YYYY-MM-DD` of a stored `spent_at` (which may be a full ISO
  /// timestamp). Null/empty → null.
  static String? _dateOnly(String? raw) {
    if (raw == null || raw.isEmpty) return null;
    try {
      return _isoFor(DateTime.parse(raw));
    } catch (_) {
      // Fall back to the first 10 chars when it parses to a date prefix.
      return raw.length >= 10 ? raw.substring(0, 10) : raw;
    }
  }

  static String _isoFor(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  static String _isoToday() => _isoFor(DateTime.now());
}

/// A token-styled dropdown project picker — visually identical to the add-expense
/// sheet's picker so the two surfaces feel like one family.
class _ProjectPicker extends StatelessWidget {
  const _ProjectPicker({
    required this.projects,
    required this.selectedId,
    required this.onChanged,
    this.onStaleSelection,
  });

  final List<Project> projects;
  final String? selectedId;
  final ValueChanged<String?> onChanged;

  /// Invoked (once, post-frame) when [selectedId] is non-null but isn't
  /// among [projects] — i.e. the guard below is about to render "no project
  /// selected" even though the caller still thinks one is selected. Lets the
  /// owning state reset its own field to match what's actually on screen, so
  /// a Save action can never resubmit an id the UI stopped displaying.
  final VoidCallback? onStaleSelection;

  @override
  Widget build(BuildContext context) {
    // Guard against a stale selectedId that isn't in the list (e.g. its project
    // was deleted) so the DropdownButton's assert doesn't fire.
    final hasSelected = projects.any((p) => p.id == selectedId);
    if (selectedId != null && !hasSelected) {
      WidgetsBinding.instance
          .addPostFrameCallback((_) => onStaleSelection?.call());
    }

    if (projects.isEmpty) {
      return Container(
        padding: const EdgeInsets.all(AppSpacing.md),
        decoration: BoxDecoration(
          color: AppColors.bgSurfaceElevated,
          borderRadius: AppRadii.rMd,
          border: Border.all(color: AppColors.borderDefault),
        ),
        child: Text(
          'No projects',
          style: AppText.body.copyWith(color: AppColors.textMuted),
        ),
      );
    }

    final value = hasSelected ? selectedId : null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Project',
          style: AppText.label.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: AppSpacing.sm),
        Container(
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rMd,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              key: const Key('expense-detail-project'),
              value: value,
              isExpanded: true,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
              dropdownColor: AppColors.bgSurfaceElevated,
              style: AppText.body,
              icon: const Icon(
                Icons.keyboard_arrow_down_rounded,
                color: AppColors.textMuted,
              ),
              hint: Text(
                'Select project',
                style: AppText.body.copyWith(color: AppColors.textMuted),
              ),
              items: projects
                  .map(
                    (p) => DropdownMenuItem<String>(
                      value: p.id,
                      child: Text(p.name, style: AppText.body),
                    ),
                  )
                  .toList(),
              onChanged: onChanged,
            ),
          ),
        ),
      ],
    );
  }
}

/// A token-styled dropdown task picker — links this expense to one of the
/// tasks in its (already-selected) project. "(no task)" is always the first
/// option and represents an explicit clear, not "leave unchanged" (the sheet
/// always saves with `taskIdSet: true`). Visually mirrors [_ProjectPicker] so
/// the two feel like one family.
class _TaskPicker extends StatelessWidget {
  const _TaskPicker({
    required this.tasks,
    required this.selectedId,
    required this.onChanged,
    this.onStaleSelection,
  });

  final List<Task> tasks;
  final String? selectedId;
  final ValueChanged<String?> onChanged;

  /// Invoked (once, post-frame) when [selectedId] is non-null but isn't
  /// among [tasks] — e.g. the linked task was deleted, or its project
  /// changed. NOT triggered merely because the linked task is done: the
  /// caller (`_tasksForCurrentProject`) always keeps the current [selectedId]
  /// in [tasks] even once it's excluded from fresh picks, so completing a
  /// task can never silently clear an expense's existing link to it. Mirrors
  /// [_ProjectPicker.onStaleSelection]: lets the owning state reset its own
  /// field so render and submitted value can never diverge.
  final VoidCallback? onStaleSelection;

  @override
  Widget build(BuildContext context) {
    // Guard against a stale selectedId that isn't among the current options
    // (e.g. its task was deleted, or the project changed since) so the
    // DropdownButton's assert doesn't fire — mirrors _ProjectPicker's guard.
    final hasSelected = tasks.any((t) => t.id == selectedId);
    if (selectedId != null && !hasSelected) {
      WidgetsBinding.instance
          .addPostFrameCallback((_) => onStaleSelection?.call());
    }
    final value = hasSelected ? selectedId : null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Task (optional)',
          style: AppText.label.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: AppSpacing.sm),
        Container(
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rMd,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              key: const Key('expense-detail-task'),
              value: value,
              isExpanded: true,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
              dropdownColor: AppColors.bgSurfaceElevated,
              style: AppText.body,
              icon: const Icon(
                Icons.keyboard_arrow_down_rounded,
                color: AppColors.textMuted,
              ),
              items: [
                DropdownMenuItem<String>(
                  value: null,
                  child: Text(
                    '(no task)',
                    style: AppText.body.copyWith(color: AppColors.textMuted),
                  ),
                ),
                for (final t in tasks)
                  DropdownMenuItem<String>(
                    value: t.id,
                    child: Text(
                      t.title,
                      style: AppText.body,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
              ],
              onChanged: onChanged,
            ),
          ),
        ),
      ],
    );
  }
}

/// A token-styled dropdown sub-task picker — links this expense to one
/// checklist item of the (already-selected) task. "No subtask" is always the
/// first option and represents an explicit clear, not "leave unchanged" (the
/// sheet always saves with `subtaskIdSet: true`, mirroring [_TaskPicker]'s
/// own `taskIdSet: true`). Disabled (greyed hint, no items, `onChanged: null`)
/// until a task is selected — a sub-task can't exist without one. Visually
/// mirrors [_TaskPicker] so all three pickers feel like one family.
class _SubtaskPicker extends StatelessWidget {
  const _SubtaskPicker({
    required this.subtasks,
    required this.selectedId,
    required this.enabled,
    required this.onChanged,
  });

  final List<Subtask> subtasks;
  final String? selectedId;
  final bool enabled;
  final ValueChanged<String?> onChanged;

  @override
  Widget build(BuildContext context) {
    // Guard against a stale selectedId that isn't among the current options
    // (e.g. its sub-task was deleted, or the task changed since) so the
    // DropdownButton's assert doesn't fire — mirrors _TaskPicker's guard.
    final hasSelected = enabled && subtasks.any((s) => s.id == selectedId);
    final value = hasSelected ? selectedId : null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Subtask (optional)',
          style: AppText.label.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: AppSpacing.sm),
        Container(
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rMd,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              key: const Key('expense-detail-subtask'),
              value: value,
              isExpanded: true,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
              dropdownColor: AppColors.bgSurfaceElevated,
              style: AppText.body,
              icon: const Icon(
                Icons.keyboard_arrow_down_rounded,
                color: AppColors.textMuted,
              ),
              hint: Text(
                enabled ? 'Select subtask' : 'Select a task first',
                style: AppText.body.copyWith(color: AppColors.textMuted),
              ),
              items: !enabled
                  ? const []
                  : [
                      DropdownMenuItem<String>(
                        value: null,
                        child: Text(
                          'No subtask',
                          style: AppText.body.copyWith(
                            color: AppColors.textMuted,
                          ),
                        ),
                      ),
                      for (final s in subtasks)
                        DropdownMenuItem<String>(
                          value: s.id,
                          child: Text(
                            s.title,
                            style: AppText.body,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
              onChanged: enabled ? onChanged : null,
            ),
          ),
        ),
      ],
    );
  }
}

/// A small uppercase section label matching the task detail sheet's headers.
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

/// Open the expense detail/edit sheet for [expense]. The sheet reads
/// [budgetsProvider] for Save/Delete; [ref] is accepted so the call site (the
/// Money screen, which already holds a [WidgetRef]) owns the invocation.
Future<void> showExpenseDetailSheet(
  BuildContext context,
  WidgetRef ref,
  Expense expense,
) {
  return LzBottomSheet.show<void>(
    context,
    title: 'Edit Expense',
    builder: (_) => ExpenseDetailSheet(expense: expense),
  );
}
