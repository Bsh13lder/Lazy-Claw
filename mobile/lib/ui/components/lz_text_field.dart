import 'package:flutter/material.dart';
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
    this.autofocus = false,
    this.enabled = true,
    this.fieldKey,
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
  final bool autofocus;
  final bool enabled;

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
          obscureText: obscureText,
          keyboardType: keyboardType,
          textInputAction: textInputAction,
          onChanged: onChanged,
          onSubmitted: onSubmitted,
          minLines: minLines,
          maxLines: obscureText ? 1 : maxLines,
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
