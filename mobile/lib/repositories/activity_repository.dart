import '../core/api/api_client.dart';

// ── Model ──────────────────────────────────────────────────────────────────

/// One agent task in the Activity view.
///
/// Unions the four task shapes returned by `GET /api/agents/status`
/// (`active`, `background`, `background_recent`, `recent`). Most fields are
/// optional — a running task carries [elapsedS] / [currentTool] / [phase],
/// while a settled task carries [durationS] / [result] / [completedAt].
///
/// [isRunning] is derived from WHICH bucket the row came from (active/background
/// → running; recent/background_recent → settled) rather than guessed from
/// [status], so the UI never has to second-guess server semantics.
class AgentTask {
  final String taskId;
  final String name;
  final String lane; // main | specialist | background
  final String status; // running | done | failed | cancelled
  final bool isRunning;

  final String? description;
  final double? elapsedS; // running tasks
  final double? durationS; // settled tasks
  final String? currentTool;
  final List<String> recentTools;
  final String? phase;
  final String? resultPreview;
  final String? result;
  final String? error;
  final String? createdAt;
  final String? completedAt;
  final double? costUsd;
  final int? tokensUsed;
  final String? projectTag;

  const AgentTask({
    required this.taskId,
    required this.name,
    required this.lane,
    required this.status,
    required this.isRunning,
    this.description,
    this.elapsedS,
    this.durationS,
    this.currentTool,
    this.recentTools = const [],
    this.phase,
    this.resultPreview,
    this.result,
    this.error,
    this.createdAt,
    this.completedAt,
    this.costUsd,
    this.tokensUsed,
    this.projectTag,
  });

  bool get isFailed => status == 'failed';
  bool get isCancelled => status == 'cancelled';

  /// Best human-facing one-liner describing what this task did / is doing.
  String? get subtitle {
    if (isRunning) {
      if (currentTool != null && currentTool!.isNotEmpty) return currentTool;
      if (phase != null && phase!.isNotEmpty) return phase;
      return description;
    }
    if (error != null && error!.isNotEmpty) return error;
    if (resultPreview != null && resultPreview!.isNotEmpty) return resultPreview;
    return result ?? description;
  }

  static String? _nonEmpty(dynamic v) {
    final s = v?.toString();
    return (s != null && s.isNotEmpty) ? s : null;
  }

  static double? _double(dynamic v) =>
      v == null ? null : (v is num ? v.toDouble() : double.tryParse(v.toString()));

  static int? _int(dynamic v) =>
      v == null ? null : (v is num ? v.toInt() : int.tryParse(v.toString()));

  factory AgentTask.fromJson(
    Map<String, dynamic> json, {
    required bool isRunning,
  }) {
    final rawName = json['name']?.toString();
    return AgentTask(
      taskId: json['task_id']?.toString() ?? '',
      name: (rawName != null && rawName.trim().isNotEmpty) ? rawName : 'Task',
      lane: json['lane']?.toString() ?? '',
      status: json['status']?.toString() ?? (isRunning ? 'running' : 'done'),
      isRunning: isRunning,
      description: _nonEmpty(json['description']),
      elapsedS: _double(json['elapsed_s']),
      durationS: _double(json['duration_s']),
      currentTool: _nonEmpty(json['current_tool']),
      recentTools: (json['recent_tools'] as List?)
              ?.map((e) => e.toString())
              .toList(growable: false) ??
          const [],
      phase: _nonEmpty(json['phase']),
      resultPreview: _nonEmpty(json['result_preview']),
      result: _nonEmpty(json['result']),
      error: _nonEmpty(json['error']),
      createdAt: _nonEmpty(json['created_at']),
      completedAt: _nonEmpty(json['completed_at']),
      costUsd: _double(json['cost_usd']),
      tokensUsed: _int(json['tokens_used']),
      projectTag: _nonEmpty(json['project_tag']),
    );
  }
}

/// Immutable snapshot of the agent's work split into two ordered groups.
class AgentActivitySnapshot {
  /// Tasks executing right now (`active` + `background`).
  final List<AgentTask> running;

  /// Tasks that have already settled (`recent` + `background_recent`),
  /// newest-first as returned by the server.
  final List<AgentTask> recent;

  const AgentActivitySnapshot({
    this.running = const [],
    this.recent = const [],
  });

  bool get isEmpty => running.isEmpty && recent.isEmpty;
  int get total => running.length + recent.length;
}

// ── Transport seam ─────────────────────────────────────────────────────────

/// Testable seam — mirrors the [AuditTransport] / BudgetsTransport pattern.
abstract class ActivityTransport {
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  });

  Future<Map<String, dynamic>> postJson(
    String path, {
    Map<String, dynamic>? body,
  });
}

class DioActivityTransport implements ActivityTransport {
  final ApiClient _client;
  DioActivityTransport(this._client);

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
    String path, {
    Map<String, dynamic>? body,
  }) =>
      _client.post<Map<String, dynamic>>(
        path,
        data: body,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );
}

// ── Repository ─────────────────────────────────────────────────────────────

/// Fetches live + recent agent activity from `GET /api/agents/status`.
///
/// Read-only — the server merges TeamLead (in-memory foreground/specialist
/// tasks) with TaskRunner (DB-persisted background tasks) and decrypts every
/// result body before returning, so the client receives plaintext JSON.
class ActivityRepository {
  final ActivityTransport _t;
  ActivityRepository(this._t);

  Future<AgentActivitySnapshot> getStatus() async {
    final json = await _t.getJson('/api/agents/status');

    List<AgentTask> parse(String key, {required bool isRunning}) =>
        ((json[key] as List?) ?? const [])
            .map((e) => AgentTask.fromJson(
                  Map<String, dynamic>.from(e as Map),
                  isRunning: isRunning,
                ))
            .toList(growable: false);

    return AgentActivitySnapshot(
      running: [
        ...parse('active', isRunning: true),
        ...parse('background', isRunning: true),
      ],
      recent: [
        ...parse('recent', isRunning: false),
        ...parse('background_recent', isRunning: false),
      ],
    );
  }

  /// Cancel one running task — foreground, specialist, or background.
  ///
  /// `POST /api/agents/cancel {task_id}` returns
  /// `{success: bool, data|error}`; false means the task was not found or no
  /// longer cancellable (it may have just settled — refresh after calling).
  Future<bool> cancelTask(String taskId) async {
    final json = await _t.postJson(
      '/api/agents/cancel',
      body: {'task_id': taskId},
    );
    return json['success'] == true;
  }

  /// Cancel every running task for the current user (foreground +
  /// background). Returns how many cancellations were fired.
  Future<int> cancelAll() async {
    final json = await _t.postJson('/api/agents/cancel-all');
    final data = json['data'];
    if (data is Map && data['count'] is num) {
      return (data['count'] as num).toInt();
    }
    return 0;
  }
}
