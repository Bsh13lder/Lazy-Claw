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

// ── helpers ──────────────────────────────────────────────────────────────────

/// Coerce a dynamic JSON value to a `Map<String, dynamic>` (empty when not a
/// map). Tolerant of the `Map<dynamic, dynamic>` shapes Dio decoding can yield.
Map<String, dynamic> _asMap(dynamic v) {
  if (v is Map<String, dynamic>) return v;
  if (v is Map) return v.map((k, val) => MapEntry(k.toString(), val));
  return const {};
}
