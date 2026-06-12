/// Extended workbook model for Univer `IWorkbookData` snapshots.
///
/// Supplements [UniverSheet] in `univer_parse.dart` with:
///   - [SelRange] – normalised rectangular selection
///   - [CellStyleView] – resolved style view
///   - Style reads / writes ([resolveStyle], [applyStyle])
///   - Number formatting ([formatNumber])
///   - Hyperlink management ([linkAt], [setLink], [removeLink], [convertUrlsToLinks])
///   - Row/column structural ops ([insertRow], [deleteRow], [insertCol], [deleteCol])
///   - Column width ([setColWidth])
///   - Range clear ([clearRange])
///   - Sort ([sortRange])
///   - Freeze pane ([toggleFreeze], [frozen])
///   - TSV import/export ([rangeToTsv], [pasteTsv])
///
/// All mutations return a NEW [UniverSheet] (immutability rule). No Flutter
/// dependencies — pure Dart, cheaply unit-testable.
library;

import 'dart:convert';
import 'package:lazyclaw_mobile/screens/documents/univer_parse.dart';

// ── SelRange ──────────────────────────────────────────────────────────────────

/// A normalised rectangular selection on a worksheet.
///
/// Row and column indices are always stored so that `r1 <= r2` and `c1 <= c2`
/// regardless of the construction order.
class SelRange {
  SelRange(int r1, int c1, int r2, int c2)
      : r1 = r1 <= r2 ? r1 : r2,
        c1 = c1 <= c2 ? c1 : c2,
        r2 = r1 <= r2 ? r2 : r1,
        c2 = c1 <= c2 ? c2 : c1;

  /// First (top) row, inclusive.
  final int r1;

  /// First (left) column, inclusive.
  final int c1;

  /// Last (bottom) row, inclusive.
  final int r2;

  /// Last (right) column, inclusive.
  final int c2;

  int get rowCount => r2 - r1 + 1;
  int get colCount => c2 - c1 + 1;
  bool get isSingle => rowCount == 1 && colCount == 1;

  /// Whether (r, c) is inside the range.
  bool contains(int r, int c) => r >= r1 && r <= r2 && c >= c1 && c <= c2;

  /// All (row, col) pairs in row-major order.
  Iterable<(int, int)> get cells sync* {
    for (var r = r1; r <= r2; r++) {
      for (var c = c1; c <= c2; c++) {
        yield (r, c);
      }
    }
  }
}

// ── CellStyleView ─────────────────────────────────────────────────────────────

/// A resolved, type-safe view over a Univer style dictionary.
class CellStyleView {
  const CellStyleView({
    this.bold = false,
    this.italic = false,
    this.underline = false,
    this.strike = false,
    this.wrap = false,
    this.color,
    this.bgColor,
    this.numFmt,
    this.hAlign = 0,
  });

  final bool bold;
  final bool italic;
  final bool underline;
  final bool strike;
  final bool wrap;

  /// Text colour as `"#rrggbb"` (null = default).
  final String? color;

  /// Background fill colour as `"#rrggbb"` (null = default).
  final String? bgColor;

  /// Number-format pattern string (null = default).
  final String? numFmt;

  /// Horizontal alignment: 0=auto, 1=left, 2=centre, 3=right.
  final int hAlign;

  static const CellStyleView empty = CellStyleView();
}

// ── Number formatting ─────────────────────────────────────────────────────────

/// Format a numeric (or non-numeric) [v] according to a Univer [pattern].
///
/// Supported patterns:
///   `"0"`          → integer (rounded)
///   `"0.00"`       → 2 decimal places
///   `"0%"`         → percent (0.5 → "50%")
///   `"#,##0.00"`   → thousands separator + 2 dp
///   `"$#,##0.00"`  → dollar prefix + thousands + 2 dp
///   `"yyyy-mm-dd"` → date (Excel serial or ISO string passthrough)
///   unknown        → v.toString()
///
/// Non-numeric [v] or null [pattern] → `v?.toString() ?? ''`.
String formatNumber(dynamic v, String? pattern) {
  if (v == null) return '';
  if (pattern == null || pattern.isEmpty) return v.toString();

  final num? n = v is num ? v : num.tryParse(v.toString().trim());

  switch (pattern) {
    case '0':
      if (n == null) return v.toString();
      return n.round().toString();

    case '0.00':
      if (n == null) return v.toString();
      return n.toStringAsFixed(2);

    case '0%':
      if (n == null) return v.toString();
      return '${(n * 100).round()}%';

    case '#,##0.00':
      if (n == null) return v.toString();
      return _thousandsSep(n.toStringAsFixed(2));

    case r'$#,##0.00':
      if (n == null) return v.toString();
      return '\$${_thousandsSep(n.toStringAsFixed(2))}';

    case 'yyyy-mm-dd':
      if (n != null) {
        // Excel serial: days since 1899-12-30
        final epoch = DateTime(1899, 12, 30);
        final d = epoch.add(Duration(days: n.round()));
        final mm = d.month.toString().padLeft(2, '0');
        final dd = d.day.toString().padLeft(2, '0');
        return '${d.year}-$mm-$dd';
      }
      // ISO-ish string passthrough
      return v.toString();

    default:
      return v.toString();
  }
}

String _thousandsSep(String fixed) {
  // Split on the decimal point if present.
  final parts = fixed.split('.');
  final intPart = parts[0];
  final decPart = parts.length > 1 ? '.${parts[1]}' : '';

  // Handle negative.
  final negative = intPart.startsWith('-');
  final digits = negative ? intPart.substring(1) : intPart;

  final buf = StringBuffer();
  for (var i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 == 0) buf.write(',');
    buf.write(digits[i]);
  }
  return '${negative ? '-' : ''}$buf$decPart';
}

// ── Style helpers (private) ───────────────────────────────────────────────────

/// Build a [CellStyleView] from a raw Univer style dict.
CellStyleView _viewFromDict(Map<String, dynamic> d) {
  return CellStyleView(
    bold: d['bl'] == 1,
    italic: d['it'] == 1,
    underline: _flagSet(d['ul']),
    strike: _flagSet(d['st']),
    wrap: d['tb'] == 3,
    color: _rgb(d['cl']),
    bgColor: _rgb(d['bg']),
    numFmt: _asMapOpt(d['n'])?['pattern']?.toString(),
    hAlign: (d['ht'] as num?)?.toInt() ?? 0,
  );
}

bool _flagSet(dynamic v) {
  if (v == null) return false;
  if (v is Map) return (v['s'] ?? 0) == 1;
  return false;
}

String? _rgb(dynamic v) {
  if (v is Map) return v['rgb']?.toString();
  return null;
}

Map<String, dynamic>? _asMapOpt(dynamic v) {
  if (v is Map<String, dynamic>) return v;
  if (v is Map) return v.map((k, val) => MapEntry(k.toString(), val));
  return null;
}

// ── Sorted-key JSON encode for style dedup ───────────────────────────────────

/// Encode [m] with sorted keys so two semantically identical style dicts
/// produce the same string (used for registry dedup).
String _stableEncode(Map<String, dynamic> m) {
  final sorted = Map.fromEntries(
    m.entries.toList()..sort((a, b) => a.key.compareTo(b.key)),
  );
  return jsonEncode(sorted);
}

// ── Hyperlink plugin constants ────────────────────────────────────────────────

const String _kLinkPlugin = 'SHEET_HYPER_LINK_PLUGIN';

// ── UniverSheetModel extension on UniverSheet ─────────────────────────────────

/// Extension methods that add the extended workbook model onto [UniverSheet].
extension UniverSheetModel on UniverSheet {
  // ─── Internal workbook access helpers ──────────────────────────────────────

  Map<String, dynamic> _wb() => toWorkbook();

  String _activeId(Map<String, dynamic> wb) {
    final sheets = _sm(wb['sheets']);
    final order = (wb['sheetOrder'] as List?)
            ?.map((e) => e.toString())
            .toList() ??
        sheets.keys.toList();
    final idx = activeIndex.clamp(0, order.isEmpty ? 0 : order.length - 1);
    return order.isEmpty ? '' : order[idx];
  }

  Map<String, dynamic> _activeSheet(Map<String, dynamic> wb) =>
      _sm(_sm(wb['sheets'])[_activeId(wb)]);

  Map<String, dynamic> _sm(dynamic v) {
    if (v is Map<String, dynamic>) return v;
    if (v is Map) return v.map((k, val) => MapEntry(k.toString(), val));
    return <String, dynamic>{};
  }

  // ─── Style registry helpers ─────────────────────────────────────────────────

  /// Resolve the style dict for cell (row, col) on the active sheet.
  ///
  /// The cell's `s` field is either a string id (look up in workbook `styles`
  /// registry) or an inline map. Returns an empty map for missing/unknown.
  Map<String, dynamic> _rawStyleDict(Map<String, dynamic> wb, int row, int col) {
    final sheet = _activeSheet(wb);
    final cellData = _sm(sheet['cellData']);
    final cell = _sm(_sm(cellData[row.toString()])[col.toString()]);
    final s = cell['s'];
    if (s == null) return <String, dynamic>{};
    if (s is Map) {
      return _sm(s);
    }
    // String id → registry lookup
    final styles = _sm(wb['styles']);
    final registered = styles[s.toString()];
    if (registered == null) return <String, dynamic>{};
    return _sm(registered);
  }

  /// Resolve style view for cell (row, col).
  CellStyleView resolveStyle(int row, int col) {
    final wb = _wb();
    final d = _rawStyleDict(wb, row, col);
    if (d.isEmpty) return CellStyleView.empty;
    return _viewFromDict(d);
  }

  /// Apply a style [patch] (Univer style dict fragment) to every cell in
  /// [range] on the active sheet. Returns a new [UniverSheet].
  ///
  /// Keys in [patch] with value `null` (or `0` for boolean keys bl/it) are
  /// REMOVED from the effective style. The result is deduped into the workbook
  /// `styles` registry.
  UniverSheet applyStyle(SelRange range, Map<String, dynamic> patch) {
    final wb = _mutableWb();
    final styles = _ensureStyles(wb);
    final sheet = _activeSheet(wb);
    final cellData = _ensureCellData(sheet);

    for (final (r, c) in range.cells) {
      // Get the current raw style dict (deep-copied from registry or inline).
      final current = Map<String, dynamic>.from(
        _rawStyleDict(wb, r, c),
      );

      // Deep-merge the patch.
      _mergePatch(current, patch);

      // Place the new style id back on the cell (or remove s if empty).
      final rKey = r.toString();
      final cKey = c.toString();
      if (!cellData.containsKey(rKey)) {
        cellData[rKey] = <String, dynamic>{};
      }
      final rowMap = (cellData[rKey] as Map<String, dynamic>);
      if (!rowMap.containsKey(cKey)) {
        rowMap[cKey] = <String, dynamic>{};
      }
      final cell = (rowMap[cKey] as Map<String, dynamic>);

      if (current.isEmpty) {
        cell.remove('s');
      } else {
        final encoded = _stableEncode(current);
        // Dedupe: find existing registry entry with same encoded value.
        final existingId = styles.entries
            .where((e) => _stableEncode(_sm(e.value)) == encoded)
            .map((e) => e.key)
            .firstOrNull;
        if (existingId != null) {
          cell['s'] = existingId;
        } else {
          // Mint a new id.
          var n = styles.length;
          String newId;
          do {
            newId = 's-mob-$n';
            n++;
          } while (styles.containsKey(newId));
          styles[newId] = current;
          cell['s'] = newId;
        }
      }
    }

    return UniverSheet.fromWorkbook(wb, active: activeIndex);
  }

  // ─── Hyperlinks ─────────────────────────────────────────────────────────────

  /// Return the URL of the link at (row, col) on the active sheet, or null.
  String? linkAt(int row, int col) {
    final wb = _wb();
    final links = _linksForSheet(wb);
    for (final entry in links) {
      final m = _sm(entry);
      if ((m['row'] as num?)?.toInt() == row &&
          (m['column'] as num?)?.toInt() == col) {
        final payload = m['payload'];
        if (payload is String) return payload;
        return _sm(payload)['url']?.toString();
      }
    }
    return null;
  }

  /// Upsert a hyperlink at (row, col). Optionally sets the cell display text.
  UniverSheet setLink(int row, int col, String url, {String? display}) {
    // Start from the base (possibly with display value written).
    final base = display != null ? setCell(row, col, value: display) : this;
    final wb = base._mutableWb();
    base._upsertLink(wb, row, col, url);
    return UniverSheet.fromWorkbook(wb, active: activeIndex);
  }

  /// Remove the hyperlink at (row, col), if any.
  UniverSheet removeLink(int row, int col) {
    final wb = _mutableWb();
    _removeLink(wb, row, col);
    return UniverSheet.fromWorkbook(wb, active: activeIndex);
  }

  /// Scan the active sheet for URL-like values and convert them to links.
  ///
  /// Handles:
  ///   - Exact markdown: `[display](url)` → display text + link
  ///   - Bare URL: cell text equals a bare `https?://…` URL (trimmed) → link
  ///
  /// Skips cells that already have a link, contain a formula, or have
  /// mixed/non-string content.
  ///
  /// Returns `(newSheet, count)`.
  (UniverSheet, int) convertUrlsToLinks() {
    final wb = _mutableWb();
    final sheet = _activeSheet(wb);
    final cellData = _sm(sheet['cellData']);
    var count = 0;

    final mdRe = RegExp(r'^\[([^\]]+)\]\((https?://[^\s)]+)\)$');
    final bareUrlRe = RegExp(r'https?://[^\s<>"]+');
    const trailChars = '.,;:!?)';

    cellData.forEach((rKey, rowVal) {
      final r = int.tryParse(rKey);
      if (r == null) return;
      _sm(rowVal).forEach((cKey, cell) {
        final c = int.tryParse(cKey);
        if (c == null) return;
        final m = _sm(cell);

        // Skip formula cells.
        if (m['f'] != null) return;

        final v = m['v'];
        if (v == null || v is! String) return;
        final text = v.trim();
        if (text.isEmpty) return;

        // Skip cells that already have a link.
        if (linkAt(r, c) != null) return;

        // Markdown pattern
        final mdMatch = mdRe.firstMatch(text);
        if (mdMatch != null) {
          final display = mdMatch.group(1)!;
          final url = mdMatch.group(2)!;
          // Update cell value to display text.
          final rMap = (_sm(cellData[rKey]));
          rMap[cKey] = {...m, 'v': display};
          cellData[rKey] = rMap;
          _upsertLink(wb, r, c, url);
          count++;
          return;
        }

        // Bare URL: trim trailing punctuation until none remain.
        final urlMatch = bareUrlRe.firstMatch(text);
        if (urlMatch != null) {
          var url = urlMatch.group(0)!;
          while (url.isNotEmpty && trailChars.contains(url[url.length - 1])) {
            url = url.substring(0, url.length - 1);
          }
          // Cell text (trimmed+trail-stripped) must equal the URL.
          var stripped = text;
          while (stripped.isNotEmpty &&
              trailChars.contains(stripped[stripped.length - 1])) {
            stripped = stripped.substring(0, stripped.length - 1);
          }
          if (stripped == url) {
            _upsertLink(wb, r, c, url);
            count++;
          }
        }
      });
    });

    return (UniverSheet.fromWorkbook(wb, active: activeIndex), count);
  }

  // ─── Structural ops ─────────────────────────────────────────────────────────

  /// Insert a blank row at index [at] (0-based), shifting rows at and below.
  UniverSheet insertRow(int at) {
    final wb = _mutableWb();
    final sheet = _activeSheet(wb);
    _shiftRowsUp(sheet, at, 1);
    _shiftRowLinks(wb, at, 1);
    _bumpDim(sheet, 'rowCount', 1);
    return UniverSheet.fromWorkbook(wb, active: activeIndex);
  }

  /// Delete the row at index [at], shifting rows above it down.
  UniverSheet deleteRow(int at) {
    final wb = _mutableWb();
    final sheet = _activeSheet(wb);
    _dropRow(sheet, at);
    _shiftRowLinks(wb, at, -1, dropAt: at);
    final rc = (sheet['rowCount'] as num?)?.toInt() ?? 0;
    if (rc > 0) sheet['rowCount'] = rc - 1;
    return UniverSheet.fromWorkbook(wb, active: activeIndex);
  }

  /// Insert a blank column at index [at] (0-based), shifting cols at and right.
  UniverSheet insertCol(int at) {
    final wb = _mutableWb();
    final sheet = _activeSheet(wb);
    _shiftColsRight(sheet, at, 1);
    _shiftColLinks(wb, at, 1);
    _bumpDim(sheet, 'columnCount', 1);
    return UniverSheet.fromWorkbook(wb, active: activeIndex);
  }

  /// Delete the column at index [at], shifting columns to the right left.
  UniverSheet deleteCol(int at) {
    final wb = _mutableWb();
    final sheet = _activeSheet(wb);
    _dropCol(sheet, at);
    _shiftColLinks(wb, at, -1, dropAt: at);
    final cc = (sheet['columnCount'] as num?)?.toInt() ?? 0;
    if (cc > 0) sheet['columnCount'] = cc - 1;
    return UniverSheet.fromWorkbook(wb, active: activeIndex);
  }

  /// Set the width of column [col] in the `columnData` map.
  UniverSheet setColWidth(int col, double w) {
    final wb = _mutableWb();
    final sheet = _activeSheet(wb);
    final colData = _ensureColData(sheet);
    final key = col.toString();
    final existing = Map<String, dynamic>.from(_sm(colData[key]));
    existing['w'] = w;
    colData[key] = existing;
    return UniverSheet.fromWorkbook(wb, active: activeIndex);
  }

  /// Clear all cells (and their links) in [r].
  UniverSheet clearRange(SelRange r) {
    final wb = _mutableWb();
    final sheet = _activeSheet(wb);
    final cellData = _ensureCellData(sheet);

    for (final (row, col) in r.cells) {
      final rKey = row.toString();
      final cKey = col.toString();
      final rowMap = _sm(cellData[rKey]);
      if (rowMap.containsKey(cKey)) {
        final updated = Map<String, dynamic>.from(rowMap)..remove(cKey);
        if (updated.isEmpty) {
          cellData.remove(rKey);
        } else {
          cellData[rKey] = updated;
        }
      }
      _removeLink(wb, row, col);
    }

    return UniverSheet.fromWorkbook(wb, active: activeIndex);
  }

  // ─── Sort ───────────────────────────────────────────────────────────────────

  /// Sort rows within [range] by the values in column [byCol].
  ///
  /// Numbers sort before strings; empty cells are always last.
  /// If [hasHeader] is true the first row of the range is preserved in place.
  UniverSheet sortRange(
    SelRange range,
    int byCol, {
    required bool asc,
    bool hasHeader = false,
  }) {
    final wb = _mutableWb();
    final sheet = _activeSheet(wb);
    final cellData = _ensureCellData(sheet);
    final links = _linksForSheet(wb);

    final firstDataRow = range.r1 + (hasHeader ? 1 : 0);
    if (firstDataRow > range.r2) {
      return UniverSheet.fromWorkbook(wb, active: activeIndex);
    }

    // Collect (rowIndex, cells map, relevant links) for data rows.
    final rows = <_SortRow>[];
    for (var r = firstDataRow; r <= range.r2; r++) {
      final rowCells = Map<String, dynamic>.from(
        _sm(cellData[r.toString()]),
      );
      // Extract only the columns inside the range.
      final rangeCells = <String, dynamic>{};
      for (var c = range.c1; c <= range.c2; c++) {
        final cKey = c.toString();
        if (rowCells.containsKey(cKey)) rangeCells[cKey] = rowCells[cKey];
      }
      // Collect link entries for this row within the range columns.
      final rowLinks = links
          .where((e) {
            final m = _sm(e);
            final lr = (m['row'] as num?)?.toInt();
            final lc = (m['column'] as num?)?.toInt();
            return lr == r && lc != null && lc >= range.c1 && lc <= range.c2;
          })
          .map((e) => _sm(e))
          .toList();
      final keyVal = _sm(rowCells[byCol.toString()])['v'];
      rows.add(_SortRow(r, rangeCells, rowLinks, keyVal));
    }

    // Sort.
    rows.sort((a, b) {
      final cmp = _sortCompare(a.keyVal, b.keyVal);
      return asc ? cmp : -cmp;
    });

    // Write sorted rows back.
    for (var i = 0; i < rows.length; i++) {
      final targetRow = firstDataRow + i;
      final sr = rows[i];
      final rKey = targetRow.toString();

      // Keep non-range columns from the TARGET row's original data.
      final existing = Map<String, dynamic>.from(_sm(cellData[rKey]));
      // Remove range columns from target.
      for (var c = range.c1; c <= range.c2; c++) {
        existing.remove(c.toString());
      }
      // Merge in the sorted row's range columns.
      existing.addAll(sr.rangeCells);
      if (existing.isEmpty) {
        cellData.remove(rKey);
      } else {
        cellData[rKey] = existing;
      }

      // Update link rows.
      for (final lnk in sr.rowLinks) {
        // Remove old entry (original row index).
        links.removeWhere((e) {
          final m = _sm(e);
          return (m['row'] as num?)?.toInt() == sr.originalRow &&
              (m['column'] as num?)?.toInt() ==
                  (lnk['column'] as num?)?.toInt();
        });
        // Insert updated entry.
        links.add({...lnk, 'row': targetRow});
      }
    }

    _setLinksForSheet(wb, links);
    return UniverSheet.fromWorkbook(wb, active: activeIndex);
  }

  // ─── Freeze pane ────────────────────────────────────────────────────────────

  /// Whether the active worksheet has a freeze pane with ySplit > 0.
  bool get frozen {
    final wb = _wb();
    final sheet = _activeSheet(wb);
    final freeze = _sm(sheet['freeze']);
    final ySplit = (freeze['ySplit'] as num?)?.toInt() ?? 0;
    return ySplit > 0;
  }

  /// Toggle the freeze pane on the active worksheet.
  ///
  /// If frozen → remove the `freeze` key. If not frozen → set to
  /// `{xSplit:1, ySplit:1, startRow:1, startColumn:1}`.
  UniverSheet toggleFreeze() {
    final wb = _mutableWb();
    final sheet = _activeSheet(wb);
    if (frozen) {
      sheet.remove('freeze');
    } else {
      sheet['freeze'] = {
        'xSplit': 1,
        'ySplit': 1,
        'startRow': 1,
        'startColumn': 1,
      };
    }
    return UniverSheet.fromWorkbook(wb, active: activeIndex);
  }

  // ─── TSV ────────────────────────────────────────────────────────────────────

  /// Export [r] as tab-separated values (display values; rows separated by
  /// newlines; grid is rectangular).
  String rangeToTsv(SelRange r) {
    final buf = StringBuffer();
    for (var row = r.r1; row <= r.r2; row++) {
      for (var col = r.c1; col <= r.c2; col++) {
        if (col > r.c1) buf.write('\t');
        buf.write(cellAt(row, col).display);
      }
      if (row < r.r2) buf.write('\n');
    }
    return buf.toString();
  }

  /// Paste tab-separated [tsv] starting at (row, col).
  ///
  /// Empty trailing line (if tsv ends with `\n`) is ignored. Values are
  /// coerced to numbers where possible (same as [UniverSheet.setCell]).
  UniverSheet pasteTsv(int row, int col, String tsv) {
    final lines = tsv.split('\n');
    // Drop trailing empty line.
    final lastLines =
        (lines.isNotEmpty && lines.last.isEmpty) ? lines.sublist(0, lines.length - 1) : lines;

    UniverSheet s = this;
    for (var ri = 0; ri < lastLines.length; ri++) {
      final cells = lastLines[ri].split('\t');
      for (var ci = 0; ci < cells.length; ci++) {
        final val = cells[ci];
        s = s.setCell(row + ri, col + ci, value: val.isEmpty ? null : val);
      }
    }
    return s;
  }

  // ─── Private mutating helpers ────────────────────────────────────────────────

  /// Produce a mutable deep copy of the workbook.
  Map<String, dynamic> _mutableWb() => toWorkbook();

  Map<String, dynamic> _ensureStyles(Map<String, dynamic> wb) {
    if (wb['styles'] == null || wb['styles'] is! Map) {
      wb['styles'] = <String, dynamic>{};
    }
    return _sm(wb['styles']);
  }

  Map<String, dynamic> _ensureCellData(Map<String, dynamic> sheet) {
    if (sheet['cellData'] == null || sheet['cellData'] is! Map) {
      sheet['cellData'] = <String, dynamic>{};
    }
    return _sm(sheet['cellData']);
  }

  Map<String, dynamic> _ensureColData(Map<String, dynamic> sheet) {
    if (sheet['columnData'] == null || sheet['columnData'] is! Map) {
      sheet['columnData'] = <String, dynamic>{};
    }
    return _sm(sheet['columnData']);
  }

  void _mergePatch(Map<String, dynamic> target, Map<String, dynamic> patch) {
    const boolishKeys = {'bl', 'it'};
    patch.forEach((key, val) {
      if (val == null) {
        target.remove(key);
      } else if (boolishKeys.contains(key) && val == 0) {
        target.remove(key);
      } else if ((key == 'ul' || key == 'st') &&
          val is Map &&
          (val['s'] == 0 || val['s'] == null)) {
        target.remove(key);
      } else {
        target[key] = val;
      }
    });
  }

  // ─── Link CRUD helpers ───────────────────────────────────────────────────────

  List<dynamic> _linksForSheet(Map<String, dynamic> wb) {
    final resources = _resourcesList(wb);
    for (final res in resources) {
      if (res is! Map) continue;
      final m = _sm(res);
      if (m['name'] != _kLinkPlugin) continue;
      final data = m['data'];
      Map<String, dynamic> decoded;
      if (data is String) {
        try {
          decoded = _sm(jsonDecode(data));
        } catch (_) {
          decoded = <String, dynamic>{};
        }
      } else {
        decoded = _sm(data);
      }
      final sid = _activeId(wb);
      final sheetLinks = decoded[sid];
      if (sheetLinks is List) return sheetLinks;
      return <dynamic>[];
    }
    return <dynamic>[];
  }

  void _setLinksForSheet(Map<String, dynamic> wb, List<dynamic> newLinks) {
    final resources = _resourcesList(wb);
    final sid = _activeId(wb);

    for (var i = 0; i < resources.length; i++) {
      final res = resources[i];
      if (res is! Map) continue;
      final m = _sm(res);
      if (m['name'] != _kLinkPlugin) continue;
      final data = m['data'];
      Map<String, dynamic> decoded;
      if (data is String) {
        try {
          decoded = _sm(jsonDecode(data));
        } catch (_) {
          decoded = <String, dynamic>{};
        }
      } else {
        decoded = Map<String, dynamic>.from(_sm(data));
      }
      decoded[sid] = newLinks;
      resources[i] = {...m, 'data': jsonEncode(decoded)};
      return;
    }

    // Plugin not present yet — create it.
    resources.add({
      'name': _kLinkPlugin,
      'data': jsonEncode({sid: newLinks}),
    });
  }

  List<dynamic> _resourcesList(Map<String, dynamic> wb) {
    if (wb['resources'] == null || wb['resources'] is! List) {
      wb['resources'] = <dynamic>[];
    }
    return wb['resources'] as List<dynamic>;
  }

  void _upsertLink(Map<String, dynamic> wb, int row, int col, String url) {
    final links = _linksForSheet(wb);
    // Remove existing link at same (row, col).
    links.removeWhere((e) {
      if (e is! Map) return false;
      final m = _sm(e);
      return (m['row'] as num?)?.toInt() == row &&
          (m['column'] as num?)?.toInt() == col;
    });
    links.add({
      'id': 'lnk-$row-$col',
      'row': row,
      'column': col,
      'payload': url,
    });
    _setLinksForSheet(wb, links);
  }

  void _removeLink(Map<String, dynamic> wb, int row, int col) {
    final links = _linksForSheet(wb);
    final before = links.length;
    links.removeWhere((e) {
      if (e is! Map) return false;
      final m = _sm(e);
      return (m['row'] as num?)?.toInt() == row &&
          (m['column'] as num?)?.toInt() == col;
    });
    if (links.length != before) {
      _setLinksForSheet(wb, links);
    }
  }

  // ─── Row/col shift helpers ───────────────────────────────────────────────────

  void _shiftRowsUp(Map<String, dynamic> sheet, int at, int delta) {
    final cellData = _ensureCellData(sheet);
    final rowData = _sm(sheet['rowData']);
    // Collect all row keys >= at, sort descending, shift up.
    final keys = cellData.keys
        .map((k) => int.tryParse(k))
        .whereType<int>()
        .where((r) => r >= at)
        .toList()
      ..sort((a, b) => b.compareTo(a)); // descending
    for (final r in keys) {
      final val = cellData.remove(r.toString());
      cellData[(r + delta).toString()] = val;
    }
    // Shift rowData too if present.
    final rdKeys = rowData.keys
        .map((k) => int.tryParse(k))
        .whereType<int>()
        .where((r) => r >= at)
        .toList()
      ..sort((a, b) => b.compareTo(a));
    final newRd = Map<String, dynamic>.from(rowData);
    for (final r in rdKeys) {
      final val = newRd.remove(r.toString());
      if (val != null) newRd[(r + delta).toString()] = val;
    }
    if (rowData.isNotEmpty || rdKeys.isNotEmpty) {
      sheet['rowData'] = newRd;
    }
  }

  void _dropRow(Map<String, dynamic> sheet, int at) {
    final cellData = _ensureCellData(sheet);
    final rowData = _sm(sheet['rowData']);
    cellData.remove(at.toString());
    // Shift rows above `at` down by 1.
    final keys = cellData.keys
        .map((k) => int.tryParse(k))
        .whereType<int>()
        .where((r) => r > at)
        .toList()
      ..sort((a, b) => a.compareTo(b)); // ascending
    for (final r in keys) {
      final val = cellData.remove(r.toString());
      cellData[(r - 1).toString()] = val;
    }
    rowData.remove(at.toString());
    final newRd = Map<String, dynamic>.from(rowData);
    for (final r in rowData.keys
        .map((k) => int.tryParse(k))
        .whereType<int>()
        .where((r) => r > at)
        .toList()
      ..sort()) {
      final val = newRd.remove(r.toString());
      if (val != null) newRd[(r - 1).toString()] = val;
    }
    if (rowData.isNotEmpty || newRd.isNotEmpty) {
      sheet['rowData'] = newRd;
    }
  }

  void _shiftColsRight(Map<String, dynamic> sheet, int at, int delta) {
    final cellData = _ensureCellData(sheet);
    final colData = _sm(sheet['columnData']);

    // For each row, shift columns >= at right.
    final rowKeys = cellData.keys.toList();
    for (final rKey in rowKeys) {
      final rowMap = Map<String, dynamic>.from(_sm(cellData[rKey]));
      final colKeys = rowMap.keys
          .map((k) => int.tryParse(k))
          .whereType<int>()
          .where((c) => c >= at)
          .toList()
        ..sort((a, b) => b.compareTo(a));
      for (final c in colKeys) {
        final val = rowMap.remove(c.toString());
        rowMap[(c + delta).toString()] = val;
      }
      cellData[rKey] = rowMap;
    }

    // Shift columnData.
    final cdKeys = colData.keys
        .map((k) => int.tryParse(k))
        .whereType<int>()
        .where((c) => c >= at)
        .toList()
      ..sort((a, b) => b.compareTo(a));
    final newCd = Map<String, dynamic>.from(colData);
    for (final c in cdKeys) {
      final val = newCd.remove(c.toString());
      if (val != null) newCd[(c + delta).toString()] = val;
    }
    if (colData.isNotEmpty || cdKeys.isNotEmpty) {
      sheet['columnData'] = newCd;
    }
  }

  void _dropCol(Map<String, dynamic> sheet, int at) {
    final cellData = _ensureCellData(sheet);
    final colData = _sm(sheet['columnData']);

    final rowKeys = cellData.keys.toList();
    for (final rKey in rowKeys) {
      final rowMap = Map<String, dynamic>.from(_sm(cellData[rKey]));
      rowMap.remove(at.toString());
      // Shift cols > at left.
      final colKeys = rowMap.keys
          .map((k) => int.tryParse(k))
          .whereType<int>()
          .where((c) => c > at)
          .toList()
        ..sort((a, b) => a.compareTo(b));
      for (final c in colKeys) {
        final val = rowMap.remove(c.toString());
        rowMap[(c - 1).toString()] = val;
      }
      if (rowMap.isEmpty) {
        cellData.remove(rKey);
      } else {
        cellData[rKey] = rowMap;
      }
    }

    // Shift columnData.
    colData.remove(at.toString());
    final newCd = Map<String, dynamic>.from(colData);
    for (final c in colData.keys
        .map((k) => int.tryParse(k))
        .whereType<int>()
        .where((c) => c > at)
        .toList()
      ..sort()) {
      final val = newCd.remove(c.toString());
      if (val != null) newCd[(c - 1).toString()] = val;
    }
    if (colData.isNotEmpty || newCd.isNotEmpty) {
      sheet['columnData'] = newCd;
    }
  }

  void _shiftRowLinks(
    Map<String, dynamic> wb,
    int at,
    int delta, {
    int? dropAt,
  }) {
    final links = _linksForSheet(wb);
    final updated = <dynamic>[];
    for (final e in links) {
      if (e is! Map) {
        updated.add(e);
        continue;
      }
      final m = _sm(e);
      final r = (m['row'] as num?)?.toInt();
      if (r == null) {
        updated.add(e);
        continue;
      }
      if (dropAt != null && r == dropAt) continue; // drop this link
      if (r >= at) {
        updated.add({...m, 'row': r + delta});
      } else {
        updated.add(e);
      }
    }
    _setLinksForSheet(wb, updated);
  }

  void _shiftColLinks(
    Map<String, dynamic> wb,
    int at,
    int delta, {
    int? dropAt,
  }) {
    final links = _linksForSheet(wb);
    final updated = <dynamic>[];
    for (final e in links) {
      if (e is! Map) {
        updated.add(e);
        continue;
      }
      final m = _sm(e);
      final c = (m['column'] as num?)?.toInt();
      if (c == null) {
        updated.add(e);
        continue;
      }
      if (dropAt != null && c == dropAt) continue;
      if (c >= at) {
        updated.add({...m, 'column': c + delta});
      } else {
        updated.add(e);
      }
    }
    _setLinksForSheet(wb, updated);
  }

  void _bumpDim(Map<String, dynamic> sheet, String key, int delta) {
    final cur = (sheet[key] as num?)?.toInt() ?? 0;
    sheet[key] = cur + delta;
  }
}

// ── Sort comparison ───────────────────────────────────────────────────────────

int _sortCompare(dynamic a, dynamic b) {
  if (a == null && b == null) return 0;
  if (a == null) return 1; // nulls last
  if (b == null) return -1;
  final na = a is num ? a : num.tryParse(a.toString().trim());
  final nb = b is num ? b : num.tryParse(b.toString().trim());
  if (na != null && nb != null) return na.compareTo(nb);
  if (na != null) return -1; // nums before strings
  if (nb != null) return 1;
  return a.toString().toLowerCase().compareTo(b.toString().toLowerCase());
}

// ── Internal sort row holder ──────────────────────────────────────────────────

class _SortRow {
  _SortRow(this.originalRow, this.rangeCells, this.rowLinks, this.keyVal);
  final int originalRow;
  final Map<String, dynamic> rangeCells;
  final List<Map<String, dynamic>> rowLinks;
  final dynamic keyVal;
}
