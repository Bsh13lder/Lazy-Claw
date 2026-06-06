import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/jobs_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeTransport implements JobsTransport {
  String? lastPath;
  String? lastMethod;
  Map<String, dynamic> response;

  _FakeTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    lastMethod = 'GET';
    lastPath = path;
    return response;
  }

  @override
  Future<Map<String, dynamic>> postJson(String path) async {
    lastMethod = 'POST';
    lastPath = path;
    return response;
  }
}

class _ThrowingTransport implements JobsTransport {
  final Object error;
  _ThrowingTransport([this.error = 'network error']);

  @override
  Future<Map<String, dynamic>> getJson(String path) async => throw error;
  @override
  Future<Map<String, dynamic>> postJson(String path) async => throw error;
}

// ── Fixtures ───────────────────────────────────────────────────────────────

Map<String, dynamic> _jobJson({
  String id = 'j1',
  String name = 'Test job',
  String instruction = 'Do something',
  String? cronExpression = '0 9 * * *',
  String status = 'active',
  String? jobType,
  String? lastRun,
  String? nextRun,
  String? lastStatus,
  String? lastError,
  String? createdAt,
  String? context,
}) =>
    {
      'id': id,
      'name': name,
      'instruction': instruction,
      'cron_expression': cronExpression,
      'status': status,
      'job_type': jobType,
      'last_run': lastRun,
      'next_run': nextRun,
      'last_status': lastStatus,
      'last_error': lastError,
      'created_at': createdAt,
      'context': context,
    };

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  // ── listJobs ─────────────────────────────────────────────────────────────

  group('JobsRepository.listJobs', () {
    test('GET /api/jobs and parses job list', () async {
      final t = _FakeTransport({
        'jobs': [
          _jobJson(id: 'a1', name: 'Morning digest'),
          _jobJson(id: 'a2', name: 'Weekly report'),
        ],
      });
      final repo = JobsRepository(t);
      final jobs = await repo.listJobs();

      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/jobs');
      expect(jobs, hasLength(2));
      expect(jobs[0].id, 'a1');
      expect(jobs[0].name, 'Morning digest');
      expect(jobs[1].id, 'a2');
    });

    test('returns empty list when jobs key is missing', () async {
      final t = _FakeTransport({});
      final jobs = await JobsRepository(t).listJobs();
      expect(jobs, isEmpty);
    });

    test('returns empty list when jobs array is empty', () async {
      final t = _FakeTransport({'jobs': <dynamic>[]});
      final jobs = await JobsRepository(t).listJobs();
      expect(jobs, isEmpty);
    });

    test('filters out watcher-type entries', () async {
      final t = _FakeTransport({
        'jobs': [
          _jobJson(id: 'w1', name: 'Page watcher', jobType: 'watcher'),
          _jobJson(id: 'c1', name: 'Daily cron', jobType: 'cron'),
        ],
      });
      final jobs = await JobsRepository(t).listJobs();
      expect(jobs, hasLength(1));
      expect(jobs[0].id, 'c1');
    });

    test('keeps jobs with null job_type', () async {
      final t = _FakeTransport({
        'jobs': [_jobJson(id: 'n1', name: 'No type job')],
      });
      final jobs = await JobsRepository(t).listJobs();
      expect(jobs, hasLength(1));
    });

    test('propagates transport errors', () async {
      final t = _ThrowingTransport('server down');
      expect(() => JobsRepository(t).listJobs(), throwsA(equals('server down')));
    });
  });

  // ── Job.fromJson ──────────────────────────────────────────────────────────

  group('Job.fromJson', () {
    test('parses all fields', () {
      final j = Job.fromJson(_jobJson(
        id: 'j1',
        name: 'Digest',
        instruction: 'Send email',
        cronExpression: '0 8 * * 1',
        status: 'active',
        jobType: 'cron',
        lastRun: '2026-06-05T08:00:00Z',
        nextRun: '2026-06-09T08:00:00Z',
        lastStatus: 'success',
        createdAt: '2026-06-01T00:00:00Z',
        context: 'some context',
      ));

      expect(j.id, 'j1');
      expect(j.name, 'Digest');
      expect(j.instruction, 'Send email');
      expect(j.cronExpression, '0 8 * * 1');
      expect(j.status, 'active');
      expect(j.jobType, 'cron');
      expect(j.lastRun, '2026-06-05T08:00:00Z');
      expect(j.nextRun, '2026-06-09T08:00:00Z');
      expect(j.lastStatus, 'success');
      expect(j.createdAt, '2026-06-01T00:00:00Z');
      expect(j.context, 'some context');
    });

    test('handles null optional fields gracefully', () {
      final j = Job.fromJson({
        'id': 'x',
        'name': 'Bare',
        'instruction': 'Do it',
        'cron_expression': null,
        'status': 'active',
      });
      expect(j.cronExpression, isNull);
      expect(j.lastRun, isNull);
      expect(j.nextRun, isNull);
      expect(j.jobType, isNull);
      expect(j.lastStatus, isNull);
      expect(j.lastError, isNull);
      expect(j.createdAt, isNull);
      expect(j.context, isNull);
    });

    test('defaults empty id/name/instruction to empty string', () {
      final j = Job.fromJson({});
      expect(j.id, '');
      expect(j.name, '');
      expect(j.instruction, '');
      expect(j.status, 'unknown');
    });
  });

  // ── Job.isRecurring ───────────────────────────────────────────────────────

  group('Job.isRecurring', () {
    test('true when job_type is cron', () {
      final j = Job.fromJson(_jobJson(jobType: 'cron'));
      expect(j.isRecurring, isTrue);
    });

    test('false when job_type is one_off', () {
      final j = Job.fromJson(_jobJson(jobType: 'one_off', cronExpression: null));
      expect(j.isRecurring, isFalse);
    });

    test('false when job_type is reminder', () {
      final j = Job.fromJson(_jobJson(jobType: 'reminder', cronExpression: null));
      expect(j.isRecurring, isFalse);
    });

    test('falls back to cronExpression when job_type is null', () {
      final withCron = Job.fromJson(_jobJson(cronExpression: '0 9 * * *'));
      expect(withCron.isRecurring, isTrue);

      final noCron = Job.fromJson(_jobJson(cronExpression: null));
      expect(noCron.isRecurring, isFalse);
    });
  });

  // ── pauseJob ──────────────────────────────────────────────────────────────

  group('JobsRepository.pauseJob', () {
    test('POST /api/jobs/{id}/pause', () async {
      final t = _FakeTransport({'status': 'paused'});
      await JobsRepository(t).pauseJob('j42');
      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/jobs/j42/pause');
    });

    test('propagates errors', () async {
      final t = _ThrowingTransport(Exception('timeout'));
      expect(
        () => JobsRepository(t).pauseJob('bad'),
        throwsA(isA<Exception>()),
      );
    });
  });

  // ── resumeJob ─────────────────────────────────────────────────────────────

  group('JobsRepository.resumeJob', () {
    test('POST /api/jobs/{id}/resume', () async {
      final t = _FakeTransport({'status': 'active'});
      await JobsRepository(t).resumeJob('j99');
      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/jobs/j99/resume');
    });

    test('propagates errors', () async {
      final t = _ThrowingTransport(Exception('server error'));
      expect(
        () => JobsRepository(t).resumeJob('bad'),
        throwsA(isA<Exception>()),
      );
    });
  });
}
