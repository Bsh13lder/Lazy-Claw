import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:lazyclaw_mobile/comms/inbox_models.dart';
import 'package:lazyclaw_mobile/comms/inbox_providers.dart';
import 'package:lazyclaw_mobile/screens/inbox/inbox_instruction_sheet.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

// ── Constants ─────────────────────────────────────────────────────────────────

/// Icon size for the per-channel leading icon.
/// Matches [AppSpacing.xl] (24 pt) so it aligns with the kit's icon scale.
const _kChannelIconSize = AppSpacing.xl;

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
    final threadsAsync = ref.watch(inboxThreadsProvider);

    return LzScaffold(
      title: 'Inbox',
      body: Column(
        children: [
          // ── Channel filter chip row ──────────────────────────────────────
          const _ChannelFilterRow(),
          // ── Threads list / loading / empty ───────────────────────────────
          Expanded(
            child: threadsAsync.when(
              loading: () => const Center(
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: AppColors.accent,
                ),
              ),
              error: (e, st) => LzErrorState(
                icon: Icons.error_outline,
                message: 'Could not load messages. Check your connection and try again.',
                onRetry: () => ref.invalidate(inboxThreadsProvider),
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

/// Scrollable row of [LzChip] filters, one per channel.
///
/// Converted to [ConsumerWidget] so it owns its own [ref] — never receives
/// [WidgetRef] as a constructor argument.
class _ChannelFilterRow extends ConsumerWidget {
  const _ChannelFilterRow();

  /// Channels that support standing instructions (server: MCP watcher
  /// services — telegram is the control channel itself).
  static const _instructable = {'whatsapp', 'email', 'instagram'};

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final activeFilter = ref.watch(inboxChannelFilterProvider);

    return SizedBox(
      height: AppSpacing.xxxl, // 48 pt — matches the chip bar spec
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
          // Auto-pilot editor for the selected channel (standing instruction).
          if (activeFilter != null && _instructable.contains(activeFilter))
            LzChip(
              label: 'Auto-pilot',
              icon: Icons.bolt_rounded,
              selected: false,
              onTap: () => showChannelInstructionSheet(context, activeFilter),
            ),
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
            size: _kChannelIconSize,
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
          color: AppColors.borderSubtle,
          indent: AppSpacing.lg + _kChannelIconSize + AppSpacing.md, // align under title
        ),
      ],
    );
  }
}
