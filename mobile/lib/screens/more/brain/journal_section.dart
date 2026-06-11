import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';

import '../../../models/note.dart';
import '../../../ui/ui.dart';
import 'brain_markdown.dart';

/// "Today + recent journal" section — one row per journal day, newest first.
/// Tapping a row opens a read-only markdown sheet with the day's content.
class JournalSection extends StatelessWidget {
  const JournalSection({super.key, required this.notes});

  final List<Note> notes;

  @override
  Widget build(BuildContext context) {
    return LzSection(
      title: 'Journal',
      child: notes.isEmpty
          ? Text(
              'No journal pages yet — the agent writes one per day.',
              style: AppText.body.copyWith(color: AppColors.textMuted),
            )
          : Column(
              children: [
                for (var i = 0; i < notes.length; i++) ...[
                  if (i > 0) const SizedBox(height: AppSpacing.sm),
                  _JournalDayRow(note: notes[i]),
                ],
              ],
            ),
    );
  }
}

// ── Day row ─────────────────────────────────────────────────────────────────

class _JournalDayRow extends StatelessWidget {
  const _JournalDayRow({required this.note});

  final Note note;

  @override
  Widget build(BuildContext context) {
    final dayLabel = _dayLabel(note.createdAt);
    return LzCard(
      onTap: () => _openDaySheet(context),
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      child: Row(
        children: [
          Container(
            width: 38,
            height: 38,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: AppColors.bgSurfaceElevated,
              borderRadius: AppRadii.rMd,
              border: Border.all(color: AppColors.borderSubtle),
            ),
            child: const Icon(
              Icons.calendar_today_outlined,
              size: 18,
              color: AppColors.accent,
            ),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(dayLabel, style: AppText.label),
                if (note.contentPreview.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(
                    note.contentPreview,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style:
                        AppText.caption.copyWith(color: AppColors.textMuted),
                  ),
                ],
              ],
            ),
          ),
          const Icon(
            Icons.chevron_right,
            size: 18,
            color: AppColors.textMuted,
          ),
        ],
      ),
    );
  }

  void _openDaySheet(BuildContext context) {
    LzBottomSheet.show<void>(
      context,
      title: note.title ?? _dayLabel(note.createdAt),
      builder: (ctx) => SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            MarkdownBody(
              data: note.content.isNotEmpty
                  ? note.content
                  : '_This day is empty so far._',
              selectable: true,
              styleSheet: brainMarkdownStyle(),
            ),
            const SizedBox(height: AppSpacing.lg),
          ],
        ),
      ),
    );
  }

  /// "Today" / "Yesterday" / "Jun 8" from the note's created_at timestamp.
  String _dayLabel(String iso) {
    final dt = DateTime.tryParse(iso.replaceFirst(' ', 'T'))?.toLocal();
    if (dt == null) return note.title ?? 'Journal';
    final now = DateTime.now();
    final day = DateTime(dt.year, dt.month, dt.day);
    final today = DateTime(now.year, now.month, now.day);
    final diff = today.difference(day).inDays;
    if (diff == 0) return 'Today';
    if (diff == 1) return 'Yesterday';
    const months = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ];
    final label = '${months[day.month - 1]} ${day.day}';
    return day.year == today.year ? label : '$label, ${day.year}';
  }
}
