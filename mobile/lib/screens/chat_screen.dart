import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import '../chat/chat_controller.dart';
import '../chat/chat_socket.dart';
import '../chat/chat_message.dart';
import '../core/config/server_config.dart';
import '../providers/auth_provider.dart';

final chatSocketProvider = Provider<ChatSocket>((ref) => ChatSocket());

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

  @override
  void initState() {
    super.initState();
    _connect();
  }

  Future<void> _connect() async {
    final base = ref.read(baseUrlProvider);
    final cookie = await ref.read(apiClientProvider).getSessionCookie();
    if (cookie == null) return;
    await ref.read(chatSocketProvider).connect(
          ServerConfig.wsUrlFor(base),
          cookie: 'session_id=$cookie',
        );
    if (mounted) setState(() => _connected = true);
  }

  @override
  Widget build(BuildContext context) {
    final messages = ref.watch(chatControllerProvider);
    return Scaffold(
      appBar: AppBar(
        title: Text(_connected ? 'Chat' : 'Connecting…'),
        actions: [
          IconButton(
            icon: const Icon(Icons.logout),
            onPressed: () => ref.read(authProvider.notifier).logout(),
          ),
        ],
      ),
      body: Column(children: [
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
                child: TextField(controller: _input,
                    decoration:
                        const InputDecoration(hintText: 'Message LazyClaw…')),
              ),
              IconButton(
                icon: const Icon(Icons.send),
                onPressed: () {
                  final t = _input.text.trim();
                  if (t.isEmpty) return;
                  ref.read(chatControllerProvider.notifier).send(t);
                  _input.clear();
                },
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
                  style: const TextStyle(color: Colors.white))
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
