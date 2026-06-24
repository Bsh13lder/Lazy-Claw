/// Hyperlink UI components extracted from sheet_editor_screen.dart:
///   - [LinkChip]  – compact action strip shown when a linked cell is selected.
///   - [LinkDialogBody] – content for the insert/edit-link bottom sheet.
///   - [resolveCurrentColWidth] – reads current column width from a workbook.
///
/// Extracted to keep sheet_editor_screen.dart under the 800-line limit.
library;

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import 'univer_links.dart';
import 'univer_parse.dart';
import 'sheet_selection.dart';
import '../../ui/ui.dart';

// ── Column width helpers ──────────────────────────────────────────────────────

/// Default Univer column width (px) used when no explicit `w` is stored.
const double kDefaultColW = 88.0;

/// Default Univer row height (px) used when no explicit `h` is stored.
const double kDefaultRowH = 36.0;

/// Resolve the active sheet's data map for a sheet, or null when unavailable.
/// Shared by the width/height resolvers below.
Map<String, dynamic>? _activeSheetData(UniverSheet sheet) {
  final wb = sheet.rawWorkbook;
  final sheetsMap = wb['sheets'];
  if (sheetsMap is! Map) return null;
  final order = (wb['sheetOrder'] as List?)?.map((e) => e.toString()).toList() ??
      sheetsMap.keys.map((e) => e.toString()).toList();
  final idx = sheet.activeIndex.clamp(0, order.isEmpty ? 0 : order.length - 1);
  final sheetId = order.isEmpty ? '' : order[idx];
  final sheetData = sheetsMap[sheetId];
  if (sheetData is! Map) return null;
  return Map<String, dynamic>.from(sheetData);
}

/// Returns the current pixel width of [col] from [sheet]'s raw workbook data,
/// falling back to the Univer default of 88 px when the column has no explicit
/// width entry.
double resolveCurrentColWidth(UniverSheet sheet, int col) {
  final sheetData = _activeSheetData(sheet);
  if (sheetData == null) return kDefaultColW;
  final colData = sheetData['columnData'];
  if (colData is! Map) return kDefaultColW;
  final entry = colData[col.toString()];
  if (entry is Map && entry['w'] is num) return (entry['w'] as num).toDouble();
  return kDefaultColW;
}

/// Returns the current pixel height of [row] from [sheet]'s raw workbook data,
/// falling back to the default row height when the row has no explicit `h`.
double resolveCurrentRowHeight(UniverSheet sheet, int row) {
  final sheetData = _activeSheetData(sheet);
  if (sheetData == null) return kDefaultRowH;
  final rowData = sheetData['rowData'];
  if (rowData is! Map) return kDefaultRowH;
  final entry = rowData[row.toString()];
  if (entry is Map && entry['h'] is num) return (entry['h'] as num).toDouble();
  return kDefaultRowH;
}

/// Compute an auto-fit width (px) for [col]: the width of its widest cell's
/// display text at the grid font, clamped to a sane range so one long cell can't
/// blow the column off-screen and an empty column doesn't collapse. Uses a
/// real [TextPainter] so it matches what the grid renders, character-for-glyph.
///
/// [rows] caps how many rows are scanned (the visible/used window) so a 1000-row
/// sheet doesn't measure every cell. [padding] mirrors the cell's horizontal
/// inset (left + right). [minW]/[maxW] clamp the result.
double autoFitColWidth(
  UniverSheet sheet,
  int col, {
  required int rows,
  TextStyle? style,
  double padding = 16.0,
  double minW = 56.0,
  double maxW = 320.0,
}) {
  final ts = style ?? const TextStyle(fontSize: 13);
  var widest = 0.0;
  final scanRows = rows.clamp(0, 2000);
  for (var r = 0; r < scanRows; r++) {
    final text = sheet.cellAt(r, col).display;
    if (text.isEmpty) continue;
    final tp = TextPainter(
      text: TextSpan(text: text, style: ts),
      maxLines: 1,
      textDirection: TextDirection.ltr,
    )..layout();
    if (tp.width > widest) widest = tp.width;
  }
  if (widest == 0.0) return kDefaultColW; // empty column → keep the default
  return (widest + padding).clamp(minW, maxW);
}

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
