import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

import '../../local/task_dao.dart';
import '../../providers/tasks_provider.dart';
import '../../ui/ui.dart';

// ── Conflict DAO helper (read-only; does NOT touch sync engines or DAOs) ─────

/// Reads all rows from the shared `conflicts` table in the local encrypted DB.
/// Returns newest-first (sorted by `at` DESC). Kept private to this file — the
/// screen is the only consumer.
Future<List<ConflictRow>> _readAllConflicts(Database db) async {
  final rows = await db.query(
    'conflicts',
    orderBy: 'at DESC',
    limit: 200,
  );
  return rows.map((r) => ConflictRow(
        id: r['id'] as String? ?? '',
        field: r['field'] as String? ?? '',
        local: r['local'] as String?,
        server: r['server'] as String?,
        at: r['at'] as String? ?? '',
      )).toList();
}

/// Async provider for the conflicts list — fires once when the sheet opens.
final _conflictsProvider = FutureProvider<List<ConflictRow>>((ref) async {
  final db = ref.watch(appDatabaseProvider);
  return _readAllConflicts(db);
});

// ── Sheet widget ─────────────────────────────────────────────────────────────

/// Shows conflicts from the local `conflicts` table in a bottom sheet.
/// Call via [LzBottomSheet.show] and pass this as the builder child.
class ConflictsSheetContent extends ConsumerWidget {
  const ConflictsSheetContent({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(_conflictsProvider);
    return async.when(
      loading: () => const SizedBox(
        height: 160,
        child: Center(child: CircularProgressIndicator()),
      ),
      error: (e, _) => Padding(
        padding: const EdgeInsets.only(bottom: AppSpacing.xl),
        child: Text(
          'Could not load conflicts: $e',
          style: AppText.body.copyWith(color: AppColors.error),
        ),
      ),
      data: (rows) => _ConflictList(rows: rows),
    );
  }
}

class _ConflictList extends StatelessWidget {
  const _ConflictList({required this.rows});

  final List<ConflictRow> rows;

  @override
  Widget build(BuildContext context) {
    if (rows.isEmpty) {
      return const Padding(
        padding: EdgeInsets.only(bottom: AppSpacing.xl),
        child: LzEmptyState(
          icon: Icons.check_circle_outline,
          title: 'No conflicts',
          hint: 'All local edits synced cleanly.',
        ),
      );
    }

    return ConstrainedBox(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.of(context).size.height * 0.6,
      ),
      child: ListView.separated(
        shrinkWrap: true,
        itemCount: rows.length,
        separatorBuilder: (_, _) => const Divider(
          height: 1,
          color: AppColors.borderSubtle,
        ),
        itemBuilder: (ctx, i) => _ConflictTile(row: rows[i]),
      ),
    );
  }
}

class _ConflictTile extends StatelessWidget {
  const _ConflictTile({required this.row});

  final ConflictRow row;

  @override
  Widget build(BuildContext context) {
    final isRejected = row.field == 'create_rejected';
    final label = isRejected ? 'Create rejected' : row.field;
    final subtitle = isRejected
        ? (row.local ?? 'unknown')
        : 'Local: ${row.local ?? "—"}  →  Server: ${row.server ?? "—"}';

    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.sm,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                isRejected ? Icons.block : Icons.compare_arrows,
                size: 14,
                color: isRejected ? AppColors.error : AppColors.warn,
              ),
              const SizedBox(width: AppSpacing.xs),
              Expanded(
                child: Text(
                  label,
                  style: AppText.label.copyWith(
                    color: isRejected ? AppColors.error : AppColors.warn,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              Text(
                _formatAt(row.at),
                style: AppText.caption,
              ),
            ],
          ),
          const SizedBox(height: 2),
          Text(
            subtitle,
            style: AppText.caption,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
          ),
          Text(
            'id: ${row.id}',
            style: AppText.caption.copyWith(color: AppColors.textMuted),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }

  String _formatAt(String iso) {
    if (iso.isEmpty) return '';
    try {
      final dt = DateTime.parse(iso).toLocal();
      return '${dt.month}/${dt.day} ${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
    } catch (_) {
      return iso.length > 10 ? iso.substring(0, 10) : iso;
    }
  }
}
