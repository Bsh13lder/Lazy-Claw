import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/notifications_repository.dart';

// ── Fake transport ───────────────────────────────────────────────────────────

class _FakeTransport implements NotificationsTransport {
  String? lastMethod;
  String? lastPath;
  Map<String, dynamic>? lastQuery;
  Map<String, dynamic>? lastBody;

  Map<String, dynamic> response;
  _FakeTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    lastMethod = 'GET';
    lastPath = path;
    lastQuery = queryParams;
    return response;
  }

  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) async {
    lastMethod = 'POST';
    lastPath = path;
    lastBody = body;
    return response;
  }
}

// ── Fixtures ──────────────────────────────────────────────────────────────────

Map<String, dynamic> _feed({
  List<Map<String, dynamic>>? notifications,
  String now = '2026-06-07T10:00:00Z',
}) =>
    {
      'notifications': notifications ??
          [
            {
              'id': 'n1',
              'kind': 'watcher',
              'title': 'Upwork',
              'body': 'New message from James',
              'created_at': '2026-06-07T09:59:00Z',
            },
            {
              'id': 'n2',
              'kind': 'background_done',
              'title': 'Task done',
              'body': 'Research complete',
              'created_at': '2026-06-07T09:58:00Z',
            },
          ],
      'now': now,
    };

void main() {
  // ── fetchSince ──────────────────────────────────────────────────────────────
  group('NotificationsRepository.fetchSince', () {
    test('GETs /api/notifications with since and parses the contract shape',
        () async {
      final t = _FakeTransport(_feed());
      final repo = NotificationsRepository(t);

      final feed = await repo.fetchSince(since: '2026-06-07T09:00:00Z');

      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/notifications');
      expect(t.lastQuery, {'since': '2026-06-07T09:00:00Z'});
      expect(feed.now, '2026-06-07T10:00:00Z');
      expect(feed.notifications, hasLength(2));
      final first = feed.notifications.first;
      expect(first.id, 'n1');
      expect(first.kind, 'watcher');
      expect(first.title, 'Upwork');
      expect(first.body, 'New message from James');
      expect(first.createdAt, '2026-06-07T09:59:00Z');
    });

    test('omits the since query param on the first pull (null/empty)', () async {
      final t = _FakeTransport(_feed());
      await NotificationsRepository(t).fetchSince();
      expect(t.lastQuery, isNull);

      await NotificationsRepository(t).fetchSince(since: '');
      expect(t.lastQuery, isNull);
    });

    test('tolerates a missing notifications list', () async {
      final t = _FakeTransport({'now': '2026-06-07T10:00:00Z'});
      final feed = await NotificationsRepository(t).fetchSince();
      expect(feed.notifications, isEmpty);
      expect(feed.now, '2026-06-07T10:00:00Z');
    });

    test('coerces non-string / missing fields without throwing', () async {
      final t = _FakeTransport({
        'notifications': [
          {'id': 42, 'created_at': null},
        ],
        'now': '',
      });
      final feed = await NotificationsRepository(t).fetchSince();
      expect(feed.notifications, hasLength(1));
      expect(feed.notifications.first.id, '42');
      expect(feed.notifications.first.title, '');
      expect(feed.notifications.first.createdAt, '');
      expect(feed.now, '');
    });

    test('unwraps a {success, data} envelope when present', () async {
      final t = _FakeTransport({
        'success': true,
        'data': _feed(now: '2026-06-07T11:00:00Z'),
      });
      final feed = await NotificationsRepository(t).fetchSince();
      expect(feed.now, '2026-06-07T11:00:00Z');
      expect(feed.notifications, hasLength(2));
    });
  });

  // ── getChannel ────────────────────────────────────────────────────────────
  group('NotificationsRepository.getChannel', () {
    test('GETs /api/settings/notifications and parses {channel}', () async {
      final t = _FakeTransport({'channel': 'both'});
      final repo = NotificationsRepository(t);
      final channel = await repo.getChannel();
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/settings/notifications');
      expect(channel, NotificationChannel.both);
    });

    test('defaults to telegram on unknown / missing channel', () async {
      expect(
        await NotificationsRepository(_FakeTransport({'channel': 'sms'}))
            .getChannel(),
        NotificationChannel.telegram,
      );
      expect(
        await NotificationsRepository(_FakeTransport({})).getChannel(),
        NotificationChannel.telegram,
      );
    });

    test('reads the channel from a data envelope too', () async {
      final t = _FakeTransport({
        'success': true,
        'data': {'channel': 'app'},
      });
      expect(await NotificationsRepository(t).getChannel(),
          NotificationChannel.app);
    });
  });

  // ── setChannel ──────────────────────────────────────────────────────────────
  group('NotificationsRepository.setChannel', () {
    test('POSTs the wire value and returns the server-confirmed channel',
        () async {
      final t = _FakeTransport({'channel': 'app'});
      final repo = NotificationsRepository(t);
      final channel = await repo.setChannel(NotificationChannel.app);
      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/settings/notifications');
      expect(t.lastBody, {'channel': 'app'});
      expect(channel, NotificationChannel.app);
    });

    test('falls back to the requested channel when response omits it',
        () async {
      final t = _FakeTransport({'ok': true});
      final channel =
          await NotificationsRepository(t).setChannel(NotificationChannel.both);
      expect(channel, NotificationChannel.both);
    });
  });

  // ── NotificationChannel enum ──────────────────────────────────────────────
  group('NotificationChannel', () {
    test('fromWire round-trips the three valid values', () {
      expect(NotificationChannel.fromWire('telegram'),
          NotificationChannel.telegram);
      expect(NotificationChannel.fromWire('app'), NotificationChannel.app);
      expect(NotificationChannel.fromWire('both'), NotificationChannel.both);
    });

    test('fromWire fails safe to telegram on null/unknown', () {
      expect(NotificationChannel.fromWire(null), NotificationChannel.telegram);
      expect(
          NotificationChannel.fromWire('bogus'), NotificationChannel.telegram);
    });
  });
}
