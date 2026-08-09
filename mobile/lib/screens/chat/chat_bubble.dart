/// Premium message bubble for the Chat screen.
///
/// User bubbles: amber-tinted surface, right-aligned, asymmetric radius
/// (full-pill on the user-side corners, smaller on the tail corner).
/// Assistant bubbles: bgSurface fill, left-aligned, matching asymmetry.
/// Streaming cursors and tool-call chip rows live inside the assistant bubble.
library;

import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import '../../chat/chat_message.dart';
import '../../ui/ui.dart';
import 'activity_timeline.dart';
import 'tool_chip.dart';

class ChatBubble extends StatelessWidget {
  const ChatBubble(this.m, {super.key, required this.onApprove});

  final ChatMessage m;
  final void Function(String id, bool approved) onApprove;

  @override
  Widget build(BuildContext context) {
    final isUser = m.role == 'user';
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Padding(
        padding: EdgeInsets.only(
          top: AppSpacing.xs,
          bottom: AppSpacing.xs,
          left: isUser ? AppSpacing.xxxl : 0,
          right: isUser ? 0 : AppSpacing.xxxl,
        ),
        child: Column(
          crossAxisAlignment:
              isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
          children: [
            _BubbleContainer(m: m, isUser: isUser),
            if (m.pendingApprovalId != null) ...[
              const SizedBox(height: AppSpacing.sm),
              _ApprovalRow(
                skill: m.pendingApprovalSkill ?? 'action',
                approvalId: m.pendingApprovalId!,
                onApprove: onApprove,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

// ── Bubble container ───────────────────────────────────────────────────────

class _BubbleContainer extends StatelessWidget {
  const _BubbleContainer({required this.m, required this.isUser});
  final ChatMessage m;
  final bool isUser;

  @override
  Widget build(BuildContext context) {
    // Asymmetric radii: the "tail" corner (bottom-right for user,
    // bottom-left for assistant) gets a smaller radius.
    final radius = isUser
        ? const BorderRadius.only(
            topLeft: Radius.circular(AppRadii.xl),
            topRight: Radius.circular(AppRadii.xl),
            bottomLeft: Radius.circular(AppRadii.xl),
            bottomRight: Radius.circular(AppRadii.sm),
          )
        : const BorderRadius.only(
            topLeft: Radius.circular(AppRadii.sm),
            topRight: Radius.circular(AppRadii.xl),
            bottomLeft: Radius.circular(AppRadii.xl),
            bottomRight: Radius.circular(AppRadii.xl),
          );

    final bgColor = isUser
        ? AppColors.accent.withValues(alpha: 0.18)
        : AppColors.bgSurface;
    final border = isUser
        ? Border.all(color: AppColors.accent.withValues(alpha: 0.35))
        : Border.all(color: AppColors.borderSubtle);

    return Container(
      constraints: BoxConstraints(
        maxWidth: MediaQuery.of(context).size.width * 0.78,
      ),
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: radius,
        border: border,
        boxShadow: AppElevation.card,
      ),
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          if (!isUser && m.thinkingText.isNotEmpty) ...[
            _ThinkingSection(text: m.thinkingText, active: m.thinking),
            const SizedBox(height: AppSpacing.sm),
          ],
          _BubbleContent(m: m, isUser: isUser),
          if (isUser && m.sendState != SendState.sent) ...[
            const SizedBox(height: AppSpacing.xs),
            _SendStateHint(state: m.sendState),
          ],
          if (m.toolActivities.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.sm),
            Wrap(
              spacing: AppSpacing.xs,
              runSpacing: AppSpacing.xs,
              children: m.toolActivities
                  .map((t) => ToolChip(activity: t))
                  .toList(),
            ),
          ],
          if (m.agentActivities.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.sm),
            ActivityTimeline(activities: m.agentActivities),
          ],
          if (m.streaming) ...[
            const SizedBox(height: AppSpacing.xs),
            _StreamingIndicator(label: _liveLabel(m)),
          ],
          if (!m.streaming &&
              m.usage != null &&
              m.usage!.summaryLine.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.xs),
            Text(
              m.usage!.summaryLine,
              style: AppText.caption.copyWith(color: AppColors.textMuted),
            ),
          ],
        ],
      ),
    );
  }
}

// ── Bubble content ─────────────────────────────────────────────────────────

class _BubbleContent extends StatelessWidget {
  const _BubbleContent({required this.m, required this.isUser});
  final ChatMessage m;
  final bool isUser;

  @override
  Widget build(BuildContext context) {
    if (isUser) {
      return Text(
        m.content,
        style: AppText.body.copyWith(color: AppColors.textPrimary),
      );
    }
    // Proactive server ping persisted as an assistant row (reminders, cron
    // results, watcher alerts): bell glyph + emphasized title line. Rows
    // without the kind field fall through to the normal bubble (graceful).
    if (m.kind == 'notification' && m.content.isNotEmpty) {
      return _NotificationContent(content: m.content);
    }
    // Assistant: markdown render. Show ellipsis when content is empty +
    // not yet streaming (edge case).
    final data = m.content.isEmpty && !m.streaming ? '…' : m.content;
    return MarkdownBody(
      data: data,
      styleSheet: _assistantMarkdownStyle(),
    );
  }
}

/// Shared markdown styling for assistant-authored text (normal replies and
/// notification bodies) — design-system tokens only.
MarkdownStyleSheet _assistantMarkdownStyle() => MarkdownStyleSheet(
      p: AppText.body.copyWith(color: AppColors.textPrimary),
      strong: AppText.body.copyWith(
        color: AppColors.textPrimary,
        fontWeight: FontWeight.w600,
      ),
      em: AppText.body.copyWith(
        color: AppColors.textSecondary,
        fontStyle: FontStyle.italic,
      ),
      code: AppText.caption.copyWith(
        color: AppColors.accent,
        fontFamily: 'monospace',
        backgroundColor: AppColors.bgSurfaceElevated,
      ),
      codeblockDecoration: BoxDecoration(
        color: AppColors.bgSurfaceElevated,
        borderRadius: AppRadii.rSm,
        border: Border.all(color: AppColors.borderSubtle),
      ),
      blockquoteDecoration: BoxDecoration(
        border: Border(
          left: BorderSide(color: AppColors.accent, width: 3),
        ),
      ),
      h1: AppText.titleL.copyWith(color: AppColors.textPrimary),
      h2: AppText.title.copyWith(color: AppColors.textPrimary),
      h3: AppText.label.copyWith(color: AppColors.textPrimary),
      listBullet: AppText.body.copyWith(color: AppColors.accent),
    );

/// Subtle distinct treatment for `kind == 'notification'` rows inside an
/// otherwise standard assistant bubble. The content contract is
/// "{title}\n{body}" — the first line renders as an emphasized header next
/// to a bell glyph, the rest as normal assistant markdown. Degrades to a
/// title-only card when there is no body line.
class _NotificationContent extends StatelessWidget {
  const _NotificationContent({required this.content});
  final String content;

  @override
  Widget build(BuildContext context) {
    final nl = content.indexOf('\n');
    final title = (nl == -1 ? content : content.substring(0, nl)).trim();
    final body = nl == -1 ? '' : content.substring(nl + 1).trim();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Padding(
              padding: EdgeInsets.only(top: 1),
              child: Icon(
                Icons.notifications_none_rounded,
                size: 15,
                color: AppColors.accent,
              ),
            ),
            const SizedBox(width: AppSpacing.xs),
            Flexible(
              child: Text(
                title.isEmpty ? 'Notification' : title,
                style: AppText.label.copyWith(color: AppColors.textPrimary),
              ),
            ),
          ],
        ),
        if (body.isNotEmpty) ...[
          const SizedBox(height: AppSpacing.xs),
          MarkdownBody(
            data: body,
            styleSheet: _assistantMarkdownStyle(),
          ),
        ],
      ],
    );
  }
}

/// Small delivery hint under a user bubble: "Sending…" while the message is
/// queued in the socket's offline outbox, "Not delivered" once its TTL
/// expired (the user retries by resending). Hidden for normal sent bubbles.
class _SendStateHint extends StatelessWidget {
  const _SendStateHint({required this.state});
  final SendState state;

  @override
  Widget build(BuildContext context) {
    final failed = state == SendState.failed;
    final color = failed ? AppColors.error : AppColors.textMuted;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(
          failed ? Icons.error_outline : Icons.schedule,
          size: 12,
          color: color,
        ),
        const SizedBox(width: AppSpacing.xs),
        Text(
          failed ? 'Not delivered' : 'Sending…',
          style: AppText.caption.copyWith(color: color),
        ),
      ],
    );
  }
}

/// The label shown beside the streaming dots: extended thinking wins, then
/// the current TAOR phase, else nothing (plain dots).
String? _liveLabel(ChatMessage m) {
  if (m.thinking) return 'Reasoning…';
  switch (m.phase) {
    case 'think':
      return 'Thinking…';
    case 'act':
      return 'Acting…';
    case 'observe':
      return 'Observing…';
    case 'reflect':
      return 'Reflecting…';
  }
  return null;
}

// ── Thinking section ───────────────────────────────────────────────────────

/// Collapsible extended-thinking panel. Collapsed by default — a tap reveals
/// the accumulated reasoning text. Pulses subtly while [active].
class _ThinkingSection extends StatefulWidget {
  const _ThinkingSection({required this.text, required this.active});
  final String text;
  final bool active;

  @override
  State<_ThinkingSection> createState() => _ThinkingSectionState();
}

class _ThinkingSectionState extends State<_ThinkingSection> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => setState(() => _expanded = !_expanded),
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
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  Icons.psychology_outlined,
                  size: 13,
                  color:
                      widget.active ? AppColors.accent : AppColors.textMuted,
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  widget.active ? 'Thinking…' : 'Thinking',
                  style: AppText.caption.copyWith(
                    color: widget.active
                        ? AppColors.accent
                        : AppColors.textMuted,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(width: AppSpacing.xs),
                Icon(
                  _expanded ? Icons.expand_less : Icons.expand_more,
                  size: 13,
                  color: AppColors.textMuted,
                ),
              ],
            ),
            if (_expanded) ...[
              const SizedBox(height: AppSpacing.xs),
              Text(
                widget.text,
                style: AppText.caption.copyWith(
                  color: AppColors.textSecondary,
                  fontWeight: FontWeight.w400,
                  fontStyle: FontStyle.italic,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

// ── Streaming indicator ────────────────────────────────────────────────────

class _StreamingIndicator extends StatefulWidget {
  const _StreamingIndicator({this.label});
  final String? label;

  @override
  State<_StreamingIndicator> createState() => _StreamingIndicatorState();
}

class _StreamingIndicatorState extends State<_StreamingIndicator>
    with SingleTickerProviderStateMixin {
  late AnimationController _ctrl;
  late Animation<double> _fade;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 600),
    )..repeat(reverse: true);
    _fade = CurvedAnimation(parent: _ctrl, curve: Curves.easeInOut);
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: _fade,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          ...List.generate(
            3,
            (i) => Padding(
              padding: EdgeInsets.only(right: i < 2 ? 3 : 0),
              child: Container(
                width: 5,
                height: 5,
                decoration: BoxDecoration(
                  color: AppColors.accent,
                  shape: BoxShape.circle,
                ),
              ),
            ),
          ),
          if (widget.label != null) ...[
            const SizedBox(width: AppSpacing.xs),
            Text(
              widget.label!,
              style: AppText.caption.copyWith(color: AppColors.textMuted),
            ),
          ],
        ],
      ),
    );
  }
}

// ── Approval row ───────────────────────────────────────────────────────────

class _ApprovalRow extends StatelessWidget {
  const _ApprovalRow({
    required this.skill,
    required this.approvalId,
    required this.onApprove,
  });

  final String skill;
  final String approvalId;
  final void Function(String id, bool approved) onApprove;

  @override
  Widget build(BuildContext context) {
    return LzCard(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      color: AppColors.bgSurfaceElevated,
      borderColor: AppColors.accent.withValues(alpha: 0.25),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(
                Icons.security_outlined,
                size: 15,
                color: AppColors.accent,
              ),
              const SizedBox(width: AppSpacing.xs),
              Expanded(
                child: Text(
                  'Approve $skill?',
                  style: AppText.label.copyWith(color: AppColors.accent),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              LzButton.ghost(
                label: 'Deny',
                onPressed: () => onApprove(approvalId, false),
              ),
              const SizedBox(width: AppSpacing.sm),
              LzButton.primary(
                label: 'Approve',
                onPressed: () => onApprove(approvalId, true),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
