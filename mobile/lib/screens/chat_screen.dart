/// Chat screen — premium redesign.
///
/// All provider wiring, socket connection logic, auth-invalidation listener,
/// message-dispatch callbacks, approval/plan responses, and autoscroll are
/// IDENTICAL to the original. Only the presentation layer is replaced:
///   - [LzScaffold] + [LzAppBar] with [LzStatusDot] connection state
///   - [LzBanner.error] for connection errors (replaces MaterialBanner)
///   - [LzEmptyState] for the empty conversation
///   - Premium message bubbles via [ChatBubble]
///   - Tool-activity chips via [ToolChip]
///   - Background-task result cards via [BgTaskCard]
///   - Plan/approval cards via [PlanCard]
///   - Multiline-growing input bar styled to the design system
///
/// Helper widgets live in `screens/chat/` to keep file size manageable.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../chat/chat_controller.dart';
import '../chat/chat_message.dart';
import '../chat/chat_socket.dart';
import '../core/config/server_config.dart';
import '../notifications/local_notifications.dart';
import '../notifications/notifications_service.dart';
import '../providers/auth_provider.dart';
import '../repositories/chat_history_repository.dart';
import '../ui/ui.dart';
import 'chat/bg_task_card.dart';
import 'chat/chat_bubble.dart';
import 'chat/mode_switcher.dart';
import 'chat/plan_card.dart';
import 'inbox/inbox_screen.dart';

// ── Providers (preserved exactly) ─────────────────────────────────────────

final chatSocketProvider = Provider<ChatSocket>((ref) {
  final s = ChatSocket();
  ref.onDispose(s.dispose);
  return s;
});

final chatControllerProvider =
    StateNotifierProvider<ChatController, List<ChatMessage>>(
        (ref) => ChatController(
              ref.watch(chatSocketProvider),
              onNotify: (title, body) =>
                  LocalNotifications.showTaskNotification(title, body),
            ));

/// Loads prior conversation history so the chat isn't empty on open.
final chatHistoryRepositoryProvider = Provider<ChatHistoryRepository>((ref) {
  return ChatHistoryRepository(
      DioChatHistoryTransport(ref.watch(apiClientProvider)));
});

// ── Top segment ──────────────────────────────────────────────────────────────

/// The two top-level segments of this tab. The unified Inbox lives INSIDE the
/// Chat tab (same pattern as Notes inside the Tasks tab) — agent chat and
/// channel conversations are one mental space.
enum _Segment { chat, inbox }

// ── Screen ─────────────────────────────────────────────────────────────────

class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});
  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _input = TextEditingController();
  final _scrollController = ScrollController();
  bool _connected = false;
  String? _connectError;

  /// Chat vs Inbox — the top segment. Rendered via an [IndexedStack] so the
  /// chat subtree (scroll position, in-flight streaming bubbles) stays alive
  /// while browsing the inbox.
  _Segment _segment = _Segment.chat;

  @override
  void initState() {
    super.initState();
    // Initialise local notifications the first time the chat screen mounts.
    // Safe to call multiple times — the implementation is idempotent.
    LocalNotifications.init();
    _connect();
    // Replay prior conversation so the screen isn't empty on open. Best-effort
    // and independent of the socket — the live chat works without it.
    unawaited(_loadHistory());
  }

  /// Fetch the primary session's history and seed the controller once.
  /// Swallows errors: history is a nicety, never a blocker.
  Future<void> _loadHistory() async {
    try {
      final history =
          await ref.read(chatHistoryRepositoryProvider).loadPrimaryHistory();
      if (!mounted) return;
      ref.read(chatControllerProvider.notifier).seedHistory(history);
    } catch (_) {
      // History is best-effort — leave the chat empty and carry on live.
    }
  }

  @override
  void dispose() {
    _input.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  // ── Connection (logic preserved exactly) ──────────────────────────────────

  Future<void> _connect() async {
    setState(() {
      _connectError = null;
      _connected = false;
    });
    try {
      final base = ref.read(baseUrlProvider);
      final cookie = await ref.read(apiClientProvider).getSessionCookie();
      if (cookie == null) {
        ref.read(authProvider.notifier).handle401();
        if (mounted) {
          setState(() =>
              _connectError = 'Session not found — please log in again');
        }
        return;
      }
      await ref.read(chatSocketProvider).connect(
            ServerConfig.wsUrlFor(base),
            cookie: 'session_id=$cookie',
          );
      if (mounted) setState(() => _connected = true);
      // Catch up on any server notifications missed while the app was away.
      // Best-effort + self-cancelling on error — never blocks the chat UI.
      unawaited(pullNotificationsFeed(ref.read(apiClientProvider)));
    } catch (e) {
      if (mounted) {
        setState(() => _connectError = e.toString());
      }
    }
  }

  // ── Send ───────────────────────────────────────────────────────────────────

  void _send() {
    final t = _input.text.trim();
    if (t.isEmpty || !_connected) return;
    ref.read(chatControllerProvider.notifier).send(t);
    _input.clear();
    // Scroll to bottom after sending.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          0,
          duration: AppMotion.base,
          curve: AppMotion.curve,
        );
      }
    });
  }

  // ── Build ──────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    // Invalidate chat providers whenever the user logs out so the next
    // user gets a fresh socket and empty message list.
    ref.listen<AuthState>(authProvider, (prev, next) {
      if (next.status == AuthStatus.unauthenticated) {
        ref.invalidate(chatControllerProvider);
        ref.invalidate(chatSocketProvider);
      }
    });

    final messages = ref.watch(chatControllerProvider);

    return LzScaffold(
      resizeToAvoidBottomInset: true,
      appBar: _buildAppBar(),
      banner: _connectError != null
          ? LzBanner.error(
              message: _connectError!,
              safeAreaTop: false,
              action: TextButton(
                onPressed: _connect,
                child: Text(
                  'Retry',
                  style: AppText.label.copyWith(color: AppColors.error),
                ),
              ),
            )
          : null,
      body: Column(
        children: [
          // ── Chat ⇄ Inbox segment toggle ────────────────────────────────
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.lg, AppSpacing.sm, AppSpacing.lg, AppSpacing.xs,
            ),
            child: Align(
              alignment: Alignment.centerLeft,
              child: _SegmentToggle(
                segment: _segment,
                onChanged: (s) => setState(() => _segment = s),
              ),
            ),
          ),
          // ── Body — IndexedStack keeps the chat subtree (scroll position,
          // streaming bubbles) alive while the inbox is showing.
          Expanded(
            child: IndexedStack(
              index: _segment == _Segment.chat ? 0 : 1,
              children: [
                Column(
                  children: [
                    // Message list
                    Expanded(
                      child: messages.isEmpty
                          ? _EmptyConversation(connected: _connected)
                          : _MessageList(
                              messages: messages,
                              scrollController: _scrollController,
                              onApprove: (id, ok) => ref
                                  .read(chatControllerProvider.notifier)
                                  .respondApproval(id, ok),
                              onSend: (text) => ref
                                  .read(chatControllerProvider.notifier)
                                  .send(text),
                            ),
                    ),
                    // Input bar — shows a stop button while a turn streams.
                    _InputBar(
                      controller: _input,
                      connected: _connected,
                      streaming:
                          messages.any((m) => m.streaming),
                      onSend: _send,
                      onCancel: () =>
                          ref.read(chatControllerProvider.notifier).cancel(),
                    ),
                  ],
                ),
                const InboxView(),
              ],
            ),
          ),
        ],
      ),
    );
  }

  // ── App bar with connection dot ────────────────────────────────────────────

  PreferredSizeWidget _buildAppBar() {
    final dot = _connected
        ? const LzStatusDot.success(size: 9, glow: true)
        : _connectError != null
            ? const LzStatusDot.error(size: 9)
            : const LzStatusDot.warn(size: 9);

    return LzAppBar(
      title: 'Chat',
      actions: [
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Agent activity — what the agent is doing & recently did.
            IconButton(
              icon: const Icon(Icons.bolt_outlined),
              color: AppColors.textSecondary,
              tooltip: 'Activity',
              visualDensity: VisualDensity.compact,
              onPressed: () => context.push('/activity'),
            ),
            // Operating-mode switcher (shared agentModeProvider — same state as
            // the Settings screen). Tappable pill → bottom-sheet picker.
            const ModeSwitcher(),
            const SizedBox(width: AppSpacing.sm),
            dot,
            const SizedBox(width: AppSpacing.xs),
            Text(
              _connected
                  ? 'Connected'
                  : _connectError != null
                      ? 'Offline'
                      : 'Connecting…',
              style: AppText.caption.copyWith(
                color: _connected ? AppColors.success : AppColors.textMuted,
              ),
            ),
            const SizedBox(width: AppSpacing.sm),
          ],
        ),
      ],
    );
  }
}

// ── Segment toggle (Chat | Inbox) ────────────────────────────────────────────

/// The top-level Chat ⇄ Inbox toggle. Built from two [LzChip]s for kit
/// consistency (mirrors the Tasks ⇄ Notes toggle on the Tasks tab).
class _SegmentToggle extends StatelessWidget {
  const _SegmentToggle({required this.segment, required this.onChanged});

  final _Segment segment;
  final void Function(_Segment) onChanged;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        LzChip(
          label: 'Chat',
          icon: Icons.chat_bubble_outline,
          selected: segment == _Segment.chat,
          onTap: () => onChanged(_Segment.chat),
        ),
        const SizedBox(width: AppSpacing.sm),
        LzChip(
          label: 'Inbox',
          icon: Icons.mail_outline,
          selected: segment == _Segment.inbox,
          onTap: () => onChanged(_Segment.inbox),
        ),
      ],
    );
  }
}

// ── Message list ───────────────────────────────────────────────────────────

class _MessageList extends StatelessWidget {
  const _MessageList({
    required this.messages,
    required this.scrollController,
    required this.onApprove,
    required this.onSend,
  });

  final List<ChatMessage> messages;
  final ScrollController scrollController;
  final void Function(String id, bool approved) onApprove;
  final void Function(String text) onSend;

  @override
  Widget build(BuildContext context) {
    return ListView.builder(
      controller: scrollController,
      reverse: true,
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      itemCount: messages.length,
      itemBuilder: (ctx, i) {
        final m = messages[messages.length - 1 - i];
        return _buildMessageWidget(m);
      },
    );
  }

  Widget _buildMessageWidget(ChatMessage m) {
    if (m.role == 'bg_task' && m.bgTaskResult != null) {
      return BgTaskCard(m.bgTaskResult!);
    }
    if (m.role == 'plan' && m.planText != null) {
      return PlanCard(
        planText: m.planText!,
        steps: m.planSteps,
        kind: m.planKind,
        resolved: m.planResolved,
        onSend: onSend,
      );
    }
    return ChatBubble(m, onApprove: onApprove);
  }
}

// ── Empty state ────────────────────────────────────────────────────────────

class _EmptyConversation extends StatelessWidget {
  const _EmptyConversation({required this.connected});
  final bool connected;

  @override
  Widget build(BuildContext context) {
    return LzEmptyState(
      icon: Icons.chat_bubble_outline_rounded,
      title: connected ? 'Start a conversation' : 'Connecting to LazyClaw…',
      hint: connected
          ? 'Ask anything — LazyClaw is ready.'
          : 'The assistant will appear here once connected.',
    );
  }
}

// ── Input bar ──────────────────────────────────────────────────────────────

class _InputBar extends StatefulWidget {
  const _InputBar({
    required this.controller,
    required this.connected,
    required this.streaming,
    required this.onSend,
    required this.onCancel,
  });

  final TextEditingController controller;
  final bool connected;

  /// True while an agent turn is streaming — shows the stop button and the
  /// side-note hint (a message sent mid-turn becomes a side-note serverside).
  final bool streaming;
  final VoidCallback onSend;

  /// Cancels the running agent turn ({"type":"cancel"} over the chat WS).
  final VoidCallback onCancel;

  @override
  State<_InputBar> createState() => _InputBarState();
}

class _InputBarState extends State<_InputBar> {
  bool _hasText = false;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onTextChanged);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onTextChanged);
    super.dispose();
  }

  void _onTextChanged() {
    final hasText = widget.controller.text.trim().isNotEmpty;
    if (hasText != _hasText) {
      setState(() => _hasText = hasText);
    }
  }

  bool get _canSend => widget.connected && _hasText;

  @override
  Widget build(BuildContext context) {
    final bottomPad = MediaQuery.of(context).viewInsets.bottom;

    return AnimatedPadding(
      duration: AppMotion.base,
      curve: AppMotion.curve,
      padding: EdgeInsets.only(bottom: bottomPad),
      child: SafeArea(
        top: false,
        child: Container(
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            border: Border(
              top: BorderSide(color: AppColors.borderSubtle, width: 1),
            ),
          ),
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md,
            vertical: AppSpacing.sm,
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              // Multiline growing text field
              Expanded(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 140),
                  child: TextField(
                    controller: widget.controller,
                    enabled: widget.connected,
                    maxLines: null,
                    minLines: 1,
                    keyboardType: TextInputType.multiline,
                    textInputAction: TextInputAction.newline,
                    style: AppText.body.copyWith(color: AppColors.textPrimary),
                    cursorColor: AppColors.accent,
                    decoration: InputDecoration(
                      hintText: !widget.connected
                          ? 'Connecting…'
                          : widget.streaming
                              ? 'Agent is working — type to add a side-note'
                              : 'Message LazyClaw…',
                      hintStyle:
                          AppText.body.copyWith(color: AppColors.textMuted),
                      filled: true,
                      fillColor: AppColors.bgSurface,
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: AppSpacing.md,
                        vertical: AppSpacing.sm + 2,
                      ),
                      border: OutlineInputBorder(
                        borderRadius: AppRadii.rLg,
                        borderSide: const BorderSide(
                          color: AppColors.borderDefault,
                        ),
                      ),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: AppRadii.rLg,
                        borderSide: const BorderSide(
                          color: AppColors.borderDefault,
                        ),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: AppRadii.rLg,
                        borderSide: const BorderSide(
                          color: AppColors.accent,
                          width: 1.5,
                        ),
                      ),
                      disabledBorder: OutlineInputBorder(
                        borderRadius: AppRadii.rLg,
                        borderSide: const BorderSide(
                          color: AppColors.borderSubtle,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              // Stop button — visible only while a turn is streaming.
              // Mirrors the web ChatInput's square-icon cancel control.
              if (widget.streaming) ...[
                GestureDetector(
                  onTap: widget.onCancel,
                  child: Semantics(
                    button: true,
                    label: 'Stop agent',
                    child: Container(
                      width: 44,
                      height: 44,
                      decoration: BoxDecoration(
                        color: AppColors.error.withValues(alpha: 0.15),
                        shape: BoxShape.circle,
                        border: Border.all(
                          color: AppColors.error.withValues(alpha: 0.35),
                        ),
                      ),
                      child: const Icon(
                        Icons.stop_rounded,
                        size: 22,
                        color: AppColors.error,
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: AppSpacing.sm),
              ],
              // Send button — disabled until connected + has text
              AnimatedOpacity(
                duration: AppMotion.fast,
                opacity: _canSend ? 1.0 : 0.35,
                child: GestureDetector(
                  onTap: _canSend ? widget.onSend : null,
                  child: Container(
                    width: 44,
                    height: 44,
                    decoration: BoxDecoration(
                      gradient: _canSend
                          ? AppColors.accentGradient
                          : null,
                      color: _canSend ? null : AppColors.bgSurface,
                      shape: BoxShape.circle,
                      border: Border.all(
                        color: _canSend
                            ? Colors.transparent
                            : AppColors.borderDefault,
                      ),
                      boxShadow: _canSend ? AppElevation.card : AppElevation.none,
                    ),
                    child: Icon(
                      Icons.arrow_upward_rounded,
                      size: 20,
                      color: _canSend
                          ? AppColors.onAccent
                          : AppColors.textMuted,
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
