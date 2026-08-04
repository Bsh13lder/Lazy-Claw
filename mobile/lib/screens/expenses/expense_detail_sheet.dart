import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/core/autosave.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/expense.dart';
import '../../models/project.dart';
import '../../models/subtask.dart';
import '../../models/task.dart';
import '../../models/task_project_link.dart';
import '../../providers/budgets_provider.dart';
import '../../providers/tasks_provider.dart';
import '../../widgets/autosave_indicator.dart';
import 'expense_detail_pickers.dart';
import 'money_helpers.dart';

/// Shown under AMOUNT when it is missing, unparseable or not positive. An
/// expense with no amount is not an expense — auto-save must refuse it rather
/// than overwrite a good figure.
const String kExpenseAmountInvalidError = 'Enter a valid amount';

/// Shown under DESCRIPTION when it is blank. Was a snackbar on the old
/// Save-only path; a snackbar is the wrong shape for auto-save (it would fire
/// on every debounce and never point at the field that is wrong).
const String kExpenseDescriptionRequiredError = 'Description is required';

/// An expense detail/edit bottom sheet. Pre-fills every field from [expense] and
/// lets the user change the amount, description, vendor, project and date.
///
/// It AUTO-SAVES: text a beat after typing stops, the pickers and the date
/// chips the moment they change, and anything pending is flushed on dismiss or
/// when the app is backgrounded. Save now means "commit now and close"; Delete
/// stays explicit (confirm, then [BudgetsNotifier.removeExpense]). Mirrors the
/// task detail sheet so the surfaces feel like one family.
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
  bool _deleting = false;
  String? _amountError;
  String? _descError;

  /// Auto-save scheduling (debounce, coalescing, background flush). [_commit]
  /// owns every decision about WHAT is written.
  late final AutosaveController _autosave;

  /// Fingerprint of the state that is currently PERSISTED — the "never write
  /// when nothing changed" gate.
  late String _savedSignature;

  AutosaveStatus _status = AutosaveStatus.idle;

  /// Set at the top of [dispose] so the dismiss-time flush can settle without
  /// calling setState on a State that is going away.
  bool _disposing = false;

  /// Captured while this widget is definitely alive: the dismiss-time flush
  /// runs from [dispose], where `ref` may no longer be used — and that flush
  /// is exactly the write this feature exists for.
  late final BudgetsNotifier _budgets;

  @override
  void initState() {
    super.initState();
    final e = widget.expense;
    _budgets = ref.read(budgetsProvider.notifier);
    _amountController = TextEditingController(text: _initialAmount(e.amount));
    _descController = TextEditingController(text: e.description ?? '');
    _vendorController = TextEditingController(text: e.vendor ?? '');
    _projectId = e.projectId.isEmpty ? null : e.projectId;
    _taskId = e.taskId;
    _subtaskId = e.subtaskId;
    _spentAt = _dateOnly(e.spentAt);
    _savedSignature = _signature();
    _autosave = AutosaveController(onCommit: _commit)
      ..addListener(_onAutosaveStatus)
      ..bindText(_amountController)
      ..bindText(_descController)
      ..bindText(_vendorController);
  }

  @override
  void dispose() {
    // ORDER IS LOAD-BEARING — see the twin comment in `task_detail_sheet.dart`.
    // `flush` runs [_commit] synchronously as far as the `await` on the write,
    // so the payload is read off the controllers BEFORE they are disposed.
    _disposing = true;
    _autosave.removeListener(_onAutosaveStatus);
    _autosave.flush();
    _autosave.dispose();
    _amountController.dispose();
    _descController.dispose();
    _vendorController.dispose();
    super.dispose();
  }

  void _onAutosaveStatus() {
    if (!mounted || _disposing) return;
    setState(() => _status = _autosave.status);
  }

  /// `setState` while alive, a plain assignment once not.
  void _apply(VoidCallback change) {
    if (!mounted || _disposing) {
      change();
      return;
    }
    setState(change);
  }

  /// Apply a DISCRETE edit and persist it at once — pickers and date chips
  /// carry a finished decision, so there is nothing to debounce.
  void _editNow(VoidCallback change) {
    setState(change);
    _autosave.markDirtyNow();
  }

  /// Fingerprint of the RESULTING RECORD.
  ///
  /// The amount is signed PARSED, not as typed: re-spelling `12.5` as `12.50`
  /// is not an edit, and writing for it would churn `updated_at` (and, under
  /// last-write-wins sync, risk clobbering a real remote change).
  ///
  /// The link ids are signed RAW. A ghost id detected during build is
  /// reconciled in state and re-baselined by [_rebaseAfterReconcile] — the
  /// sheet fixing its own stale data is not a user edit and must not write.
  String _signature() {
    final vendor = _vendorController.text.trim();
    return autosaveSignature([
      double.tryParse(_amountController.text.trim()),
      _descController.text.trim(),
      vendor.isEmpty ? null : vendor,
      _projectId,
      _taskId,
      _subtaskId,
      _spentAt,
    ]);
  }

  /// Treat the CURRENT state as the persisted one, without writing.
  ///
  /// Only for the sheet's own stale-id corrections (a deleted sub-task, a
  /// deleted project). Guarded on [AutosaveController.isIdle] so it can never
  /// swallow a real edit that happens to be waiting on the debounce.
  void _rebaseAfterReconcile() {
    if (_autosave.isIdle) _savedSignature = _signature();
  }

  /// When this expense was recorded — server `created_at`, falling back to
  /// `spent_at`. Null when neither is set/parseable (render nothing).
  String? get _savedLabel =>
      formatSavedAt(widget.expense.createdAt) ??
      formatSavedAt(widget.expense.spentAt);

  /// Persist the sheet — the dirty gate, the validity gate and the writer, in
  /// that order. Called by [AutosaveController]; never directly.
  Future<AutosaveOutcome> _commit() async {
    // A queued write must never resurrect a row on its way out.
    if (_deleting) return AutosaveOutcome.unchanged;

    final amount = double.tryParse(_amountController.text.trim());
    if (amount == null || amount <= 0) {
      _apply(() => _amountError = kExpenseAmountInvalidError);
      return AutosaveOutcome.blocked;
    }
    final desc = _descController.text.trim();
    if (desc.isEmpty) {
      _apply(() {
        _amountError = null;
        _descError = kExpenseDescriptionRequiredError;
      });
      return AutosaveOutcome.blocked;
    }
    if (_amountError != null || _descError != null) {
      _apply(() {
        _amountError = null;
        _descError = null;
      });
    }

    final signature = _signature();
    if (signature == _savedSignature) return AutosaveOutcome.unchanged;

    // Re-derive the SUBMITTABLE sub-task id from the current provider state
    // (never the raw `_subtaskId` field) — see [_effectiveSubtaskId]. A
    // sub-task deleted (locally or server-side) since this sheet's `initState`
    // ran must never be re-sent: the server validates `subtask_id` exists
    // among the task's current steps and 400s the WHOLE patch otherwise,
    // silently discarding this amount/description/etc edit along with it.
    //
    // It is recomputed HERE (not read from a cache) so it always reflects the
    // selection as it stands at this instant — see [_lastProjects].
    final vendor = _vendorController.text.trim();
    await _budgets.updateExpense(
      widget.expense.id,
      amount: amount,
      description: desc,
      vendor: vendor.isEmpty ? null : vendor,
      projectId: _projectId,
      taskId: _taskId,
      taskIdSet: true,
      subtaskId: _submittableSubtaskId,
      subtaskIdSet: true,
      spentAt: _spentAt,
    );
    _savedSignature = signature;
    return AutosaveOutcome.written;
  }

  /// The floating/footer Save: commit whatever is outstanding, then close. A
  /// blocked write keeps the sheet open with its error visible rather than
  /// closing over the problem.
  Future<void> _submit() async {
    if (_deleting) return;
    await _autosave.flush();
    if (!mounted) return;
    if (_autosave.status == AutosaveStatus.blocked) return;
    Navigator.of(context).pop();
  }

  Future<void> _delete() async {
    if (_deleting) return;
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
    // Drop anything queued BEFORE flagging: a debounced field write landing
    // after the delete would patch a row on its way out.
    _autosave.cancelPending();
    setState(() => _deleting = true);
    await _budgets.removeExpense(widget.expense.id);
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
  /// always kept in the list even once it's marked done: `ExpenseTaskPicker`'s
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
  /// [ExpenseSubtaskPicker]'s display or [BudgetsNotifier.updateExpense]'s
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

  /// The provider data the most recent build saw.
  ///
  /// Cached because the dismiss-time flush runs from `dispose`, where reading
  /// a provider through `ref` is no longer legal — and that flush is the write
  /// that most needs the ghost-id filter, since the user never saw the ghost.
  ///
  /// The LISTS are cached, not the derived id: [_effectiveSubtaskId] must be
  /// recomputed at commit time against the sheet's CURRENT selection. A cached
  /// id goes stale the instant the user picks a different sub-task, because a
  /// discrete pick commits synchronously — before the rebuild that would have
  /// refreshed it. (That is not hypothetical: it made picking "No subtask"
  /// resubmit the very id it had just cleared.)
  List<Project> _lastProjects = const [];
  List<Task> _lastTasks = const [];

  /// The sub-task id it is legal to submit right now.
  String? get _submittableSubtaskId =>
      _effectiveSubtaskId(_tasksForCurrentProject(_lastProjects, _lastTasks));

  @override
  Widget build(BuildContext context) {
    final projects = ref.watch(budgetsProvider).projects;
    final allTasks = ref.watch(tasksProvider).tasks;
    _lastProjects = projects;
    _lastTasks = allTasks;
    final availableTasks = _tasksForCurrentProject(projects, allTasks);
    final effectiveSubtaskId = _effectiveSubtaskId(availableTasks);

    // Reconcile a detected ghost into STATE (not just this frame's display) —
    // once corrected, `_subtaskId` itself can never be resubmitted on a later
    // write even if the user never touches the picker again. Scheduled for
    // after this frame (mutating state mid-build would throw); the
    // `_subtaskId != effectiveSubtaskId` re-check inside the callback (in
    // addition to the one already gating this block) makes it a strict
    // one-time correction, not a rebuild loop.
    //
    // It re-baselines rather than marking dirty: this is the SHEET repairing
    // its own stale data, not the user editing. Auto-saving it would make
    // merely opening an expense whose sub-task was deleted elsewhere write to
    // the database — the exact open-and-close churn this feature must avoid.
    if (effectiveSubtaskId != _subtaskId) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && _subtaskId != effectiveSubtaskId) {
          setState(() => _subtaskId = effectiveSubtaskId);
          _rebaseAfterReconcile();
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
            // Clearing the rejection as the user types is cosmetic only — the
            // real re-validation happens in [_commit] when the debounce fires.
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
            errorText: _descError,
            onChanged: (_) {
              if (_descError != null) setState(() => _descError = null);
            },
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
          ExpenseProjectPicker(
            projects: projects,
            selectedId: _projectId,
            // A task belongs to one project, so switching the expense's
            // project invalidates whatever task (and, transitively, sub-task)
            // was linked — reset both rather than carry them from the old
            // project forward.
            onChanged: (id) => _editNow(() {
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
              // A repair, not an edit — re-baseline instead of writing.
              setState(() => _projectId = null);
              _rebaseAfterReconcile();
            },
          ),

          const SizedBox(height: AppSpacing.lg),

          // ── Task picker (optional) ─────────────────────────────────────
          ExpenseTaskPicker(
            tasks: availableTasks,
            selectedId: _taskId,
            // A sub-task belongs to exactly one task — switching (or
            // clearing) the task invalidates whatever sub-task was linked.
            onChanged: (id) => _editNow(() {
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
              // A repair, not an edit — see the project picker above.
              setState(() => _taskId = null);
              _rebaseAfterReconcile();
            },
          ),

          const SizedBox(height: AppSpacing.lg),

          // ── Sub-task picker (optional; only meaningful with a task) ─────
          ExpenseSubtaskPicker(
            subtasks: _subtasksForSelectedTask(availableTasks),
            selectedId: effectiveSubtaskId,
            enabled: _taskId != null,
            onChanged: (id) => _editNow(() => _subtaskId = id),
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Date quick-pick ────────────────────────────────────────────
          ExpenseSectionLabel('DATE'),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              LzChip(
                label: 'Today',
                icon: Icons.today_outlined,
                selected: _spentAt == _isoToday(),
                color: AppColors.warn,
                onTap: () => _editNow(() => _spentAt = _isoToday()),
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

          const SizedBox(height: AppSpacing.xl),

          // ── Save state ─────────────────────────────────────────────────
          //
          // Directly above the footer, right-aligned over the Save button it
          // has replaced the meaning of. It is deliberately not a control:
          // this sheet already has exactly one submit affordance, and a second
          // thing that looks tappable next to Delete would be worse than no
          // indicator at all.
          Align(
            alignment: Alignment.centerRight,
            child: AutosaveIndicator(status: _status),
          ),

          const SizedBox(height: AppSpacing.md),

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
                  loading: _status == AutosaveStatus.saving,
                  expand: true,
                  onPressed: _submit,
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
    if (picked != null && mounted) {
      _editNow(() => _spentAt = _isoFor(picked));
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
