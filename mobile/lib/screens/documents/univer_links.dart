/// Hyperlink extension on [UniverSheet].
///
/// Supplements [UniverSheet] (in `univer_parse.dart`) and [UniverOps]
/// (in `univer_ops.dart`) with all hyperlink management:
///   - [linkAt] — read the URL at a cell
///   - [setLink] / [removeLink] — upsert / delete a link
///   - [convertUrlsToLinks] — batch-convert URL-like cell values
///   - [detectAutoLink] — top-level helper to detect bare-URL / Markdown patterns
///
/// All mutations return a NEW [UniverSheet] (immutability rule). No Flutter
/// dependencies — pure Dart, cheaply unit-testable.
library;

import 'dart:convert';
import 'package:lazyclaw_mobile/screens/documents/univer_parse.dart';

// ── Hyperlink plugin constant ─────────────────────────────────────────────────

const String _kLinkPlugin = 'SHEET_HYPER_LINK_PLUGIN';

// ── Auto-link detection ──────────────────────────────────────────────────────

/// Shared link regexes — single source of truth.
final _kMdLinkRe = RegExp(r'^\[([^\]]+)\]\((https?://[^\s)]+)\)$');
final _kBareUrlRe = RegExp(r'^https?://[^\s<>"]+$');
const _kTrailChars = '.,;:!?)';

/// Detect whether [rawText] is a bare URL or a Markdown link `[display](url)`.
///
/// Returns `{url, display?}` when [rawText] should auto-convert to a link.
/// Mirrors the exact semantics used by [UniverLinks.convertUrlsToLinks]:
///   - Markdown: `^\[([^\]]+)\]\(https?://...\)$` — capture display + url.
///   - Bare URL: entire trimmed value IS a URL (trailing punctuation stripped).
///   - Anything else: returns null (mixed text, plain text, formulas).
({String url, String? display})? detectAutoLink(String rawText) {
  final trimmed = rawText.trim();
  if (trimmed.isEmpty) return null;

  // Markdown [display](url)
  final mdMatch = _kMdLinkRe.firstMatch(trimmed);
  if (mdMatch != null) {
    return (url: mdMatch.group(2)!, display: mdMatch.group(1)!);
  }

  // Bare URL: strip trailing punctuation until the whole string is a URL.
  var candidate = trimmed;
  while (candidate.isNotEmpty &&
      _kTrailChars.contains(candidate[candidate.length - 1])) {
    candidate = candidate.substring(0, candidate.length - 1);
  }
  if (_kBareUrlRe.hasMatch(candidate)) {
    return (url: candidate, display: null);
  }

  return null;
}

// ── UniverLinks extension on UniverSheet ──────────────────────────────────────

/// Extension methods that add hyperlink management onto [UniverSheet].
///
/// Kept in a dedicated file to maintain the 800-line limit for all
/// `univer_*.dart` modules.
extension UniverLinks on UniverSheet {
  // ─── Internal workbook access helpers (duplicated, extension-private) ───────
  // Each extension file duplicates these tiny helpers because Dart
  // extension-private members do not cross file boundaries.

  Map<String, dynamic> _mutableWb() => toWorkbook();

  Map<String, dynamic> _sm(dynamic v) {
    if (v is Map<String, dynamic>) return v;
    if (v is Map) return v.map((k, val) => MapEntry(k.toString(), val));
    return <String, dynamic>{};
  }

  String _activeId(Map<String, dynamic> wb) {
    final sheets = _sm(wb['sheets']);
    final order = (wb['sheetOrder'] as List?)
            ?.map((e) => e.toString())
            .toList() ??
        sheets.keys.toList();
    final idx = activeIndex.clamp(0, order.isEmpty ? 0 : order.length - 1);
    return order.isEmpty ? '' : order[idx];
  }

  // ─── Hyperlinks ─────────────────────────────────────────────────────────────

  /// Return the URL of the link at (row, col) on the active sheet, or null.
  ///
  /// Uses [rawWorkbook] — no deep copy. Uses the read-only link accessor so
  /// the stored workbook is never mutated by a pure read.
  String? linkAt(int row, int col) {
    final wb = rawWorkbook;
    final links = _linksForSheetReadOnly(wb);
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
    final sheets = _sm(wb['sheets']);
    final order = (wb['sheetOrder'] as List?)
            ?.map((e) => e.toString())
            .toList() ??
        sheets.keys.toList();
    final idx = activeIndex.clamp(0, order.isEmpty ? 0 : order.length - 1);
    final sid = order.isEmpty ? '' : order[idx];
    final sheet = _sm(_sm(wb['sheets'])[sid]);
    final cellData = _sm(sheet['cellData']);
    var count = 0;

    final mdRe = _kMdLinkRe;
    final bareUrlRe = RegExp(r'https?://[^\s<>"]+');
    const trailChars = _kTrailChars;

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

  // ─── Link CRUD helpers ───────────────────────────────────────────────────────

  /// Read-only variant of [_linksForSheet]: never mutates [wb].
  ///
  /// Used by [linkAt] which routes through [rawWorkbook]. Falls back to an
  /// empty list when `resources` is absent (no ensure-initialisation).
  List<dynamic> _linksForSheetReadOnly(Map<String, dynamic> wb) {
    final raw = wb['resources'];
    if (raw == null || raw is! List) return const [];
    final resources = raw;
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
}
