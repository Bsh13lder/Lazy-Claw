import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import '../chat/chat_controller.dart';
import '../chat/chat_message.dart';
import '../chat/chat_socket.dart';
import '../core/config/server_config.dart';
import '../notifications/local_notifications.dart';
import '../providers/auth_provider.dart';

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

class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});
  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _input = TextEditingController();
  bool _connected = false;
  String? _connectError;

  @override
  void initState() {
    super.initState();
    // Initialise local notifications the first time the chat screen mounts.
    // Safe to call multiple times — the implementation is idempotent.
    LocalNotifications.init();
    _connect();
  }

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

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
    } catch (e) {
      if (mounted) {
        setState(() => _connectError = e.toString());
      }
    }
  }

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

    return Scaffold(
      appBar: AppBar(
        title: Text(_connected ? 'Chat' : 'Connecting…'),
      ),
      body: Column(children: [
        if (_connectError != null)
          MaterialBanner(
            content: Text(_connectError!),
            actions: [
              TextButton(
                onPressed: _connect,
                child: const Text('Retry'),
              ),
            ],
          ),
        Expanded(
          child: ListView.builder(
            reverse: true,
            padding: const EdgeInsets.all(12),
            itemCount: messages.length,
            itemBuilder: (c, i) {
              final m = messages[messages.length - 1 - i];
              return _buildMessageWidget(context, m);
            },
          ),
        ),
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(8),
            child: Row(children: [
              Expanded(
                child: TextField(
                    controller: _input,
                    decoration:
                        const InputDecoration(hintText: 'Message LazyClaw…')),
              ),
              IconButton(
                icon: const Icon(Icons.send),
                // Disabled until the socket is connected.
                onPressed: _connected
                    ? () {
                        final t = _input.text.trim();
                        if (t.isEmpty) return;
                        ref.read(chatControllerProvider.notifier).send(t);
                        _input.clear();
                      }
                    : null,
              ),
            ]),
          ),
        ),
      ]),
    );
  }

  Widget _buildMessageWidget(BuildContext context, ChatMessage m) {
    if (m.role == 'bg_task' && m.bgTaskResult != null) {
      return _BgTaskCard(m.bgTaskResult!);
    }
    if (m.role == 'plan' && m.planText != null) {
      return _PlanCard(
        planText: m.planText!,
        steps: m.planSteps,
        onSend: (text) =>
            ref.read(chatControllerProvider.notifier).send(text),
      );
    }
    return _Bubble(m,
        onApprove: (id, ok) =>
            ref.read(chatControllerProvider.notifier).respondApproval(id, ok));
  }
}

// ── Chat bubble ────────────────────────────────────────────────────────────

class _Bubble extends StatelessWidget {
  final ChatMessage m;
  final void Function(String id, bool approved) onApprove;
  const _Bubble(this.m, {required this.onApprove});

  @override
  Widget build(BuildContext context) {
    final isUser = m.role == 'user';
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.8),
        decoration: BoxDecoration(
          color: isUser
              ? Theme.of(context).colorScheme.primary
              : Theme.of(context).colorScheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          isUser
              ? Text(m.content,
                  style: TextStyle(
                      color: Theme.of(context).colorScheme.onPrimary))
              : MarkdownBody(data: m.content.isEmpty ? '…' : m.content),
          // Tool activity chips
          if (m.toolActivities.isNotEmpty) ...[
            const SizedBox(height: 6),
            Wrap(
              spacing: 4,
              runSpacing: 4,
              children: m.toolActivities
                  .map((t) => _ToolChip(t))
                  .toList(),
            ),
          ],
          if (m.pendingApprovalId != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Row(children: [
                Text('Approve ${m.pendingApprovalSkill}?'),
                const Spacer(),
                TextButton(
                    onPressed: () => onApprove(m.pendingApprovalId!, false),
                    child: const Text('Deny')),
                FilledButton(
                    onPressed: () => onApprove(m.pendingApprovalId!, true),
                    child: const Text('Approve')),
              ]),
            ),
        ]),
      ),
    );
  }
}

// ── Tool activity chip ─────────────────────────────────────────────────────

class _ToolChip extends StatefulWidget {
  final ToolActivity activity;
  const _ToolChip(this.activity);

  @override
  State<_ToolChip> createState() => _ToolChipState();
}

class _ToolChipState extends State<_ToolChip> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final t = widget.activity;
    final isDone = t.resultPreview != null;
    final color = isDone
        ? Theme.of(context).colorScheme.secondaryContainer
        : Theme.of(context).colorScheme.tertiaryContainer;
    final onColor = isDone
        ? Theme.of(context).colorScheme.onSecondaryContainer
        : Theme.of(context).colorScheme.onTertiaryContainer;

    return GestureDetector(
      onTap: isDone ? () => setState(() => _expanded = !_expanded) : null,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: color,
          borderRadius: BorderRadius.circular(20),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(
                isDone ? Icons.check_circle_outline : Icons.settings_outlined,
                size: 14,
                color: onColor,
              ),
              const SizedBox(width: 4),
              Text(
                t.name,
                style: Theme.of(context)
                    .textTheme
                    .labelSmall
                    ?.copyWith(color: onColor),
              ),
              if (isDone && t.resultPreview!.isNotEmpty) ...[
                const SizedBox(width: 4),
                Icon(
                  _expanded
                      ? Icons.expand_less
                      : Icons.expand_more,
                  size: 14,
                  color: onColor,
                ),
              ],
            ]),
            if (_expanded && t.resultPreview != null && t.resultPreview!.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  t.resultPreview!,
                  style: Theme.of(context)
                      .textTheme
                      .labelSmall
                      ?.copyWith(color: onColor),
                  maxLines: 5,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
          ],
        ),
      ),
    );
  }
}

// ── Background task result card ─────────────────────────────────────────────

class _BgTaskCard extends StatelessWidget {
  final BackgroundTaskResult result;
  const _BgTaskCard(this.result);

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final success = result.success;
    final bg = success
        ? scheme.primaryContainer
        : scheme.errorContainer;
    final fg = success
        ? scheme.onPrimaryContainer
        : scheme.onErrorContainer;
    final icon = success ? Icons.check_circle : Icons.error_outline;

    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.85),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(icon, size: 18, color: fg),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  result.name,
                  style: Theme.of(context)
                      .textTheme
                      .labelMedium
                      ?.copyWith(fontWeight: FontWeight.w600, color: fg),
                ),
                if (result.detail != null && result.detail!.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      result.detail!,
                      style: Theme.of(context)
                          .textTheme
                          .labelSmall
                          ?.copyWith(color: fg),
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                if (result.durationMs != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      '${(result.durationMs! / 1000).toStringAsFixed(1)}s',
                      style: Theme.of(context)
                          .textTheme
                          .labelSmall
                          ?.copyWith(color: fg.withValues(alpha: 0.7)),
                    ),
                  ),
              ],
            ),
          ),
        ]),
      ),
    );
  }
}

// ── Plan card ──────────────────────────────────────────────────────────────

class _PlanCard extends StatelessWidget {
  final String planText;
  final List<String> steps;
  final void Function(String) onSend;
  const _PlanCard({
    required this.planText,
    required this.steps,
    required this.onSend,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.all(14),
        constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.9),
        decoration: BoxDecoration(
          color: scheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: scheme.outline.withValues(alpha: 0.4)),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(Icons.assignment_outlined,
                size: 16, color: scheme.primary),
            const SizedBox(width: 6),
            Text(
              'Agent Plan',
              style: Theme.of(context)
                  .textTheme
                  .labelLarge
                  ?.copyWith(color: scheme.primary, fontWeight: FontWeight.w600),
            ),
          ]),
          const SizedBox(height: 8),
          Text(planText,
              style: Theme.of(context).textTheme.bodySmall),
          if (steps.isNotEmpty) ...[
            const SizedBox(height: 8),
            ...steps.asMap().entries.map((e) => Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${e.key + 1}. ',
                        style: Theme.of(context).textTheme.labelSmall,
                      ),
                      Expanded(
                        child: Text(
                          e.value,
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ),
                    ],
                  ),
                )),
          ],
          const SizedBox(height: 10),
          // Note: plan approval is signaled via a normal message frame.
          // Tapping "Approve" sends "approve" as a user message; "Reject" sends "reject".
          Row(children: [
            OutlinedButton(
              onPressed: () => onSend('reject'),
              child: const Text('Reject'),
            ),
            const SizedBox(width: 8),
            FilledButton(
              onPressed: () => onSend('approve'),
              child: const Text('Approve'),
            ),
          ]),
        ]),
      ),
    );
  }
}
