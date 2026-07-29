import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/actions/pending_deep_link.dart';
import '../../providers/notifications_center_provider.dart';
import '../../repositories/notifications_repository.dart';
import '../../ui/ui.dart';

/// The durable Notification Center — the single browsable home for everything
/// the server fired (watcher hits, reminders, task results, channel messages,
/// approvals). Replaces the old fire-and-forget OS toasts that left no trace.
/// Tapping a row marks it read and deep-links to its target.
class NotificationsCenterScreen extends ConsumerStatefulWidget {
  const NotificationsCenterScreen({super.key});

  @override
  ConsumerState<NotificationsCenterScreen> createState() =>
      _NotificationsCenterScreenState();
}

class _NotificationsCenterScreenState
    extends ConsumerState<NotificationsCenterScreen> {
  /// Multi-select mode: long-press a row to enter, then tap rows to (de)select
  /// and "Mark N read" to clear just those. Empty selection exits the mode.
  bool _selectionMode = false;
  final Set<String> _selected = <String>{};

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(notificationsCenterProvider.notifier).load();
    });
  }

  Future<void> _onRefresh() =>
      ref.read(notificationsCenterProvider.notifier).refresh();

  void _onTap(ServerNotification n) {
    ref.read(notificationsCenterProvider.notifier).markRead(n.id);
    // Only navigate away when the row has a specific target — a generic row
    // (routePath == the center itself) just marks read in place.
    final route = n.routePath;
    if (route != '/notifications') {
      ref.read(pendingDeepLinkProvider.notifier).state = route;
    }
  }

  /// Row tap: toggle selection while selecting, else the normal read+deep-link.
  void _onRowTap(ServerNotification n) {
    if (_selectionMode) {
      _toggleSelect(n.id);
    } else {
      _onTap(n);
    }
  }

  void _enterSelection(ServerNotification n) {
    HapticFeedback.selectionClick();
    setState(() {
      _selectionMode = true;
      _selected.add(n.id);
    });
  }

  void _toggleSelect(String id) {
    setState(() {
      if (!_selected.remove(id)) _selected.add(id);
      if (_selected.isEmpty) _selectionMode = false;
    });
  }

  void _exitSelection() {
    setState(() {
      _selectionMode = false;
      _selected.clear();
    });
  }

  void _markSelectedRead() {
    final ids = Set<String>.from(_selected);
    ref.read(notificationsCenterProvider.notifier).markReadMany(ids);
    _exitSelection();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(notificationsCenterProvider);
    return LzScaffold(
      appBar: _selectionMode
          ? LzAppBar(
              title: '${_selected.length} selected',
              leading: LzIconButton(
                icon: Icons.close,
                tooltip: 'Cancel',
                onPressed: _exitSelection,
              ),
              actions: [
                LzIconButton(
                  icon: Icons.done_all,
                  tooltip: 'Mark selected read',
                  onPressed: _markSelectedRead,
                ),
              ],
            )
          : LzAppBar(
              title: 'Notifications',
              actions: [
                if (state.unread > 0)
                  LzIconButton(
                    icon: Icons.done_all,
                    tooltip: 'Mark all read',
                    onPressed: () => ref
                        .read(notificationsCenterProvider.notifier)
                        .markAllRead(),
                  ),
              ],
            ),
      body: LzRefresh(onRefresh: _onRefresh, child: _body(state)),
    );
  }

  Widget _body(NotificationsCenterState state) {
    if (state.isLoading && !state.hasData) {
      return LzSkeleton.list(
        count: 6,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
      );
    }
    if (state.error != null && !state.hasData) {
      return LzEmptyState(
        icon: Icons.cloud_off_outlined,
        title: 'Could not load notifications',
        hint: state.error,
        actionLabel: 'Retry',
        actionIcon: Icons.refresh,
        onAction: () =>
            ref.read(notificationsCenterProvider.notifier).load(),
      );
    }
    if (state.items.isEmpty) {
      return const LzEmptyState(
        icon: Icons.notifications_none,
        title: 'No notifications yet',
        hint: 'Watcher alerts, reminders, task results and new messages will '
            'appear here — and stay, so you can always scroll back.',
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      itemCount: state.items.length,
      itemBuilder: (_, i) {
        final n = state.items[i];
        return Padding(
          padding: const EdgeInsets.only(bottom: AppSpacing.sm),
          child: _NotificationTile(
            n: n,
            selectionMode: _selectionMode,
            selected: _selected.contains(n.id),
            onTap: () => _onRowTap(n),
            onLongPress: () => _enterSelection(n),
          ),
        );
      },
    );
  }
}

// ── Row ──────────────────────────────────────────────────────────────────────

class _NotificationTile extends StatelessWidget {
  const _NotificationTile({
    required this.n,
    required this.onTap,
    this.onLongPress,
    this.selectionMode = false,
    this.selected = false,
  });

  final ServerNotification n;
  final VoidCallback onTap;
  final VoidCallback? onLongPress;
  final bool selectionMode;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final accent = _severityColor(n.severity);
    final unread = n.isUnread;
    final card = LzCard(
      onTap: onTap,
      borderColor: selected ? AppColors.accent : null,
      color: selected
          ? AppColors.accent.withValues(alpha: 0.12)
          : (unread ? AppColors.bgSurfaceElevated : AppColors.bgSurface),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (selectionMode) ...[
            Icon(
              selected ? Icons.check_circle : Icons.radio_button_unchecked,
              size: 22,
              color: selected ? AppColors.accent : AppColors.textMuted,
            ),
            const SizedBox(width: AppSpacing.md),
          ],
          Container(
            width: 34,
            height: 34,
            decoration: BoxDecoration(
              color: accent.withValues(alpha: 0.14),
              borderRadius: AppRadii.rMd,
            ),
            child: Icon(_kindIcon(n.kind), size: 18, color: accent),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        n.title.isNotEmpty ? n.title : 'Notification',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: AppText.body.copyWith(
                          color: AppColors.textPrimary,
                          fontWeight:
                              unread ? FontWeight.w700 : FontWeight.w500,
                        ),
                      ),
                    ),
                    if (n.repeatCount > 1) ...[
                      const SizedBox(width: AppSpacing.sm),
                      LzBadge(count: n.repeatCount, color: accent),
                    ],
                    if (unread) ...[
                      const SizedBox(width: AppSpacing.sm),
                      Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(
                          color: accent,
                          shape: BoxShape.circle,
                        ),
                      ),
                    ],
                  ],
                ),
                if (n.body.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(
                    n.body,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: AppText.caption.copyWith(
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
                const SizedBox(height: 4),
                Text(
                  _relativeTime(n.createdAt),
                  style: AppText.caption.copyWith(color: AppColors.textMuted),
                ),
              ],
            ),
          ),
        ],
      ),
    );

    if (onLongPress == null) return card;
    // Long-press enters multi-select. GestureDetector's long-press recognizer
    // coexists with the card's InkWell tap (distinguished by press duration).
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onLongPress: onLongPress,
      child: card,
    );
  }
}

Color _severityColor(String severity) {
  switch (severity) {
    case 'urgent':
      return AppColors.error;
    case 'important':
      return AppColors.warn;
    case 'info':
      return AppColors.info;
    default:
      return AppColors.accent;
  }
}

IconData _kindIcon(String kind) {
  final k = kind.toLowerCase();
  if (k.contains('message')) return Icons.chat_bubble_outline;
  if (k.contains('approval')) return Icons.verified_user_outlined;
  if (k.contains('watcher')) return Icons.visibility_outlined;
  if (k.contains('reminder') || k.contains('nag') || k.contains('task')) {
    return Icons.check_circle_outline;
  }
  if (k.contains('contract') || k.contains('intake')) {
    return Icons.work_outline;
  }
  if (k.contains('done') || k.contains('background') || k.contains('result')) {
    return Icons.bolt_outlined;
  }
  if (k.contains('fail') || k.contains('error')) {
    return Icons.error_outline;
  }
  return Icons.notifications_none;
}

/// Compact relative-time label from an ISO timestamp. Falls back to the raw
/// string (or empty) rather than throwing on an unparseable value.
String _relativeTime(String iso) {
  if (iso.isEmpty) return '';
  final t = DateTime.tryParse(iso);
  if (t == null) return iso;
  final diff = DateTime.now().difference(t.toLocal());
  if (diff.inSeconds < 60) return 'just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  if (diff.inDays < 7) return '${diff.inDays}d ago';
  final w = (diff.inDays / 7).floor();
  if (w < 5) return '${w}w ago';
  final mo = (diff.inDays / 30).floor();
  return '${mo}mo ago';
}
