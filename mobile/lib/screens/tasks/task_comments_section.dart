import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:lazyclaw_mobile/widgets/link_text.dart';

import '../../models/comment.dart';

/// The task-level comment thread + composer, mounted in [TaskDetailSheet]
/// after the Subtasks block.
///
/// Stateless like `SubtaskEditor` — the parent owns [comments] and is handed
/// nothing back except via [onAdd]/[onDelete]; the composer's ephemeral text
/// is scoped to the private [_CommentInputRow] below.
///
/// Only renders comments where `subtaskId == null` (task-level); comments
/// tagged to a sub-task surface in [showSubtaskCommentsSheet] instead.
class TaskCommentsSection extends StatelessWidget {
  const TaskCommentsSection({
    super.key,
    required this.comments,
    required this.onAdd,
    required this.onDelete,
    this.onAddLink,
  });

  final List<TaskComment> comments;
  final ValueChanged<String> onAdd;
  final ValueChanged<String> onDelete;
  final Future<String?> Function()? onAddLink;

  @override
  Widget build(BuildContext context) {
    final taskLevel = [
      for (final c in comments)
        if (c.subtaskId == null) c,
    ];
    return _CommentsBody(
      comments: taskLevel,
      onAdd: onAdd,
      onDelete: onDelete,
      onAddLink: onAddLink,
    );
  }
}

/// Open the sub-task comment thread as a bottom sheet.
///
/// [comments] must already be filtered to the sub-task in question — this
/// helper renders exactly what it's given (no `subtaskId` re-filtering, unlike
/// [TaskCommentsSection]'s task-level filter).
///
/// [onAdd] returns the [TaskComment] that was actually persisted (e.g.
/// `TasksNotifier.addComment`'s return) — see [_SubtaskCommentsSheetBody] for
/// why this can't be a fire-and-forget `ValueChanged<String>` like
/// [TaskCommentsSection.onAdd].
///
/// Live-update choice: this sheet is presented via `showModalBottomSheet` on
/// its own route, disconnected from the detail sheet's `ref.watch(tasksProvider)`
/// rebuild — so a fresh comment landing in the real (persisted) cache would
/// never visibly appear here without extra plumbing. Rather than pull Riverpod
/// into this otherwise provider-free widget, the sheet keeps a small local
/// optimistic copy of [comments] (see [_SubtaskCommentsSheetBody]): every add
/// calls the real [onAdd] for persistence AND appends its RETURNED comment
/// (not a locally-guessed one) to the local copy, so a later delete in the
/// same session acts on a real, persisted id. Every delete calls the real
/// [onDelete] AND removes locally, so the thread visibly appends/removes
/// without waiting on a provider round-trip. This mirrors the same
/// optimistic-update pattern `TasksNotifier` already uses for the persisted
/// store.
Future<void> showSubtaskCommentsSheet(
  BuildContext context, {
  required String subtaskTitle,
  required List<TaskComment> comments,
  required Future<TaskComment?> Function(String text) onAdd,
  required ValueChanged<String> onDelete,
}) {
  return LzBottomSheet.show<void>(
    context,
    title: subtaskTitle,
    builder: (_) => _SubtaskCommentsSheetBody(
      comments: comments,
      onAdd: onAdd,
      onDelete: onDelete,
    ),
  );
}

/// Keeps its own optimistic copy of the sub-task's comments so the sheet
/// (a separate route, see [showSubtaskCommentsSheet]) visibly updates the
/// instant a comment is added or deleted, without depending on a provider.
///
/// [onAdd] MUST return the comment that was actually persisted (its real,
/// server/DAO-minted id) — synthesizing a separate id locally here would
/// silently diverge from what `onDelete` needs to target: a delete fired
/// against a locally-guessed id that no persisted comment has is a no-op
/// against the real store (plus a bogus dirty+outbox entry), while the UI
/// here would optimistically show it gone — until the sheet is reopened and
/// re-derives from the real (unmodified) store, resurrecting the "deleted"
/// comment. See the fixed incident this class is named for in git history.
class _SubtaskCommentsSheetBody extends StatefulWidget {
  const _SubtaskCommentsSheetBody({
    required this.comments,
    required this.onAdd,
    required this.onDelete,
  });

  final List<TaskComment> comments;
  final Future<TaskComment?> Function(String text) onAdd;
  final ValueChanged<String> onDelete;

  @override
  State<_SubtaskCommentsSheetBody> createState() =>
      _SubtaskCommentsSheetBodyState();
}

class _SubtaskCommentsSheetBodyState extends State<_SubtaskCommentsSheetBody> {
  late List<TaskComment> _comments;

  @override
  void initState() {
    super.initState();
    _comments = List.of(widget.comments);
  }

  Future<void> _add(String text) async {
    // Await the REAL persisted comment (real id) before appending locally —
    // never synthesize a separate id here (see class doc).
    final created = await widget.onAdd(text);
    if (!mounted || created == null) return;
    setState(() {
      _comments = [..._comments, created];
    });
  }

  void _delete(String id) {
    widget.onDelete(id);
    setState(() {
      _comments = [
        for (final c in _comments)
          if (c.id != id) c,
      ];
    });
  }

  @override
  Widget build(BuildContext context) =>
      _CommentsBody(comments: _comments, onAdd: _add, onDelete: _delete);
}

/// The shared thread-list + composer body, shown as-is (no filtering) — the
/// filtering decision belongs to the caller ([TaskCommentsSection] filters to
/// task-level; [showSubtaskCommentsSheet] is handed an already-filtered list).
class _CommentsBody extends StatelessWidget {
  const _CommentsBody({
    required this.comments,
    required this.onAdd,
    required this.onDelete,
    this.onAddLink,
  });

  final List<TaskComment> comments;
  final ValueChanged<String> onAdd;
  final ValueChanged<String> onDelete;
  final Future<String?> Function()? onAddLink;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _CommentThread(comments: comments, onDelete: onDelete),
        if (comments.isNotEmpty) const SizedBox(height: AppSpacing.sm),
        _CommentInputRow(onAdd: onAdd, onAddLink: onAddLink),
      ],
    );
  }
}

/// Renders each comment oldest-first: author label + relative timestamp on
/// one line, then the comment text through [LinkText]. Long-press → delete
/// confirm (via [LzConfirm], which already pops the dialog's OWN context —
/// see the documented confirm-dialog-over-sheet gotcha).
class _CommentThread extends StatelessWidget {
  const _CommentThread({required this.comments, required this.onDelete});

  final List<TaskComment> comments;
  final ValueChanged<String> onDelete;

  Future<void> _confirmDelete(BuildContext context, TaskComment c) async {
    final confirmed = await LzConfirm.show(
      context,
      title: 'Delete comment?',
      message: c.text,
      confirmLabel: 'Delete',
      danger: true,
    );
    if (confirmed) onDelete(c.id);
  }

  @override
  Widget build(BuildContext context) {
    if (comments.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final c in comments)
          Padding(
            key: ValueKey('comment-${c.id}'),
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
            child: GestureDetector(
              onLongPress: () => _confirmDelete(context, c),
              behavior: HitTestBehavior.opaque,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Text(
                        c.author == 'agent' ? 'Lazy 🤖' : 'You',
                        style: AppText.label,
                      ),
                      const SizedBox(width: AppSpacing.sm),
                      Text(
                        _relativeTime(c.ts),
                        style: AppText.caption.copyWith(
                          color: AppColors.textMuted,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 2),
                  LinkText(c.text, style: AppText.body),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

/// The composer row: a text field, an optional "add link" icon (only when
/// [onAddLink] is supplied) that splices the dialog result at the cursor, and
/// a send icon. Submitting (keyboard or the send icon) fires [onAdd] and
/// clears the field.
class _CommentInputRow extends StatefulWidget {
  const _CommentInputRow({required this.onAdd, this.onAddLink});

  final ValueChanged<String> onAdd;
  final Future<String?> Function()? onAddLink;

  @override
  State<_CommentInputRow> createState() => _CommentInputRowState();
}

class _CommentInputRowState extends State<_CommentInputRow> {
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
  }

  /// Opens the "Add link" dialog and splices the returned `[text](url)`
  /// markdown into the field at the current cursor position — mirrors
  /// `TaskDetailSheet._addLink`'s notes-field behavior. Falls back to
  /// appending at the end when the selection is invalid (field not yet
  /// focused, so the controller's selection is still the default
  /// collapsed-at--1).
  Future<void> _addLink() async {
    final onAddLink = widget.onAddLink;
    if (onAddLink == null) return;
    final result = await onAddLink();
    if (result == null || !mounted) return;
    final text = _ctrl.text;
    final selection = _ctrl.selection;
    final start = selection.isValid ? selection.start : text.length;
    final end = selection.isValid ? selection.end : text.length;
    final nextText = text.replaceRange(start, end, result);
    setState(() {
      _ctrl.value = TextEditingValue(
        text: nextText,
        selection: TextSelection.collapsed(offset: start + result.length),
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Expanded(
          child: LzTextField(
            controller: _ctrl,
            fieldKey: const Key('comment-input'),
            focusNode: _focus,
            hint: 'Add a comment',
            minLines: 1,
            maxLines: 4,
            maxLength: kMaxCommentChars,
            textInputAction: TextInputAction.send,
            onSubmitted: (_) => _submit(),
          ),
        ),
        if (widget.onAddLink != null)
          IconButton(
            key: const Key('comment-add-link'),
            icon: const Icon(Icons.add_link),
            color: AppColors.textMuted,
            tooltip: 'Add link',
            onPressed: _addLink,
          ),
        IconButton(
          key: const Key('comment-send'),
          icon: const Icon(Icons.send),
          color: AppColors.accent,
          tooltip: 'Send',
          onPressed: _submit,
        ),
      ],
    );
  }
}

/// A tiny relative-time label ("just now" / "5m ago" / …). The codebase's
/// convention is a small private helper per screen rather than one shared
/// utility (see `activity_row.dart`, `audit_screen.dart`,
/// `notifications_center_screen.dart` for precedent) — this mirrors that.
String _relativeTime(String iso) {
  final dt = DateTime.tryParse(iso)?.toLocal();
  if (dt == null) return '';
  final diff = DateTime.now().difference(dt);
  if (diff.inSeconds < 60) return 'just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  if (diff.inDays < 7) return '${diff.inDays}d ago';
  return '${dt.day}/${dt.month}/${dt.year}';
}
