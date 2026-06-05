import 'package:flutter/material.dart';

const Map<String, Color> kThemeColors = {
  'orange': Color(0xFFDC6843),
  'red': Color(0xFFEF4444),
  'rose': Color(0xFFF43F5E),
  'pink': Color(0xFFEC4899),
  'purple': Color(0xFFA855F7),
  'indigo': Color(0xFF6366F1),
  'blue': Color(0xFF3B82F6),
  'cyan': Color(0xFF06B6D4),
  'teal': Color(0xFF14B8A6),
  'green': Color(0xFF22C55E),
  'lime': Color(0xFF84CC16),
  'amber': Color(0xFFF59E0B),
};

ThemeData buildTheme(String themeId) {
  final accent = kThemeColors[themeId] ?? kThemeColors['orange']!;

  return ThemeData(
    brightness: Brightness.dark,
    colorScheme: ColorScheme.dark(
      primary: accent,
      secondary: accent,
      surface: const Color(0xFF1A1A2E),
    ),
    scaffoldBackgroundColor: const Color(0xFF0F0F1A),
    appBarTheme: const AppBarTheme(
      backgroundColor: Color(0xFF1A1A2E),
      elevation: 0,
      centerTitle: false,
    ),
    bottomNavigationBarTheme: BottomNavigationBarThemeData(
      backgroundColor: const Color(0xFF1A1A2E),
      selectedItemColor: accent,
      unselectedItemColor: Colors.grey[600],
      type: BottomNavigationBarType.fixed,
      showUnselectedLabels: true,
    ),
    cardTheme: const CardThemeData(
      color: Color(0xFF1E1E35),
      elevation: 0,
      margin: EdgeInsets.symmetric(horizontal: 16, vertical: 4),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: const Color(0xFF1E1E35),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide.none,
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: accent),
      ),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: accent,
        foregroundColor: Colors.white,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
        textStyle: const TextStyle(fontWeight: FontWeight.w600),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: accent,
      ),
    ),
    floatingActionButtonTheme: FloatingActionButtonThemeData(
      backgroundColor: accent,
      foregroundColor: Colors.white,
    ),
    dividerTheme: const DividerThemeData(
      color: Color(0xFF2A2A45),
      thickness: 1,
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: const Color(0xFF2A2A45),
      contentTextStyle: const TextStyle(color: Colors.white),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      behavior: SnackBarBehavior.floating,
    ),
  );
}
