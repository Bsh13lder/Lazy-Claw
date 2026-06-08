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
                  color: AppColors.onAccent.withValues(alpha: 0.8),
                  size: 18,
                ),
                const SizedBox(width: AppSpacing.sm),
                Text(
                  'Total Spent',
                  style: AppText.caption.copyWith(
                    color: AppColors.onAccent.withValues(alpha: 0.8),
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.6,
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.md),
            // Big spend number.
            Text(
              fmtMoney(currency, totals.totalSpent),
              style: AppText.display.copyWith(
                color: AppColors.onAccent,
                fontWeight: FontWeight.w800,
                letterSpacing: -1,
              ),
            ),
            const SizedBox(height: AppSpacing.xs),
            Text(
              totals.hasBudget
                  ? 'of ${fmtMoney(currency, totals.totalBudget)} budget'
                  : 'No budget set',
              style: AppText.body.copyWith(
                color: AppColors.onAccent
                    .withValues(alpha: totals.hasBudget ? 0.7 : 0.6),
              ),
            ),
            if (totals.hasBudget) ...[
              const SizedBox(height: AppSpacing.lg),
              // Traffic-light progress bar on accent gradient — white-ish track.
              ClipRRect(
                borderRadius: AppRadii.rPill,
                child: LzProgressBar(
                  value: totals.fraction,
                  height: 10,
                  trafficLight: false,
                  color: overBudget
                      ? AppColors.error
                      : AppColors.onAccent.withValues(alpha: 0.9),
                  trackColor: AppColors.onAccent.withValues(alpha: 0.2),
                ),
              ),
              const SizedBox(height: AppSpacing.sm),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    overBudget ? 'Over budget!' : '${totals.percentUsed}% used',
                    style: AppText.caption.copyWith(
                      color: overBudget
                          ? AppColors.error
                          : AppColors.onAccent.withValues(alpha: 0.75),
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  Text(
                    overBudget
                        ? '${fmtMoney(currency, totals.totalSpent - totals.totalBudget)} over'
                        : '${fmtMoney(currency, totals.remaining)} left',
                    style: AppText.caption.copyWith(
                      color: overBudget
                          ? AppColors.error
                          : AppColors.onAccent.withValues(alpha: 0.7),
                    ),
                  ),
                ],
              ),
            ],
            const SizedBox(height: AppSpacing.lg),
            // Footer: real this-month spend + (optional) other-currency note.
            Row(
              children: [
                Icon(
                  Icons.event_note_outlined,
                  size: 14,
                  color: AppColors.onAccent.withValues(alpha: 0.7),
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  'This month ${fmtMoney(currency, totals.monthSpent)}',
                  style: AppText.caption.copyWith(
                    color: AppColors.onAccent.withValues(alpha: 0.8),
                    fontWeight: FontWeight.w600,
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
