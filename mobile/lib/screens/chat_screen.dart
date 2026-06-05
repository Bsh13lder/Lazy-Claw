import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import '../chat/chat_controller.dart';
import '../chat/chat_socket.dart';
import '../chat/chat_message.dart';
import '../core/config/server_config.dart';
import '../providers/auth_provider.dart';

final chatSocketProvider = Provider<ChatSocket>((ref) {
  final s = ChatSocket();
  ref.onDispose(s.dispose);
  return s;
});

final chatControllerProvider =
    StateNotifierProvider<ChatController, List<ChatMessage>>(
        (ref) => ChatController(ref.watch(chatSocketProvider)));

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
              return _Bubble(m, onApprove: (id, ok) =>
                  ref.read(chatControllerProvider.notifier).respondApproval(id, ok));
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
}

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
