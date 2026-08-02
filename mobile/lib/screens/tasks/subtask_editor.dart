import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:lazyclaw_mobile/widgets/link_text.dart';

import '../../models/subtask.dart';
import 'task_sort.dart';

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
  });

  final List<Subtask> subtasks;
  final ValueChanged<List<Subtask>> onChanged;

  /// Comment counts keyed by sub-task id, used to show a `+count` badge next
  /// to the comment icon. Missing/absent ids are treated as zero.
  final Map<String, int> commentCounts;

  /// When supplied, each tile shows a small comment icon (+count when > 0)
  /// between the title and the delete affordance; tapping it invokes this
  /// with the tile's sub-task id. Null (the default) keeps tiles badge-free —
  /// existing callers that don't pass it compile and render unchanged.
  final ValueChanged<String>? onOpenComments;

  void _toggle(String id) {
    onChanged([
      for (final s in subtasks)
        if (s.id == id) s.copyWith(done: !s.done) else s,
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
      Subtask(id: newSubtaskId(), title: trimmed, done: false),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final s in sortSubtasksDoneLast(subtasks))
          _SubtaskTile(
            key: ValueKey('subtask-tile-${s.id}'),
            subtask: s,
            onToggle: () => _toggle(s.id),
            onTextChanged: (t) => _editText(s.id, t),
            onDelete: () => _delete(s.id),
            commentCount: commentCounts[s.id] ?? 0,
            onOpenComments: onOpenComments == null
                ? null
                : () => onOpenComments!(s.id),
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
  });

  final Subtask subtask;
  final VoidCallback onToggle;
  final ValueChanged<String> onTextChanged;
  final VoidCallback onDelete;
  final int commentCount;
  final VoidCallback? onOpenComments;

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

  Widget _buildText(bool done) {
    if (_editing) {
      return TextField(
        key: ValueKey('subtask-edit-${widget.subtask.id}'),
        controller: _ctrl,
        focusNode: _focus,
        style: AppText.body,
        cursorColor: AppColors.accent,
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
