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
import 'inbox_instruction_sheet.dart';
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

  // Sending state (AI mode only) — kept here so the child _ReplyBar widget gets
  // it via callbacks + plain values (no ref passed via constructor).
  bool _sending = false;

  // Optimistic outbox: direct replies appear INSTANTLY as pending bubbles
  // (sending → sent / failed) instead of waiting on a live re-read that hasn't
  // seen the outgoing message yet. Session-only (a fresh open shows the real
  // server transcript), so no dedup is needed. Immutable — status transitions
  // replace the entry by id.
  List<_Outgoing> _pending = const [];
  int _seq = 0;

  @override
  void initState() {
    super.initState();
    // Fire-and-forget — errors are silently swallowed (read receipts are
    // best-effort and must never block the UI). After marking read, refresh
    // the thread list so its unread badge clears when the user navigates back
    // (the FutureProvider is cached and won't refetch on its own).
    Future.microtask(() async {
      if (!mounted) return;
      try {
        await ref.read(inboxRepositoryProvider).markRead(widget.threadId);
      } catch (_) {/* best-effort read receipt */}
      if (!mounted) return;
      ref.invalidate(inboxThreadsProvider);
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
    if (text.isEmpty) return;
    if (_aiMode) {
      await _sendAi(text);
    } else {
      _sendDirect(text);
    }
  }

  /// Direct send — optimistic. The bubble appears immediately (`sending`),
  /// then settles to `sent` / `failed` when the POST resolves. We do NOT
  /// re-fetch the live thread afterward: the outgoing message isn't in the
  /// channel's store yet, so a refetch would just wipe the bubble we optimism.
  void _sendDirect(String text) {
    final id = _seq++;
    setState(() {
      _pending = [
        ..._pending,
        _Outgoing(id: id, text: text, status: _SendStatus.sending),
      ];
    });
    _controller.clear();
    _deliver(id, text);
  }

  Future<void> _deliver(int id, String text) async {
    try {
      await ref
          .read(inboxRepositoryProvider)
          .reply(widget.threadId, text, mode: 'direct');
      _setStatus(id, _SendStatus.sent);
    } catch (_) {
      _setStatus(id, _SendStatus.failed);
    }
  }

  void _setStatus(int id, _SendStatus status) {
    if (!mounted) return;
    setState(() {
      _pending = [
        for (final m in _pending)
          if (m.id == id) m.withStatus(status) else m,
      ];
    });
  }

  void _retry(_Outgoing m) {
    _setStatus(m.id, _SendStatus.sending);
    _deliver(m.id, m.text);
  }

  /// Ask-AI — hands the thread to the agent (background). Blocks the button
  /// briefly and confirms via a snackbar; no optimistic bubble (the AI's reply
  /// arrives later through the normal thread refresh).
  Future<void> _sendAi(String text) async {
    if (_sending) return;
    setState(() => _sending = true);
    try {
      await ref
          .read(inboxRepositoryProvider)
          .reply(widget.threadId, text, mode: 'ai');
      if (!mounted) return;
      _controller.clear();
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text("On it — I'll run the conversation and report back."),
          duration: Duration(seconds: 3),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Failed: $e')),
      );
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  // ── Name contact ───────────────────────────────────────────────────────────

  Future<void> _nameContact() async {
    final controller = TextEditingController(
      text: widget.title == 'Conversation' ? '' : widget.title,
    );
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.bgSurfaceElevated,
        title: Text('Name this contact',
            style: AppText.title.copyWith(color: AppColors.textPrimary)),
        content: LzTextField(
          controller: controller,
          hint: 'e.g. Maria Garcia',
          maxLines: 1,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text('Cancel',
                style: AppText.label.copyWith(color: AppColors.textSecondary)),
          ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(controller.text.trim()),
            child:
                Text('Save', style: AppText.label.copyWith(color: AppColors.accent)),
          ),
        ],
      ),
    );
    if (name == null || name.isEmpty || !mounted) return;
    try {
      await ref.read(inboxRepositoryProvider).nameContact(widget.threadId, name);
      if (!mounted) return;
      ref.invalidate(inboxThreadsProvider);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Saved — $name added to your contacts and LazyBrain.'),
          duration: const Duration(seconds: 3),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not save name: $e')),
      );
    }
  }

  // ── Build ───────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final messagesAsync = ref.watch(inboxMessagesProvider(widget.threadId));

    return LzScaffold(
      title: widget.title,
      actions: [
        // Per-contact auto-pilot (standing instruction for THIS person).
        IconButton(
          icon: const Icon(Icons.bolt_rounded),
          color: AppColors.textSecondary,
          tooltip: 'Auto-pilot for this contact',
          visualDensity: VisualDensity.compact,
          onPressed: () => showThreadInstructionSheet(
            context,
            threadId: widget.threadId,
            contactLabel: widget.title,
          ),
        ),
        // Name the contact (contacts store + LazyBrain wikilink page).
        IconButton(
          icon: const Icon(Icons.person_outline_rounded),
          color: AppColors.textSecondary,
          tooltip: 'Name this contact',
          visualDensity: VisualDensity.compact,
          onPressed: _nameContact,
        ),
      ],
      resizeToAvoidBottomInset: true,
      body: Column(
        children: [
          // ── Message list ─────────────────────────────────────────────────
          Expanded(
            child: messagesAsync.when(
              loading: () => LzSkeleton.list(),
              error: (e, _) => LzErrorState(
                message:
                    'Could not load messages. Check your connection and try again.',
                onRetry: () =>
                    ref.invalidate(inboxMessagesProvider(widget.threadId)),
              ),
              data: (messages) {
                if (messages.isEmpty && _pending.isEmpty) {
                  return const LzEmptyState(
                    icon: Icons.chat_bubble_outline_rounded,
                    title: 'No messages yet',
                    hint: 'Start the conversation below.',
                  );
                }
                // Chat-style ordering: channel reads return NEWEST-FIRST
                // (whatsapp_read sorts desc), so `reverse: true` puts index 0
                // at the BOTTOM. Optimistic pending sends are the newest of
                // all, so they occupy the first _pending.length slots (newest
                // pending closest to the bottom); fetched messages follow.
                return ListView.builder(
                  reverse: true,
                  padding: const EdgeInsets.symmetric(
                    horizontal: AppSpacing.lg,
                    vertical: AppSpacing.md,
                  ),
                  itemCount: _pending.length + messages.length,
                  itemBuilder: (_, i) {
                    if (i < _pending.length) {
                      final p = _pending[_pending.length - 1 - i];
                      return _PendingBubble(
                        outgoing: p,
                        onRetry: () => _retry(p),
                      );
                    }
                    return _MessageBubble(
                      message: messages[i - _pending.length],
                      threadId: widget.threadId,
                    );
                  },
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
              // Chat tails: the corner pointing at the speaker stays sharp —
              // an instant visual cue of WHO each bubble belongs to, on top
              // of the color + alignment difference.
              borderRadius: BorderRadius.only(
                topLeft: const Radius.circular(AppRadii.lg),
                topRight: const Radius.circular(AppRadii.lg),
                bottomLeft: Radius.circular(isMine ? AppRadii.lg : AppRadii.sm),
                bottomRight: Radius.circular(isMine ? AppRadii.sm : AppRadii.lg),
              ),
              border: isMine
                  ? null
                  : Border.all(color: AppColors.borderDefault),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                // Speaker marker: bold accent name on received bubbles,
                // an explicit "You" tag on sent ones.
                if (isMine) ...[
                  Text(
                    'You',
                    style: AppText.caption.copyWith(
                      color: AppColors.onAccent.withValues(alpha: 0.75),
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  AppSpacing.vGap(AppSpacing.xs),
                ] else if (message.sender.isNotEmpty) ...[
                  Text(
                    message.sender,
                    style: AppText.caption.copyWith(
                      color: AppColors.accent,
                      fontWeight: FontWeight.w700,
                    ),
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
                // Timestamp — only when present (WhatsApp used to hand us an
                // empty string, which drew a blank line + wasted gap). Rendered
                // localized/relative instead of the raw "… UTC" machine string.
                if (message.timestamp.isNotEmpty) ...[
                  AppSpacing.vGap(AppSpacing.xs),
                  Text(
                    formatInboxTimestamp(message.timestamp),
                    style: AppText.caption.copyWith(
                      color: isMine
                          ? AppColors.onAccent.withValues(alpha: 0.6)
                          : AppColors.textMuted,
                    ),
                  ),
                ],
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

// ── Optimistic outbox ─────────────────────────────────────────────────────────

enum _SendStatus { sending, sent, failed }

/// An immutable optimistic outgoing message. Status transitions produce a new
/// instance (see [withStatus]) so the pending list stays immutable.
class _Outgoing {
  const _Outgoing({
    required this.id,
    required this.text,
    required this.status,
  });

  final int id;
  final String text;
  final _SendStatus status;

  _Outgoing withStatus(_SendStatus next) =>
      _Outgoing(id: id, text: text, status: next);
}

/// A right-aligned "You" bubble for a not-yet-confirmed direct send, with a
/// `sending… / ✓ sent / ⚠ tap to retry` status line. Mirrors the mine-bubble
/// styling of [_MessageBubble].
class _PendingBubble extends StatelessWidget {
  const _PendingBubble({required this.outgoing, required this.onRetry});

  final _Outgoing outgoing;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: Align(
        alignment: Alignment.centerRight,
        child: ConstrainedBox(
          constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.78,
          ),
          child: Container(
            padding: AppSpacing.card,
            decoration: const BoxDecoration(
              color: AppColors.accent,
              borderRadius: BorderRadius.only(
                topLeft: Radius.circular(AppRadii.lg),
                topRight: Radius.circular(AppRadii.lg),
                bottomLeft: Radius.circular(AppRadii.lg),
                bottomRight: Radius.circular(AppRadii.sm),
              ),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  'You',
                  style: AppText.caption.copyWith(
                    color: AppColors.onAccent.withValues(alpha: 0.75),
                    fontWeight: FontWeight.w700,
                  ),
                ),
                AppSpacing.vGap(AppSpacing.xs),
                Text(
                  outgoing.text,
                  style: AppText.body.copyWith(color: AppColors.onAccent),
                ),
                AppSpacing.vGap(AppSpacing.xs),
                _status(),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _status() {
    final muted = AppText.caption.copyWith(
      color: AppColors.onAccent.withValues(alpha: 0.6),
    );
    switch (outgoing.status) {
      case _SendStatus.sending:
        return Text('sending…', style: muted);
      case _SendStatus.sent:
        return Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.check_rounded,
                size: 14, color: AppColors.onAccent.withValues(alpha: 0.7)),
            const SizedBox(width: 2),
            Text('sent', style: muted),
          ],
        );
      case _SendStatus.failed:
        // Tappable retry — bold on-accent (readable on the green bubble).
        return GestureDetector(
          onTap: onRetry,
          behavior: HitTestBehavior.opaque,
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.refresh_rounded,
                  size: 14, color: AppColors.onAccent),
              const SizedBox(width: 4),
              Text(
                'Failed — tap to retry',
                style: AppText.caption.copyWith(
                  color: AppColors.onAccent,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
        );
    }
  }
}
