import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../local/document_cache_dao.dart';
import '../../providers/documents_provider.dart';
import '../../repositories/documents_repository.dart';
import 'doc_ai_box.dart';
import 'doc_share.dart';
import 'export_password_dialog.dart';
import 'formula_helper.dart';
import 'sheet_grid.dart';
import 'sheet_selection.dart';
import 'sheet_toolbar.dart';
import 'univer_model.dart';
import 'univer_parse.dart';

/// Full native editor for a single sheet: tap a cell to load it into the formula
/// bar, edit the value or `=formula`, and commit. Formulas recompute on the
/// server (the phone has no JS engine) and the workbook autosaves. Multi-sheet
/// tabs, fit-to-width + pinch-zoom, and the ✨ AI box round it out.
///
/// New in this pass:
///   - Range selection ([SheetSelection]) with drag-to-extend handle
///   - Formatting toolbar ([SheetToolbar]) wired to [applyStyle]
///   - Undo/redo (50-step history cap)
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
  SheetSelection? _sel;

  // ── Undo / redo stacks ──────────────────────────────────────────────────────
  final List<UniverSheet> _undo = [];
  final List<UniverSheet> _redo = [];
  static const int _maxHistory = 50;

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

  // ── Undo/redo helpers ────────────────────────────────────────────────────────

  /// Push the current sheet onto the undo stack before a mutation.
  void _pushUndo() {
    final sheet = _sheet;
    if (sheet == null) return;
    _undo.add(sheet);
    if (_undo.length > _maxHistory) _undo.removeAt(0);
    _redo.clear();
  }

  void _doUndo() {
    if (_undo.isEmpty || _sheet == null) return;
    _redo.add(_sheet!);
    final prev = _undo.removeLast();
    setState(() => _sheet = prev);
    _scheduleSave();
  }

  void _doRedo() {
    if (_redo.isEmpty || _sheet == null) return;
    _undo.add(_sheet!);
    final next = _redo.removeLast();
    setState(() => _sheet = next);
    _scheduleSave();
  }

  // ── Load ─────────────────────────────────────────────────────────────────────

  Future<void> _load() async {
    final cache = ref.read(documentCacheDaoProvider);
    // 1. Paint the cached copy instantly (no spinner) if we have one.
    CachedDoc? cached;
    if (cache != null) {
      cached = await cache.getDoc(DocKind.sheets.api, widget.id);
      if (cached != null && mounted) {
        setState(() {
          _sheet = UniverSheet.fromWorkbook(cached!.payload);
          _loading = false;
          _error = null;
        });
      }
    }
    if (cached == null) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    // 2. Revalidate over the network; refresh the view + cache when it lands.
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final detail = await repo.getPayload(DocKind.sheets, widget.id);
      if (!mounted) return;
      setState(() {
        _sheet = UniverSheet.fromWorkbook(detail.payload);
        _loading = false;
        _error = null;
      });
      await _cacheWorkbook(detail.payload, detail.name);
    } catch (_) {
      if (!mounted) return;
      // Keep showing the cached copy when offline; only error on a cold miss.
      if (cached == null) {
        setState(() {
          _error = 'Could not open this sheet. Pull to retry.';
          _loading = false;
        });
      }
    }
  }

  /// Best-effort write of the current workbook into the on-device cache so the
  /// next open is instant. Never throws into the editor.
  Future<void> _cacheWorkbook(Map<String, dynamic> workbook, String name) async {
    try {
      await ref.read(documentCacheDaoProvider)?.putDoc(
            kind: DocKind.sheets.api,
            id: widget.id,
            name: name,
            payloadJson: jsonEncode(workbook),
          );
    } catch (_) {
      // Caching is best-effort.
    }
  }

  // ── Selection ────────────────────────────────────────────────────────────────

  void _selectCell(int row, int col) {
    final sheet = _sheet;
    if (sheet == null) return;
    setState(() {
      _sel = SheetSelection.single(row, col);
      _formulaCtrl.text = sheet.cellAt(row, col).editText;
      _formulaCtrl.selection = TextSelection.collapsed(
        offset: _formulaCtrl.text.length,
      );
    });
    _formulaFocus.requestFocus();
  }

  /// Grid dimensions currently rendered — mirrors the rows/cols computed in
  /// [_buildBody] so selection extension can clamp to the visible grid.
  (int, int) _gridDims() {
    final sheet = _sheet;
    if (sheet == null) return (_minRows, _minCols);
    final (maxRow, maxCol) = sheet.usedBounds();
    return ((maxRow + 2).clamp(_minRows, 1000), (maxCol + 2).clamp(_minCols, 100));
  }

  void _extendSelectionTo(int row, int col) {
    final sel = _sel;
    if (sel == null) return;
    final (rows, cols) = _gridDims();
    setState(() {
      _sel = sel.extendTo(row.clamp(0, rows - 1), col.clamp(0, cols - 1));
    });
  }

  void _startSelectionFrom(int row, int col) {
    final sheet = _sheet;
    if (sheet == null) return;
    setState(() {
      _sel = SheetSelection.single(row, col);
      _formulaCtrl.text = sheet.cellAt(row, col).editText;
      _formulaCtrl.selection = TextSelection.collapsed(
        offset: _formulaCtrl.text.length,
      );
    });
  }

  // ── Commit cell edit ─────────────────────────────────────────────────────────

  Future<void> _commit() async {
    final sheet = _sheet;
    final sel = _sel;
    if (sheet == null || sel == null) return;
    final r = sel.anchorRow;
    final c = sel.anchorCol;
    final raw = _formulaCtrl.text;
    final isFormula = raw.trimLeft().startsWith('=');

    _pushUndo();
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

  // ── Toolbar actions ──────────────────────────────────────────────────────────

  void _handleToolbarAction(SheetToolbarAction action) {
    switch (action) {
      case SheetToolbarAction.undo:
        _doUndo();
      case SheetToolbarAction.redo:
        _doRedo();
      case SheetToolbarAction.bold:
        _applyStyleToggle('bl', _anchorStyle.bold);
      case SheetToolbarAction.italic:
        _applyStyleToggle('it', _anchorStyle.italic);
      case SheetToolbarAction.underline:
        _applyStyleUnderlineToggle(_anchorStyle.underline);
      case SheetToolbarAction.strike:
        _applyStyleStrikeToggle(_anchorStyle.strike);
      case SheetToolbarAction.wrapToggle:
        _applyStylePatch({'tb': _anchorStyle.wrap ? null : 3});
      case SheetToolbarAction.alignLeft:
        _applyStylePatch({'ht': _anchorStyle.hAlign == 1 ? null : 1});
      case SheetToolbarAction.alignCenter:
        _applyStylePatch({'ht': _anchorStyle.hAlign == 2 ? null : 2});
      case SheetToolbarAction.alignRight:
        _applyStylePatch({'ht': _anchorStyle.hAlign == 3 ? null : 3});
    }
  }

  void _applyStyleToggle(String key, bool currentlyOn) {
    _applyStylePatch({key: currentlyOn ? 0 : 1});
  }

  void _applyStyleUnderlineToggle(bool currentlyOn) {
    _applyStylePatch({'ul': currentlyOn ? {'s': 0} : {'s': 1}});
  }

  void _applyStyleStrikeToggle(bool currentlyOn) {
    _applyStylePatch({'st': currentlyOn ? {'s': 0} : {'s': 1}});
  }

  void _applyTextColor(String? rgb) {
    _applyStylePatch({'cl': rgb == null ? null : {'rgb': rgb}});
  }

  void _applyFillColor(String? rgb) {
    _applyStylePatch({'bg': rgb == null ? null : {'rgb': rgb}});
  }

  void _applyNumberFormat(String? pattern) {
    _applyStylePatch({'n': pattern == null ? null : {'pattern': pattern}});
  }

  void _applyStylePatch(Map<String, dynamic> patch) {
    final sheet = _sheet;
    final sel = _sel;
    if (sheet == null || sel == null) return;
    _pushUndo();
    setState(() {
      _sheet = sheet.applyStyle(sel.range, patch);
    });
    _scheduleSave();
  }

  CellStyleView get _anchorStyle {
    final sheet = _sheet;
    final sel = _sel;
    if (sheet == null || sel == null) return CellStyleView.empty;
    return sheet.resolveStyle(sel.anchorRow, sel.anchorCol);
  }

  // ── Save ─────────────────────────────────────────────────────────────────────

  void _scheduleSave() {
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(milliseconds: 800), _save);
  }

  Future<void> _save() async {
    final sheet = _sheet;
    if (sheet == null) return;
    setState(() => _saving = true);
    try {
      final workbook = sheet.toWorkbook();
      await ref
          .read(documentsRepositoryProvider)
          .save(DocKind.sheets, widget.id, workbook, name: widget.name);
      await _cacheWorkbook(workbook, widget.name);
      ref.read(documentsListProvider(DocKind.sheets).notifier).refresh();
    } catch (_) {
      // Autosave is best-effort; the next edit reschedules another save.
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  // ── Export ───────────────────────────────────────────────────────────────────

  static const _xlsxMime =
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

  Future<void> _export(String format, String ext, String mime) async {
    final pw = await promptExportPassword(context);
    if (pw == null || !mounted) return;
    final encrypted = pw.isNotEmpty;
    setState(() => _saving = true);
    try {
      final bytes = await ref.read(documentsRepositoryProvider).exportBytes(
            DocKind.sheets, widget.id, format,
            password: encrypted ? pw : null,
          );
      await shareDocumentBytes(
        bytes: bytes,
        stem: widget.name,
        ext: encrypted ? 'zip' : ext,
        mimeType: encrypted ? 'application/zip' : mime,
      );
    } catch (_) {
      if (mounted) _snack('Export failed. Try again.', error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  // ── AI edit ──────────────────────────────────────────────────────────────────

  Future<void> _openAi() async {
    final instruction = await DocAiBox.show(context, kindLabel: 'sheet');
    if (instruction == null || !mounted) return;
    setState(() => _applying = true);
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final result = await repo.aiEdit(DocKind.sheets, widget.id, instruction);
      if (!mounted) return;
      if (result.ok && result.snapshot != null) {
        _pushUndo();
        setState(() {
          _applying = false;
          _sheet = UniverSheet.fromWorkbook(result.snapshot!);
          _sel = null;
        });
        await _cacheWorkbook(result.snapshot!, widget.name);
        ref.read(documentsListProvider(DocKind.sheets).notifier).refresh();
        _snack(result.summary ?? 'Sheet updated.');
      } else {
        setState(() => _applying = false);
        _snack(result.error ?? 'AI could not apply that change.', error: true);
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _applying = false);
      _snack('AI edit failed. Try again.', error: true);
    }
  }

  // ── Snackbar ─────────────────────────────────────────────────────────────────

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

  // ── Build ────────────────────────────────────────────────────────────────────

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
          PopupMenuButton<String>(
            icon: const Icon(Icons.ios_share, color: AppColors.textSecondary),
            tooltip: 'Export / share',
            color: AppColors.bgSurfaceElevated,
            onSelected: (v) {
              if (v == 'xlsx') {
                _export('xlsx', 'xlsx', _xlsxMime);
              } else if (v == 'csv') {
                _export('csv', 'csv', 'text/csv');
              }
            },
            itemBuilder: (_) => const [
              PopupMenuItem(value: 'xlsx', child: Text('Export as Excel (.xlsx)')),
              PopupMenuItem(value: 'csv', child: Text('Export as CSV (.csv)')),
            ],
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
              if (sheet != null && _sel != null)
                SheetToolbar(
                  anchorStyle: _anchorStyle,
                  canUndo: _undo.isNotEmpty,
                  canRedo: _redo.isNotEmpty,
                  onAction: _handleToolbarAction,
                  onTextColor: _applyTextColor,
                  onFillColor: _applyFillColor,
                  onNumberFormat: _applyNumberFormat,
                ),
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
                  _sel = null;
                  _formulaCtrl.clear();
                }),
              ),
            ),
        ],
      ),
    );
  }

  Widget _formulaBar() {
    final hasSel = _sel != null;
    final cellRef = hasSel
        ? '${colToLetter(_sel!.anchorCol)}${_sel!.anchorRow + 1}'
        : '—';
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
              cellRef,
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
        final (rows, cols) = _gridDims();
        return SheetEditorGrid(
          sheet: sheet,
          rows: rows,
          cols: cols,
          sel: _sel,
          viewportWidth: constraints.maxWidth,
          onTapCell: _selectCell,
          onExtendSelection: _extendSelectionTo,
          onStartSelection: _startSelectionFrom,
        );
      },
    );
  }
}
