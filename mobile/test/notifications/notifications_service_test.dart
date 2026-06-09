import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/notifications/notifications_service.dart';
import 'package:lazyclaw_mobile/repositories/notifications_repository.dart';

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

ServerNotification _n(String id, {String title = 't', String body = 'b'}) =>
    ServerNotification(
        id: id, kind: 'k', title: title, body: body, createdAt: '');

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
    );

void main() {
  // ── Routing helper ─────────────────────────────────────────────────────────
  group('notificationPayloadForKind', () {
    test('channel_message → inbox', () {
      expect(notificationPayloadForKind('channel_message'), 'inbox');
    });

    test('done → chat (normal path)', () {
      expect(notificationPayloadForKind('done'), 'chat');
    });

    test('info → chat (normal path)', () {
      expect(notificationPayloadForKind('info'), 'chat');
    });

    test('empty string → chat (fallback)', () {
      expect(notificationPayloadForKind(''), 'chat');
    });

    test('unknown kind → chat (fallback)', () {
      expect(notificationPayloadForKind('some_unknown_kind'), 'chat');
    });
  });

  // ── Routing exercised via NotificationsFeedService ─────────────────────────
  group('_showFeedEntry routing via injectable show', () {
    /// Records which payload each shown notification produced.
    /// We spy by passing a custom `show` that calls [notificationPayloadForKind]
    /// and records the result — the same logic the real _showFeedEntry uses —
    /// so no real plugin is touched.
    test('channel_message notifications hit inbox path; others hit chat path',
        () async {
      final captured = <({String id, String payload})>[];

      Future<void> spyShow(ServerNotification n) async {
        captured.add((id: n.id, payload: notificationPayloadForKind(n.kind)));
      }

      final store = _FakeStore();
      final transport = _FakeRepoTransport([
        NotificationFeed(
          notifications: [
            ServerNotification(
              id: 'msg1',
              kind: 'channel_message',
              title: 'WhatsApp',
              body: 'Hey',
              createdAt: '',
            ),
            ServerNotification(
              id: 'done1',
              kind: 'done',
              title: 'Job done',
              body: 'Finished',
              createdAt: '',
            ),
            ServerNotification(
              id: 'msg2',
              kind: 'channel_message',
              title: 'Email',
              body: 'Hi',
              createdAt: '',
            ),
          ],
          now: 'T1',
        ),
      ]);

      final service = NotificationsFeedService(
        repo: NotificationsRepository(transport),
        show: spyShow,
        loadSince: store.loadSince,
        saveSince: store.saveSince,
        loadSeenIds: store.loadSeen,
        saveSeenIds: store.saveSeen,
      );
      await service.pullOnce();

      expect(
        captured,
        [
          (id: 'msg1', payload: 'inbox'),
          (id: 'done1', payload: 'chat'),
          (id: 'msg2', payload: 'inbox'),
        ],
      );
    });
  });

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

  // ── Service ─────────────────────────────────────────────────────────────────
  group('NotificationsFeedService.pullOnce', () {
    test('shows all items on the first pull and advances the cursor to now',
        () async {
      final store = _FakeStore();
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
      final store = _FakeStore();
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
      final store = _FakeStore();
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
