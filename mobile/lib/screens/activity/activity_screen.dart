import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../providers/activity_provider.dart';
import '../../repositories/activity_repository.dart';
import '../../ui/ui.dart';
import 'activity_detail_sheet.dart';
import 'activity_row.dart';

/// How often the screen silently re-polls `/api/agents/status` while open.
/// Mirrors the web AgentConsole's live polling cadence.
const _kPollInterval = Duration(seconds: 5);

/// "What the agent is doing & recently did" — the mobile mirror of the web
/// Activity surface. Reachable from the chat app-bar ⚡ button and the
/// Power-tools hub.
class ActivityScreen extends ConsumerStatefulWidget {
  const ActivityScreen({super.key});

  @override
  ConsumerState<ActivityScreen> createState() => _ActivityScreenState();
}

class _ActivityScreenState extends ConsumerState<ActivityScreen> {
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(activityProvider.notifier).load();
    });
    _poll = Timer.periodic(
      _kPollInterval,
      (_) => ref.read(activityProvider.notifier).refresh(),
    );
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _onRefresh() => ref.read(activityProvider.notifier).refresh();

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(activityProvider);

    return LzScaffold(
      appBar: const LzAppBar(title: 'Activity'),
      body: LzRefresh(
        onRefresh: _onRefresh,
        child: _buildBody(state),
      ),
    );
  }

  Widget _buildBody(ActivityState state) {
    if (state.isLoading && !state.hasData) {
      return LzSkeleton.list(
        count: 6,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
      );
    }

    if (state.error != null && !state.hasData) {
      return LzEmptyState(
        icon: Icons.cloud_off_outlined,
        title: 'Could not load activity',
        hint: state.error,
        actionLabel: 'Retry',
        actionIcon: Icons.refresh,
        onAction: () => ref.read(activityProvider.notifier).load(),
      );
    }

    final snap = state.snapshot;
    if (snap.isEmpty) {
      return LzEmptyState(
        icon: Icons.bolt_outlined,
        title: 'No agent activity yet',
        hint: 'Running tasks and recently executed work will appear here.',
      );
    }

    // Flatten the two groups into one scroll with section headers.
    final children = <Widget>[];
    if (snap.running.isNotEmpty) {
      children.add(_SectionHeader(
        label: 'Running',
        count: snap.running.length,
        live: true,
      ));
      children.addAll(snap.running.map(_tile));
    }
    if (snap.recent.isNotEmpty) {
      children.add(_SectionHeader(label: 'Recent', count: snap.recent.length));
      children.addAll(snap.recent.map(_tile));
    }

    return ListView(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      children: children,
    );
  }

  Widget _tile(AgentTask t) => Padding(
        padding: const EdgeInsets.only(bottom: AppSpacing.sm),
        child: ActivityRow(
          task: t,
          onTap: () => showActivityDetail(context, t),
        ),
      );
}

// ── Section header ─────────────────────────────────────────────────────────

class _SectionHeader extends StatelessWidget {
  const _SectionHeader({
    required this.label,
    required this.count,
    this.live = false,
  });

  final String label;
  final int count;
  final bool live;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(
        top: AppSpacing.sm,
        bottom: AppSpacing.sm,
      ),
      child: Row(
        children: [
          if (live) ...[
            const LzStatusDot.success(size: 8, glow: true),
            const SizedBox(width: AppSpacing.sm),
          ],
          Text(
            label.toUpperCase(),
            style: AppText.caption.copyWith(
              color: AppColors.textMuted,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.6,
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          Text(
            '$count',
            style: AppText.caption.copyWith(color: AppColors.textMuted),
          ),
        ],
      ),
    );
  }
}
