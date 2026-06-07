
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../chat/chat_message.dart';
import '../models/expense.dart';
import '../models/project.dart';
import '../models/task.dart';
import '../providers/budgets_provider.dart';
import '../providers/tasks_provider.dart';
import '../ui/ui.dart';
import 'expenses/budget_math.dart';
import 'expenses/money_helpers.dart';
import 'expenses/project_color_picker.dart';

// The chat provider is defined in chat_screen.dart and kept alive by
// StatefulShellRoute. We re-read it here (same ProviderScope) so the Home
// dashboard can show the last assistant message without touching the WS.
import '../screens/chat_screen.dart' show chatControllerProvider;

// ─────────────────────────────────────────────────────────────────────────────
// Home dashboard
// ─────────────────────────────────────────────────────────────────────────────

/// The app's front door — a glanceable summary of today's tasks, this month's
/// budget, the latest chat message, and quick-action buttons.
///
/// Reads: [tasksProvider], [budgetsProvider], [reachableProvider],
///        [chatControllerProvider] (from chat_screen.dart).
/// Navigation: `context.go(...)` via go_router.
class HomeScreen extends ConsumerStatefulWidget {
  const HomeScreen({super.key});

  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends ConsumerState<HomeScreen> {
  bool _tasksLoaded = false;
  bool _budgetsLoaded = false;

  @override
  void initState() {
    super.initState();
    // Kick loads after the first frame so ProviderScope is fully mounted.
    WidgetsBinding.instance.addPostFrameCallback((_) => _initialLoad());
  }

  Future<void> _initialLoad() async {
    await Future.wait([_ensureTasksLoaded(), _ensureBudgetsLoaded()]);
  }

  Future<void> _ensureTasksLoaded() async {
    if (_tasksLoaded || !mounted) return;
    _tasksLoaded = true;
    await ref.read(tasksProvider.notifier).load();
  }

  Future<void> _ensureBudgetsLoaded() async {
    if (_budgetsLoaded || !mounted) return;
    _budgetsLoaded = true;
    await ref.read(budgetsProvider.notifier).load();
  }

  Future<void> _onRefresh() async {
    if (!mounted) return;
    await Future.wait([
      ref.read(tasksProvider.notifier).refresh(),
      ref.read(budgetsProvider.notifier).refresh(),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    final isReachable = ref.watch(reachableProvider);
    final tasksState = ref.watch(tasksProvider);
    final budgetsState = ref.watch(budgetsProvider);
    final messages = ref.watch(chatControllerProvider);

    final syncState = !isReachable
        ? LzSyncState.offline
        : (tasksState.isLoading || budgetsState.isLoading)
            ? LzSyncState.syncing
            : LzSyncState.synced;

    return LzScaffold(
      appBar: LzAppBar(
        title: _greeting(),
        gradientTitle: true,
        large: false,
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: AppSpacing.lg),
            child: LzSyncBadge(state: syncState),
          ),
        ],
      ),
      banner: isReachable ? null : const LzBanner.offline(safeAreaTop: false),
      body: LzRefresh(
        onRefresh: _onRefresh,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.lg,
            AppSpacing.xl,
            AppSpacing.lg,
            AppSpacing.xxxl,
          ),
          children: [
            // ── Greeting subtitle ─────────────────────────────────────────
            Text(
              "Here's your day at a glance",
              style: AppText.body.copyWith(color: AppColors.textMuted),
            ),
            const SizedBox(height: AppSpacing.xxl),

            // ── TODAY section ─────────────────────────────────────────────
            _TodaySection(
              tasksState: tasksState,
              onSeeAll: () => context.go('/tasks'),
            ),
            const SizedBox(height: AppSpacing.xl),

            // ── THIS MONTH section ────────────────────────────────────────
            _BudgetSection(
              budgetsState: budgetsState,
              onTap: () => context.go('/expenses'),
            ),
            const SizedBox(height: AppSpacing.xl),

            // ── FAVORITES section ─────────────────────────────────────────
            // Hidden entirely when no project is starred, so the dashboard
            // stays lean for users who don't use favorites.
            _FavoritesSection(
              budgetsState: budgetsState,
              onTap: () => context.go('/expenses'),
            ),

            // ── RECENT CHAT section ───────────────────────────────────────
            _RecentChatSection(
              messages: messages,
              onTap: () => context.go('/chat'),
            ),
            const SizedBox(height: AppSpacing.xl),

            // ── Quick actions ─────────────────────────────────────────────
            _QuickActionsRow(
              onTask: () => context.go('/tasks'),
              onExpense: () => context.go('/expenses'),
              // Notes now lives as a segment inside the Tasks tab.
              onNote: () => context.go('/tasks'),
              onChat: () => context.go('/chat'),
            ),
          ],
        ),
      ),
    );
  }

  static String _greeting() {
    final h = DateTime.now().hour;
    if (h < 12) return 'Good morning';
    if (h < 17) return 'Good afternoon';
    if (h < 21) return 'Good evening';
    return 'Good night';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// TODAY — tasks due today + overdue
// ─────────────────────────────────────────────────────────────────────────────

class _TodaySection extends StatelessWidget {
  const _TodaySection({required this.tasksState, required this.onSeeAll});

  final TasksState tasksState;
  final VoidCallback onSeeAll;

  static const int _max = 3;

  @override
  Widget build(BuildContext context) {
    return LzSection(
      title: 'Today',
      action: GestureDetector(
        onTap: onSeeAll,
        child: Text(
          'See all →',
          style: AppText.caption.copyWith(color: AppColors.accent),
        ),
      ),
      child: tasksState.isLoading ? _skeleton() : _content(),
    );
  }

  Widget _skeleton() {
    return LzCard(
      padding: EdgeInsets.zero,
      child: Column(
        children: List.generate(2, (i) {
          return Column(
            children: [
              if (i > 0)
                const Divider(
                  height: 1,
                  indent: AppSpacing.lg,
                  color: AppColors.borderSubtle,
                ),
              const Padding(
                padding: EdgeInsets.symmetric(
                    horizontal: AppSpacing.lg, vertical: AppSpacing.md),
                child: Row(
                  children: [
                    LzSkeleton(
                        width: 18,
                        height: 18,
                        borderRadius: AppRadii.rSm),
                    SizedBox(width: AppSpacing.md),
                    Expanded(child: LzSkeleton(height: 13)),
                    SizedBox(width: AppSpacing.md),
                    LzSkeleton(
                        width: 56,
                        height: 20,
                        borderRadius: AppRadii.rPill),
                  ],
                ),
              ),
            ],
          );
        }),
      ),
    );
  }

  Widget _content() {
    final relevant = _relevant(tasksState.tasks);

    if (relevant.isEmpty) {
      return const LzCard(
        child: LzEmptyState(
          icon: Icons.check_circle_outline,
          title: 'All clear',
          hint: 'No overdue or due-today tasks.',
        ),
      );
    }

    final shown = relevant.take(_max).toList();
    final extra = relevant.length - shown.length;

    return LzCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (var i = 0; i < shown.length; i++) ...[
            if (i > 0)
              const Divider(
                height: 1,
                indent: AppSpacing.lg,
                color: AppColors.borderSubtle,
              ),
            _TaskRow(task: shown[i]),
          ],
          if (extra > 0) ...[
            const Divider(height: 1, color: AppColors.borderSubtle),
            Padding(
              padding: const EdgeInsets.symmetric(
                  horizontal: AppSpacing.lg, vertical: AppSpacing.sm),
              child: Text(
                '+$extra more',
                style: AppText.caption.copyWith(color: AppColors.accent),
              ),
            ),
          ],
        ],
      ),
    );
  }

  static List<Task> _relevant(List<Task> tasks) {
    final today = _iso();
    final overdue = <Task>[];
    final dueToday = <Task>[];
    for (final t in tasks) {
      if (t.isDone) continue;
      final d = t.dueDate;
      if (d == null) continue;
      final date = d.length >= 10 ? d.substring(0, 10) : d;
      if (date.compareTo(today) < 0) {
        overdue.add(t);
      } else if (date == today) {
        dueToday.add(t);
      }
    }
    return [...overdue, ...dueToday];
  }

  static String _iso() {
    final n = DateTime.now();
    return '${n.year.toString().padLeft(4, '0')}-'
        '${n.month.toString().padLeft(2, '0')}-'
        '${n.day.toString().padLeft(2, '0')}';
  }
}

class _TaskRow extends StatelessWidget {
  const _TaskRow({required this.task});

  final Task task;

  @override
  Widget build(BuildContext context) {
    final overdue = _isOverdue(task.dueDate);
    final (chipLabel, chipColor) = _chip(task.priority);

    return LzListTile(
      dense: true,
      title: task.title,
      subtitle: overdue
          ? 'Overdue'
          : task.dueDate != null
              ? _dueLine(task.dueDate!)
              : null,
      leading: Icon(
        Icons.radio_button_unchecked,
        size: 18,
        color: overdue ? AppColors.error : AppColors.textMuted,
      ),
      trailing: LzChip(
        label: chipLabel,
        dense: true,
        selected: true,
        color: chipColor,
      ),
    );
  }

  static bool _isOverdue(String? due) {
    if (due == null) return false;
    final d = due.length >= 10 ? due.substring(0, 10) : due;
    return d.compareTo(_todayIso()) < 0;
  }

  static String _todayIso() {
    final n = DateTime.now();
    return '${n.year.toString().padLeft(4, '0')}-'
        '${n.month.toString().padLeft(2, '0')}-'
        '${n.day.toString().padLeft(2, '0')}';
  }

  static String _dueLine(String due) {
    final d = due.length >= 10 ? due.substring(0, 10) : due;
    final parts = d.split('-');
    if (parts.length != 3) return d;
    const months = [
      '',
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ];
    final mon = int.tryParse(parts[1]) ?? 0;
    final day = int.tryParse(parts[2]) ?? 0;
    final label = mon >= 1 && mon <= 12 ? months[mon] : parts[1];
    return 'Due $label $day';
  }

  static (String, Color) _chip(String priority) {
    switch (priority.toLowerCase()) {
      case 'critical':
        return ('Critical', AppColors.error);
      case 'high':
        return ('High', AppColors.warn);
      case 'low':
        return ('Low', AppColors.info);
      default:
        return ('Medium', AppColors.textMuted);
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// THIS MONTH — budget snapshot
// ─────────────────────────────────────────────────────────────────────────────

class _BudgetSection extends StatelessWidget {
  const _BudgetSection({required this.budgetsState, required this.onTap});

  final BudgetsState budgetsState;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return LzSection(
      title: 'This month',
      child: budgetsState.isLoading ? _skeleton() : _content(),
    );
  }

  Widget _skeleton() {
    return const LzCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: LzSkeleton(height: 13)),
              SizedBox(width: AppSpacing.md),
              LzSkeleton(width: 80, height: 13),
            ],
          ),
          SizedBox(height: AppSpacing.md),
          LzSkeleton(height: 8, borderRadius: AppRadii.rPill),
          SizedBox(height: AppSpacing.sm),
          LzSkeleton(width: 140, height: 11),
        ],
      ),
    );
  }

  Widget _content() {
    final (budget, spent) = _totals(budgetsState.projects);
    final fraction =
        budget > 0 ? (spent / budget).clamp(0.0, 1.0) : 0.0;
    final pct = (fraction * 100).round();
    final symbol = _symbol(budgetsState.projects);
    final amountText = '$symbol${_fmt(spent)} / $symbol${_fmt(budget)}';

    return LzCard(
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text('Total spend', style: AppText.body)),
              Text(
                amountText,
                style: AppText.label.copyWith(
                    color: AppColors.trafficLight(fraction)),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          LzProgressBar(value: fraction, trafficLight: true),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              Expanded(
                child: Text(
                  budget == 0
                      ? 'No budget set — tap to add projects'
                      : '$pct% of monthly budget used',
                  style: AppText.caption,
                ),
              ),
              const Icon(Icons.chevron_right,
                  size: 15, color: AppColors.textMuted),
            ],
          ),
        ],
      ),
    );
  }

  static (double, double) _totals(List<Project> projects) {
    var budget = 0.0, spent = 0.0;
    for (final p in projects) {
      if (p.isArchived) continue;
      budget += p.budget;
      spent += p.spent ?? 0.0;
    }
    return (budget, spent);
  }

  static String _symbol(List<Project> projects) {
    for (final p in projects) {
      switch (p.currency) {
        case 'EUR':
          return '€';
        case 'GBP':
          return '£';
        case 'USD':
          return r'$';
      }
    }
    return '€';
  }

  static String _fmt(double v) =>
      v >= 1000 ? '${(v / 1000).toStringAsFixed(1)}k' : v.toStringAsFixed(0);
}

// ─────────────────────────────────────────────────────────────────────────────
// FAVORITES — starred projects with budget bar + recent expenses
// ─────────────────────────────────────────────────────────────────────────────

/// Lists the user's favorited (starred) projects so the important ones stay
/// glanceable on Home — the headline budget view, right under "This month".
/// Each entry shows Budget vs Spent vs Remaining with a traffic-light bar (when
/// a budget is set) plus its two most recent expenses. Renders nothing at all
/// when no project is favorited (Home then falls back to the total-spend card).
class _FavoritesSection extends StatelessWidget {
  const _FavoritesSection({required this.budgetsState, required this.onTap});

  final BudgetsState budgetsState;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final favorites = budgetsState.projects
        .where((p) => p.isFavorite && !p.isArchived)
        .toList();
    if (favorites.isEmpty) return const SizedBox.shrink();

    return Column(
      children: [
        LzSection(
          title: 'Favorites',
          action: GestureDetector(
            onTap: onTap,
            child: Text(
              'See all →',
              style: AppText.caption.copyWith(color: AppColors.accent),
            ),
          ),
          child: Column(
            children: [
              for (var i = 0; i < favorites.length; i++) ...[
                if (i > 0) const SizedBox(height: AppSpacing.sm),
                _FavoriteProjectCard(
                  project: favorites[i],
                  // Pass the FULL non-void expense set for this project so the
                  // card derives spend via spentForProject (offline-correct +
                  // consistent with the Expenses tab); it truncates internally
                  // for the recent-expense preview.
                  expenses: budgetsState.expenses
                      .where((e) =>
                          e.projectId == favorites[i].id && !e.isVoid)
                      .toList(),
                  onTap: onTap,
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: AppSpacing.xl),
      ],
    );
  }
}

class _FavoriteProjectCard extends StatelessWidget {
  const _FavoriteProjectCard({
    required this.project,
    required this.expenses,
    required this.onTap,
  });

  final Project project;

  /// This project's full non-void expense set (newest first). Spend is derived
  /// from it; only the first [_maxExpenses] are previewed.
  final List<Expense> expenses;
  final VoidCallback onTap;

  static const int _maxExpenses = 2;

  @override
  Widget build(BuildContext context) {
    final budget = project.budget;
    // Derive spend from the cached expense set (via the shared budget math) so
    // the bar always agrees with the Expenses tab — even offline, regardless of
    // whether the project row carries a server rollup.
    final spent = spentForProject(project.id, expenses);
    final remaining = remainingForProject(project, spent);
    final fraction = budget > 0 ? (spent / budget).clamp(0.0, 1.0) : 0.0;
    final overBudget = budget > 0 && spent > budget;
    final pctUsed = budget > 0 ? (fraction * 100).round() : 0;
    final recent = expenses.take(_maxExpenses);

    return LzCard(
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              ProjectColorDot(hex: project.color),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Text(
                  project.name,
                  style: AppText.label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const Icon(Icons.star_rounded, size: 16, color: AppColors.warn),
            ],
          ),
          if (budget > 0) ...[
            const SizedBox(height: AppSpacing.sm),
            LzProgressBar(value: fraction, height: 6, trafficLight: true),
            const SizedBox(height: AppSpacing.xs),
            // Spent · % used   ……   {remaining} left/over of {budget}
            Row(
              children: [
                Text(
                  fmtMoney(project.currency, spent),
                  style: AppText.caption.copyWith(
                    color: AppColors.trafficLight(fraction),
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  'spent · $pctUsed%',
                  style: AppText.caption.copyWith(color: AppColors.textMuted),
                ),
                const Spacer(),
                Text(
                  overBudget
                      ? '${fmtMoney(project.currency, spent - budget)} over'
                      : '${fmtMoney(project.currency, remaining)} left',
                  style: AppText.caption.copyWith(
                    color: overBudget
                        ? AppColors.error
                        : AppColors.textMuted,
                  ),
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  '/ ${fmtMoney(project.currency, budget)}',
                  style: AppText.caption.copyWith(color: AppColors.textMuted),
                ),
              ],
            ),
          ] else ...[
            const SizedBox(height: AppSpacing.xs),
            Text(
              '${fmtMoney(project.currency, spent)} spent · no budget',
              style: AppText.caption.copyWith(color: AppColors.textMuted),
            ),
          ],
          if (recent.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.sm),
            for (final e in recent)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        e.description?.trim().isNotEmpty == true
                            ? e.description!.trim()
                            : (e.vendor?.trim().isNotEmpty == true
                                ? e.vendor!.trim()
                                : 'Expense'),
                        style: AppText.caption
                            .copyWith(color: AppColors.textSecondary),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    Text(
                      fmtMoney(e.currency, e.amount),
                      style: AppText.caption,
                    ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// RECENT CHAT — last assistant message preview
// ─────────────────────────────────────────────────────────────────────────────

class _RecentChatSection extends StatelessWidget {
  const _RecentChatSection({required this.messages, required this.onTap});

  final List<ChatMessage> messages;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return LzSection(
      title: 'Recent chat',
      child: messages.isEmpty ? _empty() : _preview(),
    );
  }

  Widget _empty() {
    return LzCard(
      child: LzEmptyState(
        icon: Icons.chat_bubble_outline,
        title: 'No messages yet',
        hint: 'Start a conversation with your AI assistant.',
        actionLabel: 'Open Chat',
        actionIcon: Icons.chat_bubble_outline,
        onAction: onTap,
      ),
    );
  }

  Widget _preview() {
    // Walk backward to find the last settled assistant message.
    ChatMessage? last;
    for (var i = messages.length - 1; i >= 0; i--) {
      final m = messages[i];
      if (m.role == 'assistant' && !m.streaming) {
        last = m;
        break;
      }
    }

    final previewText =
        last != null ? _strip(last.content) : 'Conversation in progress…';

    return LzCard(
      onTap: onTap,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const LzAvatar(name: 'LazyClaw', gradient: true, size: 36),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('LazyClaw', style: AppText.label),
                const SizedBox(height: 2),
                Text(
                  previewText,
                  style:
                      AppText.body.copyWith(color: AppColors.textSecondary),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          const Icon(Icons.chevron_right,
              size: 16, color: AppColors.textMuted),
        ],
      ),
    );
  }

  /// Strip markdown noise for a clean one-liner preview.
  static String _strip(String raw) => raw
      .replaceAll(RegExp(r'^#+\s*', multiLine: true), '')
      .replaceAll(RegExp(r'\*+'), '')
      .replaceAll('`', '')
      .replaceAll(RegExp(r'\n+'), ' ')
      .trim();
}

// ─────────────────────────────────────────────────────────────────────────────
// Quick actions row
// ─────────────────────────────────────────────────────────────────────────────

class _QuickActionsRow extends StatelessWidget {
  const _QuickActionsRow({
    required this.onTask,
    required this.onExpense,
    required this.onNote,
    required this.onChat,
  });

  final VoidCallback onTask;
  final VoidCallback onExpense;
  final VoidCallback onNote;
  final VoidCallback onChat;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
            child: _QuickAction(
                icon: Icons.add_task, label: '+ Task', onTap: onTask)),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
            child: _QuickAction(
                icon: Icons.account_balance_wallet_outlined,
                label: '+ Expense',
                onTap: onExpense)),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
            child: _QuickAction(
                icon: Icons.note_add_outlined,
                label: '+ Note',
                onTap: onNote)),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
            child: _QuickAction(
                icon: Icons.chat_bubble_outline,
                label: 'Chat',
                onTap: onChat)),
      ],
    );
  }
}

class _QuickAction extends StatelessWidget {
  const _QuickAction({
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return LzCard(
      onTap: onTap,
      padding: const EdgeInsets.symmetric(
        vertical: AppSpacing.lg,
        horizontal: AppSpacing.xs,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, color: AppColors.accent, size: 22),
          const SizedBox(height: AppSpacing.sm),
          Text(
            label,
            textAlign: TextAlign.center,
            style: AppText.caption.copyWith(fontWeight: FontWeight.w700),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}
