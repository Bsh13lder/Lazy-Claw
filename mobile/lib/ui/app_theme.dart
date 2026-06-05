import 'package:flutter/material.dart';
import 'tokens/tokens.dart';

/// Builds the single, token-driven Material-3 **dark** [ThemeData] for the app.
///
/// Every value here derives from a design-system token ([AppColors], [AppText],
/// [AppRadii], [AppSpacing]). Screens should prefer the `Lz*` components and
/// the tokens directly, but anything that falls back to raw Material widgets
/// (e.g. third-party widgets, [ScaffoldMessenger]) still lands on-brand because
/// of this theme.
ThemeData buildAppTheme() {
  const colorScheme = ColorScheme.dark(
    brightness: Brightness.dark,
    primary: AppColors.accent,
    onPrimary: AppColors.onAccent,
    secondary: AppColors.accent,
    onSecondary: AppColors.onAccent,
    surface: AppColors.bgSurface,
    onSurface: AppColors.textPrimary,
    surfaceContainerHighest: AppColors.bgSurfaceElevated,
    error: AppColors.error,
    onError: AppColors.onAccent,
    outline: AppColors.borderDefault,
    outlineVariant: AppColors.borderSubtle,
  );

  final textTheme = _buildTextTheme();

  return ThemeData(
    useMaterial3: true,
    brightness: Brightness.dark,
    fontFamily: AppText.fontFamily,
    colorScheme: colorScheme,
    scaffoldBackgroundColor: AppColors.bgBase,
    canvasColor: AppColors.bgBase,
    textTheme: textTheme,
    primaryTextTheme: textTheme,
    splashColor: AppColors.accent.withValues(alpha: 0.08),
    highlightColor: AppColors.bgSurfaceHover,
    dividerColor: AppColors.borderDefault,

    appBarTheme: const AppBarTheme(
      backgroundColor: AppColors.bgBase,
      foregroundColor: AppColors.textPrimary,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 0,
      centerTitle: false,
      titleTextStyle: AppText.titleL,
      iconTheme: IconThemeData(color: AppColors.textPrimary),
    ),

    cardTheme: const CardThemeData(
      color: AppColors.bgSurface,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: AppRadii.rLg,
        side: BorderSide(color: AppColors.borderSubtle, width: 1),
      ),
    ),

    dividerTheme: const DividerThemeData(
      color: AppColors.borderDefault,
      thickness: 1,
      space: 1,
    ),

    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: AppColors.bgSurfaceElevated,
      hintStyle: AppText.body.copyWith(color: AppColors.textMuted),
      labelStyle: AppText.label.copyWith(color: AppColors.textSecondary),
      contentPadding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      border: const OutlineInputBorder(
        borderRadius: AppRadii.rMd,
        borderSide: BorderSide(color: AppColors.borderDefault),
      ),
      enabledBorder: const OutlineInputBorder(
        borderRadius: AppRadii.rMd,
        borderSide: BorderSide(color: AppColors.borderDefault),
      ),
      focusedBorder: const OutlineInputBorder(
        borderRadius: AppRadii.rMd,
        borderSide: BorderSide(color: AppColors.accent, width: 1.5),
      ),
      errorBorder: const OutlineInputBorder(
        borderRadius: AppRadii.rMd,
        borderSide: BorderSide(color: AppColors.error),
      ),
      focusedErrorBorder: const OutlineInputBorder(
        borderRadius: AppRadii.rMd,
        borderSide: BorderSide(color: AppColors.error, width: 1.5),
      ),
    ),

    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: AppColors.accent,
        foregroundColor: AppColors.onAccent,
        elevation: 0,
        textStyle: AppText.label,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.xl,
          vertical: AppSpacing.md,
        ),
        shape: const RoundedRectangleBorder(borderRadius: AppRadii.rMd),
      ),
    ),

    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: AppColors.accent,
        foregroundColor: AppColors.onAccent,
        textStyle: AppText.label,
        shape: const RoundedRectangleBorder(borderRadius: AppRadii.rMd),
      ),
    ),

    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: AppColors.accent,
        textStyle: AppText.label,
      ),
    ),

    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: AppColors.textPrimary,
        side: const BorderSide(color: AppColors.borderDefault),
        textStyle: AppText.label,
        shape: const RoundedRectangleBorder(borderRadius: AppRadii.rMd),
      ),
    ),

    iconTheme: const IconThemeData(color: AppColors.textSecondary),

    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(foregroundColor: AppColors.textSecondary),
    ),

    chipTheme: ChipThemeData(
      backgroundColor: AppColors.bgSurfaceElevated,
      side: const BorderSide(color: AppColors.borderDefault),
      labelStyle: AppText.caption.copyWith(color: AppColors.textSecondary),
      shape: const RoundedRectangleBorder(borderRadius: AppRadii.rPill),
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.xs,
      ),
    ),

    floatingActionButtonTheme: const FloatingActionButtonThemeData(
      backgroundColor: AppColors.accent,
      foregroundColor: AppColors.onAccent,
      elevation: 0,
    ),

    progressIndicatorTheme: const ProgressIndicatorThemeData(
      color: AppColors.accent,
      linearTrackColor: AppColors.bgSurfaceHover,
      circularTrackColor: Colors.transparent,
    ),

    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected)
            ? AppColors.onAccent
            : AppColors.textSecondary,
      ),
      trackColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected)
            ? AppColors.accent
            : AppColors.bgSurfaceHover,
      ),
      trackOutlineColor: WidgetStateProperty.all(Colors.transparent),
    ),

    checkboxTheme: CheckboxThemeData(
      fillColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected)
            ? AppColors.accent
            : Colors.transparent,
      ),
      checkColor: WidgetStateProperty.all(AppColors.onAccent),
      side: const BorderSide(color: AppColors.borderDefault, width: 1.5),
      shape: const RoundedRectangleBorder(borderRadius: AppRadii.rSm),
    ),

    listTileTheme: const ListTileThemeData(
      iconColor: AppColors.textSecondary,
      textColor: AppColors.textPrimary,
      titleTextStyle: AppText.body,
      subtitleTextStyle: AppText.caption,
    ),

    dialogTheme: const DialogThemeData(
      backgroundColor: AppColors.bgSurfaceElevated,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(borderRadius: AppRadii.rXl),
      titleTextStyle: AppText.titleL,
      contentTextStyle: AppText.body,
    ),

    bottomSheetTheme: const BottomSheetThemeData(
      backgroundColor: AppColors.bgSurface,
      surfaceTintColor: Colors.transparent,
      modalBackgroundColor: AppColors.bgSurface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadii.xl)),
      ),
    ),

    snackBarTheme: SnackBarThemeData(
      backgroundColor: AppColors.bgSurfaceElevated,
      contentTextStyle: AppText.body,
      actionTextColor: AppColors.accent,
      behavior: SnackBarBehavior.floating,
      shape: const RoundedRectangleBorder(borderRadius: AppRadii.rMd),
    ),

    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: AppColors.bgSurfaceElevated,
      indicatorColor: AppColors.accent.withValues(alpha: 0.18),
      labelTextStyle: WidgetStateProperty.all(AppText.caption),
      iconTheme: WidgetStateProperty.resolveWith(
        (s) => IconThemeData(
          color: s.contains(WidgetState.selected)
              ? AppColors.accent
              : AppColors.textMuted,
        ),
      ),
    ),

    tooltipTheme: const TooltipThemeData(
      decoration: BoxDecoration(
        color: AppColors.bgSurfaceHover,
        borderRadius: AppRadii.rSm,
      ),
      textStyle: AppText.caption,
    ),
  );
}

/// Maps the [AppText] scale onto Material's [TextTheme] slots so raw Material
/// widgets render with on-brand styles.
TextTheme _buildTextTheme() {
  return const TextTheme(
    displayLarge: AppText.display,
    displayMedium: AppText.display,
    displaySmall: AppText.headline,
    headlineLarge: AppText.headline,
    headlineMedium: AppText.headline,
    headlineSmall: AppText.titleL,
    titleLarge: AppText.titleL,
    titleMedium: AppText.title,
    titleSmall: AppText.label,
    bodyLarge: AppText.bodyL,
    bodyMedium: AppText.body,
    bodySmall: AppText.caption,
    labelLarge: AppText.label,
    labelMedium: AppText.label,
    labelSmall: AppText.caption,
  );
}
