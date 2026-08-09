/// Bottom detail sheet for a tool-activity chip.
///
/// Shows the display name (sheet title), the raw registry name (small,
/// muted), the status, the call arguments as pretty-printed JSON
/// (scrollable, selectable) and the result text when present. Opened by
/// [ToolChip]; degrades gracefully when fields are empty. All styling from
/// design tokens; presented via the kit's [LzBottomSheet].
library;

import 'dart:convert';

import 'package:flutter/material.dart';
import '../../chat/chat_message.dart';
import '../../ui/ui.dart';

/// Foreground color for a tool status: settled-good is success, failed is
/// error, everything in-flight or indeterminate stays muted.
Color toolStatusColor(ToolStatus status) {
  switch (status) {
    case ToolStatus.done:
      return AppColors.success;
    case ToolStatus.error:
      return AppColors.error;
    case ToolStatus.running:
    case ToolStatus.interrupted:
    case ToolStatus.unknown:
      return AppColors.textMuted;
  }
}

/// Short human label for a tool status.
String toolStatusLabel(ToolStatus status) {
  switch (status) {
    case ToolStatus.running:
      return 'Running';
    case ToolStatus.done:
      return 'Done';
    case ToolStatus.error:
      return 'Failed';
    case ToolStatus.interrupted:
      return 'Interrupted';
    case ToolStatus.unknown:
      return 'Unknown';
  }
}

/// Status glyph shared by the chip and the detail sheet: spinner while
/// running, check when done, error icon on failure, muted schedule icon for
/// interrupted/unknown.
class ToolStatusGlyph extends StatelessWidget {
  const ToolStatusGlyph({super.key, required this.status, required this.color});

  final ToolStatus status;
  final Color color;

  @override
  Widget build(BuildContext context) {
    switch (status) {
      case ToolStatus.running:
        return SizedBox(
          width: 11,
          height: 11,
          child: CircularProgressIndicator(strokeWidth: 1.5, color: color),
        );
      case ToolStatus.done:
        return Icon(Icons.check_circle_outline, size: 12, color: color);
      case ToolStatus.error:
        return Icon(Icons.error_outline, size: 12, color: color);
      case ToolStatus.interrupted:
      case ToolStatus.unknown:
        return Icon(Icons.schedule, size: 12, color: color);
    }
  }
}

/// Presents the tool detail sheet for [activity]. [effectiveStatus] lets the
/// chip pass its stall-coerced status (a wedged "running" chip presents as
/// interrupted here too); defaults to the activity's own status.
Future<void> showToolDetailSheet(
  BuildContext context,
  ToolActivity activity, {
  ToolStatus? effectiveStatus,
}) {
  return LzBottomSheet.show<void>(
    context,
    title: activity.displayName ?? activity.name,
    builder: (_) => ToolDetailSheet(
      activity: activity,
      status: effectiveStatus ?? activity.status,
    ),
  );
}

class ToolDetailSheet extends StatelessWidget {
  const ToolDetailSheet({
    super.key,
    required this.activity,
    required this.status,
  });

  final ToolActivity activity;

  /// Render status (may be the chip's stall-coerced view of a running chip).
  final ToolStatus status;

  @override
  Widget build(BuildContext context) {
    final result = activity.resultPreview;
    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          // Raw registry name — small and muted under the display title.
          Text(
            activity.name,
            style: AppText.caption.copyWith(
              color: AppColors.textMuted,
              fontFamily: 'monospace',
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              ToolStatusGlyph(status: status, color: toolStatusColor(status)),
              const SizedBox(width: AppSpacing.xs),
              Text(
                toolStatusLabel(status),
                style: AppText.caption.copyWith(
                  color: toolStatusColor(status),
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.lg),
          const _SectionLabel('Arguments'),
          const SizedBox(height: AppSpacing.xs),
          if (activity.args.isEmpty)
            const _EmptyHint('No arguments captured')
          else
            _CodeBlock(text: _prettyJson(activity.args)),
          const SizedBox(height: AppSpacing.lg),
          const _SectionLabel('Result'),
          const SizedBox(height: AppSpacing.xs),
          if (result == null || result.isEmpty)
            const _EmptyHint('No result captured')
          else
            _CodeBlock(text: result, horizontalScroll: false),
        ],
      ),
    );
  }
}

/// Pretty-prints the argument map; a non-encodable value degrades to
/// `toString()` instead of throwing.
String _prettyJson(Map<String, dynamic> args) {
  try {
    return const JsonEncoder.withIndent('  ').convert(args);
  } catch (_) {
    return args.toString();
  }
}

class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: AppText.label.copyWith(color: AppColors.textSecondary),
    );
  }
}

class _EmptyHint extends StatelessWidget {
  const _EmptyHint(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: AppText.caption.copyWith(
        color: AppColors.textMuted,
        fontStyle: FontStyle.italic,
      ),
    );
  }
}

/// Token-styled selectable code block. JSON blocks scroll horizontally so
/// long lines don't wrap into noise; plain result text wraps naturally.
class _CodeBlock extends StatelessWidget {
  const _CodeBlock({required this.text, this.horizontalScroll = true});

  final String text;
  final bool horizontalScroll;

  @override
  Widget build(BuildContext context) {
    final body = SelectableText(
      text,
      style: AppText.caption.copyWith(
        color: AppColors.textSecondary,
        fontFamily: 'monospace',
        fontWeight: FontWeight.w400,
      ),
    );
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.bgSurfaceElevated,
        borderRadius: AppRadii.rMd,
        border: Border.all(color: AppColors.borderSubtle),
      ),
      child: horizontalScroll
          ? SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: body,
            )
          : body,
    );
  }
}
