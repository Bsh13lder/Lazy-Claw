import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../providers/audit_provider.dart';
import '../../repositories/audit_repository.dart';
import '../../ui/ui.dart';

// ── Status semantics ───────────────────────────────────────────────────────

const _kFilters = [
  'execute',
  'denied',
  'approved',
  'expired',
];

Color _statusColor(String action) {
  switch (action) {
    case 'execute':
      return AppColors.success;
    case 'denied':
      return AppColors.error;
    case 'approved':
      return AppColors.info;
    default:
      return AppColors.textMuted;
  }
}

String _statusLabel(String action) {
  switch (action) {
    case 'execute':
      return 'executed';
    case 'denied':
      return 'denied';
    case 'approved':
      return 'approved';
    case 'expired':
      return 'expired';
    default:
      return action;
  }
}

// ── Relative time ──────────────────────────────────────────────────────────

String _relativeTime(String iso) {
  if (iso.isEmpty) return '';
  final dt = DateTime.tryParse(iso)?.toLocal();
  if (dt == null) return iso;
  final diff = DateTime.now().difference(dt);
  if (diff.inSeconds < 60) return 'just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  if (diff.inDays < 7) return '${diff.inDays}d ago';
  return '${dt.day}/${dt.month}/${dt.year}';
}

// ── Screen ─────────────────────────────────────────────────────────────────

class AuditScreen extends ConsumerStatefulWidget {
  const AuditScreen({super.key});

  @override
  ConsumerState<AuditScreen> createState() => _AuditScreenState();
}

class _AuditScreenState extends ConsumerState<AuditScreen> {
  @override
  void initState() {
    super.initState();
    // Kick off the load after the first frame so the provider is fully mounted.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(auditProvider.notifier).load();
    });
  }

  Future<void> _onRefresh() => ref.read(auditProvider.notifier).refresh();

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(auditProvider);

    return LzScaffold(
      appBar: const LzAppBar(title: 'Audit log'),
      body: Column(
        children: [
          // ── Filter row ───────────────────────────────────────────────────
          _FilterRow(
            activeFilter: state.activeFilter,
            onFilter: (f) => ref.read(auditProvider.notifier).setFilter(f),
          ),

          // ── Content ──────────────────────────────────────────────────────
          Expanded(
            child: LzRefresh(
              onRefresh: _onRefresh,
              child: _buildBody(state),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildBody(AuditState state) {
    if (state.isLoading) {
      return LzSkeleton.list(
        count: 6,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
      );
    }

    if (state.error != null) {
      return LzEmptyState(
        icon: Icons.cloud_off_outlined,
        title: 'Could not load audit log',
        hint: state.error,
        actionLabel: 'Retry',
        actionIcon: Icons.refresh,
        onAction: _onRefresh,
      );
    }

    final entries = state.filteredEntries;

    if (entries.isEmpty) {
      return LzEmptyState(
        icon: Icons.policy_outlined,
        title: state.activeFilter != null
            ? 'No "${state.activeFilter}" entries'
            : 'No audit entries yet',
        hint: state.activeFilter != null
            ? 'Remove the filter to see all activity.'
            : 'Skill executions, approvals, and denied requests appear here.',
      );
    }

    return ListView.separated(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      itemCount: entries.length,
      separatorBuilder: (context, index) =>
          const SizedBox(height: AppSpacing.sm),
      itemBuilder: (_, i) => _AuditTile(entry: entries[i]),
    );
  }
}

// ── Filter row ─────────────────────────────────────────────────────────────

class _FilterRow extends StatelessWidget {
  const _FilterRow({
    required this.activeFilter,
    required this.onFilter,
  });

  final String? activeFilter;
  final void Function(String) onFilter;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        children: _kFilters.map((f) {
          final selected = activeFilter == f;
          return Padding(
            padding: const EdgeInsets.only(right: AppSpacing.sm),
            child: LzChip(
              label: _statusLabel(f),
              selected: selected,
              color: _statusColor(f),
              onTap: () => onFilter(f),
            ),
          );
        }).toList(),
      ),
    );
  }
}

// ── Timeline tile ──────────────────────────────────────────────────────────

class _AuditTile extends StatelessWidget {
  const _AuditTile({required this.entry});

  final AuditEntry entry;

  @override
  Widget build(BuildContext context) {
    final color = _statusColor(entry.action);
    final label = _statusLabel(entry.action);
    final time = _relativeTime(entry.createdAt);

    return LzCard(
      padding: const EdgeInsets.all(AppSpacing.md),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Timeline dot
          Padding(
            padding: const EdgeInsets.only(top: 3),
            child: LzStatusDot(color: color, size: 9, glow: entry.action == 'execute'),
          ),
          const SizedBox(width: AppSpacing.md),

          // Body
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Top row: action name + status badge + time
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        entry.skillName ?? entry.action,
                        style: AppText.label
                            .copyWith(color: AppColors.textPrimary),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    LzBadge(label: label, color: color),
                    const SizedBox(width: AppSpacing.sm),
                    Text(
                      time,
                      style:
                          AppText.caption.copyWith(color: AppColors.textMuted),
                    ),
                  ],
                ),

                // Summary
                if (entry.resultSummary != null &&
                    entry.resultSummary!.isNotEmpty) ...[
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    entry.resultSummary!,
                    style:
                        AppText.caption.copyWith(color: AppColors.textSecondary),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],

                // Source tag
                if (entry.source.isNotEmpty) ...[
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    'via ${entry.source}',
                    style: AppText.caption
                        .copyWith(color: AppColors.textMuted, fontSize: 10.5),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}
