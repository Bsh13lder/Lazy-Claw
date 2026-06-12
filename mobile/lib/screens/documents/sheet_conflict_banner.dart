/// Conflict-resolution banner shown by sheet / doc editors when a server-side
/// optimistic-concurrency check (409) detects that another client saved while
/// this client had the document open.
///
/// Offers two resolutions:
///   • **Reload** — adopt the server version (the user's pending edits go onto
///     the undo stack so they can get them back via Ctrl-Z / ⌘Z).
///   • **Keep mine** — re-save the local version with LWW semantics (sends
///     `base_updated_at: null`), bypassing the CAS check.
library;

import 'package:flutter/material.dart';

import '../../ui/ui.dart';

/// A full-width amber banner that sits above the editor toolbar when a
/// save operation raises a [DocConflictException].
///
/// Parameters [onReload] and [onKeepMine] must not be null — the banner is
/// only rendered when a conflict is active.
class SheetConflictBanner extends StatelessWidget {
  const SheetConflictBanner({
    super.key,
    required this.onReload,
    required this.onKeepMine,
    this.label = 'Sheet changed on the server.',
  });

  /// "Reload" action: adopt the server version.
  final VoidCallback onReload;

  /// "Keep mine" action: force-save local version (LWW).
  final VoidCallback onKeepMine;

  /// Message text — override for the doc editor.
  final String label;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.warn.withValues(alpha: 0.14),
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.sm,
        ),
        child: Row(
          children: [
            const Icon(Icons.sync_problem_outlined,
                color: AppColors.warn, size: 18),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: Text(
                label,
                style: AppText.caption.copyWith(
                  color: AppColors.warn,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            _Action(
              label: 'Reload',
              onTap: onReload,
            ),
            const SizedBox(width: AppSpacing.xs),
            _Action(
              label: 'Keep mine',
              onTap: onKeepMine,
            ),
          ],
        ),
      ),
    );
  }
}

// ── Internal action button ────────────────────────────────────────────────────

class _Action extends StatelessWidget {
  const _Action({required this.label, required this.onTap});

  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: AppRadii.rSm,
      child: ConstrainedBox(
        constraints: const BoxConstraints(minHeight: 44),
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.sm,
            vertical: AppSpacing.xs,
          ),
          child: Center(
            widthFactor: 1,
            child: Text(
              label,
              style: AppText.caption.copyWith(
                color: AppColors.warn,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ),
      ),
    );
  }
}
