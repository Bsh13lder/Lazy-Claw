import '../core/api/api_client.dart';

// ── Model ──────────────────────────────────────────────────────────────────

/// A single personal-memory fact as returned by `GET /api/memory/personal`.
///
/// Web API shape (api.ts `Memory` interface):
///   { id: string, key: string, value: string, created_at: string }
///
/// NOTE: There is no `memory_type` or `importance` on this endpoint. Memory is
/// written exclusively server-side by the agent — no create endpoint exists on
/// the personal memory API. This screen is view + delete only.
class PersonalMemory {
  final String id;

  /// Short label / category slug surfaced as a chip (e.g. "preference").
  final String key;

  /// Human-readable content of the memory fact.
  final String value;

  /// ISO-8601 creation timestamp from the server.
  final String createdAt;

  const PersonalMemory({
    required this.id,
    required this.key,
    required this.value,
    required this.createdAt,
  });

  factory PersonalMemory.fromJson(Map<String, dynamic> json) {
    return PersonalMemory(
      id: json['id']?.toString() ?? '',
      key: json['key']?.toString() ?? '',
      value: json['value']?.toString() ?? '',
      createdAt: json['created_at']?.toString() ?? '',
    );
  }

  @override
  String toString() =>
      'PersonalMemory(id: $id, key: $key, createdAt: $createdAt)';
}

// ── Transport seam ─────────────────────────────────────────────────────────

/// Minimal transport interface: only the two HTTP verbs the memory API uses.
///
/// The personal memory API is GET + DELETE only — no POST/PATCH.
abstract class MemoryTransport {
  Future<Map<String, dynamic>> getJson(String path);
  Future<Map<String, dynamic>> deleteJson(String path);
}

class DioMemoryTransport implements MemoryTransport {
  final ApiClient _client;
  DioMemoryTransport(this._client);

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

// ── Repository ─────────────────────────────────────────────────────────────

/// Personal memory repository — view + delete only.
///
/// Endpoints mirrored from web/src/api.ts:
///   GET    /api/memory/personal        → { memories: Memory[] }
///   DELETE /api/memory/personal/{id}   → { status: string }
///
/// No create endpoint exists on this API surface.
class MemoryRepository {
  final MemoryTransport _t;
  MemoryRepository(this._t);

  /// Fetch all personal memory facts for the authenticated user.
  Future<List<PersonalMemory>> listMemories() async {
    final json = await _t.getJson('/api/memory/personal');
    final raw = json['memories'] as List? ?? const [];
    return raw
        .map(
          (e) => PersonalMemory.fromJson(Map<String, dynamic>.from(e as Map)),
        )
        .toList();
  }

  /// Delete a memory fact by [id].
  Future<void> deleteMemory(String id) async {
    await _t.deleteJson('/api/memory/personal/$id');
  }
}
