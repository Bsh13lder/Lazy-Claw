import '../core/api/api_client.dart';

// ── Model ──────────────────────────────────────────────────────────────────

/// One entry in the permission / skill activity log.
///
/// Mirrors the web [AuditEntry] shape from `GET /api/permissions/audit`:
/// ```json
/// { "id": "...", "action": "execute", "skill_name": "web_search",
///   "result_summary": "Searched ...", "source": "agent",
///   "created_at": "2026-06-04T12:00:00Z" }
/// ```
class AuditEntry {
  final String id;
  final String action;
  final String? skillName;
  final String? resultSummary;
  final String source;
  final String createdAt;

  const AuditEntry({
    required this.id,
    required this.action,
    this.skillName,
    this.resultSummary,
    required this.source,
    required this.createdAt,
  });

  factory AuditEntry.fromJson(Map<String, dynamic> json) => AuditEntry(
        id: json['id']?.toString() ?? '',
        action: json['action']?.toString() ?? '',
        skillName: json['skill_name']?.toString(),
        resultSummary: json['result_summary']?.toString(),
        source: json['source']?.toString() ?? '',
        createdAt: json['created_at']?.toString() ?? '',
      );
}

// ── Transport seam ─────────────────────────────────────────────────────────

/// Testable seam — mirrors the BudgetsTransport / TasksTransport pattern.
abstract class AuditTransport {
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  });
}

class DioAuditTransport implements AuditTransport {
  final ApiClient _client;
  DioAuditTransport(this._client);

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
}

// ── Repository ─────────────────────────────────────────────────────────────

/// Fetches the permission / skill audit log from `GET /api/permissions/audit`.
///
/// Supports optional [action] filter, [since] (ISO timestamp), and [limit].
/// This is read-only — audit entries are server-generated.
class AuditRepository {
  final AuditTransport _t;
  AuditRepository(this._t);

  /// Fetch audit entries.
  ///
  /// - [action]: filter by action string (e.g. `"execute"`, `"denied"`,
  ///   `"approved"`). Pass null for all.
  /// - [since]: ISO 8601 lower bound. Pass null for no time filter.
  /// - [limit]: maximum entries to return. Pass null for server default.
  Future<List<AuditEntry>> listAudit({
    String? action,
    String? since,
    int? limit,
  }) async {
    final params = <String, dynamic>{};
    if (action != null) params['action'] = action;
    if (since != null) params['since'] = since;
    if (limit != null) params['limit'] = limit.toString();

    final json = await _t.getJson(
      '/api/permissions/audit',
      queryParams: params.isEmpty ? null : params,
    );

    final rawList = json['data'] as List? ?? const [];
    return rawList
        .map((e) => AuditEntry.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }
}
