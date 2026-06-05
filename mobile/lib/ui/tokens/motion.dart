import 'package:flutter/animation.dart';

/// Motion tokens for the LazyClaw design system (spec §1.3).
///
/// Durations + the house curve. Pure constants — animations reference these so
/// timing feels uniform (fade-through page transitions, list-insert slides,
/// shimmer, success scale).
abstract final class AppMotion {
  AppMotion._();

  /// Quick feedback (taps, small state changes).
  static const Duration fast = Duration(milliseconds: 150);

  /// Default transition (most animated state).
  static const Duration base = Duration(milliseconds: 220);

  /// Emphasized, larger movements (sheets, page transitions).
  static const Duration emphasized = Duration(milliseconds: 320);

  /// One full shimmer sweep for skeleton loaders.
  static const Duration shimmer = Duration(milliseconds: 1400);

  /// House easing curve.
  static const Curve curve = Curves.easeOutCubic;

  /// Emphasized in-and-out curve (used for entrances).
  static const Curve curveEmphasized = Curves.easeOutCubic;
}
