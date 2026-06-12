/// Formatting toolbar for the native sheet editor.
///
/// Rendered as a single horizontally-scrollable row above the formula bar.
/// Width ~44 px (matching the LazyClaw row gutter). Every button uses design
/// tokens from [AppColors] / [AppText] — no hardcoded colors.
library;

import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_model.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

// ── SheetToolbarAction ────────────────────────────────────────────────────────

/// Simple toggle / navigation actions emitted by [SheetToolbar].
///
/// Parameterized actions (color picker, number format, font size) are exposed
/// as dedicated callbacks on [SheetToolbar] to carry their payload cleanly.
enum SheetToolbarAction {
  bold,
  italic,
  underline,
  strike,
  wrapToggle,
  alignLeft,
  alignCenter,
  alignRight,
  undo,
  redo,
}

// ── Color swatch palette ──────────────────────────────────────────────────────

/// Fixed 10-color swatch palette offered in the text/fill color pickers.
/// Defined as hex strings matching the Univer `{"rgb": "#rrggbb"}` format.
const List<String> kSwatchColors = [
  '#FFFFFF', // white
  '#000000', // black
  '#767676', // gray (textMuted token)
  '#EF4444', // red (error token)
  '#F59E0B', // orange/amber (warn token)
  '#FDE047', // yellow
  '#10B981', // green (accent token)
  '#06B6D4', // cyan (info token)
  '#3B82F6', // blue
  '#A855F7', // purple
];

// ── Number format options ─────────────────────────────────────────────────────

/// Label → pattern pairs for the number-format popup.
const List<({String label, String? pattern})> kNumberFormats = [
  (label: 'Auto', pattern: null),
  (label: 'Number 0', pattern: '0'),
  (label: 'Number 0.00', pattern: '0.00'),
  (label: 'Percent 0%', pattern: '0%'),
  (label: 'Thousands #,##0.00', pattern: '#,##0.00'),
  (label: r'Currency $#,##0.00', pattern: r'$#,##0.00'),
  (label: 'Date yyyy-mm-dd', pattern: 'yyyy-mm-dd'),
];

// ── SheetToolbar ──────────────────────────────────────────────────────────────

/// Horizontally-scrollable formatting toolbar for the sheet editor.
///
/// The toolbar reflects the style of the *anchor cell* of the current
/// selection ([anchorStyle]) and emits actions / color / format via callbacks.
class SheetToolbar extends StatelessWidget {
  const SheetToolbar({
    super.key,
    required this.anchorStyle,
    required this.canUndo,
    required this.canRedo,
    required this.onAction,
    required this.onTextColor,
    required this.onFillColor,
    required this.onNumberFormat,
  });

  /// Style of the anchor cell — drives active states on toggle buttons.
  final CellStyleView anchorStyle;

  final bool canUndo;
  final bool canRedo;

  /// Called for simple on/off actions.
  final ValueChanged<SheetToolbarAction> onAction;

  /// Called when the user picks a text color. `null` removes the color.
  final void Function(String? rgbOrNull) onTextColor;

  /// Called when the user picks a fill color. `null` removes the fill.
  final void Function(String? rgbOrNull) onFillColor;

  /// Called when the user picks a number format. `null` = Auto (no pattern).
  final void Function(String? pattern) onNumberFormat;

  // ── Palette height ──────────────────────────────────────────────────────────
  static const double _height = 44.0;
  static const double _btnSize = 36.0;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: _height,
      decoration: const BoxDecoration(
        color: AppColors.bgSurfaceElevated,
        border: Border(bottom: BorderSide(color: AppColors.borderSubtle)),
      ),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xs),
        child: Row(
          children: [
            // ── Bold / Italic / Underline / Strike ──────────────────────────
            _ToggleBtn(
              label: 'B',
              tooltip: 'Bold',
              bold: true,
              active: anchorStyle.bold,
              onTap: () => onAction(SheetToolbarAction.bold),
            ),
            _ToggleBtn(
              label: 'I',
              tooltip: 'Italic',
              italic: true,
              active: anchorStyle.italic,
              onTap: () => onAction(SheetToolbarAction.italic),
            ),
            _ToggleBtn(
              label: 'U',
              tooltip: 'Underline',
              underline: true,
              active: anchorStyle.underline,
              onTap: () => onAction(SheetToolbarAction.underline),
            ),
            _ToggleBtn(
              label: 'S',
              tooltip: 'Strikethrough',
              strike: true,
              active: anchorStyle.strike,
              onTap: () => onAction(SheetToolbarAction.strike),
            ),
            _divider(),
            // ── Text color ──────────────────────────────────────────────────
            _ColorSwatchBtn(
              tooltip: 'Text color',
              currentColor: anchorStyle.color,
              isText: true,
              onPick: onTextColor,
            ),
            // ── Fill color ──────────────────────────────────────────────────
            _ColorSwatchBtn(
              tooltip: 'Fill color',
              currentColor: anchorStyle.bgColor,
              isText: false,
              onPick: onFillColor,
            ),
            _divider(),
            // ── Alignment ───────────────────────────────────────────────────
            _AlignBtn(
              icon: Icons.format_align_left,
              tooltip: 'Align left',
              active: anchorStyle.hAlign == 1,
              onTap: () => onAction(SheetToolbarAction.alignLeft),
            ),
            _AlignBtn(
              icon: Icons.format_align_center,
              tooltip: 'Align center',
              active: anchorStyle.hAlign == 2,
              onTap: () => onAction(SheetToolbarAction.alignCenter),
            ),
            _AlignBtn(
              icon: Icons.format_align_right,
              tooltip: 'Align right',
              active: anchorStyle.hAlign == 3,
              onTap: () => onAction(SheetToolbarAction.alignRight),
            ),
            _divider(),
            // ── Wrap toggle ─────────────────────────────────────────────────
            _IconToggleBtn(
              icon: Icons.wrap_text,
              tooltip: 'Wrap text',
              active: anchorStyle.wrap,
              onTap: () => onAction(SheetToolbarAction.wrapToggle),
            ),
            _divider(),
            // ── Number format ────────────────────────────────────────────────
            _NumFmtBtn(
              currentPattern: anchorStyle.numFmt,
              onPick: onNumberFormat,
            ),
            _divider(),
            // ── Undo / Redo ──────────────────────────────────────────────────
            Tooltip(
              message: 'Undo',
              child: SizedBox(
                width: _btnSize,
                height: _btnSize,
                child: IconButton(
                  iconSize: 18,
                  padding: EdgeInsets.zero,
                  icon: const Icon(Icons.undo),
                  color: canUndo
                      ? AppColors.textSecondary
                      : AppColors.textMuted,
                  onPressed: canUndo
                      ? () => onAction(SheetToolbarAction.undo)
                      : null,
                ),
              ),
            ),
            Tooltip(
              message: 'Redo',
              child: SizedBox(
                width: _btnSize,
                height: _btnSize,
                child: IconButton(
                  iconSize: 18,
                  padding: EdgeInsets.zero,
                  icon: const Icon(Icons.redo),
                  color: canRedo
                      ? AppColors.textSecondary
                      : AppColors.textMuted,
                  onPressed: canRedo
                      ? () => onAction(SheetToolbarAction.redo)
                      : null,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _divider() => Container(
        width: 1,
        height: 20,
        margin: const EdgeInsets.symmetric(horizontal: 4),
        color: AppColors.borderDefault,
      );
}

// ── Private helper widgets ────────────────────────────────────────────────────

/// Text-label toggle button (B, I, U, S).
class _ToggleBtn extends StatelessWidget {
  const _ToggleBtn({
    required this.label,
    required this.tooltip,
    required this.active,
    required this.onTap,
    this.bold = false,
    this.italic = false,
    this.underline = false,
    this.strike = false,
  });

  final String label;
  final String tooltip;
  final bool active;
  final VoidCallback onTap;
  final bool bold;
  final bool italic;
  final bool underline;
  final bool strike;

  @override
  Widget build(BuildContext context) {
    final decoration = TextDecoration.combine([
      if (underline) TextDecoration.underline,
      if (strike) TextDecoration.lineThrough,
    ]);

    return Tooltip(
      message: tooltip,
      child: InkWell(
        onTap: onTap,
        borderRadius: AppRadii.rSm,
        child: Container(
          width: SheetToolbar._btnSize,
          height: SheetToolbar._btnSize,
          alignment: Alignment.center,
          decoration: active
              ? BoxDecoration(
                  color: AppColors.accent.withValues(alpha: 0.16),
                  borderRadius: AppRadii.rSm,
                )
              : null,
          child: Text(
            label,
            style: AppText.body.copyWith(
              fontWeight: bold ? FontWeight.w700 : FontWeight.w400,
              fontStyle: italic ? FontStyle.italic : FontStyle.normal,
              decoration: decoration,
              decorationColor: active
                  ? AppColors.accent
                  : AppColors.textSecondary,
              color: active ? AppColors.accent : AppColors.textSecondary,
            ),
          ),
        ),
      ),
    );
  }
}

/// Icon toggle button (wrap, align).
class _IconToggleBtn extends StatelessWidget {
  const _IconToggleBtn({
    required this.icon,
    required this.tooltip,
    required this.active,
    required this.onTap,
  });

  final IconData icon;
  final String tooltip;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: InkWell(
        onTap: onTap,
        borderRadius: AppRadii.rSm,
        child: Container(
          width: SheetToolbar._btnSize,
          height: SheetToolbar._btnSize,
          alignment: Alignment.center,
          decoration: active
              ? BoxDecoration(
                  color: AppColors.accent.withValues(alpha: 0.16),
                  borderRadius: AppRadii.rSm,
                )
              : null,
          child: Icon(
            icon,
            size: 18,
            color: active ? AppColors.accent : AppColors.textSecondary,
          ),
        ),
      ),
    );
  }
}

/// Alignment button wrapper.
class _AlignBtn extends _IconToggleBtn {
  const _AlignBtn({
    required super.icon,
    required super.tooltip,
    required super.active,
    required super.onTap,
  });
}

// ── Color swatch picker ───────────────────────────────────────────────────────

/// Button showing the current selected color; tapping opens a popup swatch
/// grid with the 10 fixed palette colors plus a "none" cell.
class _ColorSwatchBtn extends StatelessWidget {
  const _ColorSwatchBtn({
    required this.tooltip,
    required this.currentColor,
    required this.isText,
    required this.onPick,
  });

  final String tooltip;
  final String? currentColor;
  final bool isText; // true = text color icon, false = fill bucket
  final void Function(String? rgbOrNull) onPick;

  @override
  Widget build(BuildContext context) {
    final preview = _parseHex(currentColor);
    return Tooltip(
      message: tooltip,
      child: InkWell(
        borderRadius: AppRadii.rSm,
        onTap: () => _showPicker(context),
        child: SizedBox(
          width: SheetToolbar._btnSize,
          height: SheetToolbar._btnSize,
          child: Center(
            child: Stack(
              alignment: Alignment.bottomCenter,
              children: [
                Icon(
                  isText ? Icons.format_color_text : Icons.format_color_fill,
                  size: 18,
                  color: AppColors.textSecondary,
                ),
                Container(
                  width: 14,
                  height: 3,
                  decoration: BoxDecoration(
                    color: preview ?? (isText ? AppColors.textPrimary : Colors.transparent),
                    border: preview == null
                        ? Border.all(color: AppColors.borderDefault, width: 0.5)
                        : null,
                    borderRadius: BorderRadius.circular(1),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _showPicker(BuildContext context) async {
    final chosen = await showMenu<String?>(
      context: context,
      position: _buttonPosition(context),
      color: AppColors.bgSurfaceElevated,
      items: [
        PopupMenuItem<String?>(
          enabled: false,
          child: _SwatchGrid(onPick: (hex) {
            Navigator.of(context).pop(hex);
          }),
        ),
      ],
    );
    // `chosen` is null if dismissed without selecting.
    // The swatch grid pops with either a hex string or the sentinel '' for
    // "remove color".
    if (chosen != null) {
      onPick(chosen.isEmpty ? null : chosen);
    }
  }

  RelativeRect _buttonPosition(BuildContext context) {
    final box = context.findRenderObject() as RenderBox?;
    if (box == null) return RelativeRect.fill;
    final pos = box.localToGlobal(Offset.zero);
    final size = box.size;
    return RelativeRect.fromLTRB(
      pos.dx,
      pos.dy + size.height,
      pos.dx + size.width,
      pos.dy + size.height + 4,
    );
  }

  Color? _parseHex(String? hex) {
    if (hex == null || hex.isEmpty) return null;
    final clean = hex.replaceFirst('#', '');
    final val = int.tryParse(clean, radix: 16);
    if (val == null) return null;
    return Color(0xFF000000 | val);
  }
}

/// 10-color swatch grid + a "none / clear" cell.
class _SwatchGrid extends StatelessWidget {
  const _SwatchGrid({required this.onPick});

  /// Called with a hex string (e.g. `"#FF0000"`) or `""` to remove color.
  final void Function(String hex) onPick;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 4,
      runSpacing: 4,
      children: [
        // "None" cell
        Tooltip(
          message: 'Remove color',
          child: GestureDetector(
            onTap: () => onPick(''),
            child: Container(
              width: 24,
              height: 24,
              decoration: BoxDecoration(
                border: Border.all(color: AppColors.borderDefault),
                borderRadius: BorderRadius.circular(4),
              ),
              child: const Icon(Icons.close, size: 12, color: AppColors.textMuted),
            ),
          ),
        ),
        for (final hex in kSwatchColors)
          GestureDetector(
            onTap: () => onPick(hex),
            child: Container(
              width: 24,
              height: 24,
              decoration: BoxDecoration(
                color: Color(
                  0xFF000000 | int.parse(hex.replaceFirst('#', ''), radix: 16),
                ),
                borderRadius: BorderRadius.circular(4),
                border: Border.all(
                  color: AppColors.borderDefault,
                  width: 0.5,
                ),
              ),
            ),
          ),
      ],
    );
  }
}

// ── Number format picker ──────────────────────────────────────────────────────

/// PopupMenuButton showing the list of built-in number formats.
class _NumFmtBtn extends StatelessWidget {
  const _NumFmtBtn({
    required this.currentPattern,
    required this.onPick,
  });

  final String? currentPattern;
  final void Function(String? pattern) onPick;

  @override
  Widget build(BuildContext context) {
    final activeLabel = kNumberFormats
        .firstWhere(
          (f) => f.pattern == currentPattern,
          orElse: () => kNumberFormats.first,
        )
        .label;

    return Tooltip(
      message: 'Number format',
      child: PopupMenuButton<String?>(
        tooltip: '',
        color: AppColors.bgSurfaceElevated,
        onSelected: onPick,
        itemBuilder: (_) => [
          for (final fmt in kNumberFormats)
            PopupMenuItem<String?>(
              value: fmt.pattern,
              child: Text(
                fmt.label,
                style: AppText.body.copyWith(
                  fontSize: 13,
                  color: fmt.pattern == currentPattern
                      ? AppColors.accent
                      : AppColors.textPrimary,
                ),
              ),
            ),
        ],
        child: Container(
          height: SheetToolbar._btnSize,
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(horizontal: 6),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                activeLabel.length > 8
                    ? '${activeLabel.substring(0, 8)}…'
                    : activeLabel,
                style: AppText.caption.copyWith(color: AppColors.textSecondary),
              ),
              const SizedBox(width: 2),
              const Icon(Icons.arrow_drop_down,
                  size: 14, color: AppColors.textMuted),
            ],
          ),
        ),
      ),
    );
  }
}
