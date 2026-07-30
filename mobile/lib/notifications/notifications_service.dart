import '../core/api/api_client.dart';
import '../repositories/notifications_repository.dart';
import '../screens/settings/settings_prefs.dart';
import 'local_notifications.dart';

/// Maximum age a feed row may have and still surface as an OS pop. Anything
/// older (a long offline gap, a server backfill) stays in the in-app
/// Notification Center only — popping day-old rows reads as noise, not news.
const Duration kMaxNotificationPopAge = Duration(hours: 24);

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

/// Pure OS-pop policy: whether a NEW (never-seen) notification should ALSO pop
/// as an OS notification, given the current [now]. Suppressed rows stay in the
/// in-app Notification Center (that screen fetches independently) — this gates
/// only the OS banner:
///   * a row already read on another surface (web / Telegram) never pops;
///   * a task-occurrence row (`deep_link.type == 'task'` — server heartbeat
///     pre-reminders / nags) never pops: the task's own local alarms plus
///     Telegram already cover it, so an OS pop would be triple delivery;
///   * a row older than [maxAge] never pops (see [kMaxNotificationPopAge]);
///   * an unparseable `createdAt` is treated as OLD — never as fresh.
///
/// Kept as a free function with no IO so the policy is trivially unit-testable.
bool shouldPopNotification(
  ServerNotification n, {
  required DateTime now,
  Duration maxAge = kMaxNotificationPopAge,
}) {
  if (!n.isUnread) return false;
  final linkType = (n.deepLink?['type'] ?? '').toString();
  if (linkType == 'task') return false;
  final created = DateTime.tryParse(n.createdAt);
  if (created == null) return false; // defensive: unknown age → don't pop
  return now.difference(created) <= maxAge;
}

/// Pulls the server notification feed, surfaces only the NEW items that pass
/// the OS-pop policy ([shouldPopNotification]) as local notifications, and
/// advances the stored `since` cursor. The very first pull (no stored cursor)
/// is always silent — it only seeds the cursor + seen ring.
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
    DateTime Function()? now,
  })  : _repo = repo,
        _show = show,
        _loadSince = loadSince,
        _saveSince = saveSince,
        _loadSeenIds = loadSeenIds,
        _saveSeenIds = saveSeenIds,
        _maxSeenIds = maxSeenIds,
        _now = now ?? DateTime.now;

  final NotificationsRepository _repo;
  final Future<void> Function(ServerNotification) _show;
  final Future<String?> Function() _loadSince;
  final Future<void> Function(String) _saveSince;
  final Future<List<String>> Function() _loadSeenIds;
  final Future<void> Function(List<String>) _saveSeenIds;
  final int _maxSeenIds;

  /// Injectable clock for [shouldPopNotification]'s age cap (tests pin it).
  final DateTime Function() _now;

  /// Run one catch-up pass. Returns the number of NEW notifications shown.
  Future<int> pullOnce() async {
    try {
      final since = await _loadSince();
      final feed = await _repo.fetchSince(since: since);
      final seen = (await _loadSeenIds()).toSet();

      final fresh = selectNewNotifications(feed.notifications, seen);

      // First contact (no stored cursor): the server returned its FULL history
      // (`/api/notifications` with no `since`), not a delta — pop NOTHING and
      // only seed the cursor + seen ring below so the next pass has a real
      // window. A fresh install / cleared prefs must never replay the whole
      // backlog as OS notifications.
      final firstContact = since == null || since.isEmpty;
      final now = _now();
      final toShow = firstContact
          ? const <ServerNotification>[]
          : fresh.where((n) => shouldPopNotification(n, now: now)).toList();
      for (final n in toShow) {
        await _show(n);
      }

      // Advance the cursor to the server clock — only when the server gave us
      // a real `now`, so an empty response can't reset us to the start.
      if (feed.now.isNotEmpty) {
        await _saveSince(feed.now);
      }

      // Persist a bounded ring of EVERY fetched id — including rows the pop
      // policy suppressed — so an inclusive `since` boundary (server returning
      // an item whose timestamp == cursor) can't re-notify the same item next
      // pass, and a suppressed row can never pop later.
      if (fresh.isNotEmpty) {
        final merged = <String>[...seen, ...fresh.map((n) => n.id)];
        final bounded = merged.length > _maxSeenIds
            ? merged.sublist(merged.length - _maxSeenIds)
            : merged;
        await _saveSeenIds(bounded);
      }

      return toShow.length;
    } catch (_) {
      // Offline / transient backend error — never propagate. The next trigger
      // (resume / WS-connect / headless pass) retries the same window.
      return 0;
    }
  }
}

/// Single reusable entry point wired to the real repository, prefs cursor, and
/// the local-notification plugin. Safe to call from any isolate (foreground
/// resume, chat WS-connect, or the headless WorkManager pass) — it constructs
/// its own collaborators and swallows all errors.
///
/// Each shown notification carries its resolved in-app route as the payload
/// (`/inbox/<id>`, `/tasks`, `/notifications`, …) so a tap deep-links to the
/// exact target instead of the old catch-all Chat tab. A notification with no
/// specific deep_link falls back to the Notification Center, so a tap is never
/// a dead end.
Future<void> pullNotificationsFeed(ApiClient client) async {
  final service = NotificationsFeedService(
    repo: NotificationsRepository(DioNotificationsTransport(client)),
    show: (n) => LocalNotifications.showServerNotification(
      n.title.isNotEmpty ? n.title : 'LazyClaw',
      n.body.isNotEmpty ? n.body : n.title,
      payload: n.routePath,
    ),
    loadSince: SettingsPrefs.loadNotificationsSince,
    saveSince: SettingsPrefs.saveNotificationsSince,
    loadSeenIds: SettingsPrefs.loadNotificationsSeenIds,
    saveSeenIds: SettingsPrefs.saveNotificationsSeenIds,
  );
  await service.pullOnce();
}
