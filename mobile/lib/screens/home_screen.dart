import 'package:flutter/material.dart';
import '../ui/ui.dart';

/// The Home dashboard — the app's front door.
///
/// **Phase A placeholder.** This lays out the §2.1 section scaffolding (greeting
/// header + sync pill, TODAY / THIS MONTH / RECENT CHAT sections, and the
/// quick-action row) using only the `lib/ui/` kit. Phase B wires each section to
/// the real providers (tasks due today, budget snapshot, last chat message) and
/// makes the cards tappable into their tabs.
class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return LzScaffold(
      appBar: const LzAppBar(
        title: 'LazyClaw',
        gradientTitle: true,
        large: true,
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg,
          AppSpacing.md,
          AppSpacing.lg,
          AppSpacing.xxl,
        ),
        children: [
          // ── Greeting + sync pill ───────────────────────────────────────
          Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Good evening', style: AppText.headline),
                    const SizedBox(height: AppSpacing.xs),
                    Text(
                      'Here’s your day at a glance',
                      style: AppText.body.copyWith(color: AppColors.textMuted),
                    ),
                  ],
                ),
              ),
              const LzSyncBadge(state: LzSyncState.synced),
            ],
          ),
          const SizedBox(height: AppSpacing.xl),

          // ── TODAY ──────────────────────────────────────────────────────
          LzSection(
            title: 'Today',
            action: TextButton(
              onPressed: null,
              child: Text('See all',
                  style: AppText.label.copyWith(color: AppColors.accent)),
            ),
            child: const LzCard(
              child: _PlaceholderRows(
                icon: Icons.check_circle_outline,
                lines: [
                  'Tasks due today appear here',
                  'Overdue items are flagged',
                ],
              ),
            ),
          ),
          const SizedBox(height: AppSpacing.xl),

          // ── THIS MONTH ─────────────────────────────────────────────────
          LzSection(
            title: 'This month',
            child: LzCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text('Budget snapshot', style: AppText.title),
                      ),
                      Text(
                        '€0 / €0',
                        style: AppText.label
                            .copyWith(color: AppColors.textSecondary),
                      ),
                    ],
                  ),
                  const SizedBox(height: AppSpacing.md),
                  const LzProgressBar(value: 0, trafficLight: true),
                  const SizedBox(height: AppSpacing.sm),
                  Text(
                    'Spend vs budget — wired in Phase B',
                    style: AppText.caption,
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: AppSpacing.xl),

          // ── RECENT CHAT ────────────────────────────────────────────────
          LzSection(
            title: 'Recent chat',
            child: LzCard(
              onTap: () {},
              child: Row(
                children: [
                  const LzAvatar(name: 'LazyClaw', gradient: true, size: 36),
                  const SizedBox(width: AppSpacing.md),
                  Expanded(
                    child: Text(
                      'Your last assistant message will preview here',
                      style:
                          AppText.body.copyWith(color: AppColors.textSecondary),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  const Icon(Icons.chevron_right, color: AppColors.textMuted),
                ],
              ),
            ),
          ),
          const SizedBox(height: AppSpacing.xl),

          // ── Quick actions ──────────────────────────────────────────────
          Row(
            children: const [
              Expanded(child: _QuickAction(icon: Icons.add_task, label: 'Task')),
              SizedBox(width: AppSpacing.md),
              Expanded(
                  child: _QuickAction(
                      icon: Icons.account_balance_wallet_outlined,
                      label: 'Expense')),
              SizedBox(width: AppSpacing.md),
              Expanded(
                  child: _QuickAction(
                      icon: Icons.note_add_outlined, label: 'Note')),
              SizedBox(width: AppSpacing.md),
              Expanded(
                  child: _QuickAction(
                      icon: Icons.chat_bubble_outline, label: 'Chat')),
            ],
          ),
        ],
      ),
    );
  }
}

class _PlaceholderRows extends StatelessWidget {
  const _PlaceholderRows({required this.icon, required this.lines});

  final IconData icon;
  final List<String> lines;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        for (final line in lines)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
            child: Row(
              children: [
                Icon(icon, size: 18, color: AppColors.textMuted),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Text(
                    line,
                    style:
                        AppText.body.copyWith(color: AppColors.textSecondary),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

class _QuickAction extends StatelessWidget {
  const _QuickAction({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return LzCard(
      onTap: () {},
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.lg),
      child: Column(
        children: [
          Icon(icon, color: AppColors.accent, size: 22),
          const SizedBox(height: AppSpacing.sm),
          Text(label, style: AppText.caption.copyWith(fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}
