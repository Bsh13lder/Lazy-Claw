/// Live agent-activity timeline for the Chat screen.
///
/// Renders one compact, expandable row per activity subject (specialist /
/// background task / browser) under a streaming or finished assistant bubble:
/// collapsed = "subject · N tools · running", expanded = the chronological
/// event log accumulated by the reducer. All styling from design tokens.
library;

import 'package:flutter/material.dart';
import '../../chat/chat_message.dart';
import '../../ui/ui.dart';

class ActivityTimeline extends StatelessWidget {
  const ActivityTimeline({super.key, required this.activities});

  final List<AgentActivity> activities;

  @override
  Widget build(BuildContext context) {
    if (activities.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: activities
          .map((a) => Padding(
                padding: const EdgeInsets.only(top: AppSpacing.xs),
                child: ActivityTimelineRow(activity: a),
              ))
          .toList(),
    );
  }
}

/// One subject row — tap to expand the event log when there is more than the
/// latest line to show.
class ActivityTimelineRow extends StatefulWidget {
  const ActivityTimelineRow({super.key, required this.activity});

  final AgentActivity activity;

  @override
  State<ActivityTimelineRow> createState() => _ActivityTimelineRowState();
}

class _ActivityTimelineRowState extends State<ActivityTimelineRow> {
  bool _expanded = false;

  bool get _expandable => widget.activity.events.length > 1;

  @override
  Widget build(BuildContext context) {
    final a = widget.activity;
    return GestureDetector(
      onTap: _expandable ? () => setState(() => _expanded = !_expanded) : null,
      child: AnimatedContainer(
        duration: AppMotion.fast,
        curve: AppMotion.curve,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.sm,
          vertical: AppSpacing.xs,
        ),
        decoration: BoxDecoration(
          color: AppColors.bgSurfaceElevated,
          borderRadius: AppRadii.rMd,
          border: Border.all(color: AppColors.borderSubtle),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            _SummaryLine(
              activity: a,
              expanded: _expanded,
              expandable: _expandable,
            ),
            if (_expanded) _EventLog(activity: a),
          ],
        ),
      ),
    );
  }
}

// ── Summary line ───────────────────────────────────────────────────────────

class _SummaryLine extends StatelessWidget {
  const _SummaryLine({
    required this.activity,
    required this.expanded,
    required this.expandable,
  });

  final AgentActivity activity;
  final bool expanded;
  final bool expandable;

  @override
  Widget build(BuildContext context) {
    final a = activity;
    final statusColor = a.failed
        ? AppColors.error
        : a.done
            ? AppColors.success
            : AppColors.accent;

    final Widget statusIcon;
    if (a.failed) {
      statusIcon =
          const Icon(Icons.error_outline, size: 12, color: AppColors.error);
    } else if (a.done) {
      statusIcon = const Icon(Icons.check_circle_outline,
          size: 12, color: AppColors.success);
    } else {
      statusIcon = const SizedBox(
        width: 12,
        height: 12,
        child: CircularProgressIndicator(
          strokeWidth: 1.5,
          color: AppColors.accent,
        ),
      );
    }

    final toolCount = a.toolsUsed.length;
    final parts = <String>[
      a.subject,
      if (toolCount > 0) '$toolCount ${toolCount == 1 ? 'tool' : 'tools'}',
      a.failed
          ? 'failed'
          : a.done
              ? 'done'
              : a.detail,
    ];

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(_kindIcon(a.kind), size: 12, color: AppColors.textMuted),
        const SizedBox(width: AppSpacing.xs),
        statusIcon,
        const SizedBox(width: AppSpacing.xs),
        Flexible(
          child: Text(
            parts.join(' · '),
            style: AppText.caption.copyWith(
              color: a.done || a.failed ? statusColor : AppColors.textSecondary,
              fontWeight: FontWeight.w600,
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        if (expandable) ...[
          const SizedBox(width: AppSpacing.xs),
          Icon(
            expanded ? Icons.expand_less : Icons.expand_more,
            size: 13,
            color: AppColors.textMuted,
          ),
        ],
      ],
    );
  }
}

// ── Expanded event log ─────────────────────────────────────────────────────

class _EventLog extends StatelessWidget {
  const _EventLog({required this.activity});

  final AgentActivity activity;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(
        top: AppSpacing.xs,
        left: AppSpacing.lg,
      ),
      child: ActivityEventList(
        events: activity.events,
        highlightLast: !activity.done && !activity.failed,
      ),
    );
  }
}

/// Plain chronological event-line list — shared with the bg task card.
class ActivityEventList extends StatelessWidget {
  const ActivityEventList({
    super.key,
    required this.events,
    this.highlightLast = false,
  });

  final List<String> events;

  /// Tint the newest line to read as "current step" while still running.
  final bool highlightLast;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        for (var i = 0; i < events.length; i++)
          Padding(
            padding: EdgeInsets.only(top: i == 0 ? 0 : 2),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Container(
                    width: 4,
                    height: 4,
                    decoration: BoxDecoration(
                      color: highlightLast && i == events.length - 1
                          ? AppColors.accent
                          : AppColors.textMuted,
                      shape: BoxShape.circle,
                    ),
                  ),
                ),
                const SizedBox(width: AppSpacing.sm),
                Flexible(
                  child: Text(
                    events[i],
                    style: AppText.caption.copyWith(
                      color: highlightLast && i == events.length - 1
                          ? AppColors.textPrimary
                          : AppColors.textSecondary,
                      fontWeight: FontWeight.w400,
                    ),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

IconData _kindIcon(String kind) {
  switch (kind) {
    case 'delegate':
      return Icons.call_split;
    case 'specialist':
      return Icons.smart_toy_outlined;
    case 'browser':
      return Icons.public;
    default: // 'bg'
      return Icons.settings_outlined;
  }
}
