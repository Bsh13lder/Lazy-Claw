/// The quiet "when was this made / when was it finished" line, shared by a
/// task (in the detail sheet's header) and by every sub-task row.
///
/// One widget rather than two because the two surfaces must not drift into
/// different vocabularies — a task and its own checklist items sit inches
/// apart inside the same sheet, and "Created 3h ago" next to "Added 3h ago"
/// invites the reader to look for a distinction that isn't there.
///
/// It is deliberately NOT a chip. The sub-task row already carries a title, a
/// checkbox, a money sign and a 💬 badge; a fifth competing element would win
/// attention it doesn't deserve. This is muted caption text on a second line.
library;

import 'package:flutter/material.dart';

import '../../core/relative_time.dart';
import '../../ui/ui.dart';

class TaskTimestampsLine extends StatelessWidget {
  const TaskTimestampsLine({
    super.key,
    required this.keyPrefix,
    required this.createdAt,
    this.completedAt,
    this.now,
    this.padding = EdgeInsets.zero,
  });

  /// Prefix for the two part keys — `<prefix>-created` / `<prefix>-completed`
  /// — so a caller can address either half (`task-detail`, `subtask-<id>`).
  final String keyPrefix;

  /// ISO-8601 UTC, or null for a row that predates the field.
  final String? createdAt;

  /// ISO-8601 UTC of the moment the thing was finished. The CALLER decides
  /// whether it applies (a re-opened task still carries a stale
  /// `completed_at`); this widget renders whatever it is handed.
  final String? completedAt;

  /// Injectable clock, forwarded to [formatTimestampLabel] so a test pins
  /// "3h ago" instead of racing the wall clock.
  final DateTime? now;

  /// Applied only when there is something to show, so a legacy row costs
  /// exactly zero pixels rather than an empty gap.
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    final created = formatTimestampLabel(createdAt, now: now);
    final completed = formatTimestampLabel(completedAt, now: now);
    // Both absent = a legacy row. Render NOTHING — never "null", never a 1970
    // date, never an em-dash placeholder that reads as a bug.
    if (created == null && completed == null) return const SizedBox.shrink();

    final style = AppText.caption.copyWith(color: AppColors.textMuted);
    return Padding(
      padding: padding,
      // Wrap, not Row: two absolute dates ("Created Dec 4, 2025 · Done Jan 8,
      // 2026") overflow a narrow sub-task row, and an overflow stripe is
      // worse than a second line.
      child: Wrap(
        spacing: AppSpacing.sm,
        children: [
          if (created != null)
            Text(
              'Created $created',
              key: Key('$keyPrefix-created'),
              style: style,
            ),
          if (completed != null)
            Text(
              // The separator rides INSIDE this label so a Wrap that breaks
              // can't strand a lone middot at the end of a line.
              created == null ? 'Done $completed' : '· Done $completed',
              key: Key('$keyPrefix-completed'),
              style: style,
            ),
        ],
      ),
    );
  }
}
