/// Extended workbook model for Univer `IWorkbookData` snapshots.
///
/// Supplements [UniverSheet] in `univer_parse.dart` with:
///   - [SelRange] – normalised rectangular selection
///   - [CellStyleView] – resolved style view
///   - Style reads / writes ([resolveStyle], [applyStyle])
///   - Number formatting ([formatNumber])
///
/// Hyperlinks, structural ops, sort, freeze and TSV live in `univer_ops.dart`.
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
///
/// Note: thousands separator and decimal point are hardcoded en-US (`','` / `'.'`).
/// Locale-aware formatting would require the `intl` package.
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
        // 1899-12-30: standard serial→date epoch (absorbs Excel's phantom 1900-02-29; matches openpyxl).
        // Serials < 60 (Jan-Feb 1900) are off by one — irrelevant for real data.
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
  final parts = fixed.split('.');
  final intPart = parts[0];
  final decPart = parts.length > 1 ? '.${parts[1]}' : '';

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
///
/// Only top-level keys are sorted; sub-maps are encoded as-is. This is safe
/// because current Univer style sub-maps (e.g. `cl`, `bg`, `ul`, `st`, `n`)
/// are all single-key objects, so insertion order is deterministic.
String _stableEncode(Map<String, dynamic> m) {
  final sorted = Map.fromEntries(
    m.entries.toList()..sort((a, b) => a.key.compareTo(b.key)),
  );
  return jsonEncode(sorted);
}

// ── UniverSheetModel extension on UniverSheet ─────────────────────────────────

/// Extension methods that add the style model onto [UniverSheet].
///
/// Hyperlinks, structural ops, sort, freeze and TSV live in [UniverOps]
/// (`univer_ops.dart`).
extension UniverSheetModel on UniverSheet {
  // ─── Internal workbook access helpers ──────────────────────────────────────

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
  /// Dereference a Univer `s` field: an inline style map, or a string id into
  /// the workbook `styles` registry. Empty map when absent or unresolvable.
  Map<String, dynamic> _deref(Map<String, dynamic> wb, dynamic s) {
    if (s == null) return const <String, dynamic>{};
    if (s is Map) return _sm(s);
    final registered = _sm(wb['styles'])[s.toString()];
    if (registered == null) return const <String, dynamic>{};
    return _sm(registered);
  }

  Map<String, dynamic> _rawStyleDict(Map<String, dynamic> wb, int row, int col) {
    final sheet = _activeSheet(wb);
    final cellData = _sm(sheet['cellData']);
    final cell = _sm(_sm(cellData[row.toString()])[col.toString()]);
    return _deref(wb, cell['s']);
  }

  /// Resolve style view for cell (row, col), following Univer's CASCADE.
  ///
  /// Precedence, most specific first: the cell's own `s` → its row's
  /// `rowData[r].s` → its column's `columnData[c].s` → the worksheet's
  /// `defaultStyle` → the workbook's `defaultStyle`.
  ///
  /// Reading only the cell level (which is all this used to do) meant a sheet
  /// formatted BY COLUMN — the natural way to format a table, and what the web
  /// editor's toolbar produces when you select a column header — rendered
  /// completely unstyled on the phone.
  ///
  /// Uses [rawWorkbook] — no deep copy. Callers must not mutate the returned
  /// [CellStyleView] (it is already an immutable value type).
  CellStyleView resolveStyle(int row, int col) {
    final wb = rawWorkbook;
    final sheet = _activeSheet(wb);

    // Least specific first, so each later layer overwrites the earlier one.
    final merged = <String, dynamic>{};
    for (final layer in [
      _deref(wb, wb['defaultStyle']),
      _deref(wb, sheet['defaultStyle']),
      _deref(wb, _sm(_sm(sheet['columnData'])[col.toString()])['s']),
      _deref(wb, _sm(_sm(sheet['rowData'])[row.toString()])['s']),
      _rawStyleDict(wb, row, col),
    ]) {
      if (layer.isNotEmpty) merged.addAll(layer);
    }
    if (merged.isEmpty) return CellStyleView.empty;
    return _viewFromDict(merged);
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

    // Precompute encoded→id map once so per-cell lookup is O(1) instead of
    // O(styles) per cell.
    final encodedToId = <String, String>{
      for (final e in styles.entries)
        if (e.value is Map) _stableEncode(_sm(e.value)): e.key,
    };

    for (final (r, c) in range.cells) {
      final current = Map<String, dynamic>.from(
        _rawStyleDict(wb, r, c),
      );

      _mergePatch(current, patch);

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
        final existingId = encodedToId[encoded];
        if (existingId != null) {
          cell['s'] = existingId;
        } else {
          var n = styles.length;
          String newId;
          do {
            newId = 's-mob-$n';
            n++;
          } while (styles.containsKey(newId));
          styles[newId] = current;
          cell['s'] = newId;
          // Keep the lookup map in sync so later cells in this range reuse it.
          encodedToId[encoded] = newId;
        }
      }
    }

    return UniverSheet.fromWorkbook(wb, active: activeIndex);
  }

  // ─── Private mutating helpers ────────────────────────────────────────────────

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
}
