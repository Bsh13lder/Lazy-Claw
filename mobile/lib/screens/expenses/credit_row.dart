import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'budget_math.dart';
import 'money_helpers.dart';

/// A single ledger CREDIT row — a budget top-up ("Budget added", money IN).
///
/// Visually distinct from the debit [ExpenseRow]: a green leading badge, a
/// "Budget added" label with the optional source note + saved time beneath, and
/// a green `+ €X` amount trailing. Mirrors the credit styling of the
/// `_LedgerEntryTile` in `budget_log_sheet.dart`, adapted to the ledger row
/// shape so credits sit cleanly among the expense rows.
///
/// Pure/read-only: the ledger's money-mutation controls stay in the Budget log
/// sheet. This row never edits or deletes.
class CreditRow extends StatelessWidget {
  const CreditRow({super.key, required this.item});

  /// The credit ledger item (must have `isCredit == true`).
  final LedgerItem item;

  @override
  Widget build(BuildContext context) {
    final source = item.source?.trim() ?? '';
    final savedLabel = formatSavedAt(item.entry?.createdAt);
    final metaText = _metaText(source, savedLabel);

    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          // Leading badge — green-tinted so credits read as "money in" at a
          // glance, distinct from the neutral receipt badge on debits.
          Container(
            width: 40,
            height: 40,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: AppColors.success.withValues(alpha: 0.12),
              borderRadius: AppRadii.rMd,
              border: Border.all(
                color: AppColors.success.withValues(alpha: 0.35),
              ),
            ),
            child: const Icon(
              Icons.add_card_outlined,
              size: 18,
              color: AppColors.success,
            ),
          ),
          const SizedBox(width: AppSpacing.md),
          // Label + muted metadata line (source · saved time).
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  item.label,
                  style: AppText.body.copyWith(fontWeight: FontWeight.w600),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                if (metaText != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 3),
                    child: Row(
                      children: [
                        Icon(
                          source.isNotEmpty
                              ? Icons.sell_outlined
                              : Icons.schedule_outlined,
                          size: 12,
                          color: AppColors.textMuted,
                        ),
                        const SizedBox(width: AppSpacing.xs),
                        Flexible(
                          child: Text(
                            metaText,
                            style: AppText.caption
                                .copyWith(color: AppColors.textMuted),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(width: AppSpacing.md),
          // Signed credit amount — green, matching the "+ €X" log styling.
          Text(
            '+ ${fmtMoney(item.currency, item.magnitude)}',
            style: AppText.title.copyWith(
              color: AppColors.success,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }

  /// Composes the muted metadata line: the source note (when present) and the
  /// "Saved …" timestamp, joined with a thin dot. Null when there's nothing to
  /// show so the row stays a clean single line.
  String? _metaText(String source, String? savedLabel) {
    final parts = <String>[];
    if (source.isNotEmpty) parts.add(source);
    if (savedLabel != null) parts.add('Saved $savedLabel');
    if (parts.isEmpty) return null;
    return parts.join('  ·  ');
  }
}
