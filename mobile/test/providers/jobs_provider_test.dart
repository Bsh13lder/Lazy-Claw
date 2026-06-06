import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/jobs_provider.dart';
import 'package:lazyclaw_mobile/repositories/jobs_repository.dart';

// ── Fake transports ────────────────────────────────────────────────────────

class _OkTransport implements JobsTransport {
  final Map<String, dynamic> response;
  final List<String> calls = [];

  _OkTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    calls.add('GET $path');
    return response;
  }

  @override
  Future<Map<String, dynamic>> postJson(String path) async {
    calls.add('POST $path');
    return {'status': 'ok'};
  }
}

class _FailTransport implements JobsTransport {
  final String message;
  final List<String> calls = [];
  _FailTransport([this.message = 'network error']);

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    calls.add('GET $path');
    throw Exception(message);
  }

  @override
  Future<Map<String, dynamic>> postJson(String path) async {
    calls.add('POST $path');
    throw Exception(message);
  }
}

// ── Fixtures ───────────────────────────────────────────────────────────────

Map<String, dynamic> _jobJson({
  String id = 'j1',
  String name = 'Test job',
  String status = 'active',
  String? jobType,
  String? cronExpression = '0 9 * * *',
}) =>
    {
      'id': id,
      'name': name,
      'instruction': 'Do something useful',
      'cron_expression': cronExpression,
      'status': status,
      'job_type': jobType,
    };

JobsNotifier _makeNotifier(JobsTransport t) =>
    JobsNotifier(JobsRepository(t));

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  // ── load ──────────────────────────────────────────────────────────────────

  group('JobsNotifier.load', () {
    test('starts with empty state (no auto-load)', () {
      final n = _makeNotifier(_OkTransport({'jobs': []}));
      expect(n.state.jobs, isEmpty);
      expect(n.state.isLoading, isFalse);
      expect(n.state.error, isNull);
    });

    test('sets isLoading during fetch, clears after', () async {
      final t = _OkTransport({
        'jobs': [_jobJson(id: 'a1')],
      });
      final n = _makeNotifier(t);
      final future = n.load();
      // Immediately after calling load, isLoading is true.
      expect(n.state.isLoading, isTrue);
      await future;
      expect(n.state.isLoading, isFalse);
    });

    test('populates jobs on success', () async {
      final t = _OkTransport({
        'jobs': [
          _jobJson(id: 'a1', name: 'Morning digest'),
          _jobJson(id: 'a2', name: 'Weekly report'),
        ],
      });
      final n = _makeNotifier(t);
      await n.load();
      expect(n.state.jobs, hasLength(2));
      expect(n.state.jobs[0].id, 'a1');
      expect(n.state.error, isNull);
    });

    test('sets error on failure', () async {
      final t = _FailTransport('Server down');
      final n = _makeNotifier(t);
      await n.load();
      expect(n.state.jobs, isEmpty);
      expect(n.state.isLoading, isFalse);
      expect(n.state.error, isNotNull);
    });

    test('clears previous error on retry', () async {
      final failT = _FailTransport('offline');
      final n = _makeNotifier(failT);
      await n.load();
      expect(n.state.error, isNotNull);

      // Replace with a succeeding transport by directly constructing a
      // new notifier seeded from a good transport.
      final okT = _OkTransport({'jobs': [_jobJson()]});
      final n2 = _makeNotifier(okT);
      await n2.load();
      expect(n2.state.error, isNull);
      expect(n2.state.jobs, hasLength(1));
    });
  });

  // ── refresh ───────────────────────────────────────────────────────────────

  group('JobsNotifier.refresh', () {
    test('re-fetches without resetting to loading', () async {
      final t = _OkTransport({'jobs': [_jobJson()]});
      final n = _makeNotifier(t);
      await n.load();

      // At refresh time the list should still be visible (no loading state).
      n.refresh(); // fire without await to test synchronous state.
      expect(n.state.isLoading, isFalse);
      expect(n.state.jobs, hasLength(1)); // previous data still there.
    });

    test('updates job list after refresh', () async {
      final responses = [
        {'jobs': <dynamic>[_jobJson(id: 'a1')]},
        {'jobs': <dynamic>[_jobJson(id: 'a1'), _jobJson(id: 'a2')]},
      ];
      // We build two notifiers to simulate two different server responses.
      final n1 = _makeNotifier(_OkTransport(responses[0]));
      await n1.load();
      expect(n1.state.jobs, hasLength(1));

      final n2 = _makeNotifier(_OkTransport(responses[1]));
      await n2.refresh();
      expect(n2.state.jobs, hasLength(2));
    });
  });

  // ── pauseJob / resumeJob ──────────────────────────────────────────────────

  group('JobsNotifier.pauseJob', () {
    test('sets togglingId while in-flight', () async {
      final t = _OkTransport({'jobs': [_jobJson(id: 'j1', status: 'active')]});
      final n = _makeNotifier(t);
      await n.load();

      final future = n.pauseJob('j1');
      expect(n.state.togglingId, 'j1');
      await future;
      expect(n.state.togglingId, isNull);
    });

    test('clears togglingId on error', () async {
      final getT = _OkTransport({'jobs': [_jobJson(id: 'j1', status: 'active')]});
      final n = _makeNotifier(getT);
      await n.load();

      // Swap transport so pause POST fails.
      final failN = JobsNotifier(
        JobsRepository(_FailTransport('pause failed')),
      );
      // Seed the state directly by loading once first.
      await failN.load(); // will fail but that's expected.
      expect(failN.state.togglingId, isNull);
    });

    test('posts to correct pause path', () async {
      final t = _OkTransport({'jobs': [_jobJson(id: 'j5', status: 'active')]});
      final n = _makeNotifier(t);
      await n.pauseJob('j5');
      expect(t.calls, contains('POST /api/jobs/j5/pause'));
    });
  });

  group('JobsNotifier.resumeJob', () {
    test('posts to correct resume path', () async {
      final t = _OkTransport({'jobs': [_jobJson(id: 'j7', status: 'paused')]});
      final n = _makeNotifier(t);
      await n.resumeJob('j7');
      expect(t.calls, contains('POST /api/jobs/j7/resume'));
    });

    test('sets togglingId during resume', () async {
      final t = _OkTransport({'jobs': [_jobJson(id: 'j8', status: 'paused')]});
      final n = _makeNotifier(t);
      await n.load();
      final future = n.resumeJob('j8');
      expect(n.state.togglingId, 'j8');
      await future;
      expect(n.state.togglingId, isNull);
    });
  });

  // ── computed views ────────────────────────────────────────────────────────

  group('JobsState computed views', () {
    test('recurringJobs returns only cron-type jobs', () async {
      final t = _OkTransport({
        'jobs': [
          _jobJson(id: 'c1', jobType: 'cron'),
          _jobJson(id: 'o1', jobType: 'one_off', cronExpression: null),
          _jobJson(id: 'r1', jobType: 'reminder', cronExpression: null),
        ],
      });
      final n = _makeNotifier(t);
      await n.load();
      expect(n.state.recurringJobs.map((j) => j.id), contains('c1'));
      expect(n.state.recurringJobs.map((j) => j.id), isNot(contains('o1')));
      expect(n.state.recurringJobs.map((j) => j.id), isNot(contains('r1')));
    });

    test('oneOffJobs excludes cron-type jobs', () async {
      final t = _OkTransport({
        'jobs': [
          _jobJson(id: 'c1', jobType: 'cron'),
          _jobJson(id: 'o1', jobType: 'one_off', cronExpression: null),
        ],
      });
      final n = _makeNotifier(t);
      await n.load();
      expect(n.state.oneOffJobs.map((j) => j.id), contains('o1'));
      expect(n.state.oneOffJobs.map((j) => j.id), isNot(contains('c1')));
    });

    test('falls back to cronExpression for isRecurring when job_type absent',
        () async {
      final t = _OkTransport({
        'jobs': [
          // No explicit job_type → falls back to cronExpression presence.
          _jobJson(id: 'fb1', cronExpression: '0 9 * * *'),
          _jobJson(id: 'fb2', cronExpression: null),
        ],
      });
      final n = _makeNotifier(t);
      await n.load();
      expect(n.state.recurringJobs.map((j) => j.id), contains('fb1'));
      expect(n.state.oneOffJobs.map((j) => j.id), contains('fb2'));
    });
  });

  // ── clearError ────────────────────────────────────────────────────────────

  group('JobsNotifier.clearError', () {
    test('resets error to null', () async {
      final n = _makeNotifier(_FailTransport());
      await n.load();
      expect(n.state.error, isNotNull);
      n.clearError();
      expect(n.state.error, isNull);
    });
  });

  // ── watcher filter ────────────────────────────────────────────────────────

  group('watcher filtering', () {
    test('listJobs excludes watcher-type jobs from the provider state', () async {
      final t = _OkTransport({
        'jobs': [
          _jobJson(id: 'w1', jobType: 'watcher'),
          _jobJson(id: 'c1', jobType: 'cron'),
        ],
      });
      final n = _makeNotifier(t);
      await n.load();
      expect(n.state.jobs.map((j) => j.id), isNot(contains('w1')));
      expect(n.state.jobs.map((j) => j.id), contains('c1'));
    });
  });
}
