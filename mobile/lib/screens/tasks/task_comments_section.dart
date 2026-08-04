import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/core/relative_time.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:lazyclaw_mobile/widgets/link_text.dart';

import '../../models/comment.dart';

/// Key of the task-level comments affordance in the detail sheet's header.
/// Shared with the tests so a rename can't silently orphan them.
const Key kTaskCommentsBadgeKey = Key('task-detail-comments');

/// Only the TASK-level comments of [comments] (those with no `subtaskId`),
/// in their original order.
///
/// Sub-task comments have their own thread, reached from the sub-task row's
/// 💬 badge; mixing them into the task's thread would show the same comment
/// twice and make the header count disagree with what the popup lists.
///
/// Pure and allocation-only — [comments] is never mutated.
List<TaskComment> taskLevelComments(List<TaskComment> comments) => [
  for (final c in comments)
    if (c.subtaskId == null) c,
];

/// The task-level comments affordance shown beside NOTES at the TOP of the
/// detail sheet.
///
/// WHY it replaced a bottom section: the thread + composer used to be pinned
/// below sub-tasks, i.e. at the very end of the longest sheet in the app —
/// reading or leaving a comment meant scrolling the entire form past every
/// control you didn't want. As an icon it costs one row and is reachable the
/// moment the sheet opens.
class TaskCommentsBadge extends StatelessWidget {
  const TaskCommentsBadge({
    super.key,
    required this.count,
    required this.onTap,
    this.fieldKey = kTaskCommentsBadgeKey,
  });

  final int count;
  final VoidCallback onTap;

  /// The [Key] applied to the tappable [InkWell] itself (distinct from [key])
  /// — mirrors `ProjectChip.fieldKey` / `TaskTagsChip.fieldKey`.
  final Key fieldKey;

  @override
  Widget build(BuildContext context) {
    final has = count > 0;
    return Material(
      color: Colors.transparent,
      borderRadius: AppRadii.rPill,
      child: InkWell(
        key: fieldKey,
        onTap: onTap,
        borderRadius: AppRadii.rPill,
        child: Container(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md,
            vertical: AppSpacing.xs,
          ),
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rPill,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                Icons.chat_bubble_outline,
                size: 14,
                color: has ? AppColors.accent : AppColors.textMuted,
              ),
              const SizedBox(width: AppSpacing.xs),
              Text(
                // A bare "0" reads as broken; the word reads as an offer.
                has ? '$count' : 'Comments',
                style: AppText.caption.copyWith(
                  color: has ? AppColors.textPrimary : AppColors.textMuted,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Open a comment thread as a bottom sheet.
///
/// ONE sheet serves both scopes — the task's own thread (opened from
/// [TaskCommentsBadge]) and a single sub-task's (opened from the sub-task
/// row's 💬 badge). They differ only in [title] and in which comments the
/// CALLER hands over: this helper renders exactly what it is given and never
/// re-filters (use [taskLevelComments] for the task scope).
///
/// [onAdd] returns the [TaskComment] that was actually persisted (e.g.
/// `TasksNotifier.addComment`'s return) — see [_CommentsSheetBody] for why
/// this can't be a fire-and-forget `ValueChanged<String>`.
///
/// Live-update choice: this sheet is presented via `showModalBottomSheet` on
/// its own route, disconnected from the detail sheet's `ref.watch(tasksProvider)`
/// rebuild — so a fresh comment landing in the real (persisted) cache would
/// never visibly appear here without extra plumbing. Rather than pull Riverpod
/// into this otherwise provider-free widget, the sheet keeps a small local
/// optimistic copy of [comments] (see [_CommentsSheetBody]): every add
/// calls the real [onAdd] for persistence AND appends its RETURNED comment
/// (not a locally-guessed one) to the local copy, so a later delete in the
/// same session acts on a real, persisted id. Every delete calls the real
/// [onDelete] AND removes locally, so the thread visibly appends/removes
/// without waiting on a provider round-trip. This mirrors the same
/// optimistic-update pattern `TasksNotifier` already uses for the persisted
/// store.
Future<void> showCommentsSheet(
  BuildContext context, {
  required String title,
  required List<TaskComment> comments,
  required Future<TaskComment?> Function(String text) onAdd,
  required ValueChanged<String> onDelete,
  Future<String?> Function()? onAddLink,
}) {
  return LzBottomSheet.show<void>(
    context,
    title: title,
    builder: (_) => _CommentsSheetBody(
      comments: comments,
      onAdd: onAdd,
      onDelete: onDelete,
      onAddLink: onAddLink,
    ),
  );
}

/// Keeps its own optimistic copy of the thread's comments so the sheet
/// (a separate route, see [showCommentsSheet]) visibly updates the
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
class _CommentsSheetBody extends StatefulWidget {
  const _CommentsSheetBody({
    required this.comments,
    required this.onAdd,
    required this.onDelete,
    this.onAddLink,
  });

  final List<TaskComment> comments;
  final Future<TaskComment?> Function(String text) onAdd;
  final ValueChanged<String> onDelete;
  final Future<String?> Function()? onAddLink;

  @override
  State<_CommentsSheetBody> createState() => _CommentsSheetBodyState();
}

class _CommentsSheetBodyState extends State<_CommentsSheetBody> {
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
  Widget build(BuildContext context) => _CommentsBody(
    comments: _comments,
    onAdd: _add,
    onDelete: _delete,
    onAddLink: widget.onAddLink,
  );
}

/// The shared thread-list + composer body, shown as-is (no filtering) — the
/// filtering decision belongs to the caller (the task scope passes
/// [taskLevelComments]; a sub-task scope passes its own already-filtered
/// list).
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
                        // Same helper the task's and each sub-task's
                        // created/completed line uses — three spellings of
                        // "when" inside one sheet read as three different
                        // things. Empty (not "—") when the ts is unusable.
                        formatTimestampLabel(c.ts) ?? '',
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
  ///
  /// Clamped at [kMaxCommentChars]: this splice bypasses the field's
  /// `maxLength` input formatter (that only guards typed input, not
  /// programmatic `TextEditingController.value` assignment), so an insert
  /// that would push the field over the cap is refused outright — text left
  /// untouched, refusal surfaced via a snackbar rather than silently
  /// truncating the just-pasted link.
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
    if (nextText.length > kMaxCommentChars) {
      ScaffoldMessenger.maybeOf(context)?.showSnackBar(
        const SnackBar(content: Text('Comment limit is 2000 characters.')),
      );
      return;
    }
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
            // Enforcement stays (the field still hard-caps at 2000 chars) —
            // only the default "n / 2000" counter row is suppressed; it's
            // visual noise under this tight composer Row.
            buildCounter:
                (
                  _, {
                  required currentLength,
                  required isFocused,
                  required maxLength,
                }) => null,
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
