import 'package:flutter/material.dart';

import '../../../models/note.dart';
import '../../../ui/ui.dart';

/// A read-oriented note card for Brain lists (search hits, pinned notes,
/// tag drill-downs). Unlike the Notes tab's NoteCard there is no swipe-delete
/// — Brain is the agent's knowledge surface, not a note manager.
///
/// Shows: pinned indicator, title, 2-line content preview, tag chips, and an
/// optional relevance [score] badge (semantic-search hits).
class BrainNoteCard extends StatelessWidget {
  const BrainNoteCard({
    super.key,
    required this.note,
    required this.onTap,
    this.score,
  });

  final Note note;
  final VoidCallback onTap;

  /// Cosine similarity from semantic search (null on bm25/substring hits).
  final double? score;

  String get _displayTitle =>
      note.title ??
      (note.contentPreview.isNotEmpty ? note.contentPreview : 'Untitled');

  @override
  Widget build(BuildContext context) {
    return LzCard(
      onTap: onTap,
      padding: const EdgeInsets.all(AppSpacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
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
                  _displayTitle,
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
              if (score != null) ...[
                const SizedBox(width: AppSpacing.sm),
                LzChip(
                  label: '${(score!.clamp(0.0, 1.0) * 100).round()}%',
                  dense: true,
                  color: AppColors.info,
                  selected: true,
                ),
              ],
            ],
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
            Wrap(
              spacing: AppSpacing.xs,
              runSpacing: AppSpacing.xs,
              children: note.tags
                  .take(4)
                  .map((t) => LzChip(label: t, dense: true))
                  .toList(),
            ),
          ],
        ],
      ),
    );
  }
}
