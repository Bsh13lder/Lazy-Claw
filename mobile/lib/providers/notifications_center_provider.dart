import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../repositories/notifications_repository.dart';
import 'auth_provider.dart';

// ── Infrastructure ──────────────────────────────────────────────────────────

final notificationsRepositoryProvider =
    Provider<NotificationsRepository>((ref) {
  return NotificationsRepository(
    DioNotificationsTransport(ref.watch(apiClientProvider)),
  );
});

/// Lightweight unread-count for the Home app-bar bell badge. Auto-disposes;
/// re-runs when invalidated (after a mark-read, or a Home rebuild/refresh).
/// Fails soft to 0 so the badge never blocks the header.
final unreadCountProvider = FutureProvider.autoDispose<int>((ref) async {
  try {
    return await ref.watch(notificationsRepositoryProvider).fetchUnreadCount();
  } catch (_) {
    return 0;
  }
});

// ── State ───────────────────────────────────────────────────────────────────

class NotificationsCenterState {
  final List<ServerNotification> items;
  final int unread;
  final bool isLoading;
  final String? error;

  const NotificationsCenterState({
    this.items = const [],
    this.unread = 0,
    this.isLoading = false,
    this.error,
  });

  bool get hasData => items.isNotEmpty;

  NotificationsCenterState copyWith({
    List<ServerNotification>? items,
    int? unread,
    bool? isLoading,
    String? error,
    bool clearError = false,
  }) =>
      NotificationsCenterState(
        items: items ?? this.items,
        unread: unread ?? this.unread,
        isLoading: isLoading ?? this.isLoading,
        error: clearError ? null : (error ?? this.error),
      );
}

// ── Notifier ────────────────────────────────────────────────────────────────

class NotificationsCenterNotifier
    extends StateNotifier<NotificationsCenterState> {
  NotificationsCenterNotifier(this._repo, this._ref)
      : super(const NotificationsCenterState());

  final NotificationsRepository _repo;
  final Ref _ref;

  /// Full pull (no cursor) — the center always shows the complete history,
  /// newest last as the server returns it (we reverse for display).
  Future<void> load() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final feed = await _repo.fetchSince();
      state = state.copyWith(
        items: feed.notifications.reversed.toList(),
        unread: feed.unread,
        isLoading: false,
        clearError: true,
      );
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
    }
  }

  /// Silent reload for pull-to-refresh (no skeleton flicker).
  Future<void> refresh() async {
    try {
      final feed = await _repo.fetchSince();
      state = state.copyWith(
        items: feed.notifications.reversed.toList(),
        unread: feed.unread,
        clearError: true,
      );
    } catch (e) {
      if (!state.hasData) state = state.copyWith(error: e.toString());
    }
  }

  /// Mark one notification read — optimistic, then reconcile the badge.
  Future<void> markRead(String id) async {
    _applyRead({id});
    try {
      await _repo.markRead([id]);
    } catch (_) {
      // best-effort; a later refresh reconciles
    }
    _ref.invalidate(unreadCountProvider);
  }

  /// Mark a chosen subset read (multi-select) — optimistic, then reconcile.
  /// Reuses the same bulk-by-ids endpoint as [markRead]; [_applyRead] is
  /// already set-based so any number of ids apply in one pass.
  Future<void> markReadMany(Set<String> ids) async {
    if (ids.isEmpty) return;
    _applyRead(ids);
    try {
      await _repo.markRead(ids.toList());
    } catch (_) {
      // best-effort; a later refresh reconciles
    }
    _ref.invalidate(unreadCountProvider);
  }

  /// Mark everything read — optimistic, then reconcile.
  Future<void> markAllRead() async {
    _applyRead(state.items.map((n) => n.id).toSet());
    try {
      await _repo.markAllRead();
    } catch (_) {}
    _ref.invalidate(unreadCountProvider);
  }

  void _applyRead(Set<String> ids) {
    final nowIso = DateTime.now().toUtc().toIso8601String();
    final updated = [
      for (final n in state.items)
        if (ids.contains(n.id) && n.isUnread)
          ServerNotification(
            id: n.id,
            kind: n.kind,
            title: n.title,
            body: n.body,
            createdAt: n.createdAt,
            severity: n.severity,
            deepLink: n.deepLink,
            readAt: nowIso,
            repeatCount: n.repeatCount,
          )
        else
          n,
    ];
    final unread = updated.where((n) => n.isUnread).length;
    state = state.copyWith(items: updated, unread: unread);
  }
}

// ── Provider ────────────────────────────────────────────────────────────────

final notificationsCenterProvider = StateNotifierProvider<
    NotificationsCenterNotifier, NotificationsCenterState>((ref) {
  return NotificationsCenterNotifier(
    ref.watch(notificationsRepositoryProvider),
    ref,
  );
});
