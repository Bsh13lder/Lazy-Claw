import 'package:flutter/material.dart';
import '../tokens/tokens.dart';
import 'lz_app_bar.dart';

/// The standard screen scaffold. Wraps Material's [Scaffold] with the design
/// system defaults ([AppColors.bgBase] background, no surface tint) and a
/// convenient [title]/[appBar] shortcut.
///
/// Provide either [title] (builds an [LzAppBar] for you) or a custom [appBar].
/// An optional [banner] (e.g. an offline [LzBanner]) is pinned above [body].
class LzScaffold extends StatelessWidget {
  const LzScaffold({
    super.key,
    this.body,
    this.title,
    this.largeTitle = false,
    this.gradientTitle = false,
    this.appBar,
    this.actions,
    this.leading,
    this.banner,
    this.floatingActionButton,
    this.bottomNavigationBar,
    this.resizeToAvoidBottomInset,
    this.safeAreaBody = false,
  })  : assert(title != null || appBar != null || body != null,
            'LzScaffold needs at least a body, a title, or a custom appBar');

  final Widget? body;
  final String? title;
  final bool largeTitle;
  final bool gradientTitle;

  /// A fully custom app bar; takes precedence over [title].
  final PreferredSizeWidget? appBar;
  final List<Widget>? actions;
  final Widget? leading;

  /// Optional banner pinned above the body (offline / info / error).
  final Widget? banner;

  final Widget? floatingActionButton;
  final Widget? bottomNavigationBar;
  final bool? resizeToAvoidBottomInset;

  /// Wrap [body] in a [SafeArea] (default false — many bodies handle their own).
  final bool safeAreaBody;

  @override
  Widget build(BuildContext context) {
    final resolvedAppBar = appBar ??
        (title != null
            ? LzAppBar(
                title: title!,
                large: largeTitle,
                gradientTitle: gradientTitle,
                actions: actions,
                leading: leading,
              )
            : null);

    Widget? content = body;
    if (content != null && banner != null) {
      content = Column(
        children: [
          banner!,
          Expanded(child: content),
        ],
      );
    }
    if (content != null && safeAreaBody) {
      content = SafeArea(child: content);
    }

    return Scaffold(
      backgroundColor: AppColors.bgBase,
      appBar: resolvedAppBar,
      body: content,
      floatingActionButton: floatingActionButton,
      bottomNavigationBar: bottomNavigationBar,
      resizeToAvoidBottomInset: resizeToAvoidBottomInset,
    );
  }
}
