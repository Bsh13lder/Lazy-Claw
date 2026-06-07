import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/subtask.dart';

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
  });

  final List<Subtask> subtasks;
  final ValueChanged<List<Subtask>> onChanged;

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
    onChanged([for (final s in subtasks) if (s.id != id) s]);
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
        for (final s in subtasks)
          _SubtaskTile(
            key: ValueKey('subtask-tile-${s.id}'),
            subtask: s,
            onToggle: () => _toggle(s.id),
            onTextChanged: (t) => _editText(s.id, t),
            onDelete: () => _delete(s.id),
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
  });

  final Subtask subtask;
  final VoidCallback onToggle;
  final ValueChanged<String> onTextChanged;
  final VoidCallback onDelete;

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
    _ctrl.selection =
        TextSelection(baseOffset: 0, extentOffset: _ctrl.text.length);
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
      child: Text(
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
