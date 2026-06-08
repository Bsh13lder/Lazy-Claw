import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../providers/mcp_provider.dart';
import '../../repositories/mcp_repository.dart';
import '../../ui/ui.dart';

/// MCP power surface — lists all registered MCP servers with their transport
/// type, connection status, and tool count.
///
/// Route: `/more/mcp`
/// API:   `GET /api/mcp/servers` → `{ servers: [McpServer] }`
class McpScreen extends ConsumerStatefulWidget {
  const McpScreen({super.key});

  @override
  ConsumerState<McpScreen> createState() => _McpScreenState();
}

class _McpScreenState extends ConsumerState<McpScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(mcpProvider.notifier).load();
    });
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(mcpProvider);

    return LzScaffold(
      appBar: LzAppBar(
        title: 'MCP servers',
        actions: [
          if (!state.isLoading)
            LzIconButton(
              icon: Icons.refresh_outlined,
              tooltip: 'Refresh',
              onPressed: () => ref.read(mcpProvider.notifier).refresh(),
            ),
        ],
      ),
      body: _McpBody(
        state: state,
        onRetry: () => ref.read(mcpProvider.notifier).refresh(),
        onRefresh: () => ref.read(mcpProvider.notifier).refresh(),
      ),
    );
  }
}

// ── Body ──────────────────────────────────────────────────────────────────────

class _McpBody extends StatelessWidget {
  const _McpBody({
    required this.state,
    required this.onRetry,
    required this.onRefresh,
  });

  final McpState state;
  final VoidCallback onRetry;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    if (state.isLoading) {
      return LzSkeleton.list(
        count: 5,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
      );
    }

    if (state.error != null) {
      return LzEmptyState(
        icon: Icons.cloud_off_outlined,
        title: 'Could not load servers',
        hint: state.error,
        actionLabel: 'Retry',
        actionIcon: Icons.refresh_outlined,
        onAction: onRetry,
      );
    }

    if (state.servers.isEmpty) {
      return const LzEmptyState(
        icon: Icons.device_hub_outlined,
        title: 'No MCP servers registered',
        hint: 'Add MCP servers from the web dashboard to see them here.',
      );
    }

    return LzRefresh(
      onRefresh: onRefresh,
      child: _ServerList(servers: state.servers),
    );
  }
}

// ── Server list ───────────────────────────────────────────────────────────────

class _ServerList extends StatelessWidget {
  const _ServerList({required this.servers});

  final List<McpServer> servers;

  @override
  Widget build(BuildContext context) {
    final connectedCount = servers.where((s) => s.isConnected).length;
    final toolCount = servers.fold(0, (sum, s) => sum + s.toolCount);

    return CustomScrollView(
      slivers: [
        SliverPadding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.lg,
            vertical: AppSpacing.md,
          ),
          sliver: SliverList.separated(
            itemCount: servers.length,
            separatorBuilder: (ctx, idx) =>
                const SizedBox(height: AppSpacing.md),
            itemBuilder: (_, i) => _ServerCard(server: servers[i]),
          ),
        ),
        SliverPadding(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.lg,
            0,
            AppSpacing.lg,
            AppSpacing.xl,
          ),
          sliver: SliverToBoxAdapter(
            child: Text(
              '$connectedCount/${servers.length} connected · $toolCount tools',
              textAlign: TextAlign.center,
              style: AppText.caption.copyWith(color: AppColors.textMuted),
            ),
          ),
        ),
      ],
    );
  }
}

// ── Server card ───────────────────────────────────────────────────────────────

class _ServerCard extends StatelessWidget {
  const _ServerCard({required this.server});

  final McpServer server;

  @override
  Widget build(BuildContext context) {
    return LzCard(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Connection-status dot (glows when live)
          Padding(
            padding: const EdgeInsets.only(top: 3),
            child: LzStatusDot(
              color: _dotColor(server.status),
              size: 9,
              glow: server.isConnected,
            ),
          ),
          const SizedBox(width: AppSpacing.md),
          // Name + chips + URL/command
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(server.name, style: AppText.title),
                const SizedBox(height: AppSpacing.xs + 2),
                Wrap(
                  spacing: AppSpacing.sm,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    LzChip(
                      label: _transportLabel(server.transport),
                      dense: true,
                      color: _transportColor(server.transport),
                    ),
                    Text(
                      server.status,
                      style: AppText.caption.copyWith(
                        color: _dotColor(server.status),
                      ),
                    ),
                  ],
                ),
                if (server.url != null) ...[
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    server.url!,
                    style: AppText.caption
                        .copyWith(color: AppColors.textMuted),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ] else if (server.command != null) ...[
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    server.command!,
                    style: AppText.caption.copyWith(
                      color: AppColors.textMuted,
                      fontFamily: 'monospace',
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(width: AppSpacing.md),
          // Tool count
          Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                '${server.toolCount}',
                style: AppText.title.copyWith(color: AppColors.accent),
              ),
              Text(
                server.toolCount == 1 ? 'tool' : 'tools',
                style: AppText.caption,
              ),
            ],
          ),
        ],
      ),
    );
  }

  static Color _dotColor(String status) {
    switch (status) {
      case 'connected':
        return AppColors.success;
      case 'connecting':
        return AppColors.warn;
      case 'error':
        return AppColors.error;
      default:
        return AppColors.textMuted;
    }
  }

  static String _transportLabel(String transport) {
    switch (transport) {
      case 'sse':
        return 'SSE';
      case 'streamable_http':
        return 'HTTP';
      default:
        return 'stdio';
    }
  }

  static Color _transportColor(String transport) {
    switch (transport) {
      case 'sse':
        return AppColors.info;
      case 'streamable_http':
        return AppColors.accent;
      default:
        return AppColors.textSecondary;
    }
  }
}
