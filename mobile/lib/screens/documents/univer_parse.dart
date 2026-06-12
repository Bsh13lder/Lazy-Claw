/// Pure parsers over Univer's `IWorkbookData` / `IDocumentData` snapshots.
///
/// The Documents tab renders the encrypted office files the server hands back.
/// Sheets/Docs payloads are Univer's native JSON snapshots; these helpers turn
/// them into the minimal read-only shapes the mobile viewers display (a string
/// grid for sheets, a paragraph list for docs).
///
/// No Flutter / Univer runtime dependency — just dict shaping, so they're cheap
/// to unit-test. Mirrors the server-side helpers in `lazyclaw/sheets/snapshot.py`
/// and `lazyclaw/docs/snapshot.py`.
///
/// Full manual cell editing is intentionally OUT OF SCOPE for this pass — the
/// viewers are read-only; edits go through the ✨ AI box.
library;

// ── Univer document control characters (see docs/snapshot.py) ───────────────

/// Paragraph terminator inside `body.dataStream`.
const String kParagraphBreak = '\r';

/// Body terminator (section break sentinel) at the end of `dataStream`.
const String kSectionBreak = '\n';

/// Custom-range sentinels (hyperlinks etc.) bracketing a span of `dataStream`.
const String _customRangeStart = '';
const String _customRangeEnd = '';

// ── A1 column letters ─────────────────────────────────────────────────────────

/// 0-based column index → spreadsheet letters (0→A, 25→Z, 26→AA). Mirrors
/// `col_to_letter` in `lazyclaw/sheets/snapshot.py`. Used for grid headers.
String colToLetter(int col) {
  if (col < 0) return '';
  var n = col + 1;
  var letters = '';
  while (n > 0) {
    final rem = (n - 1) % 26;
    letters = String.fromCharCode(65 + rem) + letters;
    n = (n - 1) ~/ 26;
  }
  return letters;
}

// ── Sheet grid ──────────────────────────────────────────────────────────────

/// A read-only view of a single worksheet: its [name] and a dense grid of
/// display-value strings ([rows] → columns), trimmed to the used bounds.
class SheetGrid {
  const SheetGrid({required this.name, required this.rows});

  /// Worksheet display name (e.g. `Sheet1`).
  final String name;

  /// Row-major display values. Each inner list is one row, padded to the same
  /// column count. Empty when the worksheet has no populated cells.
  final List<List<String>> rows;

  int get rowCount => rows.length;
  int get colCount => rows.isEmpty ? 0 : rows.first.length;
  bool get isEmpty => rows.isEmpty;
}

/// Parse the FIRST worksheet of a Univer `IWorkbookData` [payload] into a
/// [SheetGrid] of display strings.
///
/// - The first sheet is `sheetOrder[0]` (falling back to the first `sheets`
///   key).
/// - `cellData` is keyed by *string* row index → *string* col index →
///   `{v, f}`; the display value is `v` when present, else the formula `f`,
///   else the empty string.
/// - The grid is trimmed to the populated bounds (a blank sheet → empty grid),
///   so we never materialize Univer's default 1000×20 of empties.
SheetGrid parseSheetGrid(Map<String, dynamic>? payload) {
  if (payload == null) return const SheetGrid(name: 'Sheet1', rows: []);

  final sheets = _asMap(payload['sheets']);
  if (sheets.isEmpty) return const SheetGrid(name: 'Sheet1', rows: []);

  // Resolve the first sheet id from sheetOrder, falling back to insertion order.
  final order = (payload['sheetOrder'] as List?)?.cast<dynamic>() ?? const [];
  final String sheetId =
      order.isNotEmpty ? order.first.toString() : sheets.keys.first;

  final sheet = _asMap(sheets[sheetId]);
  final name = (sheet['name'] ?? sheetId).toString();
  final cellData = _asMap(sheet['cellData']);
  if (cellData.isEmpty) return SheetGrid(name: name, rows: const []);

  // Find used bounds across populated cells.
  var maxRow = -1;
  var maxCol = -1;
  cellData.forEach((rKey, rowVal) {
    final r = int.tryParse(rKey.toString());
    if (r == null) return;
    final cols = _asMap(rowVal);
    cols.forEach((cKey, cell) {
      final c = int.tryParse(cKey.toString());
      if (c == null) return;
      if (_cellDisplay(cell).isEmpty && _asMap(cell).isEmpty) return;
      if (r > maxRow) maxRow = r;
      if (c > maxCol) maxCol = c;
    });
  });

  if (maxRow < 0 || maxCol < 0) return SheetGrid(name: name, rows: const []);

  final rows = List.generate(
    maxRow + 1,
    (_) => List<String>.filled(maxCol + 1, '', growable: false),
    growable: false,
  );

  cellData.forEach((rKey, rowVal) {
    final r = int.tryParse(rKey.toString());
    if (r == null || r > maxRow) return;
    _asMap(rowVal).forEach((cKey, cell) {
      final c = int.tryParse(cKey.toString());
      if (c == null || c > maxCol) return;
      rows[r][c] = _cellDisplay(cell);
    });
  });

  return SheetGrid(name: name, rows: rows);
}

/// The value a reader should see for one Univer `ICellData` cell: `v` (value)
/// when present, else `f` (formula), else the empty string.
String _cellDisplay(dynamic cell) {
  final m = _asMap(cell);
  final v = m['v'];
  if (v != null) return v.toString();
  final f = m['f'];
  if (f != null && f.toString().isNotEmpty) return f.toString();
  return '';
}

// ── Document text ────────────────────────────────────────────────────────────

/// Parse a Univer `IDocumentData` [payload] into a list of plain-text
/// paragraphs (in order), for read-only rendering.
///
/// Reads `body.dataStream` directly: drops the trailing section break, drops the
/// final paragraph break, splits on paragraph breaks, and strips custom-range
/// sentinel chars (hyperlink markers) from each paragraph's visible text. An
/// empty/blank document yields a single empty paragraph `['']`.
List<String> parseDocParagraphs(Map<String, dynamic>? payload) {
  if (payload == null) return const [''];
  final body = _asMap(payload['body']);
  final stream = body['dataStream'];
  if (stream is! String || stream.isEmpty) return const [''];

  var text = stream;
  if (text.endsWith(kSectionBreak)) {
    text = text.substring(0, text.length - 1);
  }
  if (text.endsWith(kParagraphBreak)) {
    text = text.substring(0, text.length - 1);
  }
  if (text.isEmpty) return const [''];

  return text
      .split(kParagraphBreak)
      .map(_stripRangeTokens)
      .toList(growable: false);
}

/// Convenience: the whole document joined as newline-separated text.
String parseDocText(Map<String, dynamic>? payload) =>
    parseDocParagraphs(payload).join('\n');

String _stripRangeTokens(String s) =>
    s.replaceAll(_customRangeStart, '').replaceAll(_customRangeEnd, '');

// ── Mutable workbook model (editor) ──────────────────────────────────────────

/// One Univer `ICellData` cell: a cached display [value] and/or a [formula].
/// Mirrors `lazyclaw/sheets/snapshot.py` (`v` = value, `f` = formula).
class UniverCell {
  const UniverCell({this.value, this.formula, this.style});

  /// Cached value (`v`) — what the grid shows once computed.
  final dynamic value;

  /// Formula string (`f`), always stored with a leading `=`.
  final String? formula;

  /// Opaque style id (`s`) referencing the workbook `styles` registry.
  final dynamic style;

  /// What the user sees: the cached value, else the formula, else empty.
  String get display {
    if (value != null) return value.toString();
    if (formula != null && formula!.isNotEmpty) return formula!;
    return '';
  }

  /// The raw value to put in an editor field: the formula when present (so the
  /// user edits the formula), else the literal value.
  String get editText {
    if (formula != null && formula!.isNotEmpty) return formula!;
    if (value != null) return value.toString();
    return '';
  }

  bool get isEmpty => value == null && (formula == null || formula!.isEmpty);

  factory UniverCell.fromJson(dynamic raw) {
    final m = _asMap(raw);
    return UniverCell(value: m['v'], formula: m['f']?.toString(), style: m['s']);
  }

  Map<String, dynamic> toJson() {
    final out = <String, dynamic>{};
    if (formula != null && formula!.isNotEmpty) {
      out['f'] = formula;
      if (value != null) out['v'] = value;
    } else if (value != null) {
      out['v'] = value;
    }
    if (style != null) out['s'] = style;
    return out;
  }
}

/// A mutable, immutable-by-copy view over a Univer `IWorkbookData` snapshot for
/// the native editor. Holds the whole workbook map plus the [activeIndex] of the
/// worksheet being edited. Every edit returns a NEW [UniverSheet] (project
/// immutability rule) so Riverpod/`setState` see a fresh reference.
class UniverSheet {
  const UniverSheet._(this._wb, this.activeIndex);

  final Map<String, dynamic> _wb;

  /// Index into [sheetNames] / `sheetOrder` of the worksheet being edited.
  final int activeIndex;

  factory UniverSheet.fromWorkbook(Map<String, dynamic>? payload, {int active = 0}) {
    final wb = _deepCopyMap(payload ?? const {});
    final order = _order(wb);
    final clamped = order.isEmpty ? 0 : active.clamp(0, order.length - 1);
    return UniverSheet._(wb, clamped);
  }

  static List<String> _order(Map<String, dynamic> wb) {
    final sheets = _asMap(wb['sheets']);
    final order = (wb['sheetOrder'] as List?)?.map((e) => e.toString()).toList();
    if (order != null && order.isNotEmpty) return order;
    return sheets.keys.toList();
  }

  /// Worksheet display names in tab order.
  List<String> get sheetNames {
    final sheets = _asMap(_wb['sheets']);
    return _order(_wb)
        .map((sid) => (_asMap(sheets[sid])['name'] ?? sid).toString())
        .toList(growable: false);
  }

  String get _activeSheetId {
    final order = _order(_wb);
    if (order.isEmpty) return '';
    return order[activeIndex.clamp(0, order.length - 1)];
  }

  Map<String, dynamic> get _activeCellData =>
      _asMap(_asMap(_asMap(_wb['sheets'])[_activeSheetId])['cellData']);

  /// The cell at (row, col) of the active worksheet (empty cell if unset).
  UniverCell cellAt(int row, int col) {
    final rowMap = _asMap(_activeCellData[row.toString()]);
    final raw = rowMap[col.toString()];
    return raw == null ? const UniverCell() : UniverCell.fromJson(raw);
  }

  /// Switch the active worksheet; returns a NEW [UniverSheet].
  UniverSheet withActiveIndex(int index) {
    final order = _order(_wb);
    final clamped = order.isEmpty ? 0 : index.clamp(0, order.length - 1);
    return UniverSheet._(_wb, clamped);
  }

  /// Set (or clear) a cell on the active worksheet; returns a NEW [UniverSheet].
  ///
  /// A non-null [formula] wins (a leading `=` is added if missing). Passing both
  /// null clears the cell. Mirrors `_apply_cell` in `sheets/snapshot.py`.
  UniverSheet setCell(int row, int col, {Object? value, String? formula}) {
    final wb = _deepCopyMap(_wb);
    final sheet = _asMap(_asMap(wb['sheets'])[_activeSheetId]);
    final cellData = (sheet['cellData'] as Map?)?.cast<String, dynamic>() ??
        (sheet['cellData'] = <String, dynamic>{});
    final rKey = row.toString();
    final cKey = col.toString();

    if (value == null && (formula == null || formula.isEmpty)) {
      final rowMap = _asMap(cellData[rKey]);
      rowMap.remove(cKey);
      if (rowMap.isEmpty) {
        cellData.remove(rKey);
      } else {
        cellData[rKey] = rowMap;
      }
      return UniverSheet._(wb, activeIndex);
    }

    final cell = <String, dynamic>{};
    if (formula != null && formula.isNotEmpty) {
      cell['f'] = formula.startsWith('=') ? formula : '=$formula';
      if (value != null) cell['v'] = _coerce(value);
    } else {
      // Reachable only when value is non-null (the clear case returned above).
      cell['v'] = _coerce(value!);
    }
    final rowMap = (cellData[rKey] as Map?)?.cast<String, dynamic>() ??
        <String, dynamic>{};
    rowMap[cKey] = cell;
    cellData[rKey] = rowMap;
    return UniverSheet._(wb, activeIndex);
  }

  /// `(maxRow, maxCol)` inclusive of populated cells on the active sheet; both
  /// `-1` when the sheet is empty.
  (int, int) usedBounds() {
    var maxRow = -1;
    var maxCol = -1;
    _activeCellData.forEach((rKey, rowVal) {
      final r = int.tryParse(rKey);
      if (r == null) return;
      _asMap(rowVal).forEach((cKey, cell) {
        final c = int.tryParse(cKey);
        if (c == null) return;
        if (UniverCell.fromJson(cell).isEmpty) return;
        if (r > maxRow) maxRow = r;
        if (c > maxCol) maxCol = c;
      });
    });
    return (maxRow, maxCol);
  }

  /// Read-only view of the underlying workbook map. Callers MUST NOT mutate
  /// it or anything reachable from it — use the mutator APIs instead. Exists
  /// so per-cell readers (styles, links) don't deep-copy the workbook.
  Map<String, dynamic> get rawWorkbook => _wb;

  /// The underlying Univer workbook map (for save / recalc round-trips).
  Map<String, dynamic> toWorkbook() => _deepCopyMap(_wb);

  /// Best-effort numeric coercion mirroring `coerce_value` in snapshot.py.
  static Object _coerce(Object value) {
    if (value is num || value is bool) return value;
    final s = value.toString().trim();
    if (s.isEmpty) return '';
    final i = int.tryParse(s);
    if (i != null) return i;
    final d = double.tryParse(s);
    if (d != null) return d;
    return value;
  }
}

// ── helpers ──────────────────────────────────────────────────────────────────

/// Coerce a dynamic JSON value to a `Map<String, dynamic>` (empty when not a
/// map). Tolerant of the `Map<dynamic, dynamic>` shapes Dio decoding can yield.
Map<String, dynamic> _asMap(dynamic v) {
  if (v is Map<String, dynamic>) return v;
  if (v is Map) return v.map((k, val) => MapEntry(k.toString(), val));
  return const {};
}

/// Recursively deep-copy a JSON-ish map so edits never mutate the source
/// snapshot (immutability rule). Lists and nested maps are copied too.
Map<String, dynamic> _deepCopyMap(Map<dynamic, dynamic> src) {
  final out = <String, dynamic>{};
  src.forEach((k, v) => out[k.toString()] = _deepCopyValue(v));
  return out;
}

dynamic _deepCopyValue(dynamic v) {
  if (v is Map) return _deepCopyMap(v);
  if (v is List) return v.map(_deepCopyValue).toList();
  return v;
}
