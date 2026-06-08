import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/mcp_repository.dart';

// ── Fake transport ────────────────────────────────────────────────────────────

class _FakeTransport implements McpTransport {
  String? lastPath;
  Map<String, dynamic> response;

  _FakeTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    lastPath = path;
    return response;
  }
}

class _ThrowingTransport implements McpTransport {
  final Exception error;
  _ThrowingTransport(this.error);

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    throw error;
  }
}

// ── Fixtures ──────────────────────────────────────────────────────────────────

Map<String, dynamic> _serverJson({
  String id = 's1',
  String name = 'mcp-taskai',
  String transport = 'stdio',
  String status = 'connected',
  int toolCount = 12,
  String? command,
  String? url,
  bool favorite = false,
}) =>
    {
      'id': id,
      'name': name,
      'transport': transport,
      'status': status,
      'tool_count': toolCount,
      'command': command,
      'url': url,
      'favorite': favorite,
    };

// ── Tests ─────────────────────────────────────────────────────────────────────

void main() {
  group('McpServer.fromJson', () {
    test('parses all required fields', () {
      final s = McpServer.fromJson(_serverJson(
        id: 'abc',
        name: 'mcp-email',
        transport: 'sse',
        status: 'connected',
        toolCount: 7,
        url: 'https://host/mcp',
        favorite: true,
      ));

      expect(s.id, 'abc');
      expect(s.name, 'mcp-email');
      expect(s.transport, 'sse');
      expect(s.status, 'connected');
      expect(s.toolCount, 7);
      expect(s.url, 'https://host/mcp');
      expect(s.favorite, isTrue);
      expect(s.command, isNull);
    });

    test('parses stdio server with command field', () {
      final s = McpServer.fromJson(_serverJson(
        transport: 'stdio',
        command: 'python -m mcp_taskai',
      ));
      expect(s.transport, 'stdio');
      expect(s.command, 'python -m mcp_taskai');
      expect(s.url, isNull);
    });

    test('parses streamable_http transport', () {
      final s = McpServer.fromJson(
        _serverJson(transport: 'streamable_http', url: 'http://host/mcp'),
      );
      expect(s.transport, 'streamable_http');
    });

    test('defaults: toolCount=0, favorite=false, status=disconnected', () {
      final s = McpServer.fromJson({
        'id': 'x',
        'name': 'bare',
        'transport': 'stdio',
      });
      expect(s.toolCount, 0);
      expect(s.favorite, isFalse);
      expect(s.status, 'disconnected');
    });

    test('isConnected is true only when status == "connected"', () {
      expect(
        McpServer.fromJson(_serverJson(status: 'connected')).isConnected,
        isTrue,
      );
      expect(
        McpServer.fromJson(_serverJson(status: 'error')).isConnected,
        isFalse,
      );
      expect(
        McpServer.fromJson(_serverJson(status: 'disconnected')).isConnected,
        isFalse,
      );
      expect(
        McpServer.fromJson(_serverJson(status: 'connecting')).isConnected,
        isFalse,
      );
    });

    test('coerces numeric tool_count from int', () {
      final s = McpServer.fromJson({..._serverJson(), 'tool_count': 42});
      expect(s.toolCount, 42);
    });

    test('coerces numeric tool_count from double', () {
      final s = McpServer.fromJson({..._serverJson(), 'tool_count': 5.0});
      expect(s.toolCount, 5);
    });

    test('handles missing id/name/transport gracefully (empty string)', () {
      final s = McpServer.fromJson({'status': 'connected', 'tool_count': 0});
      expect(s.id, '');
      expect(s.name, '');
      expect(s.transport, 'stdio');
    });
  });

  group('McpRepository.listServers', () {
    test('calls GET /api/mcp/servers', () async {
      final t = _FakeTransport({'servers': []});
      await McpRepository(t).listServers();
      expect(t.lastPath, '/api/mcp/servers');
    });

    test('returns empty list when servers array is empty', () async {
      final t = _FakeTransport({'servers': []});
      final result = await McpRepository(t).listServers();
      expect(result, isEmpty);
    });

    test('returns empty list when servers key is absent', () async {
      final t = _FakeTransport({});
      final result = await McpRepository(t).listServers();
      expect(result, isEmpty);
    });

    test('parses a single server row', () async {
      final t = _FakeTransport({
        'servers': [_serverJson(id: 'srv1', name: 'taskai', toolCount: 10)],
      });
      final result = await McpRepository(t).listServers();
      expect(result, hasLength(1));
      expect(result.first.id, 'srv1');
      expect(result.first.name, 'taskai');
      expect(result.first.toolCount, 10);
    });

    test('parses multiple servers', () async {
      final t = _FakeTransport({
        'servers': [
          _serverJson(id: 'a', name: 'A', status: 'connected'),
          _serverJson(id: 'b', name: 'B', status: 'error'),
          _serverJson(id: 'c', name: 'C', status: 'disconnected'),
        ],
      });
      final result = await McpRepository(t).listServers();
      expect(result, hasLength(3));
      expect(result.map((s) => s.id), containsAll(['a', 'b', 'c']));
    });

    test('correctly maps status, transport, and toolCount per row', () async {
      final t = _FakeTransport({
        'servers': [
          _serverJson(
            id: 'x',
            transport: 'sse',
            status: 'connected',
            toolCount: 3,
            url: 'https://host/mcp',
          ),
        ],
      });
      final result = await McpRepository(t).listServers();
      final s = result.first;
      expect(s.transport, 'sse');
      expect(s.status, 'connected');
      expect(s.toolCount, 3);
      expect(s.url, 'https://host/mcp');
      expect(s.isConnected, isTrue);
    });

    test('propagates transport errors to the caller', () async {
      final t = _ThrowingTransport(Exception('network down'));
      await expectLater(
        McpRepository(t).listServers(),
        throwsA(isA<Exception>()),
      );
    });

    test('handles favorite field correctly', () async {
      final t = _FakeTransport({
        'servers': [
          _serverJson(id: 'fav', favorite: true),
          _serverJson(id: 'nofav', favorite: false),
        ],
      });
      final result = await McpRepository(t).listServers();
      expect(result.firstWhere((s) => s.id == 'fav').favorite, isTrue);
      expect(result.firstWhere((s) => s.id == 'nofav').favorite, isFalse);
    });
  });
}
