import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:lazyclaw_mobile/widgets/link_text.dart';

import '../../models/subtask.dart';
import '../expenses/money_helpers.dart';
import 'task_sort.dart';
import 'task_timestamps.dart';

/// A controlled checklist editor for a task's sub-tasks (Todoist/Taskade-style).
///
/// The parent owns the `List<Subtask>` and is handed a fresh, immutable list via
/// [onChanged] on every mutation (toggle done, inline-edit text, delete, add).
/// Each row's tap-to-edit is ephemeral UI state managed by [_SubtaskTile]; the
/// data itself always flows up to the parent.
class SubtaskEditor extends StatelessWidget {
  const SubtaskEditor({
    super.key,
    required this.subtasks,
    required this.onChanged,
    this.commentCounts = const {},
    this.onOpenComments,
    this.expenseTotals = const {},
    this.expenseCurrency = 'USD',
    this.savedSubtaskIds = const {},
    this.onAddExpense,
    this.nowIso = subtaskNowIso,
  });

  final List<Subtask> subtasks;
  final ValueChanged<List<Subtask>> onChanged;

  /// The clock used to stamp `created_at` / `completed_at`.
  ///
  /// Stamping lives HERE rather than in the model because this widget is the
  /// only place that knows a row was just born or just ticked — the model is
  /// a dumb value type and `copyWith(done: true)` is also how unrelated code
  /// re-shapes a list without meaning "the user completed this now".
  ///
  /// The server enforces the same invariant independently, so the two must
  /// AGREE rather than fight: a value stamped here survives the round-trip
  /// untouched instead of being re-stamped on arrival.
  ///
  /// Injectable so tests can freeze it; production always gets the real clock.
  final String Function() nowIso;

  /// Comment counts keyed by sub-task id, used to show a `+count` badge next
  /// to the comment icon. A sub-task's id being ABSENT from this map (not just
  /// mapped to zero) means comments aren't available for it yet — see
  /// [onOpenComments].
  final Map<String, int> commentCounts;

  /// The sum of live (non-void) expenses linked to each sub-task, keyed by
  /// sub-task id — the amount shown on "the money sign". A sub-task's id
  /// being absent (or mapped to a non-positive total) just means "no money
  /// yet"; whether the sign itself is RENDERED is decided by [onAddExpense] /
  /// [savedSubtaskIds], not by this map. Defaults to `const {}` so every
  /// existing call site (add-task sheet, task row) compiles and renders
  /// unchanged.
  final Map<String, double> expenseTotals;

  /// The currency [expenseTotals] amounts are formatted in via [fmtMoney].
  /// A task's expenses all belong to the task's one project, hence one
  /// currency — there is no per-sub-task currency to track.
  final String expenseCurrency;

  /// The sub-task ids that exist SERVER-SIDE (i.e. are part of the saved
  /// task's `steps`), gating the tappable money affordance exactly the way
  /// [commentCounts]'s key set gates the 💬 badge — and for the same reason:
  /// an expense's `subtask_id`, like a comment's, can only point at a saved
  /// sub-task. Filing one against an in-sheet, not-yet-saved row would replay
  /// against an unknown id server-side (a definitive 400 the outbox then
  /// drains, silently erasing the expense).
  ///
  /// A separate set rather than reusing [expenseTotals]'s keys because a
  /// saved sub-task with NO money yet must still offer the affordance — that
  /// is the whole point of it — and rather than reusing [commentCounts] so
  /// money doesn't silently disappear on a caller that wires expenses but
  /// not comments.
  final Set<String> savedSubtaskIds;

  /// When supplied, each SAVED sub-task's money sign becomes tappable and
  /// invokes this with the tile's sub-task id — the caller opens the
  /// task-scoped Add Expense sheet pinned to it (see
  /// `TaskDetailSheet._addExpense`).
  ///
  /// Null (the default) keeps the pre-existing display-only behavior: the
  /// chip renders only for sub-tasks that already have money, and does
  /// nothing when touched. That is what keeps `add_task_sheet.dart` and
  /// `task_row.dart` — neither of which passes any expense data — compiling
  /// and rendering exactly as before.
  final ValueChanged<String>? onAddExpense;

  /// When supplied, a tile shows a small comment icon (+count when > 0)
  /// between the title and the delete affordance ONLY when its sub-task's id
  /// is also a key of [commentCounts]; tapping it invokes this with the
  /// tile's sub-task id. This lets a caller wire a single non-null callback
  /// for the whole list while still hiding the affordance per-tile for
  /// sub-tasks comments can't target yet (e.g. a not-yet-saved new sub-task —
  /// see `TaskDetailSheet`, which keys `commentCounts` off the SAVED task
  /// rather than the in-sheet working list for exactly this reason). Null (the
  /// default) keeps every tile badge-free — existing callers that don't pass
  /// it compile and render unchanged.
  final ValueChanged<String>? onOpenComments;

  /// Toggle done, keeping `completed_at` in lockstep: ticking stamps it,
  /// un-ticking CLEARS it. Leaving a stale completion time on an un-ticked row
  /// would strand a "finished at 09:30" reading on something visibly open.
  ///
  /// A legacy row with no `created_at` is NOT backfilled on the way through —
  /// we still don't know when it was created, and guessing "now" would date
  /// every old checklist item to the day it happened to be ticked.
  void _toggle(String id) {
    final stamp = nowIso();
    onChanged([
      for (final s in subtasks)
        if (s.id != id)
          s
        else if (s.done)
          s.copyWith(done: false, clearCompletedAt: true)
        else
          s.copyWith(done: true, completedAt: stamp),
    ]);
  }

  void _editText(String id, String title) {
    final trimmed = title.trim();
    // An empty inline commit deletes the row (Todoist behavior).
    if (trimmed.isEmpty) {
      _delete(id);
      return;
    }
    onChanged([
      for (final s in subtasks)
        if (s.id == id) s.copyWith(title: trimmed) else s,
    ]);
  }

  void _delete(String id) {
    onChanged([
      for (final s in subtasks)
        if (s.id != id) s,
    ]);
  }

  void _add(String title) {
    final trimmed = title.trim();
    if (trimmed.isEmpty) return;
    onChanged([
      ...subtasks,
      Subtask(
        id: newSubtaskId(),
        title: trimmed,
        done: false,
        // A brand-new row is the ONE moment we genuinely observe a creation
        // time, so it is the only place `created_at` is ever set.
        createdAt: nowIso(),
      ),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    // The RENDER clock is the STAMPING clock, read back once per build rather
    // than accepting a second injection point. One hook means a test that
    // freezes `nowIso` can't stamp 2026 and then measure "ago" against the
    // real wall clock — the two would silently disagree by years.
    final now = DateTime.tryParse(nowIso());
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final s in sortSubtasksDoneLast(subtasks))
          _SubtaskTile(
            key: ValueKey('subtask-tile-${s.id}'),
            subtask: s,
            now: now,
            onToggle: () => _toggle(s.id),
            onTextChanged: (t) => _editText(s.id, t),
            onDelete: () => _delete(s.id),
            commentCount: commentCounts[s.id] ?? 0,
            onOpenComments:
                (onOpenComments == null || !commentCounts.containsKey(s.id))
                ? null
                : () => onOpenComments!(s.id),
            expenseTotal: expenseTotals[s.id],
            expenseCurrency: expenseCurrency,
            onAddExpense:
                (onAddExpense == null || !savedSubtaskIds.contains(s.id))
                ? null
                : () => onAddExpense!(s.id),
          ),
        _AddSubtaskField(onAdd: _add),
      ],
    );
  }
}

/// One sub-task row: a round checkbox, an inline-editable title (tap → edit),
/// and a delete affordance.
class _SubtaskTile extends StatefulWidget {
  const _SubtaskTile({
    super.key,
    required this.subtask,
    required this.onToggle,
    required this.onTextChanged,
    required this.onDelete,
    this.commentCount = 0,
    this.onOpenComments,
    this.expenseTotal,
    this.expenseCurrency = 'USD',
    this.onAddExpense,
    this.now,
  });

  final Subtask subtask;

  /// The clock the created/completed line measures "ago" against — see
  /// [SubtaskEditor.build]. Null falls back to the real clock.
  final DateTime? now;

  final VoidCallback onToggle;
  final ValueChanged<String> onTextChanged;
  final VoidCallback onDelete;
  final int commentCount;
  final VoidCallback? onOpenComments;

  /// This sub-task's expense total, or null when it has none — see
  /// [SubtaskEditor.expenseTotals].
  final double? expenseTotal;
  final String expenseCurrency;

  /// Non-null turns the money sign into an ADD affordance — see
  /// [SubtaskEditor.onAddExpense].
  final VoidCallback? onAddExpense;

  @override
  State<_SubtaskTile> createState() => _SubtaskTileState();
}

class _SubtaskTileState extends State<_SubtaskTile> {
  late final TextEditingController _ctrl;
  late final FocusNode _focus;
  bool _editing = false;

  @override
  void initState() {
    super.initState();
    _ctrl = TextEditingController(text: widget.subtask.title);
    _focus = FocusNode();
    _focus.addListener(() {
      if (!_focus.hasFocus && _editing) _commit();
    });
  }

  @override
  void dispose() {
    _ctrl.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _beginEdit() {
    HapticFeedback.selectionClick();
    _ctrl.text = widget.subtask.title;
    _ctrl.selection = TextSelection(
      baseOffset: 0,
      extentOffset: _ctrl.text.length,
    );
    setState(() => _editing = true);
    _focus.requestFocus();
  }

  void _commit() {
    setState(() => _editing = false);
    final next = _ctrl.text.trim();
    if (next == widget.subtask.title) return;
    widget.onTextChanged(next);
  }

  @override
  Widget build(BuildContext context) {
    final done = widget.subtask.done;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          // ── Checkbox ──────────────────────────────────────────────────────
          GestureDetector(
            key: ValueKey('subtask-toggle-${widget.subtask.id}'),
            onTap: () {
              HapticFeedback.selectionClick();
              widget.onToggle();
            },
            behavior: HitTestBehavior.opaque,
            child: AnimatedContainer(
              duration: AppMotion.fast,
              width: 22,
              height: 22,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: done
                    ? AppColors.success.withValues(alpha: 0.18)
                    : Colors.transparent,
                border: Border.all(
                  color: done ? AppColors.success : AppColors.borderDefault,
                  width: 1.5,
                ),
              ),
              child: done
                  ? const Icon(Icons.check, size: 13, color: AppColors.success)
                  : null,
            ),
          ),
          const SizedBox(width: AppSpacing.md),

          // ── Title (inline-editable) ────────────────────────────────────────
          Expanded(child: _buildText(done)),

          // ── Money sign: tappable "add an expense here" when the caller
          //    wired it, else the legacy display-only chip ──────────────────
          if (widget.onAddExpense != null)
            _buildMoneyAffordance()
          else if ((widget.expenseTotal ?? 0) > 0)
            _buildMoneyChip(),

          // ── Comments badge (only when the callback is wired) ────────────────
          if (widget.onOpenComments != null) _buildCommentBadge(),

          // ── Delete ─────────────────────────────────────────────────────────
          GestureDetector(
            key: ValueKey('subtask-delete-${widget.subtask.id}'),
            onTap: () {
              HapticFeedback.selectionClick();
              widget.onDelete();
            },
            behavior: HitTestBehavior.opaque,
            child: const Padding(
              padding: EdgeInsets.all(AppSpacing.xs),
              child: Icon(Icons.close, size: 16, color: AppColors.textMuted),
            ),
          ),
        ],
      ),
    );
  }

  /// The TAPPABLE money sign: tap to add an expense pinned to this sub-task,
  /// with the running total shown beside it once there is one. Rendered for
  /// every saved sub-task, with or without money — mirroring exactly how
  /// [_buildCommentBadge] renders with or without comments, because both are
  /// "open the thing for this row" affordances rather than status readouts.
  ///
  /// Tinted accent (not muted) once money exists, so a sub-task that has cost
  /// something still reads at a glance in a long checklist.
  Widget _buildMoneyAffordance() {
    final total = widget.expenseTotal ?? 0;
    final hasMoney = total > 0;
    final color = hasMoney ? AppColors.accent : AppColors.textMuted;
    return GestureDetector(
      key: ValueKey('subtask-expense-${widget.subtask.id}'),
      onTap: () {
        HapticFeedback.selectionClick();
        widget.onAddExpense?.call();
      },
      behavior: HitTestBehavior.opaque,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xs),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.attach_money_rounded, size: 14, color: color),
            if (hasMoney) ...[
              const SizedBox(width: 2),
              Text(
                fmtMoney(widget.expenseCurrency, total),
                style: AppText.caption.copyWith(color: color),
              ),
            ],
          ],
        ),
      ),
    );
  }

  /// The legacy DISPLAY-ONLY money chip: icon + formatted total, no tap
  /// handler. Still used by callers that pass [SubtaskEditor.expenseTotals]
  /// without wiring [SubtaskEditor.onAddExpense], so those surfaces keep
  /// reading money without gaining a write affordance they can't service.
  /// Mirrors [_buildCommentBadge]'s shape (icon + text, same padding/sizing).
  Widget _buildMoneyChip() {
    return Padding(
      key: ValueKey('subtask-expense-${widget.subtask.id}'),
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xs),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(
            Icons.attach_money_rounded,
            size: 14,
            color: AppColors.textMuted,
          ),
          const SizedBox(width: 2),
          Text(
            fmtMoney(widget.expenseCurrency, widget.expenseTotal!),
            style: AppText.caption.copyWith(color: AppColors.textMuted),
          ),
        ],
      ),
    );
  }

  /// A small 💬 icon (+count when > 0) between the title and delete affordance.
  /// Only built when [_SubtaskTile.onOpenComments] is non-null.
  Widget _buildCommentBadge() {
    return GestureDetector(
      key: ValueKey('subtask-comments-${widget.subtask.id}'),
      onTap: () {
        HapticFeedback.selectionClick();
        widget.onOpenComments?.call();
      },
      behavior: HitTestBehavior.opaque,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xs),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(
              Icons.chat_bubble_outline,
              size: 14,
              color: AppColors.textMuted,
            ),
            if (widget.commentCount > 0) ...[
              const SizedBox(width: 2),
              Text(
                '${widget.commentCount}',
                style: AppText.caption.copyWith(color: AppColors.textMuted),
              ),
            ],
          ],
        ),
      ),
    );
  }

  /// The title, plus the quiet created/completed line under it.
  ///
  /// A SECOND LINE rather than a fifth chip: this row already carries a
  /// checkbox, an inline-editable title, a money sign and a 💬 badge, and a
  /// date competing for that lane would win attention it doesn't deserve. It
  /// stays visible during inline edit so correcting a title doesn't make the
  /// whole checklist jump vertically.
  ///
  /// Legacy rows have neither timestamp and render nothing at all — see
  /// [TaskTimestampsLine].
  Widget _buildText(bool done) {
    return Column(
      // STRETCH, not start. This Column replaced a bare child of the row's
      // `Expanded`, which handed that child a TIGHT full-row width — and the
      // tap-to-edit GestureDetector below relies on it: under `start` it
      // shrinks to the ~127px the words occupy, so tapping the empty space
      // beside a short sub-task silently stops opening the editor.
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        _buildTitle(done),
        TaskTimestampsLine(
          keyPrefix: 'subtask-${widget.subtask.id}',
          createdAt: widget.subtask.createdAt,
          // Gated on `done` for the same reason the task's own line is: an
          // un-ticked row must never read "Done …", whatever a hand-edited
          // or half-migrated blob happens to carry.
          completedAt: done ? widget.subtask.completedAt : null,
          now: widget.now,
        ),
      ],
    );
  }

  Widget _buildTitle(bool done) {
    if (_editing) {
      return TextField(
        key: ValueKey('subtask-edit-${widget.subtask.id}'),
        controller: _ctrl,
        focusNode: _focus,
        style: AppText.body,
        cursorColor: AppColors.accent,
        maxLines: null,
        textInputAction: TextInputAction.done,
        onSubmitted: (_) => _commit(),
        decoration: const InputDecoration(
          isDense: true,
          contentPadding: EdgeInsets.symmetric(vertical: 4),
          border: UnderlineInputBorder(),
        ),
      );
    }
    return GestureDetector(
      key: ValueKey('subtask-text-${widget.subtask.id}'),
      behavior: HitTestBehavior.opaque,
      onTap: _beginEdit,
      // LinkText's tap recognizers only claim link spans, so a tap anywhere
      // else on the title still bubbles up to this GestureDetector's onTap.
      child: LinkText(
        widget.subtask.title,
        style: done
            ? AppText.body.copyWith(
                color: AppColors.textMuted,
                decoration: TextDecoration.lineThrough,
                decorationColor: AppColors.textMuted,
              )
            : AppText.body,
      ),
    );
  }
}

/// The trailing "add a sub-task" inline field. Submitting (keyboard done or the
/// + button) appends a new sub-task and clears the field, keeping focus so the
/// user can rattle off several in a row.
class _AddSubtaskField extends StatefulWidget {
  const _AddSubtaskField({required this.onAdd});

  final ValueChanged<String> onAdd;

  @override
  State<_AddSubtaskField> createState() => _AddSubtaskFieldState();
}

class _AddSubtaskFieldState extends State<_AddSubtaskField> {
  final _ctrl = TextEditingController();
  final _focus = FocusNode();

  @override
  void dispose() {
    _ctrl.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _submit() {
    final text = _ctrl.text.trim();
    if (text.isEmpty) return;
    widget.onAdd(text);
    _ctrl.clear();
    _focus.requestFocus(); // stay in the field for the next one
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
      child: Row(
        children: [
          const Icon(Icons.add, size: 18, color: AppColors.textMuted),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: TextField(
              key: const Key('subtask-add-field'),
              controller: _ctrl,
              focusNode: _focus,
              style: AppText.body,
              cursorColor: AppColors.accent,
              textInputAction: TextInputAction.done,
              onSubmitted: (_) => _submit(),
              decoration: const InputDecoration(
                isDense: true,
                hintText: 'Add a sub-task',
                contentPadding: EdgeInsets.symmetric(vertical: 4),
                border: InputBorder.none,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
