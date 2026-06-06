import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../providers/memory_provider.dart';
import '../../repositories/memory_repository.dart';
import '../../ui/ui.dart';

/// Memory power surface.
///
/// Lists the authenticated user's personal memory facts as stored by the
/// agent. Memory is read-only from the mobile side — it is written exclusively
/// by the server-side agent. This screen provides view + delete only; there is
/// no "add memory" button because the API exposes no create endpoint on
/// `/api/memory/personal`.
///
/// Route: /more/memory
class MemoryScreen extends ConsumerStatefulWidget {
  const MemoryScreen({super.key});

  @override
  ConsumerState<MemoryScreen> createState() => _MemoryScreenState();
}

class _MemoryScreenState extends ConsumerState<MemoryScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(memoryProvider.notifier).load();
    });
  }

  Future<void> _onRefresh() => ref.read(memoryProvider.notifier).load();

  Future<void> _confirmDelete(
    BuildContext context,
    PersonalMemory memory,
  ) async {
    // Capture the messenger before the async gap to satisfy
    // use_build_context_synchronously.
    final messenger = ScaffoldMessenger.of(context);

    final preview = memory.value.length > 80
        ? '${memory.value.substring(0, 80)}…'
        : memory.value;

    final confirmed = await LzConfirm.show(
      context,
      title: 'Delete memory?',
      message: '"$preview"',
      confirmLabel: 'Delete',
      danger: true,
    );
    if (!confirmed || !mounted) return;

    final ok =
        await ref.read(memoryProvider.notifier).deleteMemory(memory.id);
    if (!ok && mounted) {
      final err =
          ref.read(memoryProvider).error ?? 'Could not delete memory.';
      messenger.showSnackBar(
        SnackBar(
          content: Text(err),
          backgroundColor: AppColors.error,
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(memoryProvider);
    return LzScaffold(
      appBar: const LzAppBar(title: 'Memory'),
      body: _buildBody(context, state),
    );
  }

  Widget _buildBody(BuildContext context, MemoryState state) {
    if (state.isLoading) {
      return LzSkeleton.list(count: 6);
    }

    if (state.error != null && state.memories.isEmpty) {
      return LzEmptyState(
        icon: Icons.error_outline,
        title: 'Could not load memories',
        hint: state.error,
        actionLabel: 'Retry',
        actionIcon: Icons.refresh,
        onAction: _onRefresh,
      );
    }

    if (state.memories.isEmpty) {
      return LzRefresh(
        onRefresh: _onRefresh,
        child: const CustomScrollView(
          slivers: [
            SliverFillRemaining(
              child: LzEmptyState(
                icon: Icons.psychology_outlined,
                title: 'No memories yet',
                hint: 'Agent-learned facts about you will appear here.',
              ),
            ),
          ],
        ),
      );
    }

    return LzRefresh(
      onRefresh: _onRefresh,
      child: ListView.separated(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
        itemCount: state.memories.length,
        separatorBuilder: (_, i) => const SizedBox(height: AppSpacing.md),
        itemBuilder: (context, index) {
          final memory = state.memories[index];
          return _MemoryCard(
            memory: memory,
            onDelete: () => _confirmDelete(context, memory),
          );
        },
      ),
    );
  }
}

// ── Memory card ───────────────────────────────────────────────────────────

/// Displays a single [PersonalMemory] with a key chip, value body, date
/// footer, swipe-to-delete, and a trailing delete icon.
class _MemoryCard extends StatelessWidget {
  const _MemoryCard({required this.memory, required this.onDelete});

  final PersonalMemory memory;
  final VoidCallback onDelete;

  /// Format an ISO-8601 string as "Jun 4, 2026" without the intl package.
  String _formatDate(String iso) {
    try {
      final dt = DateTime.parse(iso).toLocal();
      const months = [
        'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
      ];
      return '${months[dt.month - 1]} ${dt.day}, ${dt.year}';
    } catch (_) {
      return iso;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Dismissible(
      key: ValueKey(memory.id),
      direction: DismissDirection.endToStart,
      // Confirmation is handled by the caller's LzConfirm dialog.
      // Return false so Dismissible never auto-removes the item — the provider
      // update does that after the server confirms.
      confirmDismiss: (_) async {
        onDelete();
        return false;
      },
      background: Container(
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.only(right: AppSpacing.xl),
        decoration: BoxDecoration(
          color: AppColors.error.withValues(alpha: 0.16),
          borderRadius: AppRadii.rLg,
          border: Border.all(
            color: AppColors.error.withValues(alpha: 0.3),
          ),
        ),
        child: const Icon(Icons.delete_outline, color: AppColors.error),
      ),
      child: LzCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header: key chip + trailing delete icon
            Row(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                LzChip(
                  label: memory.key.isNotEmpty ? memory.key : 'memory',
                  icon: Icons.psychology_outlined,
                  color: AppColors.info,
                  dense: true,
                ),
                const Spacer(),
                GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: onDelete,
                  child: const Padding(
                    padding: EdgeInsets.all(AppSpacing.xs),
                    child: Icon(
                      Icons.delete_outline,
                      size: 18,
                      color: AppColors.textMuted,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.md),
            // Memory value body
            Text(memory.value, style: AppText.body),
            const SizedBox(height: AppSpacing.md),
            // Footer: learned date
            Text(
              'Learned ${_formatDate(memory.createdAt)}',
              style: AppText.caption.copyWith(color: AppColors.textMuted),
            ),
          ],
        ),
      ),
    );
  }
}
