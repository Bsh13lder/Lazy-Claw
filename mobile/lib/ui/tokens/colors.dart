import 'package:flutter/widgets.dart';

/// Color tokens for the LazyClaw design system.
///
/// Dark, layered surfaces with the emerald-claw accent. These are the single
/// source of truth for every color in the app — no screen hard-codes a
/// [Color]; everything references a token here (or a [ThemeData] value derived
/// from one). Values are kept in lock-step with the LazyClaw **web** palette
/// (`web/src/styles/globals.css` `@theme`) so mobile and web read as the same
/// product.
///
/// Pure constants — no widget dependencies beyond [Color].
abstract final class AppColors {
  AppColors._();

  // ── Surfaces (deepest → raised) ──────────────────────────────────────────
  /// App background. Web `--color-bg-primary`.
  static const Color bgBase = Color(0xFF0D0D0D);

  /// Cards, sheets. Web `--color-bg-secondary`.
  static const Color bgSurface = Color(0xFF171717);

  /// Raised cards, app bar. Web `--color-bg-tertiary`.
  static const Color bgSurfaceElevated = Color(0xFF212121);

  /// Pressed / hover surface. Web `--color-bg-hover`.
  static const Color bgSurfaceHover = Color(0xFF2A2A2A);

  // ── Borders (white at low opacity) ───────────────────────────────────────
  /// Hairlines (white @ 6%). Matches the web glass-card border.
  static const Color borderSubtle = Color(0x0FFFFFFF);

  /// Dividers, input borders (white @ 10%). Matches the web glass-card hover
  /// border; reads as the web `--color-border` (#2f2f2f) over a surface.
  static const Color borderDefault = Color(0x1AFFFFFF);

  // ── Text ─────────────────────────────────────────────────────────────────
  /// Headings / body. Web `--color-text-primary`.
  static const Color textPrimary = Color(0xFFECECEC);

  /// Secondary text. Web `--color-text-secondary`.
  static const Color textSecondary = Color(0xFFB4B4B4);

  /// Captions, placeholders. Web `--color-text-muted`.
  static const Color textMuted = Color(0xFF767676);

  // ── Accent (emerald claw) ────────────────────────────────────────────────
  /// Primary actions, active state. Web `--color-accent`.
  static const Color accent = Color(0xFF10B981);

  /// Pressed accent. Web `--color-accent-dim`.
  static const Color accentHover = Color(0xFF059669);

  /// Start of the 135° claw gradient. Web `--color-accent`.
  static const Color accentGradientStart = Color(0xFF10B981);

  /// End of the 135° claw gradient. Web `--color-accent-dim`.
  static const Color accentGradientEnd = Color(0xFF059669);

  /// Text / icons placed on top of the emerald accent. Web puts
  /// `text-bg-primary` (#0d0d0d) on accent surfaces.
  static const Color onAccent = Color(0xFF0D0D0D);

  // ── Semantic ─────────────────────────────────────────────────────────────
  /// Web traffic-light "good" (`bg-emerald-400`) / tip-callout ring (#34d399).
  static const Color success = Color(0xFF34D399);

  /// Web `--color-amber` (traffic-light "warning").
  static const Color warn = Color(0xFFF59E0B);

  /// Web `--color-error` (traffic-light "over").
  static const Color error = Color(0xFFEF4444);

  /// Web `--color-cyan` (info / links).
  static const Color info = Color(0xFF06B6D4);

  // ── Gradients ────────────────────────────────────────────────────────────
  /// The emerald-claw hero gradient (135°, top-left → bottom-right).
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
