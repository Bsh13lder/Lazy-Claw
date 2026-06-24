import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../core/router/app_router.dart';
import '../../local/document_cache_dao.dart';
import '../../providers/documents_provider.dart';
import '../../repositories/documents_repository.dart';
import 'doc_ai_box.dart';
import 'doc_share.dart';
import 'export_password_dialog.dart';
import 'formula_helper.dart';
import 'sheet_conflict_banner.dart';
import 'sheet_grid.dart';
import 'sheet_link_ui.dart';
import 'sheet_selection.dart';
import 'sheet_toolbar.dart';
import 'univer_links.dart';
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

class _SheetEditorScreenState extends ConsumerState<SheetEditorScreen>
    with WidgetsBindingObserver, RouteAware {
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

  // ── Optimistic-concurrency tracking ────────────────────────────────────────
  /// The `updated_at` value from the server when we last loaded or saved.
  /// Sent as `base_updated_at` on every save so the server can detect conflicts.
  /// null = we haven't committed a CAS base yet (LWW semantics for that save).
  String? _baseUpdatedAt;

  /// Non-null when a save returned a 409 DocConflictException. Cleared after
  /// the user resolves via Reload or Keep mine.
  DocPayload? _conflict;

  // Minimum populated viewport so a brand-new sheet is still editable.
  static const int _minRows = 12;
  static const int _minCols = 6;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
    loadFormulaCatalog().then((c) {
      if (mounted) setState(() => _catalog = c);
    });
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final route = ModalRoute.of(context);
    if (route != null) {
      routeObserver.subscribe(this, route);
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    routeObserver.unsubscribe(this);
    _saveTimer?.cancel();
    _formulaCtrl.dispose();
    _formulaFocus.dispose();
    super.dispose();
  }

  // ── WidgetsBindingObserver — fresh-on-resume ──────────────────────────────

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _revalidateIfIdle();
    }
  }

  // ── RouteAware — fresh when popped back to ────────────────────────────────

  @override
  void didPopNext() {
    _revalidateIfIdle();
  }

  /// Re-run the network half of [_load] when no unsaved edit is in-flight and
  /// there is no unresolved conflict. Adopts the fresh server snapshot + re-bases
  /// `_baseUpdatedAt`. Skips silently when busy so autosave is never interrupted.
  Future<void> _revalidateIfIdle() async {
    if (_saveTimer != null || _saving || _conflict != null) return;
    try {
      final d = await ref.read(documentsRepositoryProvider)
          .getPayload(DocKind.sheets, widget.id);
      if (!mounted) return;
      setState(() {
        _sheet = UniverSheet.fromWorkbook(d.payload);
        _baseUpdatedAt = d.updatedAt;
        _loading = false;
        _error = null;
      });
      await _cacheWorkbook(d.payload, d.name, updatedAt: d.updatedAt);
    } catch (_) {
      // Revalidation is best-effort; keep showing the current state.
    }
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
      // A cache read can THROW on a transient SQLite lock (the foreground app
      // and the background sync isolate share one DB). If that exception
      // escaped, `_load` would abort with `_loading` still true → the editor
      // sits on the infinite shimmer skeleton forever (a black, stuck screen).
      // Treat a cache miss/failure the same: fall through to the network path.
      try {
        cached = await cache.getDoc(DocKind.sheets.api, widget.id);
      } catch (_) {
        cached = null;
      }
      if (cached != null && mounted) {
        // Show cached copy immediately; _baseUpdatedAt stays null until the
        // network path below delivers the authoritative server value.
        setState(() {
          _sheet = UniverSheet.fromWorkbook(cached!.payload);
          _loading = false;
          _error = null;
        });
      }
    }
    if (cached == null && mounted) setState(() { _loading = true; _error = null; });
    try {
      final d = await ref.read(documentsRepositoryProvider)
          .getPayload(DocKind.sheets, widget.id);
      if (!mounted) return;
      setState(() {
        _sheet = UniverSheet.fromWorkbook(d.payload);
        _baseUpdatedAt = d.updatedAt;
        _loading = false;
        _error = null;
      });
      await _cacheWorkbook(d.payload, d.name, updatedAt: d.updatedAt);
    } catch (_) {
      if (!mounted) return;
      // The network read failed AND we have no cached copy to fall back on.
      // ALWAYS clear `_loading` so the editor leaves the infinite skeleton and
      // shows a retryable error instead of hanging on a black/stuck screen.
      if (cached == null && _sheet == null) {
        setState(() {
          _error = 'Could not open this sheet. Pull to retry.';
          _loading = false;
        });
      }
    }
  }

  /// Light refetch to re-base [_baseUpdatedAt] after an AI edit (AI bumps
  /// updated_at server-side, so the next autosave would 409 without this).
  Future<void> _rebaseFromServer() async {
    try {
      final d = await ref.read(documentsRepositoryProvider)
          .getPayload(DocKind.sheets, widget.id);
      if (mounted) _baseUpdatedAt = d.updatedAt;
    } catch (_) {
      _baseUpdatedAt = null; // LWW fallback: next save skips CAS check.
    }
  }

  /// Best-effort write of a SERVER-AUTHORITATIVE workbook into the on-device
  /// cache (clean — not pending push). Used after a successful network read/edit
  /// where the server already holds this exact content.
  Future<void> _cacheWorkbook(Map<String, dynamic> workbook, String name,
      {String? updatedAt}) async {
    try {
      await ref.read(documentCacheDaoProvider)?.putServerDoc(
          kind: DocKind.sheets.api, id: widget.id,
          name: name, payloadJson: jsonEncode(workbook),
          updatedAt: updatedAt);
    } catch (_) {}
  }

  /// Persist a LOCAL edit into the on-device cache as a DIRTY row + enqueue an
  /// `update` op on the shared outbox. This is what makes an offline edit
  /// durable: even if the subsequent network PUT throws, the edit is already in
  /// the cache and queued for the next sync. Best-effort — a cache failure must
  /// not crash the editor (the network save still runs).
  Future<void> _cacheLocalEdit(Map<String, dynamic> workbook) async {
    try {
      await ref.read(documentCacheDaoProvider)?.applyLocalEdit(
            kind: DocKind.sheets.api,
            id: widget.id,
            name: widget.name,
            payloadJson: jsonEncode(workbook),
            baseUpdatedAt: _baseUpdatedAt,
          );
    } catch (_) {}
  }

  /// Mark the cache row clean after a successful network save (so the outbox
  /// won't re-push it). [updatedAt] re-bases the cache row's sync clock.
  Future<void> _markSynced(String? updatedAt) async {
    try {
      await ref.read(documentCacheDaoProvider)?.markPushed(
            kind: DocKind.sheets.api,
            id: widget.id,
            updatedAt: updatedAt,
          );
    } catch (_) {}
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

  /// Grid dimensions passed to [SheetEditorBody] / used for clamping selections.
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
          next = next.setCell(r, c, value: link.display)
              .setLink(r, c, link.url, display: link.display);
          _formulaCtrl.text = link.display!;
          _formulaCtrl.selection =
              TextSelection.collapsed(offset: link.display!.length);
        } else {
          next = next.setLink(r, c, link.url); // Bare URL branch.
        }
      }
    }

    setState(() => _sheet = next);

    if (isFormula) {
      try {
        final recalced = await ref.read(documentsRepositoryProvider)
            .recalc(widget.id, next.toWorkbook());
        if (!mounted) return;
        setState(() => _sheet = UniverSheet.fromWorkbook(recalced, active: next.activeIndex));
      } catch (_) {
        // Keep the formula; values fill in on next successful recalc/save.
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
        _applyStylePatch({'bl': _anchorStyle.bold ? 0 : 1});
      case SheetToolbarAction.italic:
        _applyStylePatch({'it': _anchorStyle.italic ? 0 : 1});
      case SheetToolbarAction.underline:
        _applyStylePatch({'ul': _anchorStyle.underline ? {'s': 0} : {'s': 1}});
      case SheetToolbarAction.strike:
        _applyStylePatch({'st': _anchorStyle.strike ? {'s': 0} : {'s': 1}});
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
      case SheetHeaderAction.autoFitCol:
        _autoFitColumn(sheet, index); return;
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

  /// Column width dialog: reads current width via [resolveCurrentColWidth],
  /// shows slider via [promptColWidth], applies on confirm.
  Future<void> _showColWidthDialog(UniverSheet sheet, int col) async {
    if (!mounted) return;
    final chosen = await promptColWidth(
        context, resolveCurrentColWidth(sheet, col));
    if (chosen == null || !mounted) return;
    _pushUndo();
    final next = _sheet!.setColWidth(col, chosen);
    setState(() => _sheet = next);
    _scheduleSave();
  }

  /// Auto-fit: size [col] to the width of its widest cell content (clamped),
  /// persisting via setColWidth so it survives sync.
  void _autoFitColumn(UniverSheet sheet, int col) {
    final (rows, _) = _gridDims();
    final w = autoFitColWidth(sheet, col, rows: rows);
    _pushUndo();
    final next = _sheet!.setColWidth(col, w);
    setState(() => _sheet = next);
    _scheduleSave();
  }

  /// Drag-to-resize a column border → persist the new width + schedule save.
  void _resizeCol(int col, double width) {
    final sheet = _sheet;
    if (sheet == null) return;
    _pushUndo();
    final next = sheet.setColWidth(col, width);
    setState(() => _sheet = next);
    _scheduleSave();
  }

  /// Drag-to-resize a row border → persist the new height + schedule save.
  void _resizeRow(int row, double height) {
    final sheet = _sheet;
    if (sheet == null) return;
    _pushUndo();
    final next = sheet.setRowHeight(row, height);
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
          _sheet = UniverSheet.fromWorkbook(result.snapshot, active: sheet.activeIndex);
          if (result.updatedAt != null) _baseUpdatedAt = result.updatedAt;
        });
        await _cacheWorkbook(result.snapshot, widget.name,
            updatedAt: result.updatedAt);
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
    _saveTimer = null;
    final sheet = _sheet;
    if (sheet == null) return;
    setState(() => _saving = true);
    final workbook = sheet.toWorkbook();
    // Persist the edit to the on-device cache (dirty + outbox) FIRST, BEFORE the
    // network call — so an offline edit is never lost from the on-screen cache
    // even if the PUT below throws. The cache row is what the outbox/sync engine
    // later pushes to the server.
    await _cacheLocalEdit(workbook);
    try {
      // name: null — server keeps its stored name; avoids stale-rename clobber.
      final newAt = await ref.read(documentsRepositoryProvider)
          .save(DocKind.sheets, widget.id, workbook, baseUpdatedAt: _baseUpdatedAt);
      _baseUpdatedAt = newAt ?? _baseUpdatedAt; // re-base on server's returned value
      // The PUT succeeded — clear the dirty flag so the outbox doesn't re-push.
      await _markSynced(newAt);
      ref.read(documentsListProvider(DocKind.sheets).notifier).refresh();
    } on DocConflictException catch (e) {
      if (!mounted) return;
      setState(() => _conflict = e.current);
    } catch (e) {
      if (e is Exception) {
        // Check for a raw 409 without a conflict body — treat as non-recoverable.
        final msg = e.toString();
        if (msg.contains('409')) {
          if (mounted) {
            _snack('Save conflict — reloading.', error: true);
            _load();
          }
          return;
        }
      }
      // Other autosave failures are best-effort; the next edit reschedules.
      debugPrint('sheet autosave failed: $e');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  // ── Conflict resolution ───────────────────────────────────────────────────────

  /// User chose "Reload": adopt the server's current version. The pre-conflict
  /// local sheet is pushed onto the undo stack so the user can recover it.
  Future<void> _resolveConflictReload() async {
    final conflict = _conflict;
    if (conflict == null) return;
    final localSheet = _sheet;
    if (localSheet != null) _pushUndo();
    setState(() {
      _sheet = UniverSheet.fromWorkbook(conflict.payload);
      _baseUpdatedAt = conflict.updatedAt;
      _conflict = null;
      _redo.clear();
    });
    await _cacheWorkbook(conflict.payload, widget.name,
        updatedAt: conflict.updatedAt);
  }

  /// User chose "Keep mine": force-save by sending baseUpdatedAt=null (LWW).
  Future<void> _resolveConflictKeepMine() async {
    final sheet = _sheet;
    if (sheet == null) return;
    setState(() { _conflict = null; _saving = true; });
    final workbook = sheet.toWorkbook();
    // Persist locally FIRST so the forced edit isn't lost if the network throws.
    await _cacheLocalEdit(workbook);
    try {
      // LWW: omit base_updated_at so the server always accepts this write.
      final newUpdatedAt = await ref
          .read(documentsRepositoryProvider)
          .save(DocKind.sheets, widget.id, workbook);
      _baseUpdatedAt = newUpdatedAt ?? _baseUpdatedAt;
      await _markSynced(newUpdatedAt);
      ref.read(documentsListProvider(DocKind.sheets).notifier).refresh();
    } catch (e) {
      if (mounted) _snack('Save failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  // ── Export ───────────────────────────────────────────────────────────────────

  Future<void> _export(String format, String ext, String mime) async {
    final pw = await promptExportPassword(context);
    if (pw == null || !mounted) return;
    final encrypted = pw.isNotEmpty;
    setState(() => _saving = true);
    try {
      final bytes = await ref.read(documentsRepositoryProvider)
          .exportBytes(DocKind.sheets, widget.id, format, password: encrypted ? pw : null);
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
        ref.read(documentsListProvider(DocKind.sheets).notifier).refresh();
        _snack(result.summary ?? 'Sheet updated.');
        // The AI edit saved server-side (bumping updated_at). Refetch to re-base
        // so the next autosave doesn't 409 against the AI-bumped version, THEN
        // cache the snapshot clean with the rebased server clock.
        await _rebaseFromServer();
        await _cacheWorkbook(result.snapshot!, widget.name,
            updatedAt: _baseUpdatedAt);
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
    final (rows, cols) = _gridDims();
    return LzScaffold(
      appBar: LzAppBar(
        title: widget.name,
        actions: [
          SheetAppBarActions(
            saving: _saving,
            applying: _applying,
            onExport: _export,
            onConvertLinks: _bulkConvertLinks,
            onAi: _openAi,
          ),
        ],
      ),
      body: Stack(
        children: [
          SheetEditorBody(
            loading: _loading,
            error: _error,
            onRetry: _load,
            sheet: _sheet,
            sel: _sel,
            anchorStyle: _anchorStyle,
            canUndo: _undo.isNotEmpty,
            canRedo: _redo.isNotEmpty,
            conflict: _conflict,
            suggestions: filterFormulas(_catalog, _formulaCtrl.text),
            formulaCtrl: _formulaCtrl,
            formulaFocus: _formulaFocus,
            gridRows: rows,
            gridCols: cols,
            onConflictReload: _resolveConflictReload,
            onConflictKeepMine: _resolveConflictKeepMine,
            onTabSelect: (i) {
              setState(() {
                _sheet = _sheet!.withActiveIndex(i);
                _sel = null;
                _formulaCtrl.clear();
              });
            },
            onToolbarAction: _handleToolbarAction,
            onTextColor: _applyTextColor,
            onFillColor: _applyFillColor,
            onNumberFormat: _applyNumberFormat,
            onEditLink: _showLinkDialog,
            onRemoveLink: _removeSelectedLink,
            onSnack: _snack,
            onFormulaChanged: () => setState(() {}),
            onFormulaSubmit: _commit,
            onInsertFunction: _insertFunction,
            onTapCell: _selectCell,
            onExtendSelection: _extendSelectionTo,
            onStartSelection: _startSelectionFrom,
            onHeaderAction: _onHeaderAction,
            onResizeCol: _resizeCol,
            onResizeRow: _resizeRow,
          ),
          if (_applying) const AiApplyingOverlay(),
        ],
      ),
    );
  }

  void _removeSelectedLink() {
    final sel = _sel;
    final sheet = _sheet;
    if (sel == null || sheet == null) return;
    _pushUndo();
    setState(() {
      _sheet = sheet.removeLink(sel.anchorRow, sel.anchorCol);
    });
    _scheduleSave();
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
}
