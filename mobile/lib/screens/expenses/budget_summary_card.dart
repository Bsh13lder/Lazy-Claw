import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'budget_math.dart';
import 'money_helpers.dart';

/// Hero budget summary card: total spend vs total budget (in the dominant
/// currency), a prominent traffic-light progress bar, the current-month spend,
/// and big amount typography. All figures are derived from the live expense set
/// via [BudgetTotals] — never a stale server rollup.
class BudgetSummaryCard extends StatelessWidget {
  const BudgetSummaryCard({super.key, required this.totals});

  final BudgetTotals totals;

  @override
  Widget build(BuildContext context) {
    final currency = totals.currency;
    final overBudget = totals.overBudget;

    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.lg,
        AppSpacing.lg,
        AppSpacing.sm,
      ),
      child: LzCard(
        gradient: AppColors.accentGradient,
        padding: const EdgeInsets.all(AppSpacing.xl),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  Icons.account_balance_wallet_outlined,
                  color: AppColors.onAccent.withValues(alpha: 0.85),
                  size: 16,
                ),
                const SizedBox(width: AppSpacing.sm),
                Text(
                  'TOTAL SPENT',
                  style: AppText.caption.copyWith(
                    color: AppColors.onAccent.withValues(alpha: 0.85),
                    fontWeight: FontWeight.w700,
                    letterSpacing: 1.0,
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.md),
            // Big spend number + budget line, baseline-aligned so the figure
            // dominates and the "of X budget" reads as a quiet caption beside it.
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Flexible(
                  child: Text(
                    fmtMoney(currency, totals.totalSpent),
                    style: AppText.display.copyWith(
                      color: AppColors.onAccent,
                      fontWeight: FontWeight.w800,
                      letterSpacing: -1,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (totals.hasBudget) ...[
                  const SizedBox(width: AppSpacing.sm),
                  Text(
                    '/ ${fmtMoney(currency, totals.totalBudget)}',
                    style: AppText.body.copyWith(
                      color: AppColors.onAccent.withValues(alpha: 0.7),
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ],
              ],
            ),
            if (!totals.hasBudget) ...[
              const SizedBox(height: AppSpacing.xs),
              Text(
                'No budget set',
                style: AppText.body.copyWith(
                  color: AppColors.onAccent.withValues(alpha: 0.6),
                ),
              ),
            ],
            if (totals.hasBudget) ...[
              const SizedBox(height: AppSpacing.lg),
              // Progress bar on the accent gradient — white-ish track + fill,
              // flips to error red when over budget.
              ClipRRect(
                borderRadius: AppRadii.rPill,
                child: LzProgressBar(
                  value: totals.fraction,
                  height: 8,
                  trafficLight: false,
                  color: overBudget
                      ? AppColors.error
                      : AppColors.onAccent.withValues(alpha: 0.92),
                  trackColor: AppColors.onAccent.withValues(alpha: 0.18),
                ),
              ),
              const SizedBox(height: AppSpacing.md),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    overBudget ? 'Over budget' : '${totals.percentUsed}% used',
                    style: AppText.label.copyWith(
                      color: overBudget
                          ? AppColors.error
                          : AppColors.onAccent,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  Text(
                    overBudget
                        ? '${fmtMoney(currency, totals.totalSpent - totals.totalBudget)} over'
                        : '${fmtMoney(currency, totals.remaining)} left',
                    style: AppText.label.copyWith(
                      color: overBudget
                          ? AppColors.error
                          : AppColors.onAccent.withValues(alpha: 0.85),
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ],
              ),
            ],
            const SizedBox(height: AppSpacing.lg),
            // Hairline divider before the footer so the this-month figure reads
            // as a distinct secondary stat, not part of the budget block.
            Container(
              height: 1,
              color: AppColors.onAccent.withValues(alpha: 0.12),
            ),
            const SizedBox(height: AppSpacing.md),
            // Footer: real this-month spend + (optional) other-currency note.
            Row(
              children: [
                Icon(
                  Icons.event_note_outlined,
                  size: 14,
                  color: AppColors.onAccent.withValues(alpha: 0.75),
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  'This month',
                  style: AppText.caption.copyWith(
                    color: AppColors.onAccent.withValues(alpha: 0.75),
                  ),
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  fmtMoney(currency, totals.monthSpent),
                  style: AppText.caption.copyWith(
                    color: AppColors.onAccent,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                if (totals.multiCurrency) ...[
                  const Spacer(),
                  Text(
                    '+${totals.otherCurrencyCount} other '
                    '${totals.otherCurrencyCount == 1 ? 'currency' : 'currencies'}',
                    style: AppText.caption.copyWith(
                      color: AppColors.onAccent.withValues(alpha: 0.6),
                    ),
                  ),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }
}
