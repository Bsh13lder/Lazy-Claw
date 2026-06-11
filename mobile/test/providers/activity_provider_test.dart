import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/activity_provider.dart';
import 'package:lazyclaw_mobile/repositories/activity_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _Transport implements ActivityTransport {
  Map<String, dynamic>? next;
  Object? throwError;
  int calls = 0;

  Map<String, dynamic> postResponse = const {'success': true};
  Object? postThrowError;
  int postCalls = 0;
  String? lastPostPath;

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    calls++;
    if (throwError != null) throw throwError!;
    return next ?? const {};
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String path, {
    Map<String, dynamic>? body,
  }) async {
    postCalls++;
    lastPostPath = path;
    if (postThrowError != null) throw postThrowError!;
    return postResponse;
  }
}

Map<String, dynamic> _oneRunning() => {
      'active': [
        {'task_id': 'a1', 'name': 'Working', 'lane': 'main', 'status': 'running'},
      ],
    };

void main() {
  group('ActivityNotifier', () {
    test('load() populates the snapshot and clears loading', () async {
      final t = _Transport()..next = _oneRunning();
      final n = ActivityNotifier(ActivityRepository(t));

      await n.load();

      expect(n.state.isLoading, isFalse);
      expect(n.state.error, isNull);
      expect(n.state.snapshot.running, hasLength(1));
      expect(n.state.hasData, isTrue);
    });

    test('load() surfaces an error when the fetch fails', () async {
      final t = _Transport()..throwError = StateError('boom');
      final n = ActivityNotifier(ActivityRepository(t));

      await n.load();

      expect(n.state.isLoading, isFalse);
      expect(n.state.error, isNotNull);
      expect(n.state.hasData, isFalse);
    });

    test('refresh() keeps prior data and stays silent on a transient failure',
        () async {
      final t = _Transport()..next = _oneRunning();
      final n = ActivityNotifier(ActivityRepository(t));
      await n.load();
      expect(n.state.hasData, isTrue);

      // Next poll fails — must NOT wipe the snapshot or raise an error banner.
      t.throwError = StateError('network blip');
      await n.refresh();

      expect(n.state.hasData, isTrue, reason: 'previous snapshot retained');
      expect(n.state.error, isNull, reason: 'no error while data is on screen');
    });

    test('refresh() never flips the loading flag', () async {
      final t = _Transport()..next = _oneRunning();
      final n = ActivityNotifier(ActivityRepository(t));
      await n.refresh();
      expect(n.state.isLoading, isFalse);
      expect(n.state.snapshot.running, hasLength(1));
    });

    test('cancelTask() fires the cancel and refreshes the snapshot', () async {
      final t = _Transport()..next = _oneRunning();
      final n = ActivityNotifier(ActivityRepository(t));
      await n.load();
      final fetchesBefore = t.calls;

      final ok = await n.cancelTask('a1');

      expect(ok, isTrue);
      expect(t.postCalls, 1);
      expect(t.lastPostPath, '/api/agents/cancel');
      expect(t.calls, greaterThan(fetchesBefore),
          reason: 'snapshot refreshed after cancel');
    });

    test('cancelTask() returns false on failure but still refreshes',
        () async {
      final t = _Transport()
        ..next = _oneRunning()
        ..postThrowError = StateError('boom');
      final n = ActivityNotifier(ActivityRepository(t));
      await n.load();
      final fetchesBefore = t.calls;

      final ok = await n.cancelTask('a1');

      expect(ok, isFalse);
      expect(n.state.hasData, isTrue, reason: 'state survives the failure');
      expect(t.calls, greaterThan(fetchesBefore));
    });

    test('cancelAll() returns the server count and refreshes', () async {
      final t = _Transport()
        ..next = _oneRunning()
        ..postResponse = {
          'success': true,
          'data': {'cancelled': [], 'count': 3},
        };
      final n = ActivityNotifier(ActivityRepository(t));
      await n.load();

      final count = await n.cancelAll();

      expect(count, 3);
      expect(t.lastPostPath, '/api/agents/cancel-all');
    });

    test('cancelAll() returns 0 on failure', () async {
      final t = _Transport()
        ..next = _oneRunning()
        ..postThrowError = StateError('down');
      final n = ActivityNotifier(ActivityRepository(t));
      expect(await n.cancelAll(), 0);
    });
  });
}
