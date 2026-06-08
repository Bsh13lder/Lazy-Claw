import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/watchers_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeTransport implements WatchersTransport {
  String? lastMethod;
  String? lastPath;
  Map<String, dynamic>? lastBody;
  Map<String, dynamic>? lastQueryParams;

  Map<String, dynamic> response;

  _FakeTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    lastMethod = 'GET';
    lastPath = path;
    lastQueryParams = queryParams;
    return response;
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    lastMethod = 'POST';
    lastPath = path;
    lastBody = body;
    return response;
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    lastMethod = 'DELETE';
    lastPath = path;
    return response;
  }
}

// ── Fixtures ───────────────────────────────────────────────────────────────

Map<String, dynamic> _watcherJson({
  String id = 'w1',
  String name = 'Test Watcher',
  String status = 'active',
  String jobType = 'url',
  String? url = 'https://example.com',
  int? checkInterval = 3600,
  String? lastCheck = '2026-06-06T10:00:00Z',
  int checkCount = 42,
  int triggerCount = 3,
  int errorCount = 0,
}) =>
    {
      'id': id,
      'name': name,
      'status': status,
      'job_type': jobType,
      'url': url,
      'check_interval': checkInterval,
      'last_check': lastCheck,
      'last_run': null,
      'next_run': null,
      'check_count': checkCount,
      'trigger_count': triggerCount,
      'error_count': errorCount,
      'last_error': null,
      'what_to_watch': null,
      'instruction': null,
      'one_shot': false,
      'template_name': null,
      'template_icon': null,
      'last_value': null,
      'created_at': '2026-06-01T08:00:00Z',
    };

Map<String, dynamic> _summaryJson({
  int total = 5,
  int active = 3,
  int paused = 2,
}) =>
    {
      'total': total,
      'active': active,
      'paused': paused,
      'last_trigger_ts': null,
      'last_trigger_name': null,
      'last_trigger_message': null,
    };

Map<String, dynamic> _checkJson({
  int ts = 1717920000,
  bool changed = true,
  bool triggered = true,
  String? valuePreview = '\$19.99',
  String? error,
}) =>
    {
      'ts': ts,
      'changed': changed,
      'triggered': triggered,
      'value_preview': valuePreview,
      'error': error,
      'notification': null,
    };

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  // ── listWatchers ──────────────────────────────────────────────────────────

  group('WatchersRepository.listWatchers', () {
    test('GET /api/watchers and parses watcher list', () async {
      final t = _FakeTransport({
        'watchers': [
          _watcherJson(id: 'a1', name: 'Price monitor'),
          _watcherJson(id: 'a2', name: 'Job alert'),
        ],
      });
      final repo = WatchersRepository(t);
      final watchers = await repo.listWatchers();

      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/watchers');
      expect(t.lastQueryParams, isNull);
      expect(watchers, hasLength(2));
      expect(watchers[0].id, 'a1');
      expect(watchers[0].name, 'Price monitor');
      expect(watchers[1].id, 'a2');
    });

    test('returns empty list when watchers key is missing', () async {
      final t = _FakeTransport({});
      final watchers = await WatchersRepository(t).listWatchers();
      expect(watchers, isEmpty);
    });

    test('returns empty list when watchers is an empty array', () async {
      final t = _FakeTransport({'watchers': []});
      final watchers = await WatchersRepository(t).listWatchers();
      expect(watchers, isEmpty);
    });
  });

  // ── Watcher.fromJson ──────────────────────────────────────────────────────

  group('Watcher.fromJson', () {
    test('parses all fields correctly', () {
      final w = Watcher.fromJson(_watcherJson(
        id: 'w99',
        name: 'My monitor',
        status: 'paused',
        jobType: 'upwork',
        url: 'https://upwork.com',
        checkInterval: 1800,
        lastCheck: '2026-06-06T09:00:00Z',
        checkCount: 10,
        triggerCount: 1,
        errorCount: 2,
      ));

      expect(w.id, 'w99');
      expect(w.name, 'My monitor');
      expect(w.status, 'paused');
      expect(w.jobType, 'upwork');
      expect(w.url, 'https://upwork.com');
      expect(w.checkInterval, 1800);
      expect(w.lastCheck, '2026-06-06T09:00:00Z');
      expect(w.checkCount, 10);
      expect(w.triggerCount, 1);
      expect(w.errorCount, 2);
      expect(w.oneShot, false);
    });

    test('defaults to 0 for missing numeric counts', () {
      final w = Watcher.fromJson({
        'id': 'x',
        'name': 'Sparse',
        'status': 'active',
        'job_type': 'url',
      });
      expect(w.checkCount, 0);
      expect(w.triggerCount, 0);
      expect(w.errorCount, 0);
    });

    test('defaults oneShot to false when absent', () {
      final w = Watcher.fromJson({'id': 'x', 'name': 'y', 'status': 'active', 'job_type': 'url'});
      expect(w.oneShot, false);
    });

    test('parses oneShot=true', () {
      final json = _watcherJson()..['one_shot'] = true;
      final w = Watcher.fromJson(json);
      expect(w.oneShot, true);
    });

    test('nullable fields default to null', () {
      final w = Watcher.fromJson({
        'id': 'x',
        'name': 'Minimal',
        'status': 'active',
        'job_type': 'url',
      });
      expect(w.url, isNull);
      expect(w.checkInterval, isNull);
      expect(w.lastCheck, isNull);
      expect(w.lastError, isNull);
      expect(w.whatToWatch, isNull);
    });

    test('coerces numeric check_interval via num cast', () {
      final json = _watcherJson()..['check_interval'] = 7200.0;
      final w = Watcher.fromJson(json);
      expect(w.checkInterval, 7200);
    });
  });

  // ── getSummary ────────────────────────────────────────────────────────────

  group('WatchersRepository.getSummary', () {
    test('GET /api/watchers/summary and parses WatcherSummary', () async {
      final t = _FakeTransport(_summaryJson(total: 10, active: 7, paused: 3));
      final summary = await WatchersRepository(t).getSummary();

      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/watchers/summary');
      expect(summary.total, 10);
      expect(summary.active, 7);
      expect(summary.paused, 3);
    });

    test('defaults to zero counts when keys are missing', () async {
      final t = _FakeTransport({});
      final summary = await WatchersRepository(t).getSummary();
      expect(summary.total, 0);
      expect(summary.active, 0);
      expect(summary.paused, 0);
    });
  });

  // ── WatcherSummary.fromJson ───────────────────────────────────────────────

  group('WatcherSummary.fromJson', () {
    test('parses lastTrigger fields', () {
      final s = WatcherSummary.fromJson({
        'total': 2,
        'active': 1,
        'paused': 1,
        'last_trigger_ts': 1717920000,
        'last_trigger_name': 'Price drop',
        'last_trigger_message': 'Price fell to \$9.99',
      });
      expect(s.lastTriggerTs, 1717920000);
      expect(s.lastTriggerName, 'Price drop');
      expect(s.lastTriggerMessage, 'Price fell to \$9.99');
    });

    test('nullable trigger fields default to null', () {
      final s = WatcherSummary.fromJson({'total': 0, 'active': 0, 'paused': 0});
      expect(s.lastTriggerTs, isNull);
      expect(s.lastTriggerName, isNull);
    });
  });

  // ── getWatcher ────────────────────────────────────────────────────────────

  group('WatchersRepository.getWatcher', () {
    test('GET /api/watchers/{id} and returns a Watcher', () async {
      final t = _FakeTransport(_watcherJson(id: 'abc123'));
      final watcher = await WatchersRepository(t).getWatcher('abc123');

      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/watchers/abc123');
      expect(watcher.id, 'abc123');
    });
  });

  // ── getHistory ────────────────────────────────────────────────────────────

  group('WatchersRepository.getHistory', () {
    test('GET /api/watchers/{id}/history and parses check list', () async {
      final t = _FakeTransport({
        'watcher_id': 'w1',
        'checks': [
          _checkJson(ts: 1717910000, changed: false, triggered: false, valuePreview: '\$20.00'),
          _checkJson(ts: 1717920000, changed: true, triggered: true, valuePreview: '\$19.99'),
        ],
      });
      final checks = await WatchersRepository(t).getHistory('w1');

      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/watchers/w1/history');
      expect(checks, hasLength(2));
      expect(checks[0].ts, 1717910000);
      expect(checks[0].changed, false);
      expect(checks[1].ts, 1717920000);
      expect(checks[1].changed, true);
      expect(checks[1].triggered, true);
      expect(checks[1].valuePreview, '\$19.99');
    });

    test('returns empty list when checks key is missing', () async {
      final t = _FakeTransport({'watcher_id': 'w1'});
      final checks = await WatchersRepository(t).getHistory('w1');
      expect(checks, isEmpty);
    });
  });

  // ── WatcherCheck.fromJson ─────────────────────────────────────────────────

  group('WatcherCheck.fromJson', () {
    test('parses all fields', () {
      final c = WatcherCheck.fromJson(_checkJson(
        ts: 9999,
        changed: true,
        triggered: false,
        valuePreview: 'hello',
        error: 'timeout',
      ));
      expect(c.ts, 9999);
      expect(c.changed, true);
      expect(c.triggered, false);
      expect(c.valuePreview, 'hello');
      expect(c.error, 'timeout');
    });

    test('defaults to 0 ts and false booleans on sparse input', () {
      final c = WatcherCheck.fromJson({});
      expect(c.ts, 0);
      expect(c.changed, false);
      expect(c.triggered, false);
      expect(c.valuePreview, isNull);
    });
  });

  // ── pauseWatcher ──────────────────────────────────────────────────────────

  group('WatchersRepository.pauseWatcher', () {
    test('POST /api/watchers/{id}/pause with empty body', () async {
      final t = _FakeTransport({'status': 'paused'});
      await WatchersRepository(t).pauseWatcher('w42');

      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/watchers/w42/pause');
      expect(t.lastBody, isEmpty);
    });
  });

  // ── resumeWatcher ─────────────────────────────────────────────────────────

  group('WatchersRepository.resumeWatcher', () {
    test('POST /api/watchers/{id}/resume with empty body', () async {
      final t = _FakeTransport({'status': 'active'});
      await WatchersRepository(t).resumeWatcher('w42');

      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/watchers/w42/resume');
      expect(t.lastBody, isEmpty);
    });
  });

  // ── deleteWatcher ─────────────────────────────────────────────────────────

  group('WatchersRepository.deleteWatcher', () {
    test('DELETE /api/watchers/{id}', () async {
      final t = _FakeTransport({'status': 'deleted'});
      await WatchersRepository(t).deleteWatcher('w77');

      expect(t.lastMethod, 'DELETE');
      expect(t.lastPath, '/api/watchers/w77');
    });
  });
}
