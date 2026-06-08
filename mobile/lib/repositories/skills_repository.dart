import '../core/api/api_client.dart';
import '../models/skill.dart';

/// Testable transport seam — mirrors the AuthTransport / TasksTransport pattern.
///
/// Concrete callers talk to the server; tests swap in a [_FakeSkillsTransport].
abstract class SkillsTransport {
  /// `GET /api/skills` → `{ skills: [...] }`.
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  });

  /// `PATCH /api/skills/{id}` → `{ status: "ok" }`.
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  );
}

/// Dio-backed production implementation.
class DioSkillsTransport implements SkillsTransport {
  final ApiClient _client;
  DioSkillsTransport(this._client);

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
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      _client.patch<Map<String, dynamic>>(
        path,
        data: body,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );
}

/// Remote-only repository for the skills registry.
///
/// Skills live entirely server-side (they are agent capabilities, not user
/// content), so there is no local cache layer. The UI fetches on first mount
/// and on pull-to-refresh.
///
/// **Toggling** is supported via `PATCH /api/skills/{id}` with
/// `{ "enabled": bool }` — the web Skills.tsx and updateSkill() in api.ts
/// confirm this endpoint exists. Builtin and MCP skills allow toggling too.
class SkillsRepository {
  final SkillsTransport _t;
  SkillsRepository(this._t);

  /// Fetch all skills. Maps `GET /api/skills` → `{ skills: [...] }`.
  ///
  /// Returns an empty list (never throws) when the response envelope is
  /// malformed, so the UI can degrade gracefully.
  Future<List<Skill>> listSkills() async {
    final json = await _t.getJson('/api/skills');
    final raw = json['skills'];
    if (raw is! List) return const [];
    return raw
        .whereType<Map>()
        .map((e) => Skill.fromJson(Map<String, dynamic>.from(e)))
        .toList();
  }

  /// Toggle a skill on or off via `PATCH /api/skills/{id}`.
  ///
  /// The server responds with `{ status: "ok" }` on success. Throws on
  /// network / server errors so the provider can surface the error.
  Future<void> setEnabled(String id, {required bool enabled}) async {
    await _t.patchJson('/api/skills/$id', {'enabled': enabled});
  }
}
