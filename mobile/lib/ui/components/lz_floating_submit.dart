import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// A compact square submit button that floats over the bottom-right corner of
/// a form.
///
/// WHY this exists: a full-width primary button placed at the END of a tall
/// sheet column is anchored to the CONTENT. As soon as the form grows (a
/// suggestion strip opens, an extra section expands, the keyboard comes up)
/// the button is pushed past the sheet's visible bottom edge and clipped —
/// which users read as "there is no save button, it just isn't adding".
/// Anchoring the affordance to the sheet's VIEWPORT instead (see
/// [LzFloatingSubmitLayout]) keeps it on screen and tappable no matter how
/// long the form gets.
///
/// It carries no visible label, so [tooltip] is REQUIRED — it is both the
/// long-press tooltip and the screen-reader name.
///
/// Used by the Add Expense sheet, the Add Task sheet, and the Task detail
/// sheet, so keep the API generic: caller supplies the [icon] + [tooltip].
class LzFloatingSubmit extends StatelessWidget {
  const LzFloatingSubmit({
    super.key,
    required this.onPressed,
    required this.tooltip,
    this.icon = Icons.check_rounded,
    this.loading = false,
  });

  /// Null disables the button. Callers should ALSO pass `loading: true` while
  /// a submit is in flight so the spinner shows.
  final VoidCallback? onPressed;

  /// Accessible name (and tooltip). Required because the button shows no text
  /// — this is the only thing a screen reader can announce.
  final String tooltip;

  /// The glyph. Defaults to a check; use `Icons.add_rounded` for "create"
  /// surfaces, `Icons.check_rounded` for "save" surfaces.
  final IconData icon;

  /// Swaps the glyph for a spinner and hard-blocks taps (independently of
  /// [onPressed], so a caller can't accidentally leave it live mid-submit).
  final bool loading;

  /// The button's edge length. Expressed off the 4-pt spacing scale rather
  /// than as a magic number so it stays on grid (48 + 8 = 56).
  static const double size = AppSpacing.xxxl + AppSpacing.sm;

  /// Bottom padding a scrolling form should reserve so its LAST field can
  /// always be scrolled clear of the floating button. [LzFloatingSubmitLayout]
  /// applies this for you.
  static const double reservedGap = size + AppSpacing.lg;

  /// Glyph / spinner metrics — kept here (not inline) so both branches of the
  /// child stay in sync if the button size ever changes.
  static const double _iconSize = 24;
  static const double _spinnerSize = 20;

  @override
  Widget build(BuildContext context) {
    final enabled = onPressed != null && !loading;

    return Semantics(
      button: true,
      enabled: enabled,
      label: tooltip,
      child: Tooltip(
        message: tooltip,
        // The shadow lives on an OUTER decoration: a BoxDecoration painted on
        // a descendant of the Material would draw its shadow on top of the
        // accent fill instead of behind the button.
        child: Container(
          decoration: const BoxDecoration(
            borderRadius: AppRadii.rLg,
            boxShadow: AppElevation.floating,
          ),
          child: AnimatedOpacity(
            duration: AppMotion.fast,
            opacity: enabled ? 1 : 0.45,
            child: Material(
              color: AppColors.accent,
              borderRadius: AppRadii.rLg,
              child: InkWell(
                onTap: enabled ? onPressed : null,
                borderRadius: AppRadii.rLg,
                splashColor: AppColors.onAccent.withValues(alpha: 0.10),
                child: SizedBox(
                  width: size,
                  height: size,
                  child: Center(
                    child: loading
                        ? const SizedBox(
                            width: _spinnerSize,
                            height: _spinnerSize,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: AppColors.onAccent,
                            ),
                          )
                        : Icon(
                            icon,
                            size: _iconSize,
                            color: AppColors.onAccent,
                          ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Composes a form [child] with an [LzFloatingSubmit] pinned to the
/// bottom-right of the visible area.
///
/// The [Stack]'s only NON-positioned child is the scroll view, so the stack
/// shrink-wraps it: inside a bottom sheet's `Flexible` the sheet still sizes
/// to its content when the form is short, and caps + scrolls when the form is
/// taller than the space left over the keyboard. That is exactly what makes
/// the button track the sheet's bottom EDGE (which [LzBottomSheet]'s shell
/// already keeps above the keyboard) rather than the end of the content.
///
/// Do NOT wrap the result in another vertical scroll view — nesting viewports
/// gives this one unbounded height and throws.
class LzFloatingSubmitLayout extends StatelessWidget {
  const LzFloatingSubmitLayout({
    super.key,
    required this.child,
    required this.submit,
    this.controller,
  });

  /// The form. Reserved bottom padding is added for you.
  final Widget child;

  final LzFloatingSubmit submit;

  /// Optional controller for the internal scroll view (e.g. to scroll a newly
  /// revealed section into view).
  final ScrollController? controller;

  @override
  Widget build(BuildContext context) {
    return Stack(
      // The button's drop shadow bleeds a few pixels past the stack bounds;
      // the scroll view clips its own content, so nothing else can escape.
      clipBehavior: Clip.none,
      children: [
        SingleChildScrollView(
          controller: controller,
          padding: const EdgeInsets.only(bottom: LzFloatingSubmit.reservedGap),
          child: child,
        ),
        Positioned(right: 0, bottom: 0, child: submit),
      ],
    );
  }
}
