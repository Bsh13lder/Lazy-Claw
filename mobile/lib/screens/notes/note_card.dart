import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/note.dart';

/// A polished note card for the list view.
///
/// Shows: title, content preview (2 lines max), pinned indicator, tag chips,
/// and a compact [LzSyncBadge] when the note has un-pushed local edits.
/// Swipe-to-delete (end-to-start) fires [onDelete] after a [LzConfirm] dialog.
class NoteCard extends StatelessWidget {
  const NoteCard({
    super.key,
    required this.note,
    required this.pendingSync,
    required this.onTap,
    required this.onDelete,
  });

  final Note note;
  final bool pendingSync;
  final VoidCallback onTap;
  final VoidCallback onDelete;

  String get _displayTitle =>
      note.title ??
      (note.contentPreview.isNotEmpty ? note.contentPreview : 'Untitled');

  @override
  Widget build(BuildContext context) {
    return Dismissible(
      key: ValueKey(note.id),
      direction: DismissDirection.endToStart,
      background: const _DeleteBackground(),
      confirmDismiss: (_) => _confirmDelete(context),
      onDismissed: (_) => onDelete(),
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.xs + 2,
        ),
        child: LzCard(
          onTap: onTap,
          padding: const EdgeInsets.all(AppSpacing.lg),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _CardHeader(
                note: note,
                pendingSync: pendingSync,
                displayTitle: _displayTitle,
              ),
              if (note.title != null && note.contentPreview.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.xs + 2),
                Text(
                  note.contentPreview,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: AppText.body.copyWith(
                    color: AppColors.textSecondary,
                    height: 1.4,
                  ),
                ),
              ],
              if (note.tags.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.md),
                _TagRow(tags: note.tags),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Future<bool> _confirmDelete(BuildContext context) {
    final label = note.title ??
        (note.contentPreview.isNotEmpty ? note.contentPreview : 'This note');
    return LzConfirm.show(
      context,
      title: 'Delete note?',
      message: label,
      confirmLabel: 'Delete',
      cancelLabel: 'Cancel',
      danger: true,
    );
  }
}

// ── Card header row ──────────────────────────────────────────────────────────

class _CardHeader extends StatelessWidget {
  const _CardHeader({
    required this.note,
    required this.pendingSync,
    required this.displayTitle,
  });

  final Note note;
  final bool pendingSync;
  final String displayTitle;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (note.pinned)
          const Padding(
            padding: EdgeInsets.only(top: 2, right: AppSpacing.xs),
            child: Icon(
              Icons.push_pin_rounded,
              size: 14,
              color: AppColors.accent,
            ),
          ),
        Expanded(
          child: Text(
            displayTitle,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: note.title != null
                ? AppText.title
                : AppText.body.copyWith(
                    color: AppColors.textSecondary,
                    fontStyle: FontStyle.italic,
                  ),
          ),
        ),
        if (pendingSync) ...[
          const SizedBox(width: AppSpacing.sm),
          const LzSyncBadge(state: LzSyncState.offline, compact: true),
        ],
      ],
    );
  }
}

// ── Tag chips row ─────────────────────────────────────────────────────────────

class _TagRow extends StatelessWidget {
  const _TagRow({required this.tags});

  final List<String> tags;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: AppSpacing.xs,
      runSpacing: AppSpacing.xs,
      // Cap at 4 visible tags to keep the card compact.
      children: tags.take(4).map((t) => LzChip(label: t, dense: true)).toList(),
    );
  }
}

// ── Swipe-delete red background ───────────────────────────────────────────────

class _DeleteBackground extends StatelessWidget {
  const _DeleteBackground();

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.xs + 2,
      ),
      decoration: BoxDecoration(
        color: AppColors.error.withValues(alpha: 0.85),
        borderRadius: AppRadii.rLg,
      ),
      alignment: Alignment.centerRight,
      padding: const EdgeInsets.only(right: AppSpacing.xl),
      child: const Icon(
        Icons.delete_outline_rounded,
        color: Colors.white,
        size: 22,
      ),
    );
  }
}
