// Thread detail screen for the unified Inbox.
//
// Shows messages for a specific thread, marks it read on open, and provides
// a reply bar with a Send / Ask AI mode toggle.
//
// Kit constraints:
//   - LzTextField: `hint` (not hintText), accepts `controller`.
//   - LzButton: `onPressed` accepts null → disabled (opacity 0.45). Has `loading`.
//   - LzChip: `label`, `selected`, `onTap`.
//   - LzScaffold: `title`, `body`, `resizeToAvoidBottomInset`.
//   - NO passing WidgetRef via constructor; _ReplyBar is a ConsumerStatefulWidget.

library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../comms/inbox_models.dart';
import '../../comms/inbox_providers.dart';
import '../../ui/ui.dart';
import 'inbox_media_bubble.dart';

// ── Screen ────────────────────────────────────────────────────────────────────

/// Thread detail screen. Watches [inboxMessagesProvider] for [threadId] and
/// fires `markRead` on open via a microtask so initState stays synchronous.
class InboxThreadScreen extends ConsumerStatefulWidget {
  const InboxThreadScreen({
    super.key,
    required this.threadId,
    required this.title,
  });

  final String threadId;
  final String title;

  @override
  ConsumerState<InboxThreadScreen> createState() => _InboxThreadScreenState();
}

class _InboxThreadScreenState extends ConsumerState<InboxThreadScreen> {
  final _controller = TextEditingController();

  // Mode toggle: false = Send (direct), true = Ask AI.
  bool _aiMode = false;

  // Sending state — kept here so the child _ReplyBar widget gets it via
  // callbacks + plain values (no ref passed via constructor).
  bool _sending = false;

  @override
  void initState() {
    super.initState();
    // Fire-and-forget — errors are silently swallowed (read receipts are
    // best-effort and must never block the UI).
    Future.microtask(() {
      if (!mounted) return;
      ref.read(inboxRepositoryProvider).markRead(widget.threadId);
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  // ── Send ────────────────────────────────────────────────────────────────────

  Future<void> _send() async {
    final text = _controller.text.trim();
    if (text.isEmpty || _sending) return;

    setState(() => _sending = true);
    try {
      await ref.read(inboxRepositoryProvider).reply(
            widget.threadId,
            text,
            mode: _aiMode ? 'ai' : 'direct',
          );
      if (!mounted) return;
      _controller.clear();
      if (!_aiMode) {
        // Refresh the message list so the sent message appears immediately.
        ref.invalidate(inboxMessagesProvider(widget.threadId));
      } else {
        // AI mode — the agent will run the conversation in the background.
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text("On it — I'll run the conversation and report back."),
            duration: Duration(seconds: 3),
          ),
        );
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Failed: $e')),
      );
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  // ── Build ───────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final messagesAsync = ref.watch(inboxMessagesProvider(widget.threadId));

    return LzScaffold(
      title: widget.title,
      resizeToAvoidBottomInset: true,
      body: Column(
        children: [
          // ── Message list ─────────────────────────────────────────────────
          Expanded(
            child: messagesAsync.when(
              loading: () => const Center(
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: AppColors.accent,
                ),
              ),
              error: (e, _) => LzErrorState(
                message:
                    'Could not load messages. Check your connection and try again.',
                onRetry: () =>
                    ref.invalidate(inboxMessagesProvider(widget.threadId)),
              ),
              data: (messages) {
                if (messages.isEmpty) {
                  return const LzEmptyState(
                    icon: Icons.chat_bubble_outline_rounded,
                    title: 'No messages yet',
                    hint: 'Start the conversation below.',
                  );
                }
                // Chat-style ordering: channel reads return NEWEST-FIRST
                // (whatsapp_read sorts desc), so `reverse: true` puts
                // messages[0] (the newest) at the BOTTOM and opens the view
                // already scrolled there — older messages scroll up, like
                // every messaging app.
                return ListView.builder(
                  reverse: true,
                  padding: const EdgeInsets.symmetric(
                    horizontal: AppSpacing.lg,
                    vertical: AppSpacing.md,
                  ),
                  itemCount: messages.length,
                  itemBuilder: (_, i) => _MessageBubble(
                    message: messages[i],
                    threadId: widget.threadId,
                  ),
                );
              },
            ),
          ),

          // ── Reply bar ────────────────────────────────────────────────────
          _ReplyBar(
            controller: _controller,
            aiMode: _aiMode,
            sending: _sending,
            onToggleMode: (ai) => setState(() => _aiMode = ai),
            onSend: _send,
          ),
        ],
      ),
    );
  }
}

// ── Message bubble ────────────────────────────────────────────────────────────

class _MessageBubble extends StatelessWidget {
  const _MessageBubble({required this.message, required this.threadId});

  final InboxMessage message;
  final String threadId;

  @override
  Widget build(BuildContext context) {
    final isMine = message.isMine;
    final media = message.media;
    final msgId = message.id;
    // Media placeholder texts ("[audio]", "[image]") are redundant once the
    // real media bubble renders — hide them, keep captions.
    final isPlaceholderText = media != null &&
        RegExp(r'^\[[^\]]+\]$').hasMatch(message.text.trim());

    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: Align(
        alignment: isMine ? Alignment.centerRight : Alignment.centerLeft,
        child: ConstrainedBox(
          constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.78,
          ),
          child: Container(
            padding: AppSpacing.card,
            decoration: BoxDecoration(
              color: isMine
                  ? AppColors.accent
                  : AppColors.bgSurfaceElevated,
              borderRadius: AppRadii.rLg,
              border: isMine
                  ? null
                  : Border.all(color: AppColors.borderDefault),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                // Sender label only on received (non-mine) bubbles with a name.
                if (!isMine && message.sender.isNotEmpty) ...[
                  Text(
                    message.sender,
                    style: AppText.caption
                        .copyWith(color: AppColors.textMuted),
                  ),
                  AppSpacing.vGap(AppSpacing.xs),
                ],
                // Media (voice note / photo / file) renders its own bubble.
                if (media != null && msgId != null) ...[
                  InboxMediaBubble(
                    threadId: threadId,
                    messageId: msgId,
                    media: media,
                    isMine: isMine,
                  ),
                  if (!isPlaceholderText) AppSpacing.vGap(AppSpacing.xs),
                ],
                if (!isPlaceholderText)
                  Text(
                    message.text,
                    style: AppText.body.copyWith(
                      color: isMine
                          ? AppColors.onAccent
                          : AppColors.textPrimary,
                    ),
                  ),
                AppSpacing.vGap(AppSpacing.xs),
                // Timestamp
                Text(
                  message.timestamp,
                  style: AppText.caption.copyWith(
                    color: isMine
                        ? AppColors.onAccent.withValues(alpha: 0.6)
                        : AppColors.textMuted,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ── Reply bar ─────────────────────────────────────────────────────────────────

/// Reply bar with a Send / Ask AI mode toggle.
///
/// Deliberately NOT a ConsumerWidget — it has no provider access of its own.
/// All state (aiMode, sending) lives in the parent [_InboxThreadScreenState]
/// and is passed down as plain values + callbacks. This keeps the widget pure
/// and testable without a ProviderScope.
class _ReplyBar extends StatelessWidget {
  const _ReplyBar({
    required this.controller,
    required this.aiMode,
    required this.sending,
    required this.onToggleMode,
    required this.onSend,
  });

  final TextEditingController controller;
  final bool aiMode;
  final bool sending;
  final void Function(bool ai) onToggleMode;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    final bottomInset = MediaQuery.of(context).viewInsets.bottom;

    return AnimatedPadding(
      duration: AppMotion.fast,
      curve: AppMotion.curve,
      padding: EdgeInsets.only(bottom: bottomInset),
      child: SafeArea(
        top: false,
        child: Container(
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            border: Border(
              top: BorderSide(color: AppColors.borderSubtle),
            ),
          ),
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.md,
            AppSpacing.sm,
            AppSpacing.md,
            AppSpacing.md,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // ── Mode toggle chips ──────────────────────────────────────
              Row(
                children: [
                  LzChip(
                    label: 'Send',
                    selected: !aiMode,
                    onTap: () => onToggleMode(false),
                  ),
                  AppSpacing.hGap(AppSpacing.sm),
                  LzChip(
                    label: 'Ask AI',
                    selected: aiMode,
                    onTap: () => onToggleMode(true),
                  ),
                ],
              ),
              AppSpacing.vGap(AppSpacing.sm),
              // ── Input row ──────────────────────────────────────────────
              Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Expanded(
                    child: LzTextField(
                      controller: controller,
                      hint: aiMode
                          ? 'Tell the AI what to ask…'
                          : 'Type a reply…',
                      maxLines: 4,
                      minLines: 1,
                      textInputAction: TextInputAction.newline,
                      keyboardType: TextInputType.multiline,
                    ),
                  ),
                  AppSpacing.hGap(AppSpacing.sm),
                  LzButton(
                    label: aiMode ? 'Ask' : 'Send',
                    onPressed: sending ? null : onSend,
                    loading: sending,
                    variant: LzButtonVariant.primary,
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
