import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// A search input with a leading magnifier and an auto-appearing clear button.
///
/// Drives an external [controller] so the host can read/clear the query; the
/// clear button is shown only when there's text. [onChanged] fires on each
/// keystroke (search-as-you-type).
class LzSearchField extends StatefulWidget {
  const LzSearchField({
    super.key,
    required this.controller,
    this.hint = 'Search',
    this.onChanged,
    this.onSubmitted,
    this.autofocus = false,
  });

  final TextEditingController controller;
  final String hint;
  final ValueChanged<String>? onChanged;
  final ValueChanged<String>? onSubmitted;
  final bool autofocus;

  @override
  State<LzSearchField> createState() => _LzSearchFieldState();
}

class _LzSearchFieldState extends State<LzSearchField> {
  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onChange);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onChange);
    super.dispose();
  }

  void _onChange() => setState(() {});

  @override
  Widget build(BuildContext context) {
    final hasText = widget.controller.text.isNotEmpty;
    return TextField(
      controller: widget.controller,
      autofocus: widget.autofocus,
      textInputAction: TextInputAction.search,
      style: AppText.body,
      cursorColor: AppColors.accent,
      onChanged: widget.onChanged,
      onSubmitted: widget.onSubmitted,
      decoration: InputDecoration(
        hintText: widget.hint,
        prefixIcon:
            const Icon(Icons.search, size: 20, color: AppColors.textMuted),
        suffixIcon: hasText
            ? IconButton(
                icon: const Icon(Icons.close, size: 18),
                color: AppColors.textMuted,
                onPressed: () {
                  widget.controller.clear();
                  widget.onChanged?.call('');
                },
              )
            : null,
        isDense: true,
      ),
    );
  }
}
