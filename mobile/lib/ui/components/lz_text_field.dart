import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../tokens/tokens.dart';

/// The app's text input. Thin wrapper over [TextField] that applies the design
/// system's [InputDecorationTheme] plus a few common conveniences ([label],
/// [hint], [prefixIcon], multiline via [minLines]/[maxLines], [errorText]).
class LzTextField extends StatelessWidget {
  const LzTextField({
    super.key,
    this.controller,
    this.label,
    this.hint,
    this.prefixIcon,
    this.suffix,
    this.errorText,
    this.obscureText = false,
    this.keyboardType,
    this.textInputAction,
    this.onChanged,
    this.onSubmitted,
    this.minLines,
    this.maxLines = 1,
    this.maxLength,
    this.maxLengthEnforcement,
    this.buildCounter,
    this.autofocus = false,
    this.enabled = true,
    this.fieldKey,
    this.focusNode,
  });

  final TextEditingController? controller;
  final String? label;
  final String? hint;
  final IconData? prefixIcon;
  final Widget? suffix;
  final String? errorText;
  final bool obscureText;
  final TextInputType? keyboardType;
  final TextInputAction? textInputAction;
  final ValueChanged<String>? onChanged;
  final ValueChanged<String>? onSubmitted;
  final int? minLines;
  final int? maxLines;

  /// Caps input length. When set, [TextField] auto-installs a
  /// `LengthLimitingTextInputFormatter` (per [maxLengthEnforcement], which
  /// defaults to the platform's enforced behavior) so typing simply stops
  /// accepting characters past the limit — no separate validation needed for
  /// the common "hard cap" case.
  final int? maxLength;

  /// Overrides how [maxLength] is enforced. Left null (the default) to use
  /// [TextField]'s own default (enforced/truncating), which is what every
  /// current caller wants.
  final MaxLengthEnforcement? maxLengthEnforcement;

  /// Overrides how the [maxLength] counter is rendered below the field.
  /// Passed straight through to [TextField.buildCounter] — e.g. a caller
  /// can return `null` to enforce the cap silently, with no visible
  /// "n / max" counter (handy in a tight [Row]-hosted composer).
  final InputCounterWidgetBuilder? buildCounter;
  final bool autofocus;
  final bool enabled;

  /// Optional [FocusNode] for the inner [TextField] — lets a caller drive
  /// focus programmatically (e.g. focusing the field right after switching
  /// it in from a read-only preview).
  final FocusNode? focusNode;

  /// Optional [Key] applied to the inner [TextField] (handy for tests).
  final Key? fieldKey;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (label != null) ...[
          Text(
            label!,
            style: AppText.label.copyWith(color: AppColors.textSecondary),
          ),
          const SizedBox(height: AppSpacing.sm),
        ],
        TextField(
          key: fieldKey,
          controller: controller,
          focusNode: focusNode,
          obscureText: obscureText,
          keyboardType: keyboardType,
          textInputAction: textInputAction,
          onChanged: onChanged,
          onSubmitted: onSubmitted,
          minLines: minLines,
          maxLines: obscureText ? 1 : maxLines,
          maxLength: maxLength,
          maxLengthEnforcement: maxLengthEnforcement,
          buildCounter: buildCounter,
          autofocus: autofocus,
          enabled: enabled,
          style: AppText.body,
          cursorColor: AppColors.accent,
          decoration: InputDecoration(
            hintText: hint,
            errorText: errorText,
            prefixIcon: prefixIcon == null
                ? null
                : Icon(prefixIcon, size: 20, color: AppColors.textMuted),
            suffixIcon: suffix,
          ),
        ),
      ],
    );
  }
}
