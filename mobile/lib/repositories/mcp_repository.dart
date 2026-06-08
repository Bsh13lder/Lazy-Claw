import '../core/api/api_client.dart';

/// Mirrors the exact shape returned by `GET /api/mcp/servers`.
///
/// Source of truth: [web/src/api.ts] `McpServer` interface.
class McpServer {
  final String id;
  final String name;

  /// Transport type: `stdio` | `sse` | `streamable_http`
  final String transport;

  /// Only set for stdio servers (command path).
  final String? command;

  /// Only set for SSE / streamable HTTP servers.
  final String? url;

  /// Connection state: `connected` | `connecting` | `error` | `disconnected`
  final String status;

  final int toolCount;
  final bool favorite;

  const McpServer({
    required this.id,
    required this.name,
    required this.transport,
    this.command,
    this.url,
    required this.status,
    required this.toolCount,
    this.favorite = false,
  });

  factory McpServer.fromJson(Map<String, dynamic> json) => McpServer(
        id: (json['id'] ?? '').toString(),
        name: (json['name'] ?? '').toString(),
        transport: (json['transport'] ?? 'stdio').toString(),
        command: json['command']?.toString(),
        url: json['url']?.toString(),
        status: (json['status'] ?? 'disconnected').toString(),
        toolCount: (json['tool_count'] as num?)?.toInt() ?? 0,
        favorite: json['favorite'] as bool? ?? false,
      );

  bool get isConnected => status == 'connected';
}

// ── Transport seam ────────────────────────────────────────────────────────────

/// Minimal transport seam — the only method the repository needs.
/// Mirrors the [AuthTransport] / [TasksTransport] pattern for testability.
abstract class McpTransport {
  Future<Map<String, dynamic>> getJson(String path);
}

class DioMcpTransport implements McpTransport {
  final ApiClient _client;
  DioMcpTransport(this._client);

  @override
  Future<Map<String, dynamic>> getJson(String path) =>
      _client.get<Map<String, dynamic>>(
        path,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );
}

// ── Repository ────────────────────────────────────────────────────────────────

/// Read-only seam over `GET /api/mcp/servers`.
///
/// Returns `{ servers: [McpServer] }`. Unit-testable via [McpTransport].
class McpRepository {
  final McpTransport _t;
  McpRepository(this._t);

  /// Fetch the list of registered MCP servers and their live status.
  /// Maps `GET /api/mcp/servers` → `{ servers: [...] }`.
  Future<List<McpServer>> listServers() async {
    final json = await _t.getJson('/api/mcp/servers');
    final raw = json['servers'] as List? ?? const [];
    return raw
        .map((e) => McpServer.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }
}
