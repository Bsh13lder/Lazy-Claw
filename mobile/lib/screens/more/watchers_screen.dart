import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../providers/watchers_provider.dart';
import '../../repositories/watchers_repository.dart';
import '../../ui/ui.dart';

/// Watchers power surface — live URL monitors with status, interval, and
/// last-check info. Mirrors the web Watchers page at `GET /api/watchers`.
class WatchersScreen extends ConsumerStatefulWidget {
  const WatchersScreen({super.key});

  @override
  ConsumerState<WatchersScreen> createState() => _WatchersScreenState();
}

class _WatchersScreenState extends ConsumerState<WatchersScreen> {
  @override
  void initState() {
    super.initState();
    // Kick the initial load after the first frame so the provider is
    // mounted before we write to it.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(watchersProvider.notifier).load();
    });
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(watchersProvider);

    return LzScaffold(
      appBar: const LzAppBar(title: 'Watchers'),
      body: _buildBody(state),
    );
  }

  Widget _buildBody(WatchersState state) {
    if (state.isLoading) {
      return LzSkeleton.list(
        count: 4,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
      );
    }

    if (state.error != null) {
      return LzEmptyState(
        icon: Icons.cloud_off_outlined,
        title: 'Could not load watchers',
        hint: state.error,
        actionLabel: 'Retry',
        actionIcon: Icons.refresh,
        onAction: () {
          ref.read(watchersProvider.notifier).clearError();
          ref.read(watchersProvider.notifier).load();
        },
      );
    }

    if (state.watchers.isEmpty) {
      return const LzEmptyState(
        icon: Icons.visibility_outlined,
        title: 'No watchers yet',
        hint: 'Set up URL monitors from the desktop app or via the agent to'
            ' track site changes and get alerted when something happens.',
      );
    }

    return LzRefresh(
      onRefresh: () => ref.read(watchersProvider.notifier).refresh(),
      child: ListView.separated(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
        itemCount: state.watchers.length,
        separatorBuilder: (context, index) => const SizedBox(height: AppSpacing.md),
        itemBuilder: (context, index) {
          return _WatcherCard(watcher: state.watchers[index]);
        },
      ),
    );
  }
}

// ── Watcher card ──────────────────────────────────────────────────────────────

class _WatcherCard extends StatelessWidget {
  const _WatcherCard({required this.watcher});

  final Watcher watcher;

  @override
  Widget build(BuildContext context) {
    final dot = _statusDot(watcher.status);
    final intervalLabel = _intervalLabel(watcher.checkInterval);
    final lastCheckLabel = _relativeTime(watcher.lastCheck ?? watcher.lastRun);

    return LzCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── Header row: name + status dot ───────────────────────────────
          Row(
            children: [
              dot,
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Text(
                  watcher.name,
                  style: AppText.label.copyWith(color: AppColors.textPrimary),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              _StatusPill(status: watcher.status),
            ],
          ),

          // ── URL ─────────────────────────────────────────────────────────
          if (watcher.url != null) ...[
            const SizedBox(height: AppSpacing.xs),
            Text(
              watcher.url!,
              style: AppText.caption.copyWith(color: AppColors.textMuted),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ],

          const SizedBox(height: AppSpacing.md),

          // ── Meta row: interval · last check ─────────────────────────────
          Row(
            children: [
              if (intervalLabel != null) ...[
                const Icon(
                  Icons.timer_outlined,
                  size: 12,
                  color: AppColors.textMuted,
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  intervalLabel,
                  style: AppText.caption,
                ),
                const SizedBox(width: AppSpacing.lg),
              ],
              if (lastCheckLabel != null) ...[
                const Icon(
                  Icons.update,
                  size: 12,
                  color: AppColors.textMuted,
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  lastCheckLabel,
                  style: AppText.caption,
                ),
              ],
            ],
          ),

          // ── Stats row ───────────────────────────────────────────────────
          if (watcher.checkCount > 0 || watcher.triggerCount > 0) ...[
            const SizedBox(height: AppSpacing.sm),
            Row(
              children: [
                _StatChip(
                  label: '${watcher.checkCount} checks',
                  color: AppColors.textSecondary,
                ),
                if (watcher.triggerCount > 0) ...[
                  const SizedBox(width: AppSpacing.sm),
                  _StatChip(
                    label: '${watcher.triggerCount} triggered',
                    color: AppColors.accent,
                  ),
                ],
                if (watcher.errorCount > 0) ...[
                  const SizedBox(width: AppSpacing.sm),
                  _StatChip(
                    label: '${watcher.errorCount} errors',
                    color: AppColors.error,
                  ),
                ],
              ],
            ),
          ],

          // ── Last error ──────────────────────────────────────────────────
          if (watcher.lastError != null) ...[
            const SizedBox(height: AppSpacing.sm),
            Container(
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.sm,
                vertical: AppSpacing.xs,
              ),
              decoration: BoxDecoration(
                color: AppColors.error.withValues(alpha: 0.08),
                borderRadius: AppRadii.rSm,
                border: Border.all(
                  color: AppColors.error.withValues(alpha: 0.2),
                ),
              ),
              child: Text(
                watcher.lastError!,
                style: AppText.caption.copyWith(color: AppColors.error),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ],
      ),
    );
  }

  // ── helpers ──────────────────────────────────────────────────────────────

  LzStatusDot _statusDot(String status) {
    switch (status.toLowerCase()) {
      case 'active':
      case 'running':
        return const LzStatusDot.success(size: 9, glow: true);
      case 'paused':
        return const LzStatusDot.warn(size: 9);
      case 'error':
      case 'failed':
        return const LzStatusDot.error(size: 9);
      default:
        return const LzStatusDot.muted(size: 9);
    }
  }

  /// Formats check_interval (in seconds) to a human-readable string.
  String? _intervalLabel(int? seconds) {
    if (seconds == null) return null;
    if (seconds < 60) return 'every ${seconds}s';
    final minutes = seconds ~/ 60;
    if (minutes < 60) return 'every ${minutes}m';
    final hours = minutes ~/ 60;
    final rem = minutes % 60;
    if (rem == 0) return 'every ${hours}h';
    return 'every ${hours}h ${rem}m';
  }

  /// Returns a relative-time string for an ISO-8601 timestamp.
  String? _relativeTime(String? iso) {
    if (iso == null) return null;
    final dt = DateTime.tryParse(iso);
    if (dt == null) return null;
    final diff = DateTime.now().toUtc().difference(dt.toUtc());
    if (diff.isNegative) return 'just now';
    if (diff.inSeconds < 60) return '${diff.inSeconds}s ago';
    if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
    if (diff.inHours < 24) return '${diff.inHours}h ago';
    return '${diff.inDays}d ago';
  }
}

// ── Small status pill ─────────────────────────────────────────────────────────

class _StatusPill extends StatelessWidget {
  const _StatusPill({required this.status});

  final String status;

  @override
  Widget build(BuildContext context) {
    final (label, color) = _resolve(status);
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.sm,
        vertical: 2,
      ),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: AppRadii.rPill,
        border: Border.all(color: color.withValues(alpha: 0.25)),
      ),
      child: Text(
        label,
        style: AppText.caption.copyWith(
          color: color,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.4,
          fontSize: 10,
        ),
      ),
    );
  }

  (String, Color) _resolve(String status) {
    switch (status.toLowerCase()) {
      case 'active':
      case 'running':
        return ('ACTIVE', AppColors.success);
      case 'paused':
        return ('PAUSED', AppColors.warn);
      case 'error':
      case 'failed':
        return ('ERROR', AppColors.error);
      case 'one_shot':
        return ('ONE-SHOT', AppColors.info);
      default:
        return (status.toUpperCase(), AppColors.textMuted);
    }
  }
}

// ── Tiny stat chip ────────────────────────────────────────────────────────────

class _StatChip extends StatelessWidget {
  const _StatChip({required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Text(
      label,
      style: AppText.caption.copyWith(color: color, fontSize: 10.5),
    );
  }
}
