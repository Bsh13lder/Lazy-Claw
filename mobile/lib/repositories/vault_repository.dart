import '../core/api/api_client.dart';

/// Testable transport seam — mirrors the AuthTransport pattern.
///
/// The API contract (from web/src/api.ts):
///   GET    /api/vault              → { keys: string[] }   (values NOT returned)
///   PUT    /api/vault/{key}        body: { value }         → { status }
///   DELETE /api/vault/{key}                                → { status }
///
/// Values are NEVER returned by the list endpoint — the server exposes only
/// the key names. This screen is manage-only (no copy-value affordance).
abstract class VaultTransport {
  Future<Map<String, dynamic>> getJson(String path);
  Future<Map<String, dynamic>> putJson(String path, Map<String, dynamic> body);
  Future<Map<String, dynamic>> deleteJson(String path);
}

class DioVaultTransport implements VaultTransport {
  final ApiClient _client;
  DioVaultTransport(this._client);

  @override
  Future<Map<String, dynamic>> getJson(String path) =>
      _client.get<Map<String, dynamic>>(
        path,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      _client.put<Map<String, dynamic>>(
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

/// Immutable value object for a single vault entry. Values are secret —
/// the API only returns the key name, never the plaintext value.
class VaultEntry {
  final String name;

  const VaultEntry({required this.name});

  @override
  bool operator ==(Object other) =>
      identical(this, other) || (other is VaultEntry && other.name == name);

  @override
  int get hashCode => name.hashCode;
}

class VaultRepository {
  final VaultTransport _t;
  VaultRepository(this._t);

  /// Returns the list of stored credential key names.
  /// Values are NOT returned by the server — they are write-only after creation.
  Future<List<VaultEntry>> listSecrets() async {
    final json = await _t.getJson('/api/vault');
    final rawKeys = json['keys'];
    if (rawKeys == null) return const [];
    final keys = rawKeys as List;
    return keys.map((k) => VaultEntry(name: k.toString())).toList();
  }

  /// Creates or updates a vault entry. The [value] is encrypted server-side
  /// with AES-256-GCM before storage.
  Future<void> addSecret(String name, String value) async {
    final encoded = Uri.encodeComponent(name);
    await _t.putJson('/api/vault/$encoded', {'value': value});
  }

  /// Permanently removes a vault entry by its [name] (the key).
  Future<void> deleteSecret(String name) async {
    final encoded = Uri.encodeComponent(name);
    await _t.deleteJson('/api/vault/$encoded');
  }
}
