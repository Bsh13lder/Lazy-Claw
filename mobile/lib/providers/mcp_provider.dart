import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../repositories/mcp_repository.dart';
import 'auth_provider.dart';

// ── Infrastructure ────────────────────────────────────────────────────────────

final mcpRepositoryProvider = Provider<McpRepository>((ref) {
  return McpRepository(DioMcpTransport(ref.watch(apiClientProvider)));
});

// ── State ─────────────────────────────────────────────────────────────────────

class McpState {
  final List<McpServer> servers;
  final bool isLoading;
  final String? error;

  const McpState({
    this.servers = const [],
    this.isLoading = false,
    this.error,
  });

  McpState copyWith({
    List<McpServer>? servers,
    bool? isLoading,
    String? error,
    bool clearError = false,
  }) =>
      McpState(
        servers: servers ?? this.servers,
        isLoading: isLoading ?? this.isLoading,
        error: clearError ? null : (error ?? this.error),
      );

  int get connectedCount =>
      servers.where((s) => s.isConnected).length;

  int get totalToolCount =>
      servers.fold(0, (sum, s) => sum + s.toolCount);
}

// ── Notifier ──────────────────────────────────────────────────────────────────

/// Network-first (read-only) notifier for MCP servers.
///
/// MCP server registrations are managed server-side; the mobile app is a
/// read-only observer. No offline cache — connectivity is expected for this
/// power-surface screen.
class McpNotifier extends StateNotifier<McpState> {
  final McpRepository _repo;
  McpNotifier(this._repo) : super(const McpState());

  /// Initial load — sets [isLoading] while the request is in flight.
  Future<void> load() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final servers = await _repo.listServers();
      state = McpState(servers: servers, isLoading: false);
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
    }
  }

  /// Pull-to-refresh — same as [load] but called explicitly by the user.
  Future<void> refresh() => load();

  void clearError() => state = state.copyWith(clearError: true);
}

// ── Provider ──────────────────────────────────────────────────────────────────

final mcpProvider = StateNotifierProvider<McpNotifier, McpState>((ref) {
  return McpNotifier(ref.watch(mcpRepositoryProvider));
});
