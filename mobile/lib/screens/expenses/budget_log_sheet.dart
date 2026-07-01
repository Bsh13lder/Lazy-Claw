import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_exceptions.dart';
import '../../models/budget_entry.dart';
import '../../providers/budgets_provider.dart';
import '../../ui/ui.dart';
import 'money_helpers.dart';

/// Opens the Budget ledger sheet for [projectId] and refreshes the budgets
/// provider whenever a ledger mutation lands (so the project's traffic-light
/// budget bar reflects the new total). Mirrors the web "+ Add budget" / "📋 Log"
/// controls; this is the only place the `budget_entries` ledger surfaces in the
/// app.
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
      onBudgetChanged: () => ref.read(budgetsProvider.notifier).refresh(),
    ),
  );
}

/// Online-only Budget ledger surface: an "Add to budget" top-up form on top and
/// the credits/debits Log below (each entry shows date · source · signed amount,
/// with edit + delete). Reads/writes go straight to the backend via
/// [budgetsRepositoryProvider] — the ledger is NOT part of the offline sync
/// table (it mirrors the web, which is also online for these controls).
///
/// After any mutation it refetches the ledger AND fires [onBudgetChanged] so the
/// parent can re-sync the (offline-cached) project budget bar.
class BudgetLogSheet extends ConsumerStatefulWidget {
  const BudgetLogSheet({
    super.key,
    required this.projectId,
    required this.currency,
    this.onBudgetChanged,
  });

  final String projectId;

  /// Currency to render amounts in (the owning project's currency).
  final String currency;

  /// Fired after every successful ledger mutation so the caller can refresh the
  /// budgets provider (and thus the project's budget bar).
  final Future<void> Function()? onBudgetChanged;

  @override
  ConsumerState<BudgetLogSheet> createState() => _BudgetLogSheetState();
}

class _BudgetLogSheetState extends ConsumerState<BudgetLogSheet> {
  final _amountCtrl = TextEditingController();
  final _sourceCtrl = TextEditingController();

  /// null = loading. Set to a list (possibly empty) on success.
  List<BudgetEntry>? _entries;
  String? _loadError;
  bool _adding = false;
  String? _amountError;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _amountCtrl.dispose();
    _sourceCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _entries = null;
      _loadError = null;
    });
    try {
      final entries = await ref
          .read(budgetsRepositoryProvider)
          .listBudgetEntries(widget.projectId);
      if (!mounted) return;
      setState(() => _entries = entries);
    } catch (e) {
      if (!mounted) return;
      setState(() => _loadError = _friendly(e));
    }
  }

  /// Fire the parent refresh (best-effort) then refetch the ledger.
  Future<void> _afterMutation() async {
    final cb = widget.onBudgetChanged;
    if (cb != null) {
      try {
        await cb();
      } catch (_) {
        // Provider refresh is best-effort; the ledger refetch below is the
        // source of truth for THIS sheet.
      }
    }
    await _load();
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
    try {
      await ref.read(budgetsRepositoryProvider).addBudgetEntry(
            widget.projectId,
            amount,
            source: source.isEmpty ? null : source,
            currency: widget.currency,
          );
      _amountCtrl.clear();
      _sourceCtrl.clear();
      await _afterMutation();
    } catch (e) {
      // Offline / server-unreachable: don't silently lose the top-up (the old
      // behavior just surfaced an error and dropped it). Fall back to an
      // offline-safe project-budget credit that queues + syncs via the project
      // update path. ONLY on a network error — a definitive server rejection may
      // have already been processed server-side, and a local credit on top would
      // double-count the money.
      if (_isNetworkError(e)) {
        final ok = await ref
            .read(budgetsProvider.notifier)
            .creditProjectBudget(widget.projectId, amount);
        if (ok) {
          _amountCtrl.clear();
          _sourceCtrl.clear();
          _snack('Saved offline — will sync. (No ledger note while offline.)');
          await _afterMutation();
        } else {
          _snack(_friendly(e));
        }
      } else {
        _snack(_friendly(e));
      }
    } finally {
      if (mounted) setState(() => _adding = false);
    }
  }

  Future<void> _saveEdit(BudgetEntry entry, double amount, String source) async {
    try {
      await ref.read(budgetsRepositoryProvider).updateBudgetEntry(
            entry.id,
            amount: amount,
            // Empty string clears the source server-side; null would leave it.
            source: source,
          );
      await _afterMutation();
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
    try {
      await ref.read(budgetsRepositoryProvider).deleteBudgetEntry(entry.id);
      await _afterMutation();
    } catch (e) {
      _snack(_friendly(e));
    }
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

  /// True when [e] is a transport-level failure (no HTTP response reached us),
  /// so the top-up definitely didn't land server-side and an offline credit is
  /// safe. `ApiError(status: 0)` is the network shape the budgets transport
  /// surfaces (see BudgetsSync._isNetworkError).
  static bool _isNetworkError(Object e) => e is ApiError && e.status == 0;

  @override
  Widget build(BuildContext context) {
    final maxListHeight = MediaQuery.of(context).size.height * 0.42;
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
            if (_entries != null && _entries!.isNotEmpty)
              Text(
                _netLabel(_entries!),
                style: AppText.caption.copyWith(color: AppColors.textMuted),
              ),
          ],
        ),
        const SizedBox(height: AppSpacing.sm),
        // ── Ledger body ─────────────────────────────────────────────────────
        ConstrainedBox(
          constraints: BoxConstraints(maxHeight: maxListHeight),
          child: _buildBody(),
        ),
      ],
    );
  }

  String _netLabel(List<BudgetEntry> entries) {
    final net = entries.fold<double>(0, (s, e) => s + e.amount);
    final sign = net >= 0 ? '+ ' : '− ';
    return '$sign${fmtMoney(widget.currency, net.abs())} net';
  }

  Widget _buildBody() {
    if (_loadError != null) {
      return LzErrorState(message: _loadError!, onRetry: _load);
    }
    final entries = _entries;
    if (entries == null) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: AppSpacing.xl),
        child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
      );
    }
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
