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
          _BubbleContent(m: m, isUser: isUser),
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
          if (m.streaming) ...[
            const SizedBox(height: AppSpacing.xs),
            _StreamingIndicator(),
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
    // Assistant: markdown render. Show ellipsis when content is empty +
    // not yet streaming (edge case).
    final data = m.content.isEmpty && !m.streaming ? '…' : m.content;
    return MarkdownBody(
      data: data,
      styleSheet: MarkdownStyleSheet(
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
      ),
    );
  }
}

// ── Streaming indicator ────────────────────────────────────────────────────

class _StreamingIndicator extends StatefulWidget {
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
        children: List.generate(
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
