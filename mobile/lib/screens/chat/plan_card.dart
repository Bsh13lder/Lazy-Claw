/// Agent Plan card for the Chat screen.
///
/// Rendered for messages with role == 'plan'. Built with [LzCard] and
/// [LzButton] — keeps the exact Approve/Reject callbacks from the original
/// implementation (they send the string via the chat controller notifier).
library;

import 'package:flutter/material.dart';
import '../../ui/ui.dart';

class PlanCard extends StatelessWidget {
  const PlanCard({
    super.key,
    required this.planText,
    required this.steps,
    required this.onSend,
  });

  final String planText;
  final List<String> steps;

  /// Sends a plain-text message via the chat controller notifier.
  /// Called with 'approve' or 'reject'.
  final void Function(String) onSend;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
        child: ConstrainedBox(
          constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.92,
          ),
          child: LzCard(
            padding: const EdgeInsets.all(AppSpacing.lg),
            color: AppColors.bgSurface,
            borderColor: AppColors.accent.withValues(alpha: 0.25),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Header
                Row(
                  children: [
                    Container(
                      width: 28,
                      height: 28,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        color: AppColors.accent.withValues(alpha: 0.14),
                        shape: BoxShape.circle,
                        border: Border.all(
                          color: AppColors.accent.withValues(alpha: 0.25),
                        ),
                      ),
                      child: const Icon(
                        Icons.assignment_outlined,
                        size: 14,
                        color: AppColors.accent,
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    Text(
                      'Agent Plan',
                      style: AppText.label.copyWith(color: AppColors.accent),
                    ),
                  ],
                ),
                const SizedBox(height: AppSpacing.md),

                // Plan description
                Text(
                  planText,
                  style: AppText.body.copyWith(color: AppColors.textSecondary),
                ),

                // Step list
                if (steps.isNotEmpty) ...[
                  const SizedBox(height: AppSpacing.md),
                  ...steps.asMap().entries.map(
                        (e) => Padding(
                          padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Container(
                                width: 20,
                                height: 20,
                                alignment: Alignment.center,
                                decoration: BoxDecoration(
                                  color: AppColors.bgSurfaceElevated,
                                  shape: BoxShape.circle,
                                  border: Border.all(
                                    color: AppColors.borderDefault,
                                  ),
                                ),
                                child: Text(
                                  '${e.key + 1}',
                                  style: AppText.caption.copyWith(
                                    color: AppColors.accent,
                                    fontWeight: FontWeight.w700,
                                    fontSize: 10,
                                  ),
                                ),
                              ),
                              const SizedBox(width: AppSpacing.sm),
                              Expanded(
                                child: Text(
                                  e.value,
                                  style: AppText.body.copyWith(
                                    color: AppColors.textPrimary,
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                ],

                // Divider
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: AppSpacing.md),
                  child: Divider(
                    color: AppColors.borderSubtle,
                    height: 1,
                    thickness: 1,
                  ),
                ),

                // Action buttons — callbacks preserved exactly
                Row(
                  children: [
                    LzButton.secondary(
                      label: 'Reject',
                      onPressed: () => onSend('reject'),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    LzButton.primary(
                      label: 'Approve',
                      onPressed: () => onSend('approve'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
