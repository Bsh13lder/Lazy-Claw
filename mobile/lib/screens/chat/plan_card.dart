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
    this.kind = 'plan',
    this.resolved = false,
  });

  final String planText;
  final List<String> steps;

  /// 'plan' (approve/reject gate) or 'question' (the agent needs one piece
  /// of info — the user answers with a normal chat message, so no buttons).
  final String kind;

  /// True once the server confirmed approval — buttons are replaced by an
  /// "Approved" badge so they can't be re-tapped.
  final bool resolved;

  /// Sends a plain-text message via the chat controller notifier.
  /// Called with 'approve' or 'reject'.
  final void Function(String) onSend;

  bool get _isQuestion => kind == 'question';

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
                      child: Icon(
                        _isQuestion
                            ? Icons.help_outline
                            : Icons.assignment_outlined,
                        size: 14,
                        color: AppColors.accent,
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    Text(
                      _isQuestion ? 'Agent Question' : 'Agent Plan',
                      style: AppText.label.copyWith(color: AppColors.accent),
                    ),
                    if (resolved) ...[
                      const SizedBox(width: AppSpacing.sm),
                      LzBadge(label: 'Approved', color: AppColors.success),
                    ],
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

                // Footer — approve/reject for plans; questions are answered
                // with a normal chat message, resolved plans show no buttons.
                if (!_isQuestion && !resolved) ...[
                  Padding(
                    padding:
                        const EdgeInsets.symmetric(vertical: AppSpacing.md),
                    child: Divider(
                      color: AppColors.borderSubtle,
                      height: 1,
                      thickness: 1,
                    ),
                  ),
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
                if (_isQuestion) ...[
                  const SizedBox(height: AppSpacing.md),
                  Text(
                    'Reply below to answer.',
                    style:
                        AppText.caption.copyWith(color: AppColors.textMuted),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
