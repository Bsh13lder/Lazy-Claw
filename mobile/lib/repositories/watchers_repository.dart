import '../core/api/api_client.dart';

// ── Model ──────────────────────────────────────────────────────────────────

/// A URL monitor watcher.
///
/// Maps the `Watcher` shape from `GET /api/watchers` (and per-item endpoints).
/// Every field the server marks nullable is nullable here.
class Watcher {
  final String id;
  final String name;
  final String status; // "active" | "paused" | ...
  final String jobType;
  final String? url;
  final int? checkInterval; // seconds
  final String? lastCheck; // ISO-8601 or null
  final String? lastRun;
  final String? nextRun;
  final int checkCount;
  final int triggerCount;
  final int errorCount;
  final String? lastError;
  final String? whatToWatch;
  final String? instruction;
  final bool oneShot;
  final String? templateName;
  final String? templateIcon;
  final String? lastValue;
  final String? createdAt;

  const Watcher({
    required this.id,
    required this.name,
    required this.status,
    required this.jobType,
    this.url,
    this.checkInterval,
    this.lastCheck,
    this.lastRun,
    this.nextRun,
    required this.checkCount,
    required this.triggerCount,
    required this.errorCount,
    this.lastError,
    this.whatToWatch,
    this.instruction,
    this.oneShot = false,
    this.templateName,
    this.templateIcon,
    this.lastValue,
    this.createdAt,
  });

  factory Watcher.fromJson(Map<String, dynamic> json) {
    return Watcher(
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      status: json['status']?.toString() ?? 'unknown',
      jobType: json['job_type']?.toString() ?? '',
      url: json['url']?.toString(),
      checkInterval: (json['check_interval'] as num?)?.toInt(),
      lastCheck: json['last_check']?.toString(),
      lastRun: json['last_run']?.toString(),
      nextRun: json['next_run']?.toString(),
      checkCount: (json['check_count'] as num?)?.toInt() ?? 0,
      triggerCount: (json['trigger_count'] as num?)?.toInt() ?? 0,
      errorCount: (json['error_count'] as num?)?.toInt() ?? 0,
      lastError: json['last_error']?.toString(),
      whatToWatch: json['what_to_watch']?.toString(),
      instruction: json['instruction']?.toString(),
      oneShot: (json['one_shot'] as bool?) ?? false,
      templateName: json['template_name']?.toString(),
      templateIcon: json['template_icon']?.toString(),
      lastValue: json['last_value']?.toString(),
      createdAt: json['created_at']?.toString(),
    );
  }
}

/// A single check-run entry from `GET /api/watchers/{id}/history`.
class WatcherCheck {
  final int ts;
  final bool changed;
  final bool triggered;
  final String? valuePreview;
  final String? error;
  final String? notification;

  const WatcherCheck({
    required this.ts,
    required this.changed,
    required this.triggered,
    this.valuePreview,
    this.error,
    this.notification,
  });

  factory WatcherCheck.fromJson(Map<String, dynamic> json) {
    return WatcherCheck(
      ts: (json['ts'] as num?)?.toInt() ?? 0,
      changed: (json['changed'] as bool?) ?? false,
      triggered: (json['triggered'] as bool?) ?? false,
      valuePreview: json['value_preview']?.toString(),
      error: json['error']?.toString(),
      notification: json['notification']?.toString(),
    );
  }
}

/// Summary stats from `GET /api/watchers/summary`.
class WatcherSummary {
  final int total;
  final int active;
  final int paused;
  final int? lastTriggerTs;
  final String? lastTriggerName;
  final String? lastTriggerMessage;

  const WatcherSummary({
    required this.total,
    required this.active,
    required this.paused,
    this.lastTriggerTs,
    this.lastTriggerName,
    this.lastTriggerMessage,
  });

  factory WatcherSummary.fromJson(Map<String, dynamic> json) {
    return WatcherSummary(
      total: (json['total'] as num?)?.toInt() ?? 0,
      active: (json['active'] as num?)?.toInt() ?? 0,
      paused: (json['paused'] as num?)?.toInt() ?? 0,
      lastTriggerTs: (json['last_trigger_ts'] as num?)?.toInt(),
      lastTriggerName: json['last_trigger_name']?.toString(),
      lastTriggerMessage: json['last_trigger_message']?.toString(),
    );
  }
}

// ── Transport seam ─────────────────────────────────────────────────────────

/// Testable seam — mirrors the [TasksTransport] / [BudgetsTransport] pattern.
///
/// The concrete [DioWatchersTransport] wires this to [ApiClient]; tests supply
/// a fake implementation instead.
abstract class WatchersTransport {
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  });
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body);
  Future<Map<String, dynamic>> deleteJson(String path);
}

class DioWatchersTransport implements WatchersTransport {
  final ApiClient _client;
  DioWatchersTransport(this._client);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) =>
      _client.get<Map<String, dynamic>>(
        path,
        queryParams: queryParams,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      _client.post<Map<String, dynamic>>(
        path,
        data: body,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> deleteJson(String path) =>
      _client.delete<Map<String, dynamic>>(
        path,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );
}

// ── Repository ─────────────────────────────────────────────────────────────

/// Remote-only (no local cache) repository for URL monitors.
///
/// Endpoints mirrored from `web/src/api.ts`:
///   - `GET    /api/watchers`              → `{watchers: [...]}`
///   - `GET    /api/watchers/summary`      → WatcherSummary
///   - `GET    /api/watchers/{id}`         → Watcher
///   - `GET    /api/watchers/{id}/history` → `{watcher_id, checks: [...]}`
///   - `POST   /api/watchers/{id}/pause`   → `{status}`
///   - `POST   /api/watchers/{id}/resume`  → `{status}`
///   - `DELETE /api/watchers/{id}`         → `{status}`
class WatchersRepository {
  final WatchersTransport _t;
  WatchersRepository(this._t);

  /// `GET /api/watchers` — returns all watchers for the current user.
  Future<List<Watcher>> listWatchers() async {
    final json = await _t.getJson('/api/watchers');
    final raw = json['watchers'] as List? ?? const [];
    return raw
        .map((e) => Watcher.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// `GET /api/watchers/summary` — aggregate stats (total, active, paused).
  Future<WatcherSummary> getSummary() async {
    final json = await _t.getJson('/api/watchers/summary');
    return WatcherSummary.fromJson(json);
  }

  /// `GET /api/watchers/{id}` — fetch a single watcher by id.
  Future<Watcher> getWatcher(String id) async {
    final json = await _t.getJson('/api/watchers/$id');
    return Watcher.fromJson(json);
  }

  /// `GET /api/watchers/{id}/history` — check-run history for a watcher.
  Future<List<WatcherCheck>> getHistory(String id) async {
    final json = await _t.getJson('/api/watchers/$id/history');
    final raw = json['checks'] as List? ?? const [];
    return raw
        .map((e) => WatcherCheck.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// `POST /api/watchers/{id}/pause` — pause a running watcher.
  Future<void> pauseWatcher(String id) async {
    await _t.postJson('/api/watchers/$id/pause', const {});
  }

  /// `POST /api/watchers/{id}/resume` — resume a paused watcher.
  Future<void> resumeWatcher(String id) async {
    await _t.postJson('/api/watchers/$id/resume', const {});
  }

  /// `DELETE /api/watchers/{id}` — permanently remove a watcher.
  Future<void> deleteWatcher(String id) async {
    await _t.deleteJson('/api/watchers/$id');
  }
}
