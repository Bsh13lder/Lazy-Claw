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
///
/// Telegram is intentionally absent: it's the control channel (no MCP read
/// tool / ChannelGateway dispatch), so a telegram thread could never be
/// opened — advertising it produced empty "No messages yet" opens (BUG 7).
const _kChannels = [
  (key: null, label: 'All', icon: Icons.all_inbox_outlined),
  (key: 'whatsapp', label: 'WhatsApp', icon: Icons.chat_outlined),
  (key: 'email', label: 'Email', icon: Icons.email_outlined),
  (key: 'instagram', label: 'Instagram', icon: Icons.camera_alt_outlined),
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

/// A distinct on-palette hue per channel so rows are scannable at a glance
/// without avatars. Uses only design-system tokens (no hard-coded colors).
Color _channelColor(String channel) {
  switch (channel) {
    case 'whatsapp':
      return AppColors.success; // green
    case 'email':
      return AppColors.info; // cyan
    case 'instagram':
      return AppColors.warn; // amber
    default:
      return AppColors.accent;
  }
}

// ── Screen ────────────────────────────────────────────────────────────────────

/// Standalone Inbox screen — an [LzScaffold] wrapper around [InboxView].
///
/// The inbox now lives as a top segment INSIDE the Chat tab (same pattern as
/// Notes inside the Tasks tab); this wrapper remains for tests and any
/// full-screen embedding.
class InboxScreen extends ConsumerWidget {
  const InboxScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return const LzScaffold(
      title: 'Inbox',
      body: InboxView(),
    );
  }
}

/// Embeddable inbox body: channel filter row + thread list.
///
/// Watches [inboxThreadsProvider] (AsyncValue) and
/// [inboxChannelFilterProvider] (the active channel key, null = All).
/// Hosted inside the Chat tab's Chat ⇄ Inbox segment.
class InboxView extends ConsumerWidget {
  const InboxView({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final threadsAsync = ref.watch(inboxThreadsProvider);

    return Column(
        children: [
          // ── Channel filter chip row ──────────────────────────────────────
          const _ChannelFilterRow(),
          // ── Threads list / loading / empty ───────────────────────────────
          Expanded(
            child: threadsAsync.when(
              loading: () => LzSkeleton.list(),
              error: (e, st) => LzErrorState(
                icon: Icons.error_outline,
                message: 'Could not load messages. Check your connection and try again.',
                onRetry: () => ref.invalidate(inboxThreadsProvider),
              ),
              data: (threads) {
                // Pull-to-refresh works even when the list is empty or short —
                // the child is always a scrollable with overscroll physics.
                Future<void> refresh() async {
                  ref.invalidate(inboxThreadsProvider);
                  await ref.read(inboxThreadsProvider.future);
                }

                if (threads.isEmpty) {
                  return LzRefresh(
                    onRefresh: refresh,
                    child: ListView(
                      physics: const AlwaysScrollableScrollPhysics(),
                      children: const [
                        SizedBox(height: AppSpacing.xxxl * 2),
                        LzEmptyState(
                          icon: Icons.inbox_outlined,
                          title: 'No messages yet',
                          hint: 'Replies from WhatsApp, Email and Instagram '
                              'will appear here.',
                        ),
                      ],
                    ),
                  );
                }
                return LzRefresh(
                  onRefresh: refresh,
                  child: ListView.builder(
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: const EdgeInsets.only(bottom: AppSpacing.xxxl),
                    itemCount: threads.length,
                    itemBuilder: (context, index) {
                      final thread = threads[index];
                      return _ThreadRow(thread: thread);
                    },
                  ),
                );
              },
            ),
          ),
        ],
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

/// Trailing meta for a thread row: last-activity time stacked above the unread
/// badge (like every messaging app). Returns null when there's neither, so the
/// [LzListTile] doesn't reserve trailing space.
Widget? _threadTrailing(InboxThread thread) {
  final time = formatInboxTimestamp(thread.lastActivity);
  final hasTime = time.isNotEmpty;
  final hasBadge = thread.unreadCount > 0;
  if (!hasTime && !hasBadge) return null;
  return Column(
    mainAxisSize: MainAxisSize.min,
    crossAxisAlignment: CrossAxisAlignment.end,
    children: [
      if (hasTime)
        Text(
          time,
          style: AppText.caption.copyWith(color: AppColors.textMuted),
        ),
      if (hasTime && hasBadge) AppSpacing.vGap(AppSpacing.xs),
      if (hasBadge) LzBadge(count: thread.unreadCount),
    ],
  );
}

class _ThreadRow extends StatelessWidget {
  const _ThreadRow({required this.thread});

  final InboxThread thread;

  @override
  Widget build(BuildContext context) {
    // BUG 8 — a row whose encrypted fields failed to decrypt server-side can't
    // be opened (its handle is the "[encrypted]" sentinel, not a resolvable
    // contact). Render a distinct, muted, NON-tappable "unreadable" state so
    // it never masquerades as a normal thread that opens empty.
    if (thread.decryptError) {
      return const _UnreadableThreadRow();
    }

    final displayName = (thread.contactName?.isNotEmpty == true)
        ? thread.contactName!
        : prettyHandle(thread.channel, thread.contactHandle);
    final preview = (thread.lastPreview?.isNotEmpty == true)
        ? thread.lastPreview
        : null; // collapse the subtitle line when there's no preview

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        LzListTile(
          leading: Icon(
            _channelIcon(thread.channel),
            size: _kChannelIconSize,
            color: _channelColor(thread.channel),
          ),
          title: displayName,
          subtitle: preview,
          trailing: _threadTrailing(thread),
          onTap: () => context.push(
            '/inbox/${thread.id}?title=${Uri.encodeComponent(displayName)}',
          ),
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

/// Muted, non-tappable row for a thread whose server-side decrypt failed.
class _UnreadableThreadRow extends StatelessWidget {
  const _UnreadableThreadRow();

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        LzListTile(
          leading: const Icon(
            Icons.error_outline,
            size: _kChannelIconSize,
            color: AppColors.textMuted,
          ),
          title: 'Unreadable message',
          titleStyle: AppText.body.copyWith(color: AppColors.textMuted),
          subtitle: 'Could not decrypt — re-sync to restore.',
          // onTap intentionally omitted → the row is not openable.
        ),
        const Divider(
          height: 1,
          color: AppColors.borderSubtle,
          indent: AppSpacing.lg + _kChannelIconSize + AppSpacing.md,
        ),
      ],
    );
  }
}
