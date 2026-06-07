import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../ui/ui.dart';

// ── Power-tool entry descriptor ───────────────────────────────────────────────

class _PowerTool {
  final String key;
  final String label;
  final String description;
  final IconData icon;

  const _PowerTool({
    required this.key,
    required this.label,
    required this.description,
    required this.icon,
  });

  String get route => '/more/$key';
}

const _kTools = <_PowerTool>[
  _PowerTool(
    key: 'skills',
    label: 'Skills',
    description: 'Browse and manage agent skills',
    icon: Icons.auto_awesome_outlined,
  ),
  _PowerTool(
    key: 'specialists',
    label: 'Specialists',
    description: 'Declarative agent specialists',
    icon: Icons.smart_toy_outlined,
  ),
  _PowerTool(
    key: 'vault',
    label: 'Vault',
    description: 'Encrypted credential storage',
    icon: Icons.lock_outline,
  ),
  _PowerTool(
    key: 'memory',
    label: 'Memory',
    description: 'Agent memory and personal facts',
    icon: Icons.psychology_outlined,
  ),
  _PowerTool(
    key: 'jobs',
    label: 'Jobs',
    description: 'Gig pipeline and job matching',
    icon: Icons.work_outline,
  ),
  _PowerTool(
    key: 'watchers',
    label: 'Watchers',
    description: 'Live site monitors and alerts',
    icon: Icons.visibility_outlined,
  ),
  _PowerTool(
    key: 'mcp',
    label: 'MCP',
    description: 'Connected MCP servers and tools',
    icon: Icons.device_hub_outlined,
  ),
  _PowerTool(
    key: 'audit',
    label: 'Audit',
    description: 'Permission log and skill activity',
    icon: Icons.policy_outlined,
  ),
];

// ── MoreHubScreen ─────────────────────────────────────────────────────────────

/// The Power-tools hub — a navigable grid of 7 advanced surfaces (Skills, Vault,
/// Memory, Jobs, Watchers, MCP, Audit). Each tile pushes full-screen over the
/// bottom nav. Stub bodies are filled by dedicated fanned-out agents.
class MoreHubScreen extends ConsumerWidget {
  const MoreHubScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return LzScaffold(
      appBar: const LzAppBar(title: 'Power tools', large: true),
      body: ListView.separated(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.lg,
        ),
        itemCount: _kTools.length,
        separatorBuilder: (context, index) => AppSpacing.vGap(AppSpacing.sm),
        itemBuilder: (context, index) => _ToolTile(tool: _kTools[index]),
      ),
    );
  }
}

// ── _ToolTile ─────────────────────────────────────────────────────────────────

class _ToolTile extends StatelessWidget {
  const _ToolTile({required this.tool});

  final _PowerTool tool;

  @override
  Widget build(BuildContext context) {
    return LzCard(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      onTap: () => context.push(tool.route),
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: AppColors.bgSurfaceElevated,
              borderRadius: AppRadii.rMd,
              border: Border.all(color: AppColors.borderSubtle),
            ),
            child: Icon(tool.icon, size: 22, color: AppColors.accent),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(tool.label, style: AppText.label),
                const SizedBox(height: 2),
                Text(
                  tool.description,
                  style: AppText.caption.copyWith(color: AppColors.textMuted),
                ),
              ],
            ),
          ),
          const Icon(
            Icons.chevron_right,
            size: 18,
            color: AppColors.textMuted,
          ),
        ],
      ),
    );
  }
}
