import 'package:flutter/widgets.dart';
import 'colors.dart';

/// Elevation tokens for the LazyClaw design system (spec §1.3).
///
/// "Depth without heaviness": a subtle drop shadow plus a 1px [AppColors.borderSubtle]
/// hairline on cards. Pure constants + a couple of ready-made shadow lists.
abstract final class AppElevation {
  AppElevation._();

  /// Base card shadow: rgba(0,0,0,0.35), blur 16, y-offset 6.
  static const List<BoxShadow> card = [
    BoxShadow(
      color: Color(0x59000000), // black @ 35%
      blurRadius: 16,
      offset: Offset(0, 6),
    ),
  ];

  /// A lighter shadow for raised, floating affordances (sheets, FAB-likes).
  static const List<BoxShadow> floating = [
    BoxShadow(
      color: Color(0x73000000), // black @ 45%
      blurRadius: 24,
      offset: Offset(0, 10),
    ),
  ];

  /// No shadow.
  static const List<BoxShadow> none = [];

  /// The standard 1px hairline border drawn on cards/surfaces.
  static const Border hairline = Border.fromBorderSide(
    BorderSide(color: AppColors.borderSubtle, width: 1),
  );
}
