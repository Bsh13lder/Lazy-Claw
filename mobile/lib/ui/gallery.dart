import 'package:flutter/material.dart';
import 'ui.dart';

/// A dev-only gallery rendering every `Lz*` component against the tokens.
///
/// Not wired into the router — open it manually during development
/// (`Navigator.push(... GalleryScreen())`) or use it as a visual smoke test.
/// Useful for verifying the design system in isolation before screens consume
/// it.
class GalleryScreen extends StatelessWidget {
  const GalleryScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final searchController = TextEditingController();
    return LzScaffold(
      appBar: const LzAppBar(
        title: 'UI Gallery',
        gradientTitle: true,
        large: true,
      ),
      banner: const LzBanner.offline(),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          _section('Buttons', [
            Wrap(
              spacing: AppSpacing.md,
              runSpacing: AppSpacing.md,
              children: [
                LzButton.primary(label: 'Primary', onPressed: () {}),
                LzButton.secondary(label: 'Secondary', onPressed: () {}),
                LzButton.ghost(label: 'Ghost', onPressed: () {}),
                LzButton.danger(label: 'Danger', onPressed: () {}),
                LzButton.primary(
                    label: 'Loading', onPressed: () {}, loading: true),
                LzButton.primary(
                    label: 'Disabled', onPressed: null),
              ],
            ),
            const SizedBox(height: AppSpacing.md),
            Row(
              children: [
                LzIconButton(icon: Icons.favorite, onPressed: () {}),
                LzIconButton(
                    icon: Icons.add, onPressed: () {}, filled: true, accent: true),
                LzIconButton(
                    icon: Icons.settings, onPressed: () {}, filled: true),
              ],
            ),
          ]),
          _section('Cards & sections', [
            LzCard(
              child: Text('A plain LzCard with body text.', style: AppText.body),
            ),
            const SizedBox(height: AppSpacing.md),
            LzCard(
              gradient: AppColors.accentGradient,
              child: Text('Gradient hero card',
                  style: AppText.title.copyWith(color: AppColors.onAccent)),
            ),
          ]),
          _section('Chips, badges, pills, dots', [
            Wrap(
              spacing: AppSpacing.sm,
              runSpacing: AppSpacing.sm,
              children: [
                LzChip(label: 'All', selected: true, onTap: () {}),
                LzChip(label: 'Filter', onTap: () {}),
                const LzChip(label: 'high', color: AppColors.warn),
                const LzBadge(count: 3),
                const LzBadge(count: 128),
                const LzPill(label: 'Connected', dotColor: AppColors.success),
                const LzStatusDot.success(glow: true),
                const LzStatusDot.error(),
              ],
            ),
          ]),
          _section('Sync badges', [
            Wrap(
              spacing: AppSpacing.md,
              children: const [
                LzSyncBadge(state: LzSyncState.synced),
                LzSyncBadge(state: LzSyncState.syncing),
                LzSyncBadge(state: LzSyncState.offline),
              ],
            ),
          ]),
          _section('Inputs', [
            LzTextField(
                label: 'Text field', hint: 'Type something', prefixIcon: Icons.edit),
            const SizedBox(height: AppSpacing.md),
            LzSearchField(controller: searchController, hint: 'Search notes'),
          ]),
          _section('Progress', [
            const LzProgressBar(value: 0.4),
            const SizedBox(height: AppSpacing.sm),
            const LzProgressBar(value: 0.75, trafficLight: true),
            const SizedBox(height: AppSpacing.sm),
            const LzProgressBar(value: 0.95, trafficLight: true),
          ]),
          _section('Avatars', [
            Row(
              children: const [
                LzAvatar(name: 'James Blue'),
                SizedBox(width: AppSpacing.md),
                LzAvatar(name: 'LazyClaw', gradient: true),
                SizedBox(width: AppSpacing.md),
                LzAvatar(),
              ],
            ),
          ]),
          _section('List tile', [
            LzCard(
              padding: EdgeInsets.zero,
              child: LzListTile(
                leading: const LzStatusDot.warn(),
                title: 'Ship invoice',
                subtitle: 'Due 6pm · Survival',
                trailing: const Icon(Icons.chevron_right,
                    color: AppColors.textMuted),
                onTap: () {},
              ),
            ),
          ]),
          _section('Banners', [
            const LzBanner.info(message: 'Informational banner'),
            const SizedBox(height: AppSpacing.sm),
            const LzBanner.error(message: 'Something went wrong'),
          ]),
          _section('Skeleton', [
            LzSkeleton.list(count: 2, padding: EdgeInsets.zero),
          ]),
          _section('Overlays', [
            Wrap(
              spacing: AppSpacing.md,
              children: [
                LzButton.secondary(
                  label: 'Bottom sheet',
                  onPressed: () => LzBottomSheet.show(
                    context,
                    title: 'Example sheet',
                    builder: (_) => Text('Sheet content', style: AppText.body),
                  ),
                ),
                LzButton.secondary(
                  label: 'Confirm',
                  onPressed: () => LzConfirm.show(
                    context,
                    title: 'Delete?',
                    message: 'This cannot be undone.',
                    danger: true,
                  ),
                ),
              ],
            ),
          ]),
          _section('Empty state', [
            SizedBox(
              height: 260,
              child: LzEmptyState(
                icon: Icons.inbox_outlined,
                title: 'Nothing here yet',
                hint: 'Add your first item to get started.',
                actionLabel: 'Add item',
                actionIcon: Icons.add,
                onAction: () {},
              ),
            ),
          ]),
        ],
      ),
    );
  }

  Widget _section(String title, List<Widget> children) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.xl),
      child: LzSection(
        title: title,
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: children),
      ),
    );
  }
}
