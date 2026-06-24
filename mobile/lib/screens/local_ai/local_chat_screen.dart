/// On-device chat — talks to the local model, fully offline.
///
/// Reuses the server chat's [ChatBubble] for rendering; the messages come from
/// [localChatControllerProvider], which streams tokens straight from the
/// llama.cpp engine on this phone. No WebSocket, no network.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons/lucide_icons.dart';

import '../../local_ai/local_ai_providers.dart';
import '../../local_ai/local_model.dart';
import '../chat/chat_bubble.dart';
import '../../ui/ui.dart';

class LocalChatScreen extends ConsumerStatefulWidget {
  const LocalChatScreen({super.key});

  @override
  ConsumerState<LocalChatScreen> createState() => _LocalChatScreenState();
}

class _LocalChatScreenState extends ConsumerState<LocalChatScreen> {
  final _input = TextEditingController();
  final _scroll = ScrollController();

  @override
  void dispose() {
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _send() {
    final text = _input.text.trim();
    if (text.isEmpty) return;
    ref.read(localChatControllerProvider.notifier).send(text);
    _input.clear();
    _scrollToBottomSoon();
  }

  void _scrollToBottomSoon() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final messages = ref.watch(localChatControllerProvider);
    final controller = ref.read(localChatControllerProvider.notifier);
    final ai = ref.watch(localAiControllerProvider);
    final modelName =
        localModelById(ai.activeModelId ?? '')?.displayName ?? 'Local AI';
    // Keep the view pinned to the newest token as it streams in.
    if (messages.isNotEmpty) _scrollToBottomSoon();

    return LzScaffold(
      appBar: LzAppBar(
        title: '$modelName · on-device',
        leading: LzIconButton(
          icon: LucideIcons.arrowLeft,
          onPressed: () => Navigator.of(context).maybePop(),
        ),
      ),
      body: Column(
        children: [
          Expanded(
            child: messages.isEmpty
                ? const LzEmptyState(
                    icon: LucideIcons.cpu,
                    title: 'Chat runs on this phone',
                    hint: 'Messages never leave the device. Ask anything.',
                  )
                : ListView.builder(
                    controller: _scroll,
                    padding: const EdgeInsets.all(AppSpacing.md),
                    itemCount: messages.length,
                    itemBuilder: (_, i) => Padding(
                      padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                      child: ChatBubble(messages[i], onApprove: (_, _) {}),
                    ),
                  ),
          ),
          _InputRow(
            controller: _input,
            streaming: controller.isStreaming,
            onSend: _send,
            onStop: controller.cancel,
          ),
        ],
      ),
    );
  }
}

class _InputRow extends StatelessWidget {
  const _InputRow({
    required this.controller,
    required this.streaming,
    required this.onSend,
    required this.onStop,
  });

  final TextEditingController controller;
  final bool streaming;
  final VoidCallback onSend;
  final VoidCallback onStop;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(
          AppSpacing.md, AppSpacing.sm, AppSpacing.md, AppSpacing.md),
      decoration: const BoxDecoration(
        color: AppColors.bgSurface,
        border: Border(top: BorderSide(color: AppColors.borderDefault)),
      ),
      child: SafeArea(
        top: false,
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Expanded(
              child: LzTextField(
                controller: controller,
                hint: 'Message the local model…',
                minLines: 1,
                maxLines: 5,
                onSubmitted: (_) => streaming ? null : onSend(),
              ),
            ),
            const SizedBox(width: AppSpacing.sm),
            LzIconButton(
              icon: streaming ? LucideIcons.square : LucideIcons.arrowUp,
              filled: true,
              accent: true,
              onPressed: streaming ? onStop : onSend,
            ),
          ],
        ),
      ),
    );
  }
}
