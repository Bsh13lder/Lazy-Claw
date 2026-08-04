/// The quiet "your work is safe" signal on an auto-saving edit sheet.
///
/// WHY it is not optional: once a sheet persists on its own, the Save button
/// stops being the thing that makes an edit real — and a surface that writes
/// silently is indistinguishable from one that does nothing. This is the only
/// feedback the user gets, so it must be legible at a glance and must never be
/// mistaken for a button (no fill, no border, no tap target) — the sheet
/// already has exactly one submit affordance and a second one competing with
/// it would be worse than no indicator at all.
///
/// Deliberately NOT in `lib/ui/` (the shared kit): it is meaningful only
/// alongside an [AutosaveController], and the kit stays free of feature
/// coupling. It consumes kit TOKENS only — no literal colour, size or radius.
library;

import 'package:flutter/material.dart';

import '../core/autosave.dart';
import '../ui/ui.dart';

/// Applied to whatever this renders (including the empty state) so a test can
/// assert "the indicator is showing nothing" as easily as "it says Saved".
const Key kAutosaveIndicatorKey = Key('autosave-indicator');

/// The user-facing wording, exported so tests assert against the constant
/// rather than a string literal that a copy edit would silently orphan.
const String kAutosavePendingLabel = 'Unsaved…';
const String kAutosaveSavingLabel = 'Saving…';
const String kAutosaveSavedLabel = 'Saved';
const String kAutosaveBlockedLabel = 'Not saved';
const String kAutosaveFailedLabel = 'Save failed';

class AutosaveIndicator extends StatelessWidget {
  const AutosaveIndicator({super.key, required this.status});

  final AutosaveStatus status;

  @override
  Widget build(BuildContext context) {
    // Idle renders nothing at all: before the first edit there is no state to
    // report, and a permanent "Saved" on an untouched sheet is noise that
    // trains the user to stop reading the one label that matters.
    if (status == AutosaveStatus.idle) {
      return const SizedBox.shrink(key: kAutosaveIndicatorKey);
    }

    final (label, color) = switch (status) {
      AutosaveStatus.idle => (kAutosaveSavedLabel, AppColors.textMuted),
      AutosaveStatus.pending => (kAutosavePendingLabel, AppColors.textMuted),
      AutosaveStatus.saving => (kAutosaveSavingLabel, AppColors.textMuted),
      AutosaveStatus.saved => (kAutosaveSavedLabel, AppColors.success),
      AutosaveStatus.blocked => (kAutosaveBlockedLabel, AppColors.warn),
      AutosaveStatus.failed => (kAutosaveFailedLabel, AppColors.error),
    };

    return Row(
      key: kAutosaveIndicatorKey,
      mainAxisSize: MainAxisSize.min,
      children: [
        _Glyph(status: status, color: color),
        const SizedBox(width: AppSpacing.xs),
        // Animated so a saved→pending→saved cycle reads as one settling
        // motion instead of three flashes of unrelated text.
        AnimatedDefaultTextStyle(
          duration: AppMotion.fast,
          style: AppText.caption.copyWith(color: color),
          child: Text(label),
        ),
      ],
    );
  }
}

/// The leading mark: a spinner while writing, a dot otherwise. Sized off the
/// spacing scale so it stays on grid with the caption beside it.
class _Glyph extends StatelessWidget {
  const _Glyph({required this.status, required this.color});

  final AutosaveStatus status;
  final Color color;

  static const double _size = AppSpacing.md;

  @override
  Widget build(BuildContext context) {
    if (status == AutosaveStatus.saving) {
      return SizedBox(
        width: _size,
        height: _size,
        child: CircularProgressIndicator(strokeWidth: AppSpacing.xs / 2, color: color),
      );
    }
    return Icon(
      switch (status) {
        AutosaveStatus.saved => Icons.cloud_done_outlined,
        AutosaveStatus.blocked || AutosaveStatus.failed =>
          Icons.error_outline_rounded,
        _ => Icons.cloud_upload_outlined,
      },
      size: AppSpacing.lg,
      color: color,
    );
  }
}
