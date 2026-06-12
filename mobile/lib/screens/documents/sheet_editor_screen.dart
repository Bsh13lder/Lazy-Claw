import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
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
import 'sheet_formula_bar.dart';
import 'sheet_link_ui.dart';
import 'sheet_selection.dart';
import 'sheet_toolbar.dart';
import 'univer_model.dart';
import 'univer_ops.dart';
import 'univer_parse.dart';

/// Full native editor for a single Univer sheet (formula bar, toolbar, undo/redo,
/// row/col ops, TSV copy/paste, sort, freeze). Formulas recompute server-side.
/// UI helpers extracted to sheet_formula_bar.dart, sheet_link_ui.dart.
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
    CachedDoc? cached;
    if (cache != null) {
      cached = await cache.getDoc(DocKind.sheets.api, widget.id);
      if (cached != null && mounted) {
        setState(() { _sheet = UniverSheet.fromWorkbook(cached!.payload); _loading = false; _error = null; });
      }
    }
    if (cached == null) setState(() { _loading = true; _error = null; });
    try {
      final detail = await ref.read(documentsRepositoryProvider).getPayload(DocKind.sheets, widget.id);
      if (!mounted) return;
      setState(() { _sheet = UniverSheet.fromWorkbook(detail.payload); _loading = false; _error = null; });
      await _cacheWorkbook(detail.payload, detail.name);
    } catch (_) {
      if (!mounted) return;
      if (cached == null) setState(() { _error = 'Could not open this sheet. Pull to retry.'; _loading = false; });
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
    final next = sel.extendTo(row.clamp(0, rows - 1), col.clamp(0, cols - 1));
    // Skip the setState when the clamped extend produces the same selection —
    // this avoids per-pixel full-screen rebuilds during handle drags.
    if (next == _sel) return;
    setState(() => _sel = next);
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

    // ── Auto-convert bare URLs and markdown links typed into the formula bar ──
    if (!isFormula) {
      final link = detectAutoLink(raw);
      if (link != null) {
        if (link.display != null) {
          // Markdown branch: update display text + refresh formula bar.
          next = next.setCell(r, c, value: link.display);
          next = next.setLink(r, c, link.url, display: link.display);
          _formulaCtrl.text = link.display!;
          _formulaCtrl.selection = TextSelection.collapsed(
            offset: link.display!.length,
          );
        } else {
          // Bare URL branch.
          next = next.setLink(r, c, link.url);
        }
      }
    }

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
      case SheetToolbarAction.insertLink:
        _showLinkDialog();
      case SheetToolbarAction.copy:
        _copySelection();
      case SheetToolbarAction.paste:
        _pasteFromClipboard();
      case SheetToolbarAction.freezeToggle:
        _toggleFreeze();
    }
  }

  // ── Link dialog ──────────────────────────────────────────────────────────────

  /// Open the insert/edit-link bottom sheet for the anchor cell.
  Future<void> _showLinkDialog() async {
    final sheet = _sheet;
    final sel = _sel;
    if (sheet == null || sel == null) return;
    final r = sel.anchorRow;
    final c = sel.anchorCol;

    final existingUrl = sheet.linkAt(r, c);
    final existingDisplay = sheet.cellAt(r, c).display;

    await LzBottomSheet.show<void>(
      context,
      title: existingUrl != null ? 'Edit link' : 'Insert link',
      builder: (_) => LinkDialogBody(
        initialDisplay: existingDisplay,
        initialUrl: existingUrl ?? '',
        onSave: (display, url) {
          _pushUndo();
          var next = _sheet!;
          next = next.setLink(r, c, url,
              display: display.trim().isEmpty ? null : display.trim());
          setState(() => _sheet = next);
          _scheduleSave();
        },
      ),
    );
  }

  // ── Header context menu actions ───────────────────────────────────────────────

  /// Recalc server-side if the workbook has any formula — best-effort.
  Future<void> _recalcIfFormulas(UniverSheet s) async {
    if (!sheetHasAnyFormula(s)) return;
    try {
      final r = await ref.read(documentsRepositoryProvider)
          .recalc(widget.id, s.toWorkbook());
      if (mounted) {
        setState(() => _sheet = UniverSheet.fromWorkbook(r, active: s.activeIndex));
      }
    } catch (_) {}
  }

  /// Handle column/row header context menu actions from [SheetEditorGrid].
  Future<void> _onHeaderAction(SheetHeaderAction action, int index) async {
    final sheet = _sheet;
    if (sheet == null) return;
    _pushUndo();
    final (rows, cols) = _gridDims();
    UniverSheet? next;
    bool clearSel = false;
    switch (action) {
      case SheetHeaderAction.insertLeft:
        next = sheet.insertCol(index); clearSel = true;
      case SheetHeaderAction.insertRight:
        next = sheet.insertCol(index + 1); clearSel = true;
      case SheetHeaderAction.deleteCol:
        next = sheet.deleteCol(index); clearSel = true;
      case SheetHeaderAction.clearCol:
        next = sheet.clearRange(SelRange(0, index, rows - 1, index));
      case SheetHeaderAction.sortAsc:
        await _sortByCol(sheet, index, asc: true); return;
      case SheetHeaderAction.sortDesc:
        await _sortByCol(sheet, index, asc: false); return;
      case SheetHeaderAction.colWidth:
        await _showColWidthDialog(sheet, index); return;
      case SheetHeaderAction.insertAbove:
        next = sheet.insertRow(index); clearSel = true;
      case SheetHeaderAction.insertBelow:
        next = sheet.insertRow(index + 1); clearSel = true;
      case SheetHeaderAction.deleteRow:
        next = sheet.deleteRow(index); clearSel = true;
      case SheetHeaderAction.clearRow:
        next = sheet.clearRange(SelRange(index, 0, index, cols - 1));
    }
    // next is always set by the non-early-return cases above.
    setState(() { _sheet = next!; if (clearSel) _sel = null; });
    _scheduleSave();
    await _recalcIfFormulas(_sheet!);
  }

  /// Sort the full used range by [col]. hasHeader=true: row 0 is always preserved.
  Future<void> _sortByCol(UniverSheet sheet, int col, {required bool asc}) async {
    final (maxRow, maxCol) = sheet.usedBounds();
    if (maxRow < 0 || maxCol < 0) return;
    final next = sheet.sortRange(SelRange(0, 0, maxRow, maxCol), col,
        asc: asc, hasHeader: true);
    setState(() => _sheet = next);
    _scheduleSave();
    await _recalcIfFormulas(next);
  }

  /// Column width dialog: reads current width from columnData (fallback 88),
  /// shows slider via [promptColWidth], applies on confirm.
  Future<void> _showColWidthDialog(UniverSheet sheet, int col) async {
    // Read current width from columnData, fallback to default 88.
    final wb = sheet.rawWorkbook;
    final sheetsMap = wb['sheets'];
    double currentW = 88.0;
    if (sheetsMap is Map) {
      final order = (wb['sheetOrder'] as List?)?.map((e) => e.toString()).toList()
          ?? (sheetsMap).keys.cast<String>().toList();
      final idx = sheet.activeIndex.clamp(0, order.isEmpty ? 0 : order.length - 1);
      final sheetId = order.isEmpty ? '' : order[idx];
      final sheetData = sheetsMap[sheetId];
      if (sheetData is Map) {
        final colData = sheetData['columnData'];
        if (colData is Map) {
          final entry = colData[col.toString()];
          if (entry is Map && entry['w'] is num) {
            currentW = (entry['w'] as num).toDouble();
          }
        }
      }
    }

    if (!mounted) return;
    final chosen = await promptColWidth(context, currentW);
    if (chosen == null || !mounted) return;
    _pushUndo();
    final next = _sheet!.setColWidth(col, chosen);
    setState(() => _sheet = next);
    _scheduleSave();
  }

  // ── Copy / Paste ──────────────────────────────────────────────────────────────

  void _copySelection() {
    final sheet = _sheet;
    final sel = _sel;
    if (sheet == null || sel == null) return;
    final tsv = sheet.rangeToTsv(sel.range);
    Clipboard.setData(ClipboardData(text: tsv));
    final rows = sel.range.rowCount;
    final cols = sel.range.colCount;
    _snack('Copied ${rows}x$cols cells');
  }

  Future<void> _pasteFromClipboard() async {
    final sheet = _sheet;
    final sel = _sel;
    if (sheet == null || sel == null) return;
    final data = await Clipboard.getData('text/plain');
    final text = data?.text;
    if (text == null || text.isEmpty) {
      _snack('Clipboard is empty.', error: true);
      return;
    }
    _pushUndo();
    final next = sheet.pasteTsv(sel.anchorRow, sel.anchorCol, text);
    setState(() => _sheet = next);
    _scheduleSave();
    await _recalcIfFormulas(next);
  }

  // ── Freeze toggle ─────────────────────────────────────────────────────────────

  void _toggleFreeze() {
    final sheet = _sheet;
    if (sheet == null) return;
    _pushUndo();
    final next = sheet.toggleFreeze();
    setState(() => _sheet = next);
    _scheduleSave();
  }

  // ── Bulk convert ─────────────────────────────────────────────────────────────

  Future<void> _bulkConvertLinks() async {
    final sheet = _sheet;
    if (sheet == null) return;
    setState(() => _saving = true);
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final result = await repo.convertLinks(widget.id);
      if (!mounted) return;
      if (result.converted == 0) {
        _snack('No plain URLs found.');
      } else {
        _pushUndo();
        setState(() {
          _sheet = UniverSheet.fromWorkbook(
            result.snapshot,
            active: sheet.activeIndex,
          );
        });
        await _cacheWorkbook(result.snapshot, widget.name);
        if (!mounted) return;
        _snack('Converted ${result.converted} link${result.converted == 1 ? '' : 's'}.');
      }
    } catch (_) {
      if (!mounted) return;
      _snack('Convert failed. Try again.', error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
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
    } catch (e) {
      // Autosave is best-effort; the next edit reschedules another save.
      debugPrint('sheet autosave failed: $e');
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
              } else if (v == 'convert_links') {
                _bulkConvertLinks();
              }
            },
            itemBuilder: (_) => const [
              PopupMenuItem(value: 'xlsx', child: Text('Export as Excel (.xlsx)')),
              PopupMenuItem(value: 'csv', child: Text('Export as CSV (.csv)')),
              PopupMenuDivider(),
              PopupMenuItem(
                value: 'convert_links',
                child: Text('Convert URLs to links'),
              ),
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
                SheetTabs(
                  sheetNames: sheet.sheetNames,
                  activeIndex: sheet.activeIndex,
                  onSelect: (i) => setState(() {
                    _sheet = sheet.withActiveIndex(i);
                    _sel = null;
                    _formulaCtrl.clear();
                  }),
                ),
              if (sheet != null && _sel != null)
                SheetToolbar(
                  anchorStyle: _anchorStyle,
                  canUndo: _undo.isNotEmpty,
                  canRedo: _redo.isNotEmpty,
                  frozen: sheet.frozen,
                  hasSelection: _sel != null,
                  onAction: _handleToolbarAction,
                  onTextColor: _applyTextColor,
                  onFillColor: _applyFillColor,
                  onNumberFormat: _applyNumberFormat,
                ),
              if (sheet != null && _sel != null && _sel!.isSingle)
                LinkChip(
                  sheet: sheet,
                  sel: _sel!,
                  onEdit: _showLinkDialog,
                  onRemove: () {
                    if (_sel == null || _sheet == null) return;
                    _pushUndo();
                    setState(() {
                      _sheet = _sheet!.removeLink(
                        _sel!.anchorRow,
                        _sel!.anchorCol,
                      );
                    });
                    _scheduleSave();
                  },
                  onSnack: _snack,
                ),
              if (sheet != null)
                SheetFormulaBar(
                  cellRef: _sel != null
                      ? '${colToLetter(_sel!.anchorCol)}${_sel!.anchorRow + 1}'
                      : '—',
                  controller: _formulaCtrl,
                  focusNode: _formulaFocus,
                  hasSel: _sel != null,
                  onChanged: () => setState(() {}),
                  onSubmitted: _commit,
                  onApply: _commit,
                ),
              if (suggestions.isNotEmpty)
                SheetFormulaHelper(
                  suggestions: suggestions,
                  onTap: _insertFunction,
                ),
              Expanded(child: _buildBody()),
            ],
          ),
          if (_applying) const AiApplyingOverlay(),
        ],
      ),
    );
  }

  void _insertFunction(FormulaFn f) {
    final text = _formulaCtrl.text;
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
          onHeaderAction: _onHeaderAction,
        );
      },
    );
  }
}

