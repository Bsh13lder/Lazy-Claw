import 'package:flutter/widgets.dart';

/// Color tokens for the LazyClaw design system.
///
/// Dark, layered surfaces with the amber-claw accent. These are the single
/// source of truth for every color in the app — no screen hard-codes a
/// [Color]; everything references a token here (or a [ThemeData] value derived
/// from one). Values come straight from the design spec (§1.1).
///
/// Pure constants — no widget dependencies beyond [Color].
abstract final class AppColors {
  AppColors._();

  // ── Surfaces (deepest → raised) ──────────────────────────────────────────
  /// App background.
  static const Color bgBase = Color(0xFF0B0B12);

  /// Cards, sheets.
  static const Color bgSurface = Color(0xFF14141F);

  /// Raised cards, app bar.
  static const Color bgSurfaceElevated = Color(0xFF1C1C2B);

  /// Pressed / hover surface.
  static const Color bgSurfaceHover = Color(0xFF24243A);

  // ── Borders (white at low opacity) ───────────────────────────────────────
  /// Hairlines (white @ 6%).
  static const Color borderSubtle = Color(0x0FFFFFFF);

  /// Dividers, input borders (white @ 10%).
  static const Color borderDefault = Color(0x1AFFFFFF);

  // ── Text ─────────────────────────────────────────────────────────────────
  /// Headings / body.
  static const Color textPrimary = Color(0xFFF5F5FA);

  /// Secondary text.
  static const Color textSecondary = Color(0xFFA8A8B8);

  /// Captions, placeholders.
  static const Color textMuted = Color(0xFF6E6E80);

  // ── Accent (amber claw) ──────────────────────────────────────────────────
  /// Primary actions, active state.
  static const Color accent = Color(0xFFFF8A3D);

  /// Pressed accent.
  static const Color accentHover = Color(0xFFFF9F5C);

  /// Start of the 135° claw gradient.
  static const Color accentGradientStart = Color(0xFFFF9F45);

  /// End of the 135° claw gradient.
  static const Color accentGradientEnd = Color(0xFFFF6B35);

  /// Text / icons placed on top of the amber accent.
  static const Color onAccent = Color(0xFF1A1208);

  // ── Semantic ─────────────────────────────────────────────────────────────
  static const Color success = Color(0xFF3DD68C);
  static const Color warn = Color(0xFFFFB020);
  static const Color error = Color(0xFFFF5C5C);
  static const Color info = Color(0xFF5AA9FF);

  // ── Gradients ────────────────────────────────────────────────────────────
  /// The amber-claw hero gradient (135°, top-left → bottom-right).
  static const Gradient accentGradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [accentGradientStart, accentGradientEnd],
  );

  /// Returns the traffic-light color for a 0..1 usage [ratio]
  /// (<70% success, 70–90% warn, ≥90% error). Used by budget/progress bars.
  static Color trafficLight(double ratio) {
    if (ratio >= 0.9) return error;
    if (ratio >= 0.7) return warn;
    return success;
  }
}
