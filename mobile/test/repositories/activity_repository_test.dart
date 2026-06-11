import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/repositories/activity_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeActivityTransport implements ActivityTransport {
  String? lastPath;
  String? lastPostPath;
  Map<String, dynamic>? lastPostBody;
  final Map<String, dynamic> response;
  Map<String, dynamic> postResponse = const {'success': true};
  _FakeActivityTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    lastPath = path;
    return response;
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String path, {
    Map<String, dynamic>? body,
  }) async {
    lastPostPath = path;
    lastPostBody = body;
    return postResponse;
  }
}

/// Throws the SAME exception shape the real Dio transport surfaces — a
/// [DioException] wrapping an [ApiError] — so the test exercises the actual
/// failure path (per the sync-engine lesson: fakes must throw the production
/// error type or they green-light data-loss bugs).
class _ThrowingActivityTransport implements ActivityTransport {
  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    throw DioException(
      requestOptions: RequestOptions(path: path),
      error: ApiError(503, 'Service unavailable'),
    );
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String path, {
    Map<String, dynamic>? body,
  }) async {
    throw DioException(
      requestOptions: RequestOptions(path: path),
      error: ApiError(503, 'Service unavailable'),
    );
  }
}

// ── Fixtures ───────────────────────────────────────────────────────────────

Map<String, dynamic> _fullStatus() => {
      'active': [
        {
          'task_id': 'a1',
          'name': 'Research market',
          'lane': 'main',
          'status': 'running',
          'elapsed_s': 12.3,
          'current_tool': 'web_search',
          'recent_tools': ['web_search', 'browser'],
          'phase': 'act',
        },
      ],
      'background': [
        {
          'task_id': 'b1',
          'name': 'Scaffold app',
          'lane': 'background',
          'status': 'running',
          'elapsed_s': 5.0,
        },
      ],
      'background_recent': [
        {
          'task_id': 'br1',
          'name': 'Send invoices',
          'lane': 'background',
          'status': 'done',
          'result': 'Sent 3 invoices',
          'result_preview': 'Sent 3 invoices',
          'created_at': '2026-06-09T10:00:00Z',
          'completed_at': '2026-06-09T10:01:00Z',
          'cost_usd': 0.0123,
          'tokens_used': 4200,
        },
      ],
      'recent': [
        {
          'task_id': 'r1',
          'name': 'Book table',
          'lane': 'main',
          'status': 'done',
          'duration_s': 2.5,
          'result_preview': 'Reserved 7pm',
        },
      ],
    };

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  group('ActivityRepository.getStatus', () {
    test('GETs /api/agents/status', () async {
      final t = _FakeActivityTransport(_fullStatus());
      await ActivityRepository(t).getStatus();
      expect(t.lastPath, '/api/agents/status');
    });

    test('splits running (active+background) and recent (recent+bg_recent)',
        () async {
      final snap = await ActivityRepository(_FakeActivityTransport(_fullStatus()))
          .getStatus();
      expect(snap.running, hasLength(2));
      expect(snap.recent, hasLength(2));
      // active first, then background, in order.
      expect(snap.running[0].taskId, 'a1');
      expect(snap.running[1].taskId, 'b1');
      // recent first, then background_recent.
      expect(snap.recent[0].taskId, 'r1');
      expect(snap.recent[1].taskId, 'br1');
    });

    test('derives isRunning from the source bucket, not the status field',
        () async {
      final snap = await ActivityRepository(_FakeActivityTransport(_fullStatus()))
          .getStatus();
      expect(snap.running.every((t) => t.isRunning), isTrue);
      expect(snap.recent.every((t) => !t.isRunning), isTrue);
    });

    test('maps running task fields', () async {
      final snap = await ActivityRepository(_FakeActivityTransport(_fullStatus()))
          .getStatus();
      final a = snap.running.first;
      expect(a.name, 'Research market');
      expect(a.lane, 'main');
      expect(a.elapsedS, 12.3);
      expect(a.currentTool, 'web_search');
      expect(a.recentTools, ['web_search', 'browser']);
      expect(a.phase, 'act');
    });

    test('maps settled background task fields incl. cost/tokens', () async {
      final snap = await ActivityRepository(_FakeActivityTransport(_fullStatus()))
          .getStatus();
      final br = snap.recent.firstWhere((t) => t.taskId == 'br1');
      expect(br.result, 'Sent 3 invoices');
      expect(br.completedAt, '2026-06-09T10:01:00Z');
      expect(br.costUsd, 0.0123);
      expect(br.tokensUsed, 4200);
    });

    test('returns empty groups when all keys are missing', () async {
      final snap =
          await ActivityRepository(_FakeActivityTransport({})).getStatus();
      expect(snap.isEmpty, isTrue);
      expect(snap.total, 0);
    });

    test('tolerates a partial payload (only recent present)', () async {
      final snap = await ActivityRepository(_FakeActivityTransport({
        'recent': [
          {'task_id': 'r9', 'name': 'X', 'lane': 'main', 'status': 'done'},
        ],
      })).getStatus();
      expect(snap.running, isEmpty);
      expect(snap.recent, hasLength(1));
    });

    test('propagates the production DioException(ApiError) shape', () async {
      final repo = ActivityRepository(_ThrowingActivityTransport());
      await expectLater(
        repo.getStatus(),
        throwsA(isA<DioException>().having(
          (e) => e.error,
          'error',
          isA<ApiError>(),
        )),
      );
    });
  });

  group('ActivityRepository.cancelTask', () {
    test('POSTs /api/agents/cancel with the task_id body', () async {
      final t = _FakeActivityTransport({});
      final ok = await ActivityRepository(t).cancelTask('t42');
      expect(t.lastPostPath, '/api/agents/cancel');
      expect(t.lastPostBody, {'task_id': 't42'});
      expect(ok, isTrue);
    });

    test('returns false when the server reports failure', () async {
      final t = _FakeActivityTransport({})
        ..postResponse = {
          'success': false,
          'error': 'Task not found or not cancellable',
        };
      expect(await ActivityRepository(t).cancelTask('gone'), isFalse);
    });

    test('returns false on a malformed response', () async {
      final t = _FakeActivityTransport({})..postResponse = {};
      expect(await ActivityRepository(t).cancelTask('x'), isFalse);
    });

    test('propagates the production DioException(ApiError) shape', () async {
      final repo = ActivityRepository(_ThrowingActivityTransport());
      await expectLater(
        repo.cancelTask('x'),
        throwsA(isA<DioException>()
            .having((e) => e.error, 'error', isA<ApiError>())),
      );
    });
  });

  group('ActivityRepository.cancelAll', () {
    test('POSTs /api/agents/cancel-all and returns the count', () async {
      final t = _FakeActivityTransport({})
        ..postResponse = {
          'success': true,
          'data': {
            'cancelled': [
              {'task_id': 'a', 'name': 'A'},
              {'task_id': 'b', 'name': 'B'},
            ],
            'count': 2,
          },
        };
      final count = await ActivityRepository(t).cancelAll();
      expect(t.lastPostPath, '/api/agents/cancel-all');
      expect(t.lastPostBody, isNull);
      expect(count, 2);
    });

    test('returns 0 on a malformed response', () async {
      final t = _FakeActivityTransport({})..postResponse = {'success': true};
      expect(await ActivityRepository(t).cancelAll(), 0);
    });
  });

  group('AgentTask.fromJson', () {
    test('falls back to "Task" when name missing/empty', () {
      expect(
        AgentTask.fromJson({'task_id': 'x'}, isRunning: true).name,
        'Task',
      );
      expect(
        AgentTask.fromJson({'task_id': 'x', 'name': '  '}, isRunning: false)
            .name,
        'Task',
      );
    });

    test('coerces numeric-ish fields and ignores empty strings', () {
      final t = AgentTask.fromJson({
        'task_id': 'x',
        'name': 'N',
        'elapsed_s': 3,
        'current_tool': '',
        'error': '',
      }, isRunning: true);
      expect(t.elapsedS, 3.0);
      expect(t.currentTool, isNull);
      expect(t.error, isNull);
    });

    test('isFailed / isCancelled reflect status', () {
      expect(
        AgentTask.fromJson({'task_id': 'x', 'status': 'failed'},
                isRunning: false)
            .isFailed,
        isTrue,
      );
      expect(
        AgentTask.fromJson({'task_id': 'x', 'status': 'cancelled'},
                isRunning: false)
            .isCancelled,
        isTrue,
      );
    });

    test('subtitle prefers current tool while running, preview when settled',
        () {
      final running = AgentTask.fromJson(
        {'task_id': 'x', 'current_tool': 'browser', 'phase': 'act'},
        isRunning: true,
      );
      expect(running.subtitle, 'browser');

      final done = AgentTask.fromJson(
        {'task_id': 'x', 'result_preview': 'Done', 'result': 'Done long'},
        isRunning: false,
      );
      expect(done.subtitle, 'Done');

      final failed = AgentTask.fromJson(
        {'task_id': 'x', 'status': 'failed', 'error': 'boom'},
        isRunning: false,
      );
      expect(failed.subtitle, 'boom');
    });
  });
}
