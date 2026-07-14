import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_exceptions.dart';
import '../../models/budget_entry.dart';
import '../../providers/budgets_provider.dart';
import '../../ui/ui.dart';
import 'money_helpers.dart';

/// Opens the Budget ledger sheet for [projectId]. Reads + writes flow through
/// the offline-first budgets provider (shared with the rest of the screen), so
/// the project's traffic-light budget bar re-renders automatically. Mirrors the
/// web "+ Add budget" / "📋 Log" controls; this is the primary place the
/// `budget_entries` ledger surfaces in the app.
Future<void> showBudgetLogSheet(
  BuildContext context,
  WidgetRef ref, {
  required String projectId,
  required String currency,
}) {
  return LzBottomSheet.show<void>(
    context,
    title: 'Budget log',
    builder: (_) => BudgetLogSheet(
      projectId: projectId,
      currency: currency,
    ),
  );
}

/// Offline-first Budget ledger surface: an "Add to budget" top-up form on top
/// and the credits/debits Log below (each entry shows date · source · signed
/// amount, with edit + delete).
///
/// Reads come from the synced local cache via [budgetsProvider] (instant, works
/// offline, reflects cross-device top-ups after a sync). A top-up / delete is
/// written optimistically to the cache + outbox and best-effort synced — so an
/// offline top-up is NEVER lost and records its where-from note. (Editing a
/// ledger entry is still an online PATCH.)
class BudgetLogSheet extends ConsumerStatefulWidget {
  const BudgetLogSheet({
    super.key,
    required this.projectId,
    required this.currency,
  });

  final String projectId;

  /// Currency to render amounts in (the owning project's currency).
  final String currency;

  @override
  ConsumerState<BudgetLogSheet> createState() => _BudgetLogSheetState();
}

class _BudgetLogSheetState extends ConsumerState<BudgetLogSheet> {
  final _amountCtrl = TextEditingController();
  final _sourceCtrl = TextEditingController();

  bool _adding = false;
  String? _amountError;

  @override
  void initState() {
    super.initState();
    // Revalidate on open so a cross-device top-up shows even if the last sync
    // predates it. Best-effort — failures are silent (the cache already paints).
    Future.microtask(() => ref.read(budgetsProvider.notifier).refresh());
  }

  @override
  void dispose() {
    _amountCtrl.dispose();
    _sourceCtrl.dispose();
    super.dispose();
  }

  Future<void> _add() async {
    final amount = double.tryParse(_amountCtrl.text.trim());
    if (amount == null || amount == 0) {
      setState(() => _amountError = 'Enter a non-zero amount');
      return;
    }
    final source = _sourceCtrl.text.trim();
    setState(() {
      _adding = true;
      _amountError = null;
    });
    // Offline-first: optimistic ledger row + budget bump, queued for sync. Never
    // loses the money offline (unlike the old online-only POST) and always
    // records the where-from note.
    final ok = await ref.read(budgetsProvider.notifier).addBudgetEntryLocal(
          widget.projectId,
          amount,
          source: source.isEmpty ? null : source,
        );
    if (!mounted) return;
    if (ok) {
      _amountCtrl.clear();
      _sourceCtrl.clear();
    } else {
      _snack(ref.read(budgetsProvider).error ?? 'Could not add to budget.');
    }
    setState(() => _adding = false);
  }

  Future<void> _saveEdit(BudgetEntry entry, double amount, String source) async {
    // Editing a ledger entry remains an online PATCH (rarer than add/delete);
    // it works for a synced entry. A refresh pulls the change back into cache.
    try {
      await ref.read(budgetsRepositoryProvider).updateBudgetEntry(
            entry.id,
            amount: amount,
            // Empty string clears the source server-side; null would leave it.
            source: source,
          );
      await ref.read(budgetsProvider.notifier).refresh();
    } catch (e) {
      _snack(_friendly(e));
    }
  }

  Future<void> _delete(BudgetEntry entry) async {
    final confirmed = await LzConfirm.show(
      context,
      title: entry.isEdit ? 'Delete budget edit?' : 'Delete budget top-up?',
      message: 'This rolls back its effect on the project budget.',
      confirmLabel: 'Delete',
      danger: true,
    );
    if (!confirmed) return;
    // Offline-first delete: drops it from state + rolls back the budget bump +
    // queues the server delete.
    await ref.read(budgetsProvider.notifier).removeBudgetEntry(entry.id);
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(msg),
        backgroundColor: AppColors.bgSurfaceElevated,
      ),
    );
  }

  static String _friendly(Object e) {
    if (e is ApiError) return e.message;
    return 'Something went wrong. Try again.';
  }

  @override
  Widget build(BuildContext context) {
    final maxListHeight = MediaQuery.of(context).size.height * 0.42;
    // Reactive: entries come from the synced cache, newest first (DAO order).
    final entries = ref.watch(budgetsProvider).entriesForProject(widget.projectId);
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // ── Add to budget ──────────────────────────────────────────────────
        LzTextField(
          controller: _amountCtrl,
          fieldKey: const Key('budget-add-amount'),
          label: 'Add to budget',
          hint: '0.00',
          prefixIcon: Icons.add_card_outlined,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          textInputAction: TextInputAction.next,
          errorText: _amountError,
          enabled: !_adding,
          onChanged: (_) {
            if (_amountError != null) setState(() => _amountError = null);
          },
        ),
        const SizedBox(height: AppSpacing.md),
        LzTextField(
          controller: _sourceCtrl,
          fieldKey: const Key('budget-add-source'),
          label: 'Source (optional)',
          hint: 'Where is this money from? e.g. client deposit',
          prefixIcon: Icons.sell_outlined,
          textInputAction: TextInputAction.done,
          enabled: !_adding,
          onSubmitted: (_) => _adding ? null : _add(),
        ),
        const SizedBox(height: AppSpacing.lg),
        LzButton.primary(
          key: const Key('budget-add-submit'),
          label: 'Add to budget',
          icon: Icons.add_rounded,
          loading: _adding,
          expand: true,
          onPressed: _adding ? null : _add,
        ),
        const SizedBox(height: AppSpacing.xl),
        Row(
          children: [
            Text('Log', style: AppText.label.copyWith(
                color: AppColors.textSecondary)),
            const Spacer(),
            if (entries.isNotEmpty)
              Text(
                _netLabel(entries),
                style: AppText.caption.copyWith(color: AppColors.textMuted),
              ),
          ],
        ),
        const SizedBox(height: AppSpacing.sm),
        // ── Ledger body ─────────────────────────────────────────────────────
        ConstrainedBox(
          constraints: BoxConstraints(maxHeight: maxListHeight),
          child: _buildBody(entries),
        ),
      ],
    );
  }

  String _netLabel(List<BudgetEntry> entries) {
    final net = entries.fold<double>(0, (s, e) => s + e.amount);
    final sign = net >= 0 ? '+ ' : '− ';
    return '$sign${fmtMoney(widget.currency, net.abs())} net';
  }

  Widget _buildBody(List<BudgetEntry> entries) {
    if (entries.isEmpty) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.xl),
        child: Center(
          child: Text(
            'No budget added yet.\nAdd a top-up above to fund this project.',
            textAlign: TextAlign.center,
            style: AppText.body.copyWith(color: AppColors.textMuted),
          ),
        ),
      );
    }
    return LzCard(
      padding: EdgeInsets.zero,
      child: ListView.separated(
        shrinkWrap: true,
        padding: EdgeInsets.zero,
        itemCount: entries.length,
        separatorBuilder: (_, _) => Divider(
          height: 0.5,
          thickness: 0.5,
          color: AppColors.borderSubtle,
        ),
        itemBuilder: (_, i) => _LedgerEntryTile(
          key: ValueKey('ledger-${entries[i].id}'),
          entry: entries[i],
          currency: widget.currency,
          onSave: _saveEdit,
          onDelete: _delete,
        ),
      ),
    );
  }
}

/// One ledger row. Read-only by default; tapping the pencil flips it into an
/// inline editor (amount + source) that calls [onSave]. The trash calls
/// [onDelete]. Manages only its own local editing state.
class _LedgerEntryTile extends StatefulWidget {
  const _LedgerEntryTile({
    super.key,
    required this.entry,
    required this.currency,
    required this.onSave,
    required this.onDelete,
  });

  final BudgetEntry entry;
  final String currency;
  final Future<void> Function(BudgetEntry entry, double amount, String source)
      onSave;
  final Future<void> Function(BudgetEntry entry) onDelete;

  @override
  State<_LedgerEntryTile> createState() => _LedgerEntryTileState();
}

class _LedgerEntryTileState extends State<_LedgerEntryTile> {
  bool _editing = false;
  bool _busy = false;
  late final TextEditingController _amountCtrl;
  late final TextEditingController _sourceCtrl;

  @override
  void initState() {
    super.initState();
    _amountCtrl = TextEditingController(text: _fmtAmount(widget.entry.amount));
    _sourceCtrl = TextEditingController(text: widget.entry.source ?? '');
  }

  @override
  void dispose() {
    _amountCtrl.dispose();
    _sourceCtrl.dispose();
    super.dispose();
  }

  static String _fmtAmount(double v) {
    if (v == v.truncateToDouble()) return v.toInt().toString();
    return v.toStringAsFixed(2);
  }

  Future<void> _save() async {
    final amount = double.tryParse(_amountCtrl.text.trim());
    if (amount == null) return;
    setState(() => _busy = true);
    await widget.onSave(widget.entry, amount, _sourceCtrl.text.trim());
    // The parent refetches the whole ledger on success, rebuilding this tile
    // from scratch — so just collapse the editor if we're still mounted.
    if (mounted) {
      setState(() {
        _busy = false;
        _editing = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final entry = widget.entry;
    final isEdit = entry.isEdit;
    final isNeg = entry.amount < 0;
    final label = isEdit ? 'Budget edited' : 'Budget added';
    final amountColor = isEdit
        ? (isNeg ? AppColors.error : AppColors.warn)
        : AppColors.success;

    if (_editing) {
      return Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            LzTextField(
              controller: _amountCtrl,
              label: 'Amount',
              hint: '0.00',
              prefixIcon: Icons.attach_money_rounded,
              keyboardType:
                  const TextInputType.numberWithOptions(decimal: true, signed: true),
              enabled: !_busy,
            ),
            const SizedBox(height: AppSpacing.sm),
            LzTextField(
              controller: _sourceCtrl,
              label: 'Source / reason',
              hint: 'Where is this from?',
              prefixIcon: Icons.sell_outlined,
              enabled: !_busy,
            ),
            const SizedBox(height: AppSpacing.sm),
            Row(
              children: [
                Expanded(
                  child: LzButton.primary(
                    label: 'Save',
                    icon: Icons.check_rounded,
                    loading: _busy,
                    expand: true,
                    onPressed: _busy ? null : _save,
                  ),
                ),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: LzButton.ghost(
                    label: 'Cancel',
                    onPressed: _busy
                        ? null
                        : () => setState(() {
                              _editing = false;
                              _amountCtrl.text = _fmtAmount(entry.amount);
                              _sourceCtrl.text = entry.source ?? '';
                            }),
                  ),
                ),
              ],
            ),
          ],
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        children: [
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(label, style: AppText.label),
              if (entry.date.isNotEmpty)
                Text(entry.date,
                    style: AppText.caption.copyWith(color: AppColors.textMuted)),
            ],
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Text(
              entry.source ?? '',
              style: AppText.caption.copyWith(color: AppColors.textMuted),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          Text(
            '${entry.amount >= 0 ? '+ ' : '− '}'
            '${fmtMoney(widget.currency, entry.amount.abs())}',
            style: AppText.label.copyWith(color: amountColor),
          ),
          IconButton(
            icon: const Icon(Icons.edit_outlined, size: 18),
            color: AppColors.textMuted,
            visualDensity: VisualDensity.compact,
            tooltip: 'Edit entry',
            onPressed: () => setState(() => _editing = true),
          ),
          IconButton(
            icon: const Icon(Icons.delete_outline_rounded, size: 18),
            color: AppColors.textMuted,
            visualDensity: VisualDensity.compact,
            tooltip: 'Delete entry',
            onPressed: () => widget.onDelete(entry),
          ),
        ],
      ),
    );
  }
}
