import 'package:flutter/widgets.dart';
import 'colors.dart';

/// Typography tokens for the LazyClaw design system (spec §1.2).
///
/// Uses **Inter** (bundled as a variable .ttf — see [fontFamily]). The scale
/// below is the single source of truth for every [TextStyle] in the app; line
/// heights are ~1.2 for headings and ~1.35 for body. All colors come from
/// [AppColors].
///
/// Pure constants. Use [AppText.style.copyWith(color: ...)] when a one-off
/// color override is genuinely needed (still a token color).
abstract final class AppText {
  AppText._();

  /// The bundled Inter family name (registered in `pubspec.yaml`).
  static const String fontFamily = 'Inter';

  // ── Display / headings ───────────────────────────────────────────────────
  static const TextStyle display = TextStyle(
    fontFamily: fontFamily,
    fontSize: 30,
    fontWeight: FontWeight.w700,
    letterSpacing: -0.5,
    height: 1.2,
    color: AppColors.textPrimary,
  );

  static const TextStyle headline = TextStyle(
    fontFamily: fontFamily,
    fontSize: 24,
    fontWeight: FontWeight.w700,
    letterSpacing: -0.3,
    height: 1.2,
    color: AppColors.textPrimary,
  );

  static const TextStyle titleL = TextStyle(
    fontFamily: fontFamily,
    fontSize: 20,
    fontWeight: FontWeight.w600,
    height: 1.25,
    color: AppColors.textPrimary,
  );

  static const TextStyle title = TextStyle(
    fontFamily: fontFamily,
    fontSize: 17,
    fontWeight: FontWeight.w600,
    height: 1.3,
    color: AppColors.textPrimary,
  );

  // ── Body ─────────────────────────────────────────────────────────────────
  static const TextStyle bodyL = TextStyle(
    fontFamily: fontFamily,
    fontSize: 16,
    fontWeight: FontWeight.w400,
    height: 1.35,
    color: AppColors.textPrimary,
  );

  static const TextStyle body = TextStyle(
    fontFamily: fontFamily,
    fontSize: 14,
    fontWeight: FontWeight.w400,
    letterSpacing: 0.1,
    height: 1.35,
    color: AppColors.textPrimary,
  );

  // ── Supporting ───────────────────────────────────────────────────────────
  static const TextStyle label = TextStyle(
    fontFamily: fontFamily,
    fontSize: 13,
    fontWeight: FontWeight.w600,
    height: 1.3,
    color: AppColors.textPrimary,
  );

  static const TextStyle caption = TextStyle(
    fontFamily: fontFamily,
    fontSize: 11.5,
    fontWeight: FontWeight.w500,
    letterSpacing: 0.3,
    height: 1.3,
    color: AppColors.textSecondary,
  );
}
