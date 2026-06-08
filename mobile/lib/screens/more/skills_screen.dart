import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/skill.dart';
import '../../providers/skills_provider.dart';
import '../../ui/ui.dart';

/// Skills power surface — browse, filter, and toggle agent skills by category.
///
/// Route: `/more/skills`
///
/// API contract (web/src/api.ts):
///   GET  /api/skills        → { skills: Skill[] }
///   PATCH /api/skills/{id}  → { enabled: bool }   (toggle supported)
class SkillsScreen extends ConsumerStatefulWidget {
  const SkillsScreen({super.key});

  @override
  ConsumerState<SkillsScreen> createState() => _SkillsScreenState();
}

class _SkillsScreenState extends ConsumerState<SkillsScreen> {
  final _searchController = TextEditingController();

  @override
  void initState() {
    super.initState();
    // Defer so the provider is ready before the first read.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(skillsProvider.notifier).load();
    });
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(skillsProvider);

    return LzScaffold(
      appBar: const LzAppBar(title: 'Skills'),
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

// ── Search bar ─────────────────────────────────────────────────────────────

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
        hint: 'Filter skills…',
        onChanged: (q) => ref.read(skillsProvider.notifier).search(q),
      ),
    );
  }
}

// ── Error banner ───────────────────────────────────────────────────────────

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
        onPressed: () => ref.read(skillsProvider.notifier).clearError(),
        color: AppColors.error,
        padding: EdgeInsets.zero,
        constraints: const BoxConstraints(),
      ),
    );
  }
}

// ── Body (skeleton / empty / list) ─────────────────────────────────────────

class _Body extends ConsumerWidget {
  const _Body({required this.state});
  final SkillsState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (state.isLoading) {
      return LzSkeleton.list(count: 7);
    }

    final grouped = state.grouped;

    if (grouped.isEmpty) {
      return LzRefresh(
        onRefresh: () => ref.read(skillsProvider.notifier).refresh(),
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: const [
            SizedBox(height: AppSpacing.xxxl),
            LzEmptyState(
              icon: Icons.auto_awesome_outlined,
              title: 'No skills found',
              hint: 'Try a different search term or pull down to refresh.',
            ),
          ],
        ),
      );
    }

    return LzRefresh(
      onRefresh: () => ref.read(skillsProvider.notifier).refresh(),
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.only(
          left: AppSpacing.lg,
          right: AppSpacing.lg,
          bottom: AppSpacing.xxl,
        ),
        children: [
          _CountHeader(
            total: state.skills.length,
            filtered: state.filtered.length,
          ),
          for (final entry in grouped.entries) ...[
            const SizedBox(height: AppSpacing.lg),
            _CategorySection(
              category: entry.key,
              skills: entry.value,
            ),
          ],
        ],
      ),
    );
  }
}

// ── Count header ───────────────────────────────────────────────────────────

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

// ── Category section ───────────────────────────────────────────────────────

class _CategorySection extends StatelessWidget {
  const _CategorySection({
    required this.category,
    required this.skills,
  });

  final String category;
  final List<Skill> skills;

  @override
  Widget build(BuildContext context) {
    return LzSection(
      title: '$category (${skills.length})',
      child: LzCard(
        padding: EdgeInsets.zero,
        child: Column(
          children: [
            for (int i = 0; i < skills.length; i++) ...[
              _SkillTile(skill: skills[i]),
              if (i < skills.length - 1)
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

// ── Skill tile ─────────────────────────────────────────────────────────────

class _SkillTile extends ConsumerWidget {
  const _SkillTile({required this.skill});
  final Skill skill;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return LzListTile(
      title: skill.name,
      subtitle: skill.description.isNotEmpty ? skill.description : null,
      leading: _EnabledDot(enabled: skill.enabled),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          LzChip(label: skill.displayCategory, dense: true),
          const SizedBox(width: AppSpacing.sm),
          _ToggleSwitch(skill: skill),
        ],
      ),
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
    );
  }
}

// ── Enabled status dot ─────────────────────────────────────────────────────

class _EnabledDot extends StatelessWidget {
  const _EnabledDot({required this.enabled});
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 8,
      height: 8,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: enabled ? AppColors.success : AppColors.textMuted,
      ),
    );
  }
}

// ── Toggle switch ──────────────────────────────────────────────────────────

class _ToggleSwitch extends ConsumerWidget {
  const _ToggleSwitch({required this.skill});
  final Skill skill;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Switch(
      value: skill.enabled,
      activeThumbColor: AppColors.accent,
      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
      onChanged: (v) =>
          ref.read(skillsProvider.notifier).toggleEnabled(skill.id, enabled: v),
    );
  }
}
