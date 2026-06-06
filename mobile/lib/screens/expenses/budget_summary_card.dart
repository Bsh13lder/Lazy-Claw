import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// Hero budget summary card: this-month total spend vs total budget,
/// a prominent traffic-light [LzProgressBar], and big amount typography.
class BudgetSummaryCard extends StatelessWidget {
  const BudgetSummaryCard({
    super.key,
    required this.totalSpent,
    required this.totalBudget,
    required this.currency,
  });

  final double totalSpent;
  final double totalBudget;
  final String currency;

  @override
  Widget build(BuildContext context) {
    final ratio = totalBudget > 0
        ? (totalSpent / totalBudget).clamp(0.0, 1.0)
        : 0.0;
    final overBudget = totalBudget > 0 && totalSpent > totalBudget;
    final pct = totalBudget > 0 ? (ratio * 100).round() : 0;

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
                  'This Month',
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
              _fmtMoney(currency, totalSpent),
              style: AppText.display.copyWith(
                color: AppColors.onAccent,
                fontWeight: FontWeight.w800,
                letterSpacing: -1,
              ),
            ),
            if (totalBudget > 0) ...[
              const SizedBox(height: AppSpacing.xs),
              Text(
                'of ${_fmtMoney(currency, totalBudget)} budget',
                style: AppText.body.copyWith(
                  color: AppColors.onAccent.withValues(alpha: 0.7),
                ),
              ),
            ] else ...[
              const SizedBox(height: AppSpacing.xs),
              Text(
                'No budget set',
                style: AppText.body.copyWith(
                  color: AppColors.onAccent.withValues(alpha: 0.6),
                ),
              ),
            ],
            const SizedBox(height: AppSpacing.lg),
            // Traffic-light progress bar on accent gradient — use white track.
            ClipRRect(
              borderRadius: AppRadii.rPill,
              child: LzProgressBar(
                value: ratio,
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
                  overBudget ? 'Over budget!' : '$pct% used',
                  style: AppText.caption.copyWith(
                    color: overBudget
                        ? AppColors.error
                        : AppColors.onAccent.withValues(alpha: 0.75),
                    fontWeight: FontWeight.w700,
                  ),
                ),
                if (totalBudget > 0 && !overBudget)
                  Text(
                    '${_fmtMoney(currency, totalBudget - totalSpent)} left',
                    style: AppText.caption.copyWith(
                      color: AppColors.onAccent.withValues(alpha: 0.7),
                    ),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  String _fmtMoney(String currency, double v) {
    final sym = currency == 'USD'
        ? '\$'
        : currency == 'EUR'
            ? '€'
            : currency == 'GBP'
                ? '£'
                : '$currency ';
    if (v == v.truncateToDouble()) return '$sym${v.toInt()}';
    return '$sym${v.toStringAsFixed(2)}';
  }
}
