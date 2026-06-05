import 'package:flutter/widgets.dart';

/// 4-pt spacing scale for the LazyClaw design system (spec §1.3).
///
/// Every padding, gap, and inset references one of these constants so layout
/// rhythm stays consistent across screens. Pure constants.
abstract final class AppSpacing {
  AppSpacing._();

  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double xxl = 32;
  static const double xxxl = 48;

  // ── Convenience EdgeInsets (common cases) ────────────────────────────────
  /// Standard screen edge padding (horizontal [lg], vertical [md]).
  static const EdgeInsets screen =
      EdgeInsets.symmetric(horizontal: lg, vertical: md);

  /// Default card content padding.
  static const EdgeInsets card = EdgeInsets.all(lg);

  /// Horizontal-only list padding.
  static const EdgeInsets listH = EdgeInsets.symmetric(horizontal: lg);

  /// A zero-height SizedBox of the given vertical [gap].
  static SizedBox vGap(double gap) => SizedBox(height: gap);

  /// A zero-width SizedBox of the given horizontal [gap].
  static SizedBox hGap(double gap) => SizedBox(width: gap);
}
