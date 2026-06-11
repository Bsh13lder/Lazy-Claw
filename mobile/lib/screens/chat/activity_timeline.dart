/// Live agent-activity timeline for the Chat screen.
///
/// Renders one compact, expandable row per activity subject (specialist /
/// background task / browser) under a streaming or finished assistant bubble:
/// collapsed = "subject · N tools · running", expanded = the chronological
/// event log accumulated by the reducer. All styling from design tokens.
library;

import 'dart:async';

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

  /// A running row that received no updates for [_stallAfter] is likely a
  /// stale replayed frame — swap its spinner for a muted schedule icon so it
  /// doesn't read as live work forever.
  bool _stalled = false;
  Timer? _stallTimer;

  static const Duration _stallAfter = Duration(minutes: 3);

  bool get _expandable => widget.activity.events.length > 1;

  bool get _running => !widget.activity.done && !widget.activity.failed;

  @override
  void initState() {
    super.initState();
    if (_running) _armStallTimer();
  }

  @override
  void didUpdateWidget(covariant ActivityTimelineRow oldWidget) {
    super.didUpdateWidget(oldWidget);
    final a = widget.activity;
    final o = oldWidget.activity;
    final contentChanged = a.events.length != o.events.length ||
        a.detail != o.detail ||
        a.currentTool != o.currentTool ||
        a.done != o.done ||
        a.failed != o.failed;
    if (!contentChanged) return;
    // Fresh content — the row is alive again; restart the stall clock.
    _stallTimer?.cancel();
    _stalled = false;
    if (_running) _armStallTimer();
  }

  void _armStallTimer() {
    _stallTimer = Timer(_stallAfter, () {
      if (mounted) setState(() => _stalled = true);
    });
  }

  @override
  void dispose() {
    _stallTimer?.cancel();
    super.dispose();
  }

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
              stalled: _stalled,
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
    required this.stalled,
  });

  final AgentActivity activity;
  final bool expanded;
  final bool expandable;

  /// Running row with no updates for too long — render a muted schedule
  /// icon instead of an ever-spinning progress indicator.
  final bool stalled;

  @override
  Widget build(BuildContext context) {
    final a = activity;
    final running = !a.done && !a.failed;
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
    } else if (stalled) {
      statusIcon =
          const Icon(Icons.schedule, size: 12, color: AppColors.textMuted);
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

    final baseStyle = AppText.caption.copyWith(
      color: a.done || a.failed ? statusColor : AppColors.textSecondary,
      fontWeight: FontWeight.w600,
    );

    final toolCount = a.toolsUsed.length;
    final spans = <TextSpan>[TextSpan(text: a.subject)];
    if (running) {
      if (toolCount > 0) {
        spans.add(TextSpan(
            text: ' · $toolCount ${toolCount == 1 ? 'tool' : 'tools'}'));
      }
      if (a.currentTool != null) {
        // Live tool segment — accent-tinted so the eye lands on it.
        spans.add(TextSpan(
          text: ' · using ${a.currentTool}…',
          style: baseStyle.copyWith(color: AppColors.accent),
        ));
      } else {
        spans.add(TextSpan(text: ' · ${a.detail}'));
      }
    } else {
      if (toolCount > 0) {
        spans.add(TextSpan(text: ' · ${_toolSummary(a.toolsUsed)}'));
      }
      spans.add(TextSpan(text: a.failed ? ' · failed' : ' · done'));
    }

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(_kindIcon(a.kind), size: 12, color: AppColors.textMuted),
        const SizedBox(width: AppSpacing.xs),
        statusIcon,
        const SizedBox(width: AppSpacing.xs),
        Flexible(
          child: Text.rich(
            TextSpan(style: baseStyle, children: spans),
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

/// Up to five tool names joined with ', ', plus a ' +N' overflow suffix —
/// replaces the bare "N tools" count once a row settles.
String _toolSummary(List<String> tools) {
  const cap = 5;
  if (tools.length <= cap) return tools.join(', ');
  return '${tools.take(cap).join(', ')} +${tools.length - cap}';
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
