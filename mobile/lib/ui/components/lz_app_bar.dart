import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// The app's standard top bar. Two sizes via [large]:
///  - standard (default): a compact 17/title bar.
///  - large: a 24/headline title for top-level screens.
///
/// Set [gradientTitle] to paint the title with the amber-claw
/// [AppColors.accentGradient] (the brand "claw" motif). Honors the ambient
/// [AppBarTheme] for colors so it stays on-brand automatically.
class LzAppBar extends StatelessWidget implements PreferredSizeWidget {
  const LzAppBar({
    super.key,
    required this.title,
    this.large = false,
    this.gradientTitle = false,
    this.actions,
    this.leading,
    this.bottom,
    this.centerTitle = false,
  });

  final String title;
  final bool large;
  final bool gradientTitle;
  final List<Widget>? actions;
  final Widget? leading;
  final PreferredSizeWidget? bottom;
  final bool centerTitle;

  @override
  Size get preferredSize => Size.fromHeight(
        (large ? 72.0 : kToolbarHeight) + (bottom?.preferredSize.height ?? 0),
      );

  @override
  Widget build(BuildContext context) {
    final style = large ? AppText.headline : AppText.titleL;

    Widget titleWidget = Text(title, style: style);
    if (gradientTitle) {
      titleWidget = ShaderMask(
        shaderCallback: (bounds) =>
            AppColors.accentGradient.createShader(bounds),
        blendMode: BlendMode.srcIn,
        child: Text(title, style: style.copyWith(color: Colors.white)),
      );
    }

    return AppBar(
      title: titleWidget,
      centerTitle: centerTitle,
      toolbarHeight: large ? 72 : kToolbarHeight,
      leading: leading,
      actions: actions == null
          ? null
          : [
              ...actions!.map((a) => Padding(
                    padding: const EdgeInsets.only(right: AppSpacing.xs),
                    child: a,
                  )),
            ],
      bottom: bottom,
    );
  }
}
