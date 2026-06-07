import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../providers/documents_provider.dart';
import '../../repositories/documents_repository.dart';
import 'doc_ai_box.dart';
import 'formula_helper.dart';
import 'univer_parse.dart';

/// Full native editor for a single sheet: tap a cell to load it into the formula
/// bar, edit the value or `=formula`, and commit. Formulas recompute on the
/// server (the phone has no JS engine) and the workbook autosaves. Multi-sheet
/// tabs, fit-to-width + pinch-zoom, and the ✨ AI box round it out.
///
/// Grid library decision: a custom `Table`-in-`InteractiveViewer` (not
/// pluto_grid) — we need fit-to-width sizing, a shared formula bar/helper, and
/// Univer cell fidelity, which the custom grid gives us directly.
class SheetEditorScreen extends ConsumerStatefulWidget {
  const SheetEditorScreen({super.key, required this.id, required this.name});

  final String id;
  final String name;

  @override
  ConsumerState<SheetEditorScreen> createState() => _SheetEditorScreenState();
}

class _SheetEditorScreenState extends ConsumerState<SheetEditorScreen> {
  bool _loading = true;
  bool _applying = false;
  bool _saving = false;
  String? _error;

  UniverSheet? _sheet;
  int? _selRow;
  int? _selCol;

  final TextEditingController _formulaCtrl = TextEditingController();
  final FocusNode _formulaFocus = FocusNode();
  List<FormulaFn> _catalog = const [];
  Timer? _saveTimer;

  // Minimum populated viewport so a brand-new sheet is still editable.
  static const int _minRows = 12;
  static const int _minCols = 6;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
    loadFormulaCatalog().then((c) {
      if (mounted) setState(() => _catalog = c);
    });
  }

  @override
  void dispose() {
    _saveTimer?.cancel();
    _formulaCtrl.dispose();
    _formulaFocus.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final detail = await repo.getPayload(DocKind.sheets, widget.id);
      if (!mounted) return;
      setState(() {
        _sheet = UniverSheet.fromWorkbook(detail.payload);
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = 'Could not open this sheet. Pull to retry.';
        _loading = false;
      });
    }
  }

  void _selectCell(int row, int col) {
    final sheet = _sheet;
    if (sheet == null) return;
    setState(() {
      _selRow = row;
      _selCol = col;
      _formulaCtrl.text = sheet.cellAt(row, col).editText;
      _formulaCtrl.selection = TextSelection.collapsed(
        offset: _formulaCtrl.text.length,
      );
    });
    _formulaFocus.requestFocus();
  }

  Future<void> _commit() async {
    final sheet = _sheet;
    final r = _selRow;
    final c = _selCol;
    if (sheet == null || r == null || c == null) return;
    final raw = _formulaCtrl.text;
    final isFormula = raw.trimLeft().startsWith('=');

    var next = isFormula
        ? sheet.setCell(r, c, formula: raw.trim())
        : sheet.setCell(r, c, value: raw);
    setState(() => _sheet = next);

    if (isFormula) {
      try {
        final repo = ref.read(documentsRepositoryProvider);
        final recalced = await repo.recalc(widget.id, next.toWorkbook());
        if (!mounted) return;
        setState(() => _sheet = UniverSheet.fromWorkbook(
              recalced,
              active: next.activeIndex,
            ));
      } catch (_) {
        // Keep the formula; values fill in on the next successful recalc/save.
      }
    }
    _scheduleSave();
  }

  void _scheduleSave() {
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(milliseconds: 800), _save);
  }

  Future<void> _save() async {
    final sheet = _sheet;
    if (sheet == null) return;
    setState(() => _saving = true);
    try {
      await ref
          .read(documentsRepositoryProvider)
          .save(DocKind.sheets, widget.id, widget.name, sheet.toWorkbook());
      ref.read(documentsListProvider(DocKind.sheets).notifier).refresh();
    } catch (_) {
      // Autosave is best-effort; the next edit reschedules another save.
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _openAi() async {
    final instruction = await DocAiBox.show(context, kindLabel: 'sheet');
    if (instruction == null || !mounted) return;
    setState(() => _applying = true);
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final result = await repo.aiEdit(DocKind.sheets, widget.id, instruction);
      if (!mounted) return;
      setState(() {
        _applying = false;
        if (result.ok && result.snapshot != null) {
          _sheet = UniverSheet.fromWorkbook(result.snapshot!);
          _selRow = null;
          _selCol = null;
        }
      });
      if (result.ok && result.snapshot != null) {
        ref.read(documentsListProvider(DocKind.sheets).notifier).refresh();
        _snack(result.summary ?? 'Sheet updated.');
      } else {
        _snack(result.error ?? 'AI could not apply that change.', error: true);
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _applying = false);
      _snack('AI edit failed. Try again.', error: true);
    }
  }

  void _snack(String msg, {bool error = false}) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.bgSurfaceElevated,
        content: Text(
          msg,
          style: AppText.body.copyWith(
            color: error ? AppColors.error : AppColors.textPrimary,
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final sheet = _sheet;
    final suggestions = filterFormulas(_catalog, _formulaCtrl.text);
    return LzScaffold(
      appBar: LzAppBar(
        title: widget.name,
        actions: [
          if (_saving)
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: AppSpacing.md),
              child: Center(
                child: SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: AppColors.accent,
                  ),
                ),
              ),
            ),
          LzIconButton(
            icon: Icons.auto_awesome,
            tooltip: 'Ask AI to edit',
            accent: true,
            onPressed: _applying ? null : _openAi,
          ),
        ],
      ),
      body: Stack(
        children: [
          Column(
            children: [
              if (sheet != null && sheet.sheetNames.length > 1)
                _sheetTabs(sheet),
              if (sheet != null) _formulaBar(),
              if (suggestions.isNotEmpty) _formulaHelper(suggestions),
              Expanded(child: _buildBody()),
            ],
          ),
          if (_applying) const AiApplyingOverlay(),
        ],
      ),
    );
  }

  Widget _sheetTabs(UniverSheet sheet) {
    return SizedBox(
      height: 40,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm),
        children: [
          for (var i = 0; i < sheet.sheetNames.length; i++)
            Padding(
              padding: const EdgeInsets.only(right: AppSpacing.xs),
              child: ChoiceChip(
                label: Text(sheet.sheetNames[i]),
                selected: i == sheet.activeIndex,
                onSelected: (_) => setState(() {
                  _sheet = sheet.withActiveIndex(i);
                  _selRow = null;
                  _selCol = null;
                  _formulaCtrl.clear();
                }),
              ),
            ),
        ],
      ),
    );
  }

  Widget _formulaBar() {
    final hasSel = _selRow != null && _selCol != null;
    final ref = hasSel ? '${colToLetter(_selCol!)}${_selRow! + 1}' : '—';
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
          SizedBox(
            width: 48,
            child: Text(
              ref,
              style: AppText.caption.copyWith(
                color: AppColors.textMuted,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
          const Text('ƒx  ', style: TextStyle(color: AppColors.textMuted)),
          Expanded(
            child: TextField(
              controller: _formulaCtrl,
              focusNode: _formulaFocus,
              enabled: hasSel,
              onChanged: (_) => setState(() {}), // refresh formula helper
              onSubmitted: (_) => _commit(),
              textInputAction: TextInputAction.done,
              style: AppText.body.copyWith(fontSize: 14),
              decoration: InputDecoration(
                isDense: true,
                border: InputBorder.none,
                hintText: hasSel ? 'Value or =formula' : 'Tap a cell to edit',
                hintStyle: AppText.body.copyWith(color: AppColors.textMuted),
              ),
            ),
          ),
          if (hasSel)
            LzIconButton(
              icon: Icons.check,
              tooltip: 'Apply',
              onPressed: _commit,
            ),
        ],
      ),
    );
  }

  Widget _formulaHelper(List<FormulaFn> suggestions) {
    return Container(
      constraints: const BoxConstraints(maxHeight: 168),
      color: AppColors.bgSurface,
      child: ListView.builder(
        shrinkWrap: true,
        itemCount: suggestions.length,
        itemBuilder: (_, i) {
          final f = suggestions[i];
          return ListTile(
            dense: true,
            title: Text(f.signature, style: AppText.body.copyWith(fontSize: 13)),
            subtitle: Text(f.help, style: AppText.caption),
            onTap: () => _insertFunction(f),
          );
        },
      ),
    );
  }

  void _insertFunction(FormulaFn f) {
    final text = _formulaCtrl.text;
    // Replace the trailing partial token with the function + "(".
    final replaced = text.replaceFirst(RegExp(r'[A-Za-z]*$'), '${f.name}(');
    final next = replaced.startsWith('=') ? replaced : '=$replaced';
    setState(() {
      _formulaCtrl.text = next;
      _formulaCtrl.selection = TextSelection.collapsed(offset: next.length);
    });
    _formulaFocus.requestFocus();
  }

  Widget _buildBody() {
    if (_loading) return LzSkeleton.list(count: 5);
    if (_error != null) return LzErrorState(message: _error!, onRetry: _load);
    final sheet = _sheet;
    if (sheet == null) return const SizedBox.shrink();
    return LayoutBuilder(
      builder: (context, constraints) {
        final (maxRow, maxCol) = sheet.usedBounds();
        final rows = (maxRow + 2).clamp(_minRows, 1000);
        final cols = (maxCol + 2).clamp(_minCols, 100);
        return _SheetGrid(
          sheet: sheet,
          rows: rows,
          cols: cols,
          selRow: _selRow,
          selCol: _selCol,
          viewportWidth: constraints.maxWidth,
          onTapCell: _selectCell,
        );
      },
    );
  }
}

// ── Editable grid ────────────────────────────────────────────────────────────

class _SheetGrid extends StatelessWidget {
  const _SheetGrid({
    required this.sheet,
    required this.rows,
    required this.cols,
    required this.selRow,
    required this.selCol,
    required this.viewportWidth,
    required this.onTapCell,
  });

  final UniverSheet sheet;
  final int rows;
  final int cols;
  final int? selRow;
  final int? selCol;
  final double viewportWidth;
  final void Function(int row, int col) onTapCell;

  static const double _gutterW = 44;
  static const double _minColW = 88;
  static const double _maxColW = 160;
  static const double _rowH = 36;

  double get _colW {
    final avail = viewportWidth - _gutterW;
    final fit = avail / cols;
    return fit.clamp(_minColW, _maxColW);
  }

  @override
  Widget build(BuildContext context) {
    final colW = _colW;
    return InteractiveViewer(
      panEnabled: true,
      scaleEnabled: true,
      minScale: 0.5,
      maxScale: 3.0,
      constrained: false,
      child: SingleChildScrollView(
        scrollDirection: Axis.vertical,
        child: SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _headerRow(colW),
              for (var r = 0; r < rows; r++) _dataRow(r, colW),
            ],
          ),
        ),
      ),
    );
  }

  Widget _headerRow(double colW) {
    return Row(
      children: [
        _gutter(''),
        for (var c = 0; c < cols; c++)
          _staticCell(colToLetter(c), colW, header: true),
      ],
    );
  }

  Widget _dataRow(int r, double colW) {
    return Row(
      children: [
        _gutter('${r + 1}'),
        for (var c = 0; c < cols; c++) _cell(r, c, colW),
      ],
    );
  }

  Widget _gutter(String text) => Container(
        width: _gutterW,
        height: _rowH,
        alignment: Alignment.center,
        decoration: const BoxDecoration(
          color: AppColors.bgSurfaceElevated,
          border: Border(
            right: BorderSide(color: AppColors.borderSubtle),
            bottom: BorderSide(color: AppColors.borderSubtle),
          ),
        ),
        child: Text(text, style: AppText.caption.copyWith(color: AppColors.textMuted)),
      );

  Widget _staticCell(String text, double colW, {bool header = false}) => Container(
        width: colW,
        height: _rowH,
        alignment: Alignment.center,
        decoration: const BoxDecoration(
          color: AppColors.bgSurfaceElevated,
          border: Border(
            right: BorderSide(color: AppColors.borderSubtle),
            bottom: BorderSide(color: AppColors.borderSubtle),
          ),
        ),
        child: Text(
          text,
          style: AppText.caption
              .copyWith(color: AppColors.textSecondary, fontWeight: FontWeight.w700),
        ),
      );

  Widget _cell(int r, int c, double colW) {
    final selected = r == selRow && c == selCol;
    final text = sheet.cellAt(r, c).display;
    return GestureDetector(
      onTap: () => onTapCell(r, c),
      child: Container(
        width: colW,
        height: _rowH,
        alignment: Alignment.centerLeft,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm),
        decoration: BoxDecoration(
          color: selected
              ? AppColors.accent.withValues(alpha: 0.16)
              : AppColors.bgSurface,
          border: Border.all(
            color: selected ? AppColors.accent : AppColors.borderSubtle,
            width: selected ? 1.5 : 0.5,
          ),
        ),
        child: Text(
          text,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: AppText.body.copyWith(fontSize: 13),
        ),
      ),
    );
  }
}
