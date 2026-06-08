import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../providers/jobs_provider.dart';
import '../../repositories/jobs_repository.dart';
import '../../ui/ui.dart';

// ── Helpers ───────────────────────────────────────────────────────────────

/// Relative-time label for an ISO-8601 string (matches the web Jobs page logic).
String _relativeTime(String iso) {
  final then = DateTime.tryParse(iso);
  if (then == null) return iso;
  final now = DateTime.now();
  final diff = then.difference(now);
  final abs = diff.abs();

  if (abs.inSeconds < 60) return 'just now';
  if (abs.inMinutes < 60) {
    final label = '${abs.inMinutes}m';
    return diff.isNegative ? '$label ago' : 'in $label';
  }
  if (abs.inHours < 24) {
    final label = '${abs.inHours}h';
    return diff.isNegative ? '$label ago' : 'in $label';
  }
  final days = abs.inDays;
  if (days < 30) {
    final label = '${days}d';
    return diff.isNegative ? '$label ago' : 'in $label';
  }
  final months = (days / 30).floor();
  final label = '${months}mo';
  return diff.isNegative ? '$label ago' : 'in $label';
}

// ── Status config ─────────────────────────────────────────────────────────

class _StatusCfg {
  final Color dot;
  final String label;
  final bool glow;
  const _StatusCfg({required this.dot, required this.label, this.glow = false});
}

_StatusCfg _statusCfg(String status) {
  switch (status) {
    case 'active':
      return _StatusCfg(dot: AppColors.success, label: 'Active', glow: true);
    case 'paused':
      return _StatusCfg(dot: AppColors.warn, label: 'Paused');
    case 'completed':
      return _StatusCfg(dot: AppColors.success, label: 'Done');
    case 'failed':
      return _StatusCfg(dot: AppColors.error, label: 'Failed');
    default:
      return _StatusCfg(dot: AppColors.textMuted, label: status);
  }
}

// ── Tab enum ───────────────────────────────────────────────────────────────

enum _Tab { all, recurring, oneOff }

extension _TabExt on _Tab {
  String get label {
    switch (this) {
      case _Tab.all:
        return 'All';
      case _Tab.recurring:
        return 'Recurring';
      case _Tab.oneOff:
        return 'One-off';
    }
  }
}

// ── Tab bar ───────────────────────────────────────────────────────────────

class _TabBar extends StatelessWidget {
  const _TabBar({
    required this.tab,
    required this.counts,
    required this.onChange,
  });

  final _Tab tab;
  final Map<_Tab, int> counts;
  final ValueChanged<_Tab> onChange;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        children: _Tab.values.map((t) {
          final active = tab == t;
          return Padding(
            padding: const EdgeInsets.only(right: AppSpacing.sm),
            child: GestureDetector(
              onTap: () => onChange(t),
              child: AnimatedContainer(
                duration: AppMotion.fast,
                padding: const EdgeInsets.symmetric(
                  horizontal: AppSpacing.md,
                  vertical: AppSpacing.xs,
                ),
                decoration: BoxDecoration(
                  color: active
                      ? AppColors.accent
                      : AppColors.bgSurfaceElevated,
                  borderRadius: AppRadii.rPill,
                  border: Border.all(
                    color: active
                        ? AppColors.accent
                        : AppColors.borderDefault,
                  ),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      t.label,
                      style: AppText.label.copyWith(
                        color: active
                            ? AppColors.onAccent
                            : AppColors.textSecondary,
                        fontSize: 12,
                      ),
                    ),
                    const SizedBox(width: 4),
                    Text(
                      '${counts[t] ?? 0}',
                      style: AppText.caption.copyWith(
                        color: active
                            ? AppColors.onAccent.withValues(alpha: 0.8)
                            : AppColors.textMuted,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }
}

// ── Pill label ────────────────────────────────────────────────────────────

class _Pill extends StatelessWidget {
  const _Pill(this.label, {required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.15),
        borderRadius: AppRadii.rPill,
      ),
      child: Text(
        label,
        style: AppText.caption.copyWith(color: color, fontSize: 10.5),
      ),
    );
  }
}

// ── Metadata chip ─────────────────────────────────────────────────────────

class _MetaChip extends StatelessWidget {
  const _MetaChip({
    required this.icon,
    required this.label,
    this.mono = false,
  });

  final IconData icon;
  final String label;
  final bool mono;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 12, color: AppColors.textMuted),
        const SizedBox(width: 3),
        Text(
          label,
          style: AppText.caption.copyWith(
            color: AppColors.textMuted,
            fontSize: 11,
            fontFamily: mono ? 'monospace' : AppText.fontFamily,
          ),
        ),
      ],
    );
  }
}

// ── Outcome row ───────────────────────────────────────────────────────────

class _OutcomeRow extends StatelessWidget {
  const _OutcomeRow({required this.lastStatus, this.lastError});

  final String lastStatus;
  final String? lastError;

  @override
  Widget build(BuildContext context) {
    if (lastStatus == 'success') {
      return Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 6,
            height: 6,
            decoration: const BoxDecoration(
              color: AppColors.success,
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 4),
          Text(
            'Ran OK',
            style: AppText.caption.copyWith(color: AppColors.textMuted),
          ),
        ],
      );
    }

    if (lastStatus == 'failed') {
      final rawError = lastError ?? 'Unknown error';
      final preview = rawError.replaceAll('\n', ' ');
      return Tooltip(
        message: rawError,
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 6,
              height: 6,
              decoration: const BoxDecoration(
                color: AppColors.error,
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 4),
            Flexible(
              child: Text(
                'Failed: $preview',
                style: AppText.caption.copyWith(color: AppColors.error),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      );
    }

    return const SizedBox.shrink();
  }
}

// ── Toggle button ─────────────────────────────────────────────────────────

class _ToggleButton extends StatelessWidget {
  const _ToggleButton({
    required this.status,
    required this.isToggling,
    required this.onPause,
    required this.onResume,
  });

  final String status;
  final bool isToggling;
  final VoidCallback onPause;
  final VoidCallback onResume;

  @override
  Widget build(BuildContext context) {
    if (isToggling) {
      return const SizedBox(
        width: 32,
        height: 32,
        child: Center(
          child: SizedBox(
            width: 16,
            height: 16,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              color: AppColors.accent,
            ),
          ),
        ),
      );
    }

    if (status == 'active') {
      return _SmallIconButton(
        icon: Icons.pause_rounded,
        tooltip: 'Pause',
        color: AppColors.warn,
        onTap: onPause,
      );
    }

    if (status == 'paused') {
      return _SmallIconButton(
        icon: Icons.play_arrow_rounded,
        tooltip: 'Resume',
        color: AppColors.success,
        onTap: onResume,
      );
    }

    // completed / failed / unknown — no toggle available
    return const SizedBox(width: 32);
  }
}

class _SmallIconButton extends StatelessWidget {
  const _SmallIconButton({
    required this.icon,
    required this.tooltip,
    required this.color,
    required this.onTap,
  });

  final IconData icon;
  final String tooltip;
  final Color color;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: Material(
        color: Colors.transparent,
        borderRadius: AppRadii.rSm,
        child: InkWell(
          onTap: onTap,
          borderRadius: AppRadii.rSm,
          child: Padding(
            padding: const EdgeInsets.all(AppSpacing.xs),
            child: Icon(icon, size: 22, color: color),
          ),
        ),
      ),
    );
  }
}

// ── Job card ──────────────────────────────────────────────────────────────

class _JobCard extends StatelessWidget {
  const _JobCard({
    required this.job,
    required this.isToggling,
    required this.onPause,
    required this.onResume,
  });

  final Job job;
  final bool isToggling;
  final VoidCallback onPause;
  final VoidCallback onResume;

  String get _typeLabel {
    final t = job.jobType ?? (job.isRecurring ? 'cron' : 'one_off');
    switch (t) {
      case 'cron':
        return '🔁 Recurring';
      case 'one_off':
        return '1× One-off';
      case 'reminder':
        return '🔔 Reminder';
      default:
        return job.isRecurring ? '🔁 Recurring' : '1× One-off';
    }
  }

  @override
  Widget build(BuildContext context) {
    final cfg = _statusCfg(job.status);

    return LzCard(
      padding: const EdgeInsets.all(AppSpacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── Header row ──────────────────────────────────────────────────
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Padding(
                padding: const EdgeInsets.only(top: 3),
                child: LzStatusDot(color: cfg.dot, glow: cfg.glow),
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      job.name,
                      style: AppText.label.copyWith(color: AppColors.textPrimary),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 4),
                    Wrap(
                      spacing: AppSpacing.xs,
                      runSpacing: AppSpacing.xs,
                      children: [
                        _Pill(cfg.label, color: cfg.dot),
                        _Pill(_typeLabel, color: AppColors.accent),
                      ],
                    ),
                  ],
                ),
              ),
              _ToggleButton(
                status: job.status,
                isToggling: isToggling,
                onPause: onPause,
                onResume: onResume,
              ),
            ],
          ),

          // ── Instruction ─────────────────────────────────────────────────
          const SizedBox(height: AppSpacing.sm),
          Text(
            job.instruction,
            style: AppText.body.copyWith(
              color: AppColors.textSecondary,
              fontSize: 13,
            ),
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
          ),

          // ── Metadata strip ──────────────────────────────────────────────
          const SizedBox(height: AppSpacing.sm),
          Wrap(
            spacing: AppSpacing.md,
            runSpacing: 4,
            children: [
              if (job.cronExpression != null)
                _MetaChip(
                  icon: Icons.schedule_outlined,
                  label: job.cronExpression!,
                  mono: true,
                ),
              if (job.lastRun != null)
                _MetaChip(
                  icon: Icons.history_outlined,
                  label: 'Last: ${_relativeTime(job.lastRun!)}',
                ),
              if (job.nextRun != null)
                _MetaChip(
                  icon: Icons.next_plan_outlined,
                  label: 'Next: ${_relativeTime(job.nextRun!)}',
                ),
            ],
          ),

          // ── Last-run outcome ────────────────────────────────────────────
          if (job.lastStatus != null) ...[
            const SizedBox(height: AppSpacing.xs),
            _OutcomeRow(
              lastStatus: job.lastStatus!,
              lastError: job.lastError,
            ),
          ],
        ],
      ),
    );
  }
}

// ── Jobs screen ───────────────────────────────────────────────────────────

/// Jobs power-surface screen — route `/more/jobs`.
///
/// Displays all non-watcher scheduled jobs (cron + one-off) pulled live from
/// `GET /api/jobs`. Supports pause / resume via the matching POST sub-routes.
/// Create / edit / delete are intentionally omitted on mobile v1 — those
/// require a cron-expression editor that belongs on the web UI.
class JobsScreen extends ConsumerStatefulWidget {
  const JobsScreen({super.key});

  @override
  ConsumerState<JobsScreen> createState() => _JobsScreenState();
}

class _JobsScreenState extends ConsumerState<JobsScreen> {
  _Tab _tab = _Tab.all;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(jobsProvider.notifier).load();
    });
  }

  List<Job> _visible(JobsState s) {
    switch (_tab) {
      case _Tab.all:
        return s.jobs;
      case _Tab.recurring:
        return s.recurringJobs;
      case _Tab.oneOff:
        return s.oneOffJobs;
    }
  }

  Map<_Tab, int> _counts(JobsState s) => {
        _Tab.all: s.jobs.length,
        _Tab.recurring: s.recurringJobs.length,
        _Tab.oneOff: s.oneOffJobs.length,
      };

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(jobsProvider);
    final notifier = ref.read(jobsProvider.notifier);

    final visible = _visible(state);
    final counts = _counts(state);

    return LzScaffold(
      appBar: LzAppBar(
        title: 'Jobs',
        actions: [
          if (!state.isLoading)
            IconButton(
              icon: const Icon(Icons.refresh_rounded, size: 20),
              color: AppColors.textSecondary,
              tooltip: 'Refresh',
              onPressed: notifier.refresh,
            ),
        ],
      ),
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── Error banner ─────────────────────────────────────────────────
          if (state.error != null)
            LzBanner.error(
              message: state.error!,
              safeAreaTop: false,
              action: TextButton(
                onPressed: notifier.clearError,
                child: Text(
                  'Dismiss',
                  style: AppText.caption
                      .copyWith(color: AppColors.error, fontWeight: FontWeight.w700),
                ),
              ),
            ),

          // ── Tab bar (only when there are jobs) ────────────────────────
          if (!state.isLoading && state.jobs.isNotEmpty)
            _TabBar(
              tab: _tab,
              counts: counts,
              onChange: (t) => setState(() => _tab = t),
            ),

          // ── Body ────────────────────────────────────────────────────────
          Expanded(
            child: state.isLoading
                ? LzSkeleton.list(
                    count: 4,
                    padding: const EdgeInsets.symmetric(
                      horizontal: AppSpacing.lg,
                      vertical: AppSpacing.md,
                    ),
                  )
                : visible.isEmpty
                    ? LzEmptyState(
                        icon: Icons.schedule_outlined,
                        title: state.jobs.isEmpty
                            ? 'No jobs scheduled'
                            : 'No ${_tab == _Tab.recurring ? "recurring" : "one-off"} jobs',
                        hint: state.jobs.isEmpty
                            ? 'Create cron or one-off jobs on the web to automate tasks.'
                            : 'Switch to "All" to see every job.',
                      )
                    : LzRefresh(
                        onRefresh: notifier.refresh,
                        child: ListView.separated(
                          padding: const EdgeInsets.symmetric(
                            horizontal: AppSpacing.lg,
                            vertical: AppSpacing.md,
                          ),
                          itemCount: visible.length,
                          separatorBuilder: (context, index) =>
                              const SizedBox(height: AppSpacing.sm),
                          itemBuilder: (context, i) {
                            final job = visible[i];
                            return _JobCard(
                              job: job,
                              isToggling: state.togglingId == job.id,
                              onPause: () => notifier.pauseJob(job.id),
                              onResume: () => notifier.resumeJob(job.id),
                            );
                          },
                        ),
                      ),
          ),
        ],
      ),
    );
  }
}
