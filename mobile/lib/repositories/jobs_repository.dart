import '../core/api/api_client.dart';

// ── Model ──────────────────────────────────────────────────────────────────

/// A scheduled job returned by `GET /api/jobs`.
///
/// Mirrors the web [Job] TypeScript interface exactly:
/// ```ts
/// interface Job {
///   id: string; name: string; instruction: string;
///   cron_expression: string | null; status: string;
///   last_run: string | null; next_run: string | null;
///   job_type?: string; context?: string;
///   created_at?: string | null; last_status?: string | null;
///   last_error?: string | null;
/// }
/// ```
class Job {
  final String id;
  final String name;
  final String instruction;
  final String? cronExpression;
  final String status;
  final String? lastRun;
  final String? nextRun;
  final String? jobType;
  final String? context;
  final String? createdAt;
  final String? lastStatus;
  final String? lastError;

  const Job({
    required this.id,
    required this.name,
    required this.instruction,
    this.cronExpression,
    required this.status,
    this.lastRun,
    this.nextRun,
    this.jobType,
    this.context,
    this.createdAt,
    this.lastStatus,
    this.lastError,
  });

  factory Job.fromJson(Map<String, dynamic> json) => Job(
        id: json['id']?.toString() ?? '',
        name: json['name']?.toString() ?? '',
        instruction: json['instruction']?.toString() ?? '',
        cronExpression: json['cron_expression']?.toString(),
        status: json['status']?.toString() ?? 'unknown',
        lastRun: json['last_run']?.toString(),
        nextRun: json['next_run']?.toString(),
        jobType: json['job_type']?.toString(),
        context: json['context']?.toString(),
        createdAt: json['created_at']?.toString(),
        lastStatus: json['last_status']?.toString(),
        lastError: json['last_error']?.toString(),
      );

  /// Whether this is a recurring (cron) job vs one-off / reminder.
  bool get isRecurring {
    final t = jobType;
    if (t == 'cron') return true;
    if (t == 'one_off' || t == 'reminder') return false;
    return cronExpression != null;
  }
}

// ── Transport seam ─────────────────────────────────────────────────────────

/// Testable transport seam — mirrors the pattern from TasksTransport.
///
/// Only the methods actually used by [JobsRepository] are declared here.
abstract class JobsTransport {
  Future<Map<String, dynamic>> getJson(String path);
  Future<Map<String, dynamic>> postJson(String path);
}

/// Dio-backed production transport.
class DioJobsTransport implements JobsTransport {
  final ApiClient _client;
  DioJobsTransport(this._client);

  @override
  Future<Map<String, dynamic>> getJson(String path) =>
      _client.get<Map<String, dynamic>>(
        path,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> postJson(String path) =>
      _client.post<Map<String, dynamic>>(
        path,
        data: const <String, dynamic>{},
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );
}

// ── Repository ─────────────────────────────────────────────────────────────

/// Remote-only (network-first) repository for scheduled jobs.
///
/// Jobs are server-owned state — they fire on the server clock and
/// persist across devices. No local cache is warranted; a simple fetch
/// on every screen visit is the correct pattern here.
///
/// Supported endpoints (mirroring `web/src/api.ts`):
///
/// | Method | Path                  | Purpose                         |
/// |--------|-----------------------|---------------------------------|
/// | GET    | /api/jobs             | List all non-watcher jobs       |
/// | POST   | /api/jobs/{id}/pause  | Pause an active recurring job   |
/// | POST   | /api/jobs/{id}/resume | Resume a paused recurring job   |
///
/// **Not exposed (create/update/delete):** those operations require a cron
/// expression editor and multi-field form — read+status-toggle is the
/// correct mobile scope for v1. The web UI exposes the full CRUD surface.
class JobsRepository {
  final JobsTransport _t;
  JobsRepository(this._t);

  /// Fetch all jobs, filtering out internal `watcher`-type entries.
  ///
  /// Maps `GET /api/jobs` → `{ jobs: Job[] }`.
  Future<List<Job>> listJobs() async {
    final json = await _t.getJson('/api/jobs');
    final rawList = json['jobs'] as List? ?? const [];
    return rawList
        .map((e) => Job.fromJson(Map<String, dynamic>.from(e as Map)))
        .where((j) => j.jobType != 'watcher')
        .toList();
  }

  /// Pause an active recurring job.
  ///
  /// Maps `POST /api/jobs/{id}/pause` → `{ status: string }`.
  Future<void> pauseJob(String id) async {
    await _t.postJson('/api/jobs/$id/pause');
  }

  /// Resume a paused recurring job.
  ///
  /// Maps `POST /api/jobs/{id}/resume` → `{ status: string }`.
  Future<void> resumeJob(String id) async {
    await _t.postJson('/api/jobs/$id/resume');
  }
}
