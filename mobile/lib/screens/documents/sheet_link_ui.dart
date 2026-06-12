/// Hyperlink UI components extracted from sheet_editor_screen.dart:
///   - [LinkChip]  – compact action strip shown when a linked cell is selected.
///   - [LinkDialogBody] – content for the insert/edit-link bottom sheet.
///
/// Extracted to keep sheet_editor_screen.dart under the 800-line limit.
library;

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import 'univer_links.dart';
import 'univer_parse.dart';
import 'sheet_selection.dart';
import '../../ui/ui.dart';

// ── Column width dialog ───────────────────────────────────────────────────────

/// Show a dialog with a slider (60–320 px) to set column width.
///
/// [currentWidth] is the initial slider value (clamped to [60, 320]).
/// Returns the chosen width in pixels, or null if the dialog was dismissed.
Future<double?> promptColWidth(BuildContext context, double currentWidth) async {
  double sliderVal = currentWidth.clamp(60.0, 320.0);
  return showDialog<double>(
    context: context,
    builder: (ctx) => StatefulBuilder(
      builder: (ctx2, setD) => AlertDialog(
        backgroundColor: AppColors.bgSurfaceElevated,
        title: Text('Column width', style: AppText.body),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('${sliderVal.round()} px',
                style: AppText.caption.copyWith(color: AppColors.textMuted)),
            Slider(
              min: 60,
              max: 320,
              value: sliderVal,
              activeColor: AppColors.accent,
              onChanged: (v) => setD(() => sliderVal = v),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(sliderVal),
            child: Text('Apply', style: TextStyle(color: AppColors.accent)),
          ),
        ],
      ),
    ),
  );
}

// ── LinkChip ──────────────────────────────────────────────────────────────────

/// Compact action strip shown when the anchor cell has a hyperlink.
/// Renders: "🔗 {host}" + Open / Edit / Remove buttons.
class LinkChip extends StatelessWidget {
  const LinkChip({
    super.key,
    required this.sheet,
    required this.sel,
    required this.onEdit,
    required this.onRemove,
    required this.onSnack,
  });

  final UniverSheet sheet;
  final SheetSelection sel;
  final VoidCallback onEdit;
  final VoidCallback onRemove;
  final void Function(String msg, {bool error}) onSnack;

  @override
  Widget build(BuildContext context) {
    final url = sheet.linkAt(sel.anchorRow, sel.anchorCol);
    if (url == null) return const SizedBox.shrink();

    String hostLabel;
    try {
      hostLabel = Uri.parse(url).host.isNotEmpty ? Uri.parse(url).host : url;
    } catch (_) {
      hostLabel = url;
    }

    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.xs,
      ),
      decoration: const BoxDecoration(
        color: AppColors.bgSurfaceElevated,
        border: Border(bottom: BorderSide(color: AppColors.borderSubtle)),
      ),
      child: Row(
        children: [
          Text('🔗 ', style: AppText.caption),
          Expanded(
            child: Text(
              hostLabel,
              style: AppText.caption.copyWith(color: AppColors.accent),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          // Open
          TextButton(
            style: TextButton.styleFrom(
              minimumSize: Size.zero,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm, vertical: AppSpacing.xs),
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            onPressed: () async {
              try {
                await launchUrl(
                  Uri.parse(url),
                  mode: LaunchMode.externalApplication,
                );
              } catch (_) {
                onSnack('Could not open link.', error: true);
              }
            },
            child: Text(
              'Open',
              style: AppText.caption.copyWith(color: AppColors.accent),
            ),
          ),
          // Edit
          TextButton(
            style: TextButton.styleFrom(
              minimumSize: Size.zero,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm, vertical: AppSpacing.xs),
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            onPressed: onEdit,
            child: Text(
              'Edit',
              style: AppText.caption.copyWith(color: AppColors.textSecondary),
            ),
          ),
          // Remove
          TextButton(
            style: TextButton.styleFrom(
              minimumSize: Size.zero,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm, vertical: AppSpacing.xs),
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            onPressed: onRemove,
            child: Text(
              'Remove',
              style: AppText.caption.copyWith(color: AppColors.error),
            ),
          ),
        ],
      ),
    );
  }
}

// ── LinkDialogBody ────────────────────────────────────────────────────────────

/// Content for the insert/edit-link bottom sheet.
///
/// Two fields: display text (optional) and URL (required, must start http(s)://).
/// Calls [onSave] when the user taps Save with a valid URL.
class LinkDialogBody extends StatefulWidget {
  const LinkDialogBody({
    super.key,
    required this.initialDisplay,
    required this.initialUrl,
    required this.onSave,
  });

  final String initialDisplay;
  final String initialUrl;
  final void Function(String display, String url) onSave;

  @override
  State<LinkDialogBody> createState() => _LinkDialogBodyState();
}

class _LinkDialogBodyState extends State<LinkDialogBody> {
  late final TextEditingController _displayCtrl;
  late final TextEditingController _urlCtrl;
  String? _urlError;

  @override
  void initState() {
    super.initState();
    _displayCtrl = TextEditingController(text: widget.initialDisplay);
    _urlCtrl = TextEditingController(text: widget.initialUrl);
  }

  @override
  void dispose() {
    _displayCtrl.dispose();
    _urlCtrl.dispose();
    super.dispose();
  }

  bool _validate() {
    final url = _urlCtrl.text.trim();
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      setState(() => _urlError = 'URL must start with http:// or https://');
      return false;
    }
    setState(() => _urlError = null);
    return true;
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: _displayCtrl,
          decoration: InputDecoration(
            labelText: 'Display text (optional)',
            labelStyle: AppText.caption.copyWith(color: AppColors.textMuted),
            border: OutlineInputBorder(
              borderRadius: AppRadii.rSm,
              borderSide: const BorderSide(color: AppColors.borderDefault),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: AppRadii.rSm,
              borderSide: const BorderSide(color: AppColors.borderDefault),
            ),
          ),
          style: AppText.body,
          textInputAction: TextInputAction.next,
        ),
        const SizedBox(height: AppSpacing.md),
        TextField(
          controller: _urlCtrl,
          decoration: InputDecoration(
            labelText: 'URL',
            labelStyle: AppText.caption.copyWith(color: AppColors.textMuted),
            errorText: _urlError,
            border: OutlineInputBorder(
              borderRadius: AppRadii.rSm,
              borderSide: const BorderSide(color: AppColors.borderDefault),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: AppRadii.rSm,
              borderSide: const BorderSide(color: AppColors.borderDefault),
            ),
          ),
          style: AppText.body,
          keyboardType: TextInputType.url,
          textInputAction: TextInputAction.done,
          onSubmitted: (_) => _submit(),
        ),
        const SizedBox(height: AppSpacing.lg),
        LzButton(
          label: 'Save',
          onPressed: _submit,
        ),
      ],
    );
  }

  void _submit() {
    if (!_validate()) return;
    final url = _urlCtrl.text.trim();
    final display = _displayCtrl.text;
    Navigator.of(context).pop();
    widget.onSave(display, url);
  }
}
