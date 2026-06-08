import '../core/api/api_client.dart';
import '../models/specialist.dart';

/// Testable transport seam — mirrors the SkillsTransport / McpTransport pattern.
///
/// Concrete callers talk to the server; tests swap in a fake transport.
abstract class SpecialistsTransport {
  /// `GET /api/specialists` → `{ ok, specialists: [...] }`.
  Future<Map<String, dynamic>> getJson(String path);

  /// `DELETE /api/specialists/{name}` → `{ ok: true }`.
  Future<Map<String, dynamic>> deleteJson(String path);
}

/// Dio-backed production implementation.
class DioSpecialistsTransport implements SpecialistsTransport {
  final ApiClient _client;
  DioSpecialistsTransport(this._client);

  @override
  Future<Map<String, dynamic>> getJson(String path) =>
      _client.get<Map<String, dynamic>>(
        path,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> deleteJson(String path) =>
      _client.delete<Map<String, dynamic>>(
        path,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );
}

/// Remote-only repository for the specialist registry.
///
/// Specialists are agent definitions (builtins live in-repo; custom ones are
/// encrypted server-side) — not offline user content — so there is no local
/// cache layer. The UI fetches on first mount and on pull-to-refresh.
///
/// Builtins are read-only (forkable on the web dashboard). Mobile v1 supports
/// list + view, plus deleting a custom specialist. Full create/edit is deferred
/// to the web Specialists surface.
class SpecialistsRepository {
  final SpecialistsTransport _t;
  SpecialistsRepository(this._t);

  /// Fetch all specialists. Maps `GET /api/specialists` →
  /// `{ ok, specialists: [...] }`.
  ///
  /// Returns an empty list (never throws) when the envelope is malformed, so
  /// the UI can degrade gracefully. Transport/network errors still propagate.
  Future<List<Specialist>> listSpecialists() async {
    final json = await _t.getJson('/api/specialists');
    final raw = json['specialists'];
    if (raw is! List) return const [];
    return raw
        .whereType<Map>()
        .map((e) => Specialist.fromJson(Map<String, dynamic>.from(e)))
        .toList();
  }

  /// Delete a custom specialist via `DELETE /api/specialists/{name}`.
  ///
  /// Throws on network / server errors (e.g. a 4xx for a builtin) so the
  /// provider can surface the failure and roll back.
  Future<void> deleteSpecialist(String name) async {
    await _t.deleteJson('/api/specialists/${Uri.encodeComponent(name)}');
  }
}
