import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/notifications/notifications_service.dart';
import 'package:lazyclaw_mobile/repositories/notifications_repository.dart';

// ── Fixed clock ───────────────────────────────────────────────────────────────

/// Pinned "now" injected into the service so the 24h OS-pop age cap is
/// deterministic.
final DateTime tNow = DateTime.utc(2026, 7, 30, 12);

/// A createdAt comfortably inside the pop window (1h before [tNow]).
const String kFreshCreatedAt = '2026-07-30T11:00:00Z';

/// A createdAt outside the pop window (25h before [tNow]).
const String kStaleCreatedAt = '2026-07-29T11:00:00Z';

// ── Fakes ─────────────────────────────────────────────────────────────────────

/// A repository whose transport returns scripted feeds (one per pull). Throws
/// when [shouldThrow] is set, to exercise the offline-tolerant path.
class _FakeRepoTransport implements NotificationsTransport {
  final List<NotificationFeed> _feeds;
  int _i = 0;
  bool shouldThrow;

  _FakeRepoTransport(this._feeds, {this.shouldThrow = false});

  NotificationFeed _next() {
    final f = _i < _feeds.length ? _feeds[_i] : _feeds.last;
    _i++;
    return f;
  }

  @override
  Future<Map<String, dynamic>> getJson(String path,
      {Map<String, dynamic>? queryParams}) async {
    if (shouldThrow) throw Exception('offline');
    final f = _next();
    return {
      'notifications': [
        for (final n in f.notifications)
          {
            'id': n.id,
            'kind': n.kind,
            'title': n.title,
            'body': n.body,
            'created_at': n.createdAt,
            if (n.readAt != null) 'read_at': n.readAt,
            if (n.deepLink != null) 'deep_link': n.deepLink,
          },
      ],
      'now': f.now,
    };
  }

  @override
  Future<Map<String, dynamic>> postJson(
          String path, Map<String, dynamic> body) async =>
      {};
}

ServerNotification _n(
  String id, {
  String title = 't',
  String body = 'b',
  String createdAt = kFreshCreatedAt,
  String? readAt,
  Map<String, dynamic>? deepLink,
}) =>
    ServerNotification(
      id: id,
      kind: 'k',
      title: title,
      body: body,
      createdAt: createdAt,
      readAt: readAt,
      deepLink: deepLink,
    );

/// In-memory cursor + seen-ids store mirroring the SettingsPrefs static API.
class _FakeStore {
  String? since;
  List<String> seen = [];
  final List<ServerNotification> shown = [];

  Future<String?> loadSince() async => since;
  Future<void> saveSince(String v) async => since = v;
  Future<List<String>> loadSeen() async => List.of(seen);
  Future<void> saveSeen(List<String> ids) async => seen = List.of(ids);
  Future<void> show(ServerNotification n) async => shown.add(n);
}

NotificationsFeedService _service(
  _FakeStore store,
  NotificationsTransport transport, {
  int maxSeenIds = 200,
}) =>
    NotificationsFeedService(
      repo: NotificationsRepository(transport),
      show: store.show,
      loadSince: store.loadSince,
      saveSince: store.saveSince,
      loadSeenIds: store.loadSeen,
      saveSeenIds: store.saveSeen,
      maxSeenIds: maxSeenIds,
      now: () => tNow,
    );

void main() {
  // ── Pure selector ──────────────────────────────────────────────────────────
  group('selectNewNotifications', () {
    test('returns only ids not already seen, preserving order', () {
      final fetched = [_n('a'), _n('b'), _n('c')];
      final result = selectNewNotifications(fetched, {'b'});
      expect(result.map((e) => e.id), ['a', 'c']);
    });

    test('de-duplicates within a single batch', () {
      final fetched = [_n('a'), _n('a'), _n('b')];
      final result = selectNewNotifications(fetched, {});
      expect(result.map((e) => e.id), ['a', 'b']);
    });

    test('drops empty ids (cannot dedup safely)', () {
      final fetched = [_n(''), _n('a')];
      final result = selectNewNotifications(fetched, {});
      expect(result.map((e) => e.id), ['a']);
    });

    test('returns nothing when all ids are already seen', () {
      final fetched = [_n('a'), _n('b')];
      expect(selectNewNotifications(fetched, {'a', 'b'}), isEmpty);
    });
  });

  // ── Pure OS-pop policy ─────────────────────────────────────────────────────
  group('shouldPopNotification', () {
    test('a fresh unread row pops', () {
      expect(shouldPopNotification(_n('a'), now: tNow), isTrue);
    });

    test('a row already read elsewhere does not pop', () {
      final read = _n('a', readAt: '2026-07-30T11:30:00Z');
      expect(shouldPopNotification(read, now: tNow), isFalse);
    });

    test('a task-occurrence row (deep_link.type == task) does not pop', () {
      final task = _n('a', deepLink: {'type': 'task', 'id': 't1'});
      expect(shouldPopNotification(task, now: tNow), isFalse);
    });

    test('a non-task deep_link still pops', () {
      final thread = _n('a', deepLink: {'type': 'thread', 'id': 'th1'});
      expect(shouldPopNotification(thread, now: tNow), isTrue);
    });

    test('a row older than 24h does not pop', () {
      expect(
        shouldPopNotification(_n('a', createdAt: kStaleCreatedAt), now: tNow),
        isFalse,
      );
    });

    test('an unparseable createdAt is treated as old — no pop', () {
      expect(shouldPopNotification(_n('a', createdAt: ''), now: tNow), isFalse);
      expect(
        shouldPopNotification(_n('a', createdAt: 'not-a-date'), now: tNow),
        isFalse,
      );
    });
  });

  // ── Service ─────────────────────────────────────────────────────────────────
  group('NotificationsFeedService.pullOnce', () {
    test(
        'FIRST CONTACT is silent: full-history pull shows nothing but seeds '
        'the cursor and the seen ring', () async {
      final store = _FakeStore(); // since == null → first contact
      final transport = _FakeRepoTransport([
        NotificationFeed(
          notifications: [_n('a'), _n('b')],
          now: 'T1',
        ),
      ]);

      final count = await _service(store, transport).pullOnce();

      expect(count, 0);
      expect(store.shown, isEmpty);
      expect(store.since, 'T1'); // cursor still advances
      expect(store.seen, ['a', 'b']); // ids still recorded
    });

    test('shows new items on a delta pull and advances the cursor', () async {
      final store = _FakeStore()..since = 'T0';
      final transport = _FakeRepoTransport([
        NotificationFeed(
          notifications: [_n('a'), _n('b')],
          now: 'T1',
        ),
      ]);

      final count = await _service(store, transport).pullOnce();

      expect(count, 2);
      expect(store.shown.map((e) => e.id), ['a', 'b']);
      expect(store.since, 'T1');
      expect(store.seen, ['a', 'b']);
    });

    test('does not re-show ids carried in the seen ring across pulls',
        () async {
      final store = _FakeStore()..since = 'T0';
      final transport = _FakeRepoTransport([
        NotificationFeed(notifications: [_n('a'), _n('b')], now: 'T1'),
        // Second pull: the boundary item 'b' reappears (inclusive cursor) plus a
        // genuinely new 'c'. Only 'c' should be shown.
        NotificationFeed(notifications: [_n('b'), _n('c')], now: 'T2'),
      ]);
      final service = _service(store, transport);

      await service.pullOnce();
      final count2 = await service.pullOnce();

      expect(count2, 1);
      expect(store.shown.map((e) => e.id), ['a', 'b', 'c']);
      expect(store.since, 'T2');
    });

    test('skips rows already read elsewhere but still records their ids',
        () async {
      final store = _FakeStore()..since = 'T0';
      final transport = _FakeRepoTransport([
        NotificationFeed(
          notifications: [
            _n('read', readAt: '2026-07-30T11:30:00Z'),
            _n('unread'),
          ],
          now: 'T1',
        ),
      ]);

      final count = await _service(store, transport).pullOnce();

      expect(count, 1);
      expect(store.shown.map((e) => e.id), ['unread']);
      // The suppressed row is still bookkept so it can never pop later.
      expect(store.seen, ['read', 'unread']);
      expect(store.since, 'T1');
    });

    test(
        'skips task-occurrence rows (deep_link.type == task) but records '
        'their ids', () async {
      final store = _FakeStore()..since = 'T0';
      final transport = _FakeRepoTransport([
        NotificationFeed(
          notifications: [
            _n('nag', deepLink: {'type': 'task', 'id': 't1'}),
            _n('watcher', deepLink: {'type': 'watcher'}),
          ],
          now: 'T1',
        ),
      ]);

      final count = await _service(store, transport).pullOnce();

      expect(count, 1);
      expect(store.shown.map((e) => e.id), ['watcher']);
      expect(store.seen, ['nag', 'watcher']);
    });

    test('skips rows older than 24h and rows with unparseable createdAt',
        () async {
      final store = _FakeStore()..since = 'T0';
      final transport = _FakeRepoTransport([
        NotificationFeed(
          notifications: [
            _n('stale', createdAt: kStaleCreatedAt),
            _n('undated', createdAt: ''),
            _n('fresh'),
          ],
          now: 'T1',
        ),
      ]);

      final count = await _service(store, transport).pullOnce();

      expect(count, 1);
      expect(store.shown.map((e) => e.id), ['fresh']);
      expect(store.seen, ['stale', 'undated', 'fresh']);
    });

    test(
        'suppressed rows can never pop later: ids are in the seen ring and the '
        'cursor advanced', () async {
      final store = _FakeStore()..since = 'T0';
      final transport = _FakeRepoTransport([
        // First pull: everything is suppressed by the policy.
        NotificationFeed(
          notifications: [
            _n('x', readAt: '2026-07-30T11:30:00Z'),
            _n('y', deepLink: {'type': 'task'}),
          ],
          now: 'T1',
        ),
        // Second pull: the SAME ids come back in a now-poppable shape (unread,
        // fresh, no task link) — they must stay silent because they were seen.
        NotificationFeed(
          notifications: [_n('x'), _n('y')],
          now: 'T2',
        ),
      ]);
      final service = _service(store, transport);

      final count1 = await service.pullOnce();
      final count2 = await service.pullOnce();

      expect(count1, 0);
      expect(count2, 0);
      expect(store.shown, isEmpty);
      expect(store.seen, ['x', 'y']);
      expect(store.since, 'T2');
    });

    test('passes the stored since cursor to the next fetch', () async {
      final store = _FakeStore()..since = 'PRIOR';
      // Capture the query the transport receives.
      late Map<String, dynamic>? seenQuery;
      final transport = _CapturingTransport((q) => seenQuery = q);
      await _service(store, transport).pullOnce();
      expect(seenQuery, {'since': 'PRIOR'});
    });

    test('does not advance the cursor when now is empty', () async {
      final store = _FakeStore()..since = 'KEEP';
      final transport = _FakeRepoTransport([
        NotificationFeed(notifications: [_n('a')], now: ''),
      ]);
      await _service(store, transport).pullOnce();
      expect(store.since, 'KEEP'); // unchanged
      expect(store.shown.map((e) => e.id), ['a']); // still shown
    });

    test('is offline-tolerant: fetch failure shows nothing, cursor intact',
        () async {
      final store = _FakeStore()..since = 'KEEP';
      final transport =
          _FakeRepoTransport([NotificationFeed(notifications: [], now: 'T')],
              shouldThrow: true);
      final count = await _service(store, transport).pullOnce();
      expect(count, 0);
      expect(store.shown, isEmpty);
      expect(store.since, 'KEEP');
    });

    test('bounds the seen ring to maxSeenIds (keeps the most recent)',
        () async {
      final store = _FakeStore()..since = 'T0';
      final transport = _FakeRepoTransport([
        NotificationFeed(
          notifications: [_n('a'), _n('b'), _n('c')],
          now: 'T1',
        ),
      ]);
      await _service(store, transport, maxSeenIds: 2).pullOnce();
      // Only the 2 most recent ids are retained.
      expect(store.seen, ['b', 'c']);
    });
  });
}

/// A transport that records the query params handed to [getJson] and returns an
/// empty feed.
class _CapturingTransport implements NotificationsTransport {
  final void Function(Map<String, dynamic>?) onGet;
  _CapturingTransport(this.onGet);

  @override
  Future<Map<String, dynamic>> getJson(String path,
      {Map<String, dynamic>? queryParams}) async {
    onGet(queryParams);
    return {'notifications': [], 'now': 'T'};
  }

  @override
  Future<Map<String, dynamic>> postJson(
          String path, Map<String, dynamic> body) async =>
      {};
}
