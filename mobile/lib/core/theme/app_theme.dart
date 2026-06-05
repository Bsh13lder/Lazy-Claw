/// DEPRECATED — superseded by the design system in `lib/ui/`.
///
/// The old per-accent `buildTheme(String)` / `kThemeColors` map has been
/// replaced by the single token-driven [buildAppTheme] in
/// `package:lazyclaw_mobile/ui/app_theme.dart`. This file is kept only as a
/// thin re-export so any lingering import keeps compiling; prefer importing
/// `ui/app_theme.dart` (or `ui/ui.dart`) directly.
library;

export '../../ui/app_theme.dart';
