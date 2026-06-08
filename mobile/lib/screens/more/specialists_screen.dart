import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/specialist.dart';
import '../../providers/specialists_provider.dart';
import '../../ui/ui.dart';

/// Specialists power surface — browse declarative agent specialists.
///
/// Route: `/more/specialists`
///
/// API contract (ADR-0005):
///   GET    /api/specialists           → { ok, specialists: [Specialist] }
///   DELETE /api/specialists/{name}    → { ok: true }   (custom only)
///
/// Builtins are read-only (fork/edit on the web dashboard); custom specialists
/// can be viewed and deleted here. Full create/edit is deferred to web for v1.
class SpecialistsScreen extends ConsumerStatefulWidget {
  const SpecialistsScreen({super.key});

  @override
  ConsumerState<SpecialistsScreen> createState() => _SpecialistsScreenState();
}

class _SpecialistsScreenState extends ConsumerState<SpecialistsScreen> {
  final _searchController = TextEditingController();

  @override
  void initState() {
    super.initState();
    // Defer so the provider is ready before the first read.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(specialistsProvider.notifier).load();
    });
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(specialistsProvider);

    return LzScaffold(
      appBar: LzAppBar(
        title: 'Specialists',
        actions: [
          if (!state.isLoading)
            LzIconButton(
              icon: Icons.refresh_outlined,
              tooltip: 'Refresh',
              onPressed: () => ref.read(specialistsProvider.notifier).refresh(),
            ),
        ],
      ),
      body: Column(
        children: [
          _SearchBar(controller: _searchController),
          if (state.error != null) _ErrorBanner(message: state.error!),
          Expanded(child: _Body(state: state)),
        ],
      ),
    );
  }
}

// ── Search bar ───────────────────────────────────────────────────────────────

class _SearchBar extends ConsumerWidget {
  const _SearchBar({required this.controller});
  final TextEditingController controller;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      child: LzSearchField(
        controller: controller,
        hint: 'Filter specialists…',
        onChanged: (q) => ref.read(specialistsProvider.notifier).search(q),
      ),
    );
  }
}

// ── Error banner ─────────────────────────────────────────────────────────────

class _ErrorBanner extends ConsumerWidget {
  const _ErrorBanner({required this.message});
  final String message;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return LzBanner.error(
      message: message,
      safeAreaTop: false,
      action: IconButton(
        icon: const Icon(Icons.close, size: 16),
        onPressed: () => ref.read(specialistsProvider.notifier).clearError(),
        color: AppColors.error,
        padding: EdgeInsets.zero,
        constraints: const BoxConstraints(),
      ),
    );
  }
}

// ── Body (skeleton / empty / list) ───────────────────────────────────────────

class _Body extends ConsumerWidget {
  const _Body({required this.state});
  final SpecialistsState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (state.isLoading) {
      return LzSkeleton.list(count: 6);
    }

    final builtins = state.builtins;
    final customs = state.customs;

    if (builtins.isEmpty && customs.isEmpty) {
      return LzRefresh(
        onRefresh: () => ref.read(specialistsProvider.notifier).refresh(),
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: const [
            SizedBox(height: AppSpacing.xxxl),
            LzEmptyState(
              icon: Icons.smart_toy_outlined,
              title: 'No specialists found',
              hint: 'Try a different search term or pull down to refresh.',
            ),
          ],
        ),
      );
    }

    return LzRefresh(
      onRefresh: () => ref.read(specialistsProvider.notifier).refresh(),
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.only(
          left: AppSpacing.lg,
          right: AppSpacing.lg,
          bottom: AppSpacing.xxl,
        ),
        children: [
          _CountHeader(
            total: state.specialists.length,
            filtered: state.filtered.length,
          ),
          if (builtins.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.lg),
            _SpecialistSection(title: 'Built-in', specialists: builtins),
          ],
          if (customs.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.lg),
            _SpecialistSection(title: 'Custom', specialists: customs),
          ],
        ],
      ),
    );
  }
}

// ── Count header ─────────────────────────────────────────────────────────────

class _CountHeader extends StatelessWidget {
  const _CountHeader({required this.total, required this.filtered});
  final int total;
  final int filtered;

  @override
  Widget build(BuildContext context) {
    final label =
        filtered == total ? '$total registered' : '$filtered of $total';
    return Padding(
      padding: const EdgeInsets.only(top: AppSpacing.sm),
      child: Text(
        label,
        style: AppText.caption.copyWith(color: AppColors.textMuted),
      ),
    );
  }
}

// ── Specialist section ───────────────────────────────────────────────────────

class _SpecialistSection extends StatelessWidget {
  const _SpecialistSection({required this.title, required this.specialists});

  final String title;
  final List<Specialist> specialists;

  @override
  Widget build(BuildContext context) {
    return LzSection(
      title: '$title (${specialists.length})',
      child: LzCard(
        padding: EdgeInsets.zero,
        child: Column(
          children: [
            for (int i = 0; i < specialists.length; i++) ...[
              _SpecialistTile(specialist: specialists[i]),
              if (i < specialists.length - 1)
                const Divider(
                  height: 1,
                  thickness: 1,
                  color: AppColors.borderSubtle,
                  indent: AppSpacing.lg,
                  endIndent: AppSpacing.lg,
                ),
            ],
          ],
        ),
      ),
    );
  }
}

// ── Specialist tile ──────────────────────────────────────────────────────────

class _SpecialistTile extends StatelessWidget {
  const _SpecialistTile({required this.specialist});
  final Specialist specialist;

  @override
  Widget build(BuildContext context) {
    final toolCount = specialist.tools.length;
    final subtitleParts = <String>[
      '$toolCount ${toolCount == 1 ? 'tool' : 'tools'}',
      if (specialist.model != null && specialist.model!.isNotEmpty)
        specialist.model!,
    ];

    return LzListTile(
      leading: const Icon(
        Icons.smart_toy_outlined,
        size: 22,
        color: AppColors.accent,
      ),
      title: specialist.displayName,
      subtitle: subtitleParts.join(' · '),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          LzChip(
            label: specialist.isBuiltin ? 'Built-in' : 'Custom',
            dense: true,
            color: specialist.isBuiltin ? AppColors.info : AppColors.accent,
            selected: !specialist.isBuiltin,
          ),
          const SizedBox(width: AppSpacing.sm),
          const Icon(Icons.chevron_right, size: 18, color: AppColors.textMuted),
        ],
      ),
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      onTap: () => LzBottomSheet.show<void>(
        context,
        title: specialist.displayName,
        builder: (_) => _SpecialistDetailSheet(specialist: specialist),
      ),
    );
  }
}

// ── Detail sheet ─────────────────────────────────────────────────────────────

/// Read-only detail view for a specialist. Shows the name, model, scraper flag,
/// tool list, and full system prompt. Custom specialists get a Delete action;
/// builtins show an "edit on web" hint instead.
class _SpecialistDetailSheet extends ConsumerWidget {
  const _SpecialistDetailSheet({required this.specialist});
  final Specialist specialist;

  Future<void> _delete(BuildContext context, WidgetRef ref) async {
    final confirmed = await LzConfirm.show(
      context,
      title: 'Delete specialist?',
      message:
          'Permanently delete "${specialist.displayName}". This cannot be undone.',
      confirmLabel: 'Delete',
      cancelLabel: 'Cancel',
      danger: true,
    );
    if (!confirmed || !context.mounted) return;

    final err = await ref
        .read(specialistsProvider.notifier)
        .deleteSpecialist(specialist.name);
    if (!context.mounted) return;

    if (err == null) {
      Navigator.of(context).pop();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Deleted "${specialist.displayName}"')),
      );
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Delete failed: $err')),
      );
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final maxHeight = MediaQuery.of(context).size.height * 0.7;
    final hasModel =
        specialist.model != null && specialist.model!.isNotEmpty;

    return ConstrainedBox(
      constraints: BoxConstraints(maxHeight: maxHeight),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg,
          0,
          AppSpacing.lg,
          AppSpacing.lg,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            // Identity row: builtin/custom badge + raw name.
            Row(
              children: [
                LzChip(
                  label: specialist.isBuiltin ? 'Built-in' : 'Custom',
                  dense: true,
                  color:
                      specialist.isBuiltin ? AppColors.info : AppColors.accent,
                  selected: !specialist.isBuiltin,
                ),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: Text(
                    specialist.name,
                    style: AppText.caption.copyWith(
                      color: AppColors.textMuted,
                      fontFamily: 'monospace',
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.lg),

            // Model + scraper meta.
            _MetaRow(
              icon: Icons.memory_outlined,
              label: 'Model',
              value: hasModel ? specialist.model! : 'Mode default',
            ),
            const SizedBox(height: AppSpacing.sm),
            _MetaRow(
              icon: Icons.travel_explore_outlined,
              label: 'Scraper',
              value: specialist.includeScraper ? 'Included' : 'Not included',
              valueColor: specialist.includeScraper
                  ? AppColors.success
                  : AppColors.textMuted,
            ),
            const SizedBox(height: AppSpacing.lg),

            // Tools.
            Text(
              'TOOLS (${specialist.tools.length})',
              style: AppText.caption.copyWith(
                color: AppColors.textMuted,
                letterSpacing: 0.8,
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
            if (specialist.tools.isEmpty)
              Text(
                'No tools assigned.',
                style: AppText.caption.copyWith(color: AppColors.textMuted),
              )
            else
              Wrap(
                spacing: AppSpacing.sm,
                runSpacing: AppSpacing.sm,
                children: [
                  for (final tool in specialist.tools)
                    LzChip(label: tool, dense: true),
                ],
              ),
            const SizedBox(height: AppSpacing.lg),

            // System prompt.
            Text(
              'SYSTEM PROMPT',
              style: AppText.caption.copyWith(
                color: AppColors.textMuted,
                letterSpacing: 0.8,
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
            LzCard(
              color: AppColors.bgSurfaceElevated,
              child: SelectableText(
                specialist.systemPrompt.isNotEmpty
                    ? specialist.systemPrompt
                    : '(empty)',
                style: AppText.caption.copyWith(
                  color: AppColors.textSecondary,
                  height: 1.5,
                ),
              ),
            ),
            const SizedBox(height: AppSpacing.lg),

            // Action footer.
            if (specialist.isBuiltin)
              Text(
                'Built-in specialists are read-only. Fork or edit them on the '
                'web dashboard.',
                style: AppText.caption.copyWith(color: AppColors.textMuted),
              )
            else ...[
              Text(
                'Edit custom specialists on the web dashboard.',
                style: AppText.caption.copyWith(color: AppColors.textMuted),
              ),
              const SizedBox(height: AppSpacing.md),
              LzButton.danger(
                label: 'Delete specialist',
                icon: Icons.delete_outline,
                expand: true,
                onPressed: () => _delete(context, ref),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

// ── Meta row ─────────────────────────────────────────────────────────────────

class _MetaRow extends StatelessWidget {
  const _MetaRow({
    required this.icon,
    required this.label,
    required this.value,
    this.valueColor,
  });

  final IconData icon;
  final String label;
  final String value;
  final Color? valueColor;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(icon, size: 16, color: AppColors.textMuted),
        const SizedBox(width: AppSpacing.sm),
        Text(
          '$label: ',
          style: AppText.caption.copyWith(
            color: AppColors.textMuted,
            fontWeight: FontWeight.w600,
          ),
        ),
        Expanded(
          child: Text(
            value,
            style: AppText.caption.copyWith(
              color: valueColor ?? AppColors.textSecondary,
            ),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}
