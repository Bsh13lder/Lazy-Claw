import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lazyclaw_mobile/comms/inbox_models.dart';
import 'package:lazyclaw_mobile/comms/inbox_providers.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

// ── Channel filter chips ───────────────────────────────────────────────────────

/// Ordered list of channel filter entries: (key, label, icon).
/// key=null means "All".
const _kChannels = [
  (key: null, label: 'All', icon: Icons.all_inbox_outlined),
  (key: 'whatsapp', label: 'WhatsApp', icon: Icons.chat_outlined),
  (key: 'email', label: 'Email', icon: Icons.email_outlined),
  (key: 'instagram', label: 'Instagram', icon: Icons.camera_alt_outlined),
  (key: 'telegram', label: 'Telegram', icon: Icons.send_outlined),
];

// ── Leading icon per channel ───────────────────────────────────────────────────

IconData _channelIcon(String channel) {
  switch (channel) {
    case 'whatsapp':
      return Icons.chat_outlined;
    case 'email':
      return Icons.email_outlined;
    case 'instagram':
      return Icons.camera_alt_outlined;
    case 'telegram':
      return Icons.send_outlined;
    default:
      return Icons.message_outlined;
  }
}

// ── Screen ────────────────────────────────────────────────────────────────────

/// Inbox list screen. Shows all unified-inbox threads, filtered by channel.
///
/// Watches [inboxThreadsProvider] (AsyncValue) and
/// [inboxChannelFilterProvider] (the active channel key, null = All).
class InboxScreen extends ConsumerWidget {
  const InboxScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final filter = ref.watch(inboxChannelFilterProvider);
    final threadsAsync = ref.watch(inboxThreadsProvider);

    return LzScaffold(
      title: 'Inbox',
      body: Column(
        children: [
          // ── Channel filter chip row ──────────────────────────────────────
          _ChannelFilterRow(activeFilter: filter, ref: ref),
          // ── Threads list / loading / empty ───────────────────────────────
          Expanded(
            child: threadsAsync.when(
              loading: () => const Center(
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: AppColors.accent,
                ),
              ),
              error: (e, st) => const LzEmptyState(
                icon: Icons.error_outline,
                title: 'Could not load messages',
                hint: 'Check your connection and try again.',
              ),
              data: (threads) {
                if (threads.isEmpty) {
                  return const LzEmptyState(
                    icon: Icons.inbox_outlined,
                    title: 'No messages yet',
                    hint: 'Replies from WhatsApp, Email, Instagram and '
                        'Telegram will appear here.',
                  );
                }
                return ListView.builder(
                  padding: const EdgeInsets.only(bottom: AppSpacing.xxxl),
                  itemCount: threads.length,
                  itemBuilder: (context, index) {
                    final thread = threads[index];
                    return _ThreadRow(thread: thread);
                  },
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

// ── Channel filter row ─────────────────────────────────────────────────────────

class _ChannelFilterRow extends StatelessWidget {
  const _ChannelFilterRow({
    required this.activeFilter,
    required this.ref,
  });

  final String? activeFilter;
  final WidgetRef ref;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 48,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: AppSpacing.listH,
        children: [
          for (final entry in _kChannels) ...[
            LzChip(
              label: entry.label,
              icon: entry.icon,
              selected: activeFilter == entry.key,
              onTap: () => ref
                  .read(inboxChannelFilterProvider.notifier)
                  .state = entry.key,
            ),
            AppSpacing.hGap(AppSpacing.sm),
          ],
        ],
      ),
    );
  }
}

// ── Thread row ─────────────────────────────────────────────────────────────────

class _ThreadRow extends StatelessWidget {
  const _ThreadRow({required this.thread});

  final InboxThread thread;

  @override
  Widget build(BuildContext context) {
    final displayName = (thread.contactName?.isNotEmpty == true)
        ? thread.contactName!
        : thread.contactHandle;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        LzListTile(
          leading: Icon(
            _channelIcon(thread.channel),
            size: 22,
            color: AppColors.accent,
          ),
          title: displayName,
          subtitle: thread.lastPreview ?? '',
          trailing: thread.unreadCount > 0
              ? LzBadge(count: thread.unreadCount)
              : null,
          onTap: () => context.push('/inbox/${thread.id}'),
        ),
        const Divider(
          height: 1,
          thickness: 1,
          color: AppColors.borderSubtle,
          indent: AppSpacing.lg + 22 + AppSpacing.md, // align under title
        ),
      ],
    );
  }
}
