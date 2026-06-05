import 'package:flutter/widgets.dart';

/// Corner-radius scale for the LazyClaw design system (spec §1.3).
///
/// Pure constants. [BorderRadius] helpers cover the common cases so widgets
/// never construct radii ad-hoc.
abstract final class AppRadii {
  AppRadii._();

  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 20;

  /// Fully-rounded (pills, avatars, dots).
  static const double pill = 999;

  static const BorderRadius rSm = BorderRadius.all(Radius.circular(sm));
  static const BorderRadius rMd = BorderRadius.all(Radius.circular(md));
  static const BorderRadius rLg = BorderRadius.all(Radius.circular(lg));
  static const BorderRadius rXl = BorderRadius.all(Radius.circular(xl));
  static const BorderRadius rPill = BorderRadius.all(Radius.circular(pill));
}
