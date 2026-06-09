import '../core/api/api_client.dart';
import '../repositories/notifications_repository.dart';
import '../screens/settings/settings_prefs.dart';
import 'local_notifications.dart';

/// Pure selector: given the [fetched] feed and the set of already-[seenIds],
/// return only the notifications that are NEW — id not previously seen — also
/// de-duplicated within the batch, preserving the server's order.
///
/// Notifications with an empty id are dropped (we cannot dedup them safely).
/// Kept as a free function with no IO so the "which items are new" decision is
/// trivially unit-testable.
List<ServerNotification> selectNewNotifications(
  List<ServerNotification> fetched,
  Set<String> seenIds,
) {
  final result = <ServerNotification>[];
  final batchSeen = <String>{};
  for (final n in fetched) {
    if (n.id.isEmpty) continue;
    if (seenIds.contains(n.id)) continue;
    if (!batchSeen.add(n.id)) continue; // duplicate within this batch
    result.add(n);
  }
  return result;
}

/// Pulls the server notification feed, surfaces only the NEW items as local
/// notifications, and advances the stored `since` cursor.
///
/// Every dependency is injected so the whole flow runs in a background isolate
/// (no Riverpod scope) AND is unit-testable with fakes. [pullOnce] never throws
/// — it is offline-tolerant by design (a failed fetch simply shows nothing and
/// leaves the cursor untouched so the next pass retries the same window).
class NotificationsFeedService {
  NotificationsFeedService({
    required NotificationsRepository repo,
    required Future<void> Function(ServerNotification) show,
    required Future<String?> Function() loadSince,
    required Future<void> Function(String) saveSince,
    required Future<List<String>> Function() loadSeenIds,
    required Future<void> Function(List<String>) saveSeenIds,
    int maxSeenIds = 200,
  })  : _repo = repo,
        _show = show,
        _loadSince = loadSince,
        _saveSince = saveSince,
        _loadSeenIds = loadSeenIds,
        _saveSeenIds = saveSeenIds,
        _maxSeenIds = maxSeenIds;

  final NotificationsRepository _repo;
  final Future<void> Function(ServerNotification) _show;
  final Future<String?> Function() _loadSince;
  final Future<void> Function(String) _saveSince;
  final Future<List<String>> Function() _loadSeenIds;
  final Future<void> Function(List<String>) _saveSeenIds;
  final int _maxSeenIds;

  /// Run one catch-up pass. Returns the number of NEW notifications shown.
  Future<int> pullOnce() async {
    try {
      final since = await _loadSince();
      final feed = await _repo.fetchSince(since: since);
      final seen = (await _loadSeenIds()).toSet();

      final fresh = selectNewNotifications(feed.notifications, seen);
      for (final n in fresh) {
        await _show(n);
      }

      // Advance the cursor to the server clock — only when the server gave us
      // a real `now`, so an empty response can't reset us to the start.
      if (feed.now.isNotEmpty) {
        await _saveSince(feed.now);
      }

      // Persist a bounded ring of recently-shown ids so an inclusive `since`
      // boundary (server returning an item whose timestamp == cursor) can't
      // re-notify the same item next pass.
      if (fresh.isNotEmpty) {
        final merged = <String>[...seen, ...fresh.map((n) => n.id)];
        final bounded = merged.length > _maxSeenIds
            ? merged.sublist(merged.length - _maxSeenIds)
            : merged;
        await _saveSeenIds(bounded);
      }

      return fresh.length;
    } catch (_) {
      // Offline / transient backend error — never propagate. The next trigger
      // (resume / WS-connect / headless pass) retries the same window.
      return 0;
    }
  }
}

/// Dispatch a single [ServerNotification] to the appropriate local-notification
/// channel based on its [kind].
///
/// `channel_message` entries use the inbox channel (payload `'inbox'`) so a
/// tap opens the Inbox list. All other kinds fall back to the general server
/// notifications channel (payload `'chat'`).
Future<void> _showFeedEntry(ServerNotification n) {
  final title = n.title.isNotEmpty ? n.title : 'LazyClaw';
  final body = n.body.isNotEmpty ? n.body : n.title;
  if (n.kind == 'channel_message') {
    return LocalNotifications.showInboxNotification(title, body);
  }
  return LocalNotifications.showServerNotification(title, body, payload: 'chat');
}

/// Single reusable entry point wired to the real repository, prefs cursor, and
/// the local-notification plugin. Safe to call from any isolate (foreground
/// resume, chat WS-connect, or the headless WorkManager pass) — it constructs
/// its own collaborators and swallows all errors.
Future<void> pullNotificationsFeed(ApiClient client) async {
  final service = NotificationsFeedService(
    repo: NotificationsRepository(DioNotificationsTransport(client)),
    show: _showFeedEntry,
    loadSince: SettingsPrefs.loadNotificationsSince,
    saveSince: SettingsPrefs.saveNotificationsSince,
    loadSeenIds: SettingsPrefs.loadNotificationsSeenIds,
    saveSeenIds: SettingsPrefs.saveNotificationsSeenIds,
  );
  await service.pullOnce();
}
