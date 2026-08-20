/// Polished tool-activity chip for the Chat screen.
///
/// Shows the tool's display name (registry-id fallback) with a status glyph:
/// spinner while running, check when done, error icon on failure, muted
/// schedule icon for interrupted/unknown. A running chip that receives no
/// update for 3 minutes swaps its spinner for the interrupted look (mirrors
/// ActivityTimelineRow's stall guard) so a lost result can't spin forever.
/// Tapping ANY chip opens the tool detail bottom sheet. All styling from
/// design tokens.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import '../../chat/chat_message.dart';
import '../../ui/ui.dart';
import 'tool_detail_sheet.dart';

/// Chip label: display name, plus the dispatch target for `agent`/legacy
/// `delegate` calls. Three parallel dispatches rendered as identical
/// "Dispatch Agent" chips (2026-08-20) were indistinguishable from one
/// eternal chip — the target gives each dispatch an identity.
String _chipLabel(ToolActivity t) {
  final base = t.displayName ?? t.name;
  final target = t.args['agent_type'] ?? t.args['specialist'];
  if (target is String && target.trim().isNotEmpty) {
    return '$base · ${target.trim()}';
  }
  return base;
}

class ToolChip extends StatefulWidget {
  const ToolChip({super.key, required this.activity});
  final ToolActivity activity;

  @override
  State<ToolChip> createState() => _ToolChipState();
}

class _ToolChipState extends State<ToolChip> {
  /// A running chip with no update for [_stallAfter] is likely a dropped or
  /// replayed frame — render it interrupted instead of live-forever.
  bool _stalled = false;
  Timer? _stallTimer;

  static const Duration _stallAfter = Duration(minutes: 3);

  bool get _running => widget.activity.status == ToolStatus.running;

  @override
  void initState() {
    super.initState();
    if (_running) _armStallTimer();
  }

  @override
  void didUpdateWidget(covariant ToolChip oldWidget) {
    super.didUpdateWidget(oldWidget);
    final a = widget.activity;
    final o = oldWidget.activity;
    final contentChanged = a.status != o.status ||
        a.resultPreview != o.resultPreview ||
        a.name != o.name;
    if (!contentChanged) return;
    // Fresh content — the chip is alive again; restart the stall clock.
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

  /// Status used for rendering: a stalled running chip takes the interrupted
  /// look without mutating the model (a late result can still settle it).
  ToolStatus get _effectiveStatus =>
      _running && _stalled ? ToolStatus.interrupted : widget.activity.status;

  @override
  Widget build(BuildContext context) {
    final t = widget.activity;
    final status = _effectiveStatus;
    final fg = toolStatusColor(status);
    final settledTint =
        status == ToolStatus.done || status == ToolStatus.error;
    final bgColor = settledTint
        ? fg.withValues(alpha: 0.12)
        : AppColors.bgSurfaceElevated;
    final borderColor = settledTint
        ? fg.withValues(alpha: 0.30)
        : AppColors.borderSubtle;

    return GestureDetector(
      // Always tappable — running, settled and unknown chips all open the
      // detail sheet (name, status, arguments, result).
      onTap: () => showToolDetailSheet(
        context,
        widget.activity,
        effectiveStatus: _effectiveStatus,
      ),
      child: AnimatedContainer(
        duration: AppMotion.fast,
        curve: AppMotion.curve,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.sm + 2,
          vertical: AppSpacing.xs + 1,
        ),
        decoration: BoxDecoration(
          color: bgColor,
          borderRadius: AppRadii.rPill,
          border: Border.all(color: borderColor),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            ToolStatusGlyph(status: status, color: fg),
            const SizedBox(width: 5),
            Flexible(
              child: Text(
                _chipLabel(t),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: AppText.caption.copyWith(
                  color: fg,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
