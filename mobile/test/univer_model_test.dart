// Tests for the extended Univer workbook model (univer_model.dart).
//
// Pure Dart — no Flutter runtime needed.
// Run: cd mobile && flutter test test/univer_model_test.dart
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_model.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_ops.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_parse.dart';

// ── Workbook factory helpers ──────────────────────────────────────────────────

Map<String, dynamic> _simpleWb({Map<String, dynamic>? cellData}) => {
      'sheetOrder': ['s1'],
      'sheets': {
        's1': {
          'name': 'Sheet1',
          'cellData': cellData ?? <String, dynamic>{},
        },
      },
    };

Map<String, dynamic> _wbWithStyles({
  Map<String, dynamic>? cellData,
  Map<String, dynamic>? styles,
}) =>
    {
      'sheetOrder': ['s1'],
      'styles': styles ?? <String, dynamic>{},
      'sheets': {
        's1': {
          'name': 'Sheet1',
          'cellData': cellData ?? <String, dynamic>{},
        },
      },
    };

Map<String, dynamic> _wbWithLinks({
  Map<String, dynamic>? cellData,
  List<dynamic>? links,
}) {
  final sid = 's1';
  return {
    'sheetOrder': [sid],
    'sheets': {
      sid: {
        'name': 'Sheet1',
        'cellData': cellData ?? <String, dynamic>{},
      },
    },
    'resources': [
      {
        'name': 'SHEET_HYPER_LINK_PLUGIN',
        'data': jsonEncode({
          sid: links ?? <dynamic>[],
        }),
      }
    ],
  };
}

void main() {
  // ──────────────────────────────────────────────────────────────────────────
  // SelRange
  // ──────────────────────────────────────────────────────────────────────────
  group('SelRange', () {
    test('normalises r1<=r2 c1<=c2 when given reversed coords', () {
      final r = SelRange(3, 5, 1, 2);
      expect(r.r1, 1);
      expect(r.r2, 3);
      expect(r.c1, 2);
      expect(r.c2, 5);
    });

    test('already-normalised coords unchanged', () {
      final r = SelRange(0, 0, 2, 2);
      expect(r.r1, 0);
      expect(r.r2, 2);
    });

    test('rowCount / colCount', () {
      final r = SelRange(1, 1, 3, 4);
      expect(r.rowCount, 3);
      expect(r.colCount, 4);
    });

    test('isSingle when 1×1', () {
      expect(SelRange(2, 3, 2, 3).isSingle, isTrue);
      expect(SelRange(2, 3, 2, 4).isSingle, isFalse);
    });

    test('contains inside and outside', () {
      final r = SelRange(1, 1, 3, 3);
      expect(r.contains(2, 2), isTrue);
      expect(r.contains(1, 1), isTrue);
      expect(r.contains(3, 3), isTrue);
      expect(r.contains(0, 0), isFalse);
      expect(r.contains(4, 4), isFalse);
    });

    test('cells yields all (row, col) pairs row-major', () {
      final r = SelRange(0, 0, 1, 1);
      final pairs = r.cells.toList();
      expect(pairs, [(0, 0), (0, 1), (1, 0), (1, 1)]);
    });

    test('cells for single cell', () {
      final r = SelRange(2, 3, 2, 3);
      expect(r.cells.toList(), [(2, 3)]);
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // resolveStyle
  // ──────────────────────────────────────────────────────────────────────────
  group('resolveStyle', () {
    test('returns defaults for a cell with no style', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final s = sheet.resolveStyle(0, 0);
      expect(s.bold, isFalse);
      expect(s.italic, isFalse);
      expect(s.color, isNull);
    });

    test('resolves style from registry id', () {
      final sheet = UniverSheet.fromWorkbook(_wbWithStyles(
        styles: {
          'sid1': {'bl': 1, 'it': 1, 'cl': {'rgb': '#ff0000'}},
        },
        cellData: {
          '0': {'0': {'v': 'Hello', 's': 'sid1'}},
        },
      ));
      final s = sheet.resolveStyle(0, 0);
      expect(s.bold, isTrue);
      expect(s.italic, isTrue);
      expect(s.color, '#ff0000');
    });

    test('resolves inline style map on cell', () {
      final sheet = UniverSheet.fromWorkbook(_wbWithStyles(
        cellData: {
          '2': {
            '1': {
              'v': 'X',
              's': {'bl': 1, 'bg': {'rgb': '#00ff00'}},
            },
          },
        },
      ));
      final s = sheet.resolveStyle(2, 1);
      expect(s.bold, isTrue);
      expect(s.bgColor, '#00ff00');
    });

    test('underline and strikethrough flags', () {
      final sheet = UniverSheet.fromWorkbook(_wbWithStyles(
        styles: {
          'u': {'ul': {'s': 1}, 'st': {'s': 1}},
        },
        cellData: {
          '0': {'0': {'v': 'A', 's': 'u'}},
        },
      ));
      final s = sheet.resolveStyle(0, 0);
      expect(s.underline, isTrue);
      expect(s.strike, isTrue);
    });

    test('wrap, hAlign, numFmt', () {
      final sheet = UniverSheet.fromWorkbook(_wbWithStyles(
        styles: {
          'fmt': {
            'tb': 3,
            'ht': 2,
            'n': {'pattern': '0.00'},
          },
        },
        cellData: {
          '1': {'1': {'v': 1.5, 's': 'fmt'}},
        },
      ));
      final s = sheet.resolveStyle(1, 1);
      expect(s.wrap, isTrue);
      expect(s.hAlign, 2);
      expect(s.numFmt, '0.00');
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // applyStyle
  // ──────────────────────────────────────────────────────────────────────────
  group('applyStyle', () {
    test('bold a single cell, creates registry entry', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'Hi'}},
      }));
      final next = sheet.applyStyle(SelRange(0, 0, 0, 0), {'bl': 1});
      expect(next.resolveStyle(0, 0).bold, isTrue);
      // Original untouched.
      expect(sheet.resolveStyle(0, 0).bold, isFalse);
    });

    test('bold a range including empty cell — creates cell entry', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final next = sheet.applyStyle(SelRange(0, 0, 1, 1), {'bl': 1});
      expect(next.resolveStyle(0, 0).bold, isTrue);
      expect(next.resolveStyle(0, 1).bold, isTrue);
      expect(next.resolveStyle(1, 0).bold, isTrue);
      expect(next.resolveStyle(1, 1).bold, isTrue);
    });

    test('two cells with same style share one registry entry', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'A'}, '1': {'v': 'B'}},
      }));
      final next = sheet.applyStyle(SelRange(0, 0, 0, 1), {'bl': 1});
      final wb = next.toWorkbook();
      final cellData = wb['sheets']['s1']['cellData'] as Map;
      final s0 = (cellData['0'] as Map)['0']['s'];
      final s1 = (cellData['0'] as Map)['1']['s'];
      expect(s0, equals(s1)); // same registry id
    });

    test('un-bold with bl:null removes the key from style', () {
      final sheet = UniverSheet.fromWorkbook(_wbWithStyles(
        styles: {'bold': {'bl': 1}},
        cellData: {
          '0': {'0': {'v': 'X', 's': 'bold'}},
        },
      ));
      final next = sheet.applyStyle(SelRange(0, 0, 0, 0), {'bl': null});
      expect(next.resolveStyle(0, 0).bold, isFalse);
    });

    test('un-bold with bl:0 removes the key from style', () {
      final sheet = UniverSheet.fromWorkbook(_wbWithStyles(
        styles: {'bold': {'bl': 1}},
        cellData: {
          '0': {'0': {'v': 'X', 's': 'bold'}},
        },
      ));
      final next = sheet.applyStyle(SelRange(0, 0, 0, 0), {'bl': 0});
      expect(next.resolveStyle(0, 0).bold, isFalse);
    });

    test('color patch is applied', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'A'}},
      }));
      final next = sheet.applyStyle(
          SelRange(0, 0, 0, 0), {'cl': {'rgb': '#123456'}});
      expect(next.resolveStyle(0, 0).color, '#123456');
    });

    test('hAlign patch', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final next = sheet.applyStyle(SelRange(0, 0, 0, 0), {'ht': 3});
      expect(next.resolveStyle(0, 0).hAlign, 3);
    });

    test('wrap patch', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final next = sheet.applyStyle(SelRange(0, 0, 0, 0), {'tb': 3});
      expect(next.resolveStyle(0, 0).wrap, isTrue);
    });

    test('numFmt patch', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final next = sheet.applyStyle(
          SelRange(0, 0, 0, 0), {'n': {'pattern': '0%'}});
      expect(next.resolveStyle(0, 0).numFmt, '0%');
    });

    test('empty style dict after patch removes s from cell', () {
      final sheet = UniverSheet.fromWorkbook(_wbWithStyles(
        styles: {'bold': {'bl': 1}},
        cellData: {
          '0': {'0': {'v': 'X', 's': 'bold'}},
        },
      ));
      // Remove the only style key.
      final next = sheet.applyStyle(SelRange(0, 0, 0, 0), {'bl': null});
      final wb = next.toWorkbook();
      final cell = (wb['sheets']['s1']['cellData'] as Map)['0']['0'] as Map;
      expect(cell.containsKey('s'), isFalse);
    });

    test('original workbook unchanged after applyStyle (immutability)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'Z'}},
      }));
      final original = sheet.toWorkbook();
      sheet.applyStyle(SelRange(0, 0, 0, 0), {'bl': 1});
      expect(sheet.toWorkbook(), equals(original));
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // formatNumber
  // ──────────────────────────────────────────────────────────────────────────
  group('formatNumber', () {
    test('"0" rounds to integer', () {
      expect(formatNumber(42.7, '0'), '43');
      expect(formatNumber(42.3, '0'), '42');
    });

    test('"0.00" two decimal places', () {
      expect(formatNumber(3.1, '0.00'), '3.10');
      expect(formatNumber(1000.0, '0.00'), '1000.00');
    });

    test('"0%" percent', () {
      expect(formatNumber(0.5, '0%'), '50%');
      expect(formatNumber(1.0, '0%'), '100%');
    });

    test('"#,##0.00" thousands separator', () {
      expect(formatNumber(1234567.89, '#,##0.00'), '1,234,567.89');
      expect(formatNumber(1000.0, '#,##0.00'), '1,000.00');
    });

    test(r'"$#,##0.00" dollar prefix', () {
      expect(formatNumber(1234.5, r'$#,##0.00'), '\$1,234.50');
    });

    test('"yyyy-mm-dd" Excel serial (1 = 1899-12-31)', () {
      // Excel serial 1 = 1899-12-31 (epoch + 1 day)
      final result = formatNumber(1, 'yyyy-mm-dd');
      expect(result, '1899-12-31');
    });

    test('"yyyy-mm-dd" modern date serial 45292 = 2024-01-01 (epoch pin)', () {
      // Verifies 1899-12-30 epoch: days(1899-12-30 → 2024-01-01) = 45292 (matches openpyxl).
      expect(formatNumber(45292, 'yyyy-mm-dd'), '2024-01-01');
    });

    test('"#,##0.00" negative number keeps minus sign', () {
      expect(formatNumber(-1234567.89, '#,##0.00'), '-1,234,567.89');
    });

    test('"yyyy-mm-dd" passes through ISO string', () {
      expect(formatNumber('2024-06-15', 'yyyy-mm-dd'), '2024-06-15');
    });

    test('null pattern → v.toString()', () {
      expect(formatNumber(42, null), '42');
    });

    test('non-num with a numeric pattern → v.toString()', () {
      expect(formatNumber('hello', '0.00'), 'hello');
    });

    test('null v → empty string', () {
      expect(formatNumber(null, '0'), '');
    });

    test('unknown pattern → v.toString()', () {
      expect(formatNumber(99, 'custom_fmt'), '99');
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // linkAt / setLink / removeLink
  // ──────────────────────────────────────────────────────────────────────────
  group('linkAt / setLink / removeLink', () {
    test('linkAt returns null when no links exist', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      expect(sheet.linkAt(0, 0), isNull);
    });

    test('setLink stores a link and linkAt retrieves it', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final next = sheet.setLink(0, 0, 'https://example.com');
      expect(next.linkAt(0, 0), 'https://example.com');
      // Original untouched.
      expect(sheet.linkAt(0, 0), isNull);
    });

    test('setLink with display sets the cell value', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final next =
          sheet.setLink(1, 2, 'https://example.com', display: 'Example');
      expect(next.cellAt(1, 2).display, 'Example');
      expect(next.linkAt(1, 2), 'https://example.com');
    });

    test('setLink upserts — replaces existing link at same cell', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final s2 = sheet.setLink(0, 0, 'https://first.com');
      final s3 = s2.setLink(0, 0, 'https://second.com');
      expect(s3.linkAt(0, 0), 'https://second.com');
    });

    test('original workbook unchanged after setLink (immutability)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'A'}},
      }));
      final original = sheet.toWorkbook();
      sheet.setLink(0, 0, 'https://example.com', display: 'Example');
      expect(sheet.toWorkbook(), equals(original));
    });

    test('removeLink removes the link', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb())
          .setLink(0, 0, 'https://example.com');
      final next = sheet.removeLink(0, 0);
      expect(next.linkAt(0, 0), isNull);
      // Original untouched.
      expect(sheet.linkAt(0, 0), 'https://example.com');
    });

    test('original workbook unchanged after removeLink (immutability)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb())
          .setLink(0, 0, 'https://example.com');
      final original = sheet.toWorkbook();
      sheet.removeLink(0, 0);
      expect(sheet.toWorkbook(), equals(original));
    });

    test('removeLink on cell with no link is a no-op', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final next = sheet.removeLink(5, 5);
      expect(next.linkAt(5, 5), isNull);
    });

    test('existing link from resources list is read', () {
      final sheet = UniverSheet.fromWorkbook(_wbWithLinks(
        links: [
          {'id': 'l1', 'row': 3, 'column': 2, 'payload': 'https://test.com'},
        ],
      ));
      expect(sheet.linkAt(3, 2), 'https://test.com');
    });

    test('non-dict resources entries are preserved', () {
      final wb = {
        'sheetOrder': ['s1'],
        'sheets': {
          's1': {'name': 'S1', 'cellData': <String, dynamic>{}},
        },
        'resources': ['plain_string', 42],
      };
      final sheet = UniverSheet.fromWorkbook(wb)
          .setLink(0, 0, 'https://example.com');
      final out = sheet.toWorkbook();
      final resources = out['resources'] as List;
      // The non-map entries are preserved.
      expect(resources.any((e) => e == 'plain_string'), isTrue);
    });

    test('other plugin entries are preserved', () {
      final wb = {
        'sheetOrder': ['s1'],
        'sheets': {
          's1': {'name': 'S1', 'cellData': <String, dynamic>{}},
        },
        'resources': [
          {'name': 'OTHER_PLUGIN', 'data': 'some-data'},
        ],
      };
      final sheet = UniverSheet.fromWorkbook(wb)
          .setLink(0, 0, 'https://example.com');
      final resources = sheet.toWorkbook()['resources'] as List;
      final other =
          resources.where((e) => e is Map && e['name'] == 'OTHER_PLUGIN');
      expect(other.length, 1);
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // convertUrlsToLinks
  // ──────────────────────────────────────────────────────────────────────────
  group('convertUrlsToLinks', () {
    test('markdown link converted, display text + link set, count returned', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': '[Example](https://example.com)'}},
      }));
      final (next, count) = sheet.convertUrlsToLinks();
      expect(count, 1);
      expect(next.cellAt(0, 0).display, 'Example');
      expect(next.linkAt(0, 0), 'https://example.com');
    });

    test('bare URL converted and link set', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '1': {'0': {'v': 'https://bare.example.com'}},
      }));
      final (next, count) = sheet.convertUrlsToLinks();
      expect(count, 1);
      expect(next.linkAt(1, 0), 'https://bare.example.com');
    });

    test('trailing punctuation stripped from bare URL', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'https://example.com.'}},
      }));
      final (next, count) = sheet.convertUrlsToLinks();
      expect(count, 1);
      expect(next.linkAt(0, 0), 'https://example.com');
    });

    test('formula cells are skipped', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {
          '0': {'v': 'https://example.com', 'f': '=A1'},
        },
      }));
      final (_, count) = sheet.convertUrlsToLinks();
      expect(count, 0);
    });

    test('cells already linked are skipped', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'https://example.com'}},
      })).setLink(0, 0, 'https://already.com');
      final (_, count) = sheet.convertUrlsToLinks();
      expect(count, 0);
    });

    test('mixed text with URL not exactly matching is skipped', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'click here https://example.com for info'}},
      }));
      final (_, count) = sheet.convertUrlsToLinks();
      expect(count, 0);
    });

    test('idempotent — second call on same sheet converts 0 more', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'https://example.com'}},
      }));
      final (next, _) = sheet.convertUrlsToLinks();
      final (_, count2) = next.convertUrlsToLinks();
      expect(count2, 0);
    });

    test('count reflects multiple converted cells', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {
          '0': {'v': 'https://a.com'},
          '1': {'v': 'https://b.com'},
          '2': {'v': 'not a url'},
        },
      }));
      final (_, count) = sheet.convertUrlsToLinks();
      expect(count, 2);
    });

    test('original workbook unchanged after convertUrlsToLinks (immutability)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {
          '0': {'v': 'https://a.com'},
          '1': {'v': '[Foo](https://foo.com)'},
        },
      }));
      final original = sheet.toWorkbook();
      sheet.convertUrlsToLinks();
      expect(sheet.toWorkbook(), equals(original));
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // insertRow / deleteRow
  // ──────────────────────────────────────────────────────────────────────────
  group('insertRow', () {
    test('shifts cells below at downward', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'Row0'}},
        '2': {'0': {'v': 'Row2'}},
      }));
      final next = sheet.insertRow(1);
      // Row0 stays at 0.
      expect(next.cellAt(0, 0).display, 'Row0');
      // Original Row2 shifts to Row3.
      expect(next.cellAt(3, 0).display, 'Row2');
      // New row 1 is empty.
      expect(next.cellAt(1, 0).display, '');
    });

    test('rowCount incremented', () {
      final wb = {
        'sheetOrder': ['s1'],
        'sheets': {
          's1': {'name': 'S1', 'rowCount': 5, 'cellData': <String, dynamic>{}},
        },
      };
      final next = UniverSheet.fromWorkbook(wb).insertRow(2);
      expect(next.toWorkbook()['sheets']['s1']['rowCount'], 6);
    });

    test('links shift when row inserted above them', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '2': {'1': {'v': 'linked'}},
      })).setLink(2, 1, 'https://example.com');
      final next = sheet.insertRow(1);
      // Link moved from row 2 to row 3.
      expect(next.linkAt(3, 1), 'https://example.com');
      expect(next.linkAt(2, 1), isNull);
    });

    test('original unchanged after insertRow (immutability)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'X'}},
      }));
      final before = sheet.toWorkbook();
      sheet.insertRow(0);
      expect(sheet.toWorkbook(), equals(before));
    });
  });

  group('deleteRow', () {
    test('drops row at index and shifts rows above down', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'R0'}},
        '1': {'0': {'v': 'R1'}},
        '2': {'0': {'v': 'R2'}},
      }));
      final next = sheet.deleteRow(1);
      expect(next.cellAt(0, 0).display, 'R0'); // unchanged
      expect(next.cellAt(1, 0).display, 'R2'); // shifted from 2→1
    });

    test('links in deleted row are removed, links above shift down', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '1': {'0': {'v': 'linked row 1'}},
        '2': {'0': {'v': 'linked row 2'}},
      }))
          .setLink(1, 0, 'https://del.com')
          .setLink(2, 0, 'https://shift.com');
      final next = sheet.deleteRow(1);
      // Row 1's link is deleted.
      expect(next.linkAt(0, 0), isNull);
      // Row 2's link shifted to row 1.
      expect(next.linkAt(1, 0), 'https://shift.com');
    });

    test('original unchanged after deleteRow (immutability)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'Y'}},
        '1': {'0': {'v': 'Z'}},
      }));
      final before = sheet.toWorkbook();
      sheet.deleteRow(0);
      expect(sheet.toWorkbook(), equals(before));
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // insertCol / deleteCol
  // ──────────────────────────────────────────────────────────────────────────
  group('insertCol', () {
    test('shifts cells right of at', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'C0'}, '1': {'v': 'C1'}},
      }));
      final next = sheet.insertCol(1);
      expect(next.cellAt(0, 0).display, 'C0');
      expect(next.cellAt(0, 1).display, ''); // new blank col
      expect(next.cellAt(0, 2).display, 'C1'); // shifted
    });

    test('columnCount incremented', () {
      final wb = {
        'sheetOrder': ['s1'],
        'sheets': {
          's1': {
            'name': 'S1',
            'columnCount': 3,
            'cellData': <String, dynamic>{},
          },
        },
      };
      final next = UniverSheet.fromWorkbook(wb).insertCol(1);
      expect(next.toWorkbook()['sheets']['s1']['columnCount'], 4);
    });

    test('links shift when col inserted to their left', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'2': {'v': 'linked'}},
      })).setLink(0, 2, 'https://link.com');
      final next = sheet.insertCol(1);
      expect(next.linkAt(0, 3), 'https://link.com');
      expect(next.linkAt(0, 2), isNull);
    });

    test('original unchanged after insertCol (immutability)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'A'}},
      }));
      final before = sheet.toWorkbook();
      sheet.insertCol(0);
      expect(sheet.toWorkbook(), equals(before));
    });
  });

  group('deleteCol', () {
    test('drops col and shifts remaining columns left', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {
          '0': {'v': 'A'},
          '1': {'v': 'B'},
          '2': {'v': 'C'},
        },
      }));
      final next = sheet.deleteCol(1);
      expect(next.cellAt(0, 0).display, 'A');
      expect(next.cellAt(0, 1).display, 'C');
    });

    test('links in deleted col are removed, links to right shift', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'1': {'v': 'X'}, '2': {'v': 'Y'}},
      }))
          .setLink(0, 1, 'https://del.com')
          .setLink(0, 2, 'https://shift.com');
      final next = sheet.deleteCol(1);
      expect(next.linkAt(0, 1), 'https://shift.com');
      expect(next.linkAt(0, 0), isNull);
    });

    test('original unchanged after deleteCol (immutability)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'Q'}, '1': {'v': 'R'}},
      }));
      final before = sheet.toWorkbook();
      sheet.deleteCol(0);
      expect(sheet.toWorkbook(), equals(before));
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // setColWidth
  // ──────────────────────────────────────────────────────────────────────────
  group('setColWidth', () {
    test('stores width in columnData', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final next = sheet.setColWidth(2, 120.0);
      final wd = next.toWorkbook()['sheets']['s1']['columnData'] as Map;
      expect((wd['2'] as Map)['w'], 120.0);
    });

    test('merges with existing columnData entry', () {
      final wb = {
        'sheetOrder': ['s1'],
        'sheets': {
          's1': {
            'name': 'S1',
            'columnData': {
              '2': {'hidden': false},
            },
            'cellData': <String, dynamic>{},
          },
        },
      };
      final next = UniverSheet.fromWorkbook(wb).setColWidth(2, 80.0);
      final wd = next.toWorkbook()['sheets']['s1']['columnData'] as Map;
      expect((wd['2'] as Map)['w'], 80.0);
      expect((wd['2'] as Map)['hidden'], false);
    });

    test('original unchanged after setColWidth (immutability)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final before = sheet.toWorkbook();
      sheet.setColWidth(0, 200.0);
      expect(sheet.toWorkbook(), equals(before));
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // clearRange
  // ──────────────────────────────────────────────────────────────────────────
  group('clearRange', () {
    test('removes cells in range', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'A'}, '1': {'v': 'B'}},
        '1': {'0': {'v': 'C'}},
      }));
      final next = sheet.clearRange(SelRange(0, 0, 0, 1));
      expect(next.cellAt(0, 0).display, '');
      expect(next.cellAt(0, 1).display, '');
      expect(next.cellAt(1, 0).display, 'C'); // outside range, kept
    });

    test('removes links in range', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '1': {'1': {'v': 'link'}},
      })).setLink(1, 1, 'https://example.com');
      final next = sheet.clearRange(SelRange(1, 1, 1, 1));
      expect(next.linkAt(1, 1), isNull);
    });

    test('original unchanged after clearRange (immutability)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'Z'}},
      }));
      final before = sheet.toWorkbook();
      sheet.clearRange(SelRange(0, 0, 0, 0));
      expect(sheet.toWorkbook(), equals(before));
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // sortRange
  // ──────────────────────────────────────────────────────────────────────────
  group('sortRange', () {
    UniverSheet _twoColSheet() => UniverSheet.fromWorkbook(_simpleWb(cellData: {
          '0': {'0': {'v': 3}, '1': {'v': 'C'}},
          '1': {'0': {'v': 1}, '1': {'v': 'A'}},
          '2': {'0': {'v': 2}, '1': {'v': 'B'}},
        }));

    test('numeric ascending by col 0', () {
      final next = _twoColSheet().sortRange(
        SelRange(0, 0, 2, 1),
        0,
        asc: true,
      );
      expect(next.cellAt(0, 0).value, 1);
      expect(next.cellAt(1, 0).value, 2);
      expect(next.cellAt(2, 0).value, 3);
    });

    test('numeric descending by col 0', () {
      final next = _twoColSheet().sortRange(
        SelRange(0, 0, 2, 1),
        0,
        asc: false,
      );
      expect(next.cellAt(0, 0).value, 3);
      expect(next.cellAt(1, 0).value, 2);
      expect(next.cellAt(2, 0).value, 1);
    });

    test('string sort ascending by col 1', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'Z'}},
        '1': {'0': {'v': 'A'}},
        '2': {'0': {'v': 'M'}},
      }));
      final next = sheet.sortRange(SelRange(0, 0, 2, 0), 0, asc: true);
      expect(next.cellAt(0, 0).display, 'A');
      expect(next.cellAt(1, 0).display, 'M');
      expect(next.cellAt(2, 0).display, 'Z');
    });

    test('hasHeader preserves first row', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'Name'}}, // header
        '1': {'0': {'v': 'Z'}},
        '2': {'0': {'v': 'A'}},
      }));
      final next = sheet.sortRange(
        SelRange(0, 0, 2, 0),
        0,
        asc: true,
        hasHeader: true,
      );
      expect(next.cellAt(0, 0).display, 'Name'); // header preserved
      expect(next.cellAt(1, 0).display, 'A');
      expect(next.cellAt(2, 0).display, 'Z');
    });

    test('empty cells sort last', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': <String, dynamic>{}}, // empty
        '1': {'0': {'v': 3}},
        '2': {'0': {'v': 1}},
      }));
      final next =
          sheet.sortRange(SelRange(0, 0, 2, 0), 0, asc: true);
      expect(next.cellAt(0, 0).value, 1);
      expect(next.cellAt(1, 0).value, 3);
      expect(next.cellAt(2, 0).display, ''); // empty last
    });

    test('links move with their rows', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 3}, '1': {'v': 'link row'}},
        '1': {'0': {'v': 1}, '1': {'v': 'other'}},
      })).setLink(0, 1, 'https://linked.com');
      final next = sheet.sortRange(SelRange(0, 0, 1, 1), 0, asc: true);
      // Original row 0 (v=3) moves to index 1.
      expect(next.linkAt(1, 1), 'https://linked.com');
      expect(next.linkAt(0, 1), isNull);
    });

    test('original unchanged after sortRange (immutability)', () {
      final sheet = _twoColSheet();
      final before = sheet.toWorkbook();
      sheet.sortRange(SelRange(0, 0, 2, 1), 0, asc: true);
      expect(sheet.toWorkbook(), equals(before));
    });

    test('sortRange with range starting at c1=2 sorts by byCol=3 (absolute column semantics)', () {
      // Range is columns 2–3; byCol=3 is the absolute column index (not relative).
      // Row 0: col2=10, col3='Z'; Row 1: col2=20, col3='A'; Row 2: col2=30, col3='M'
      // After asc sort by col3: A(row1), M(row2), Z(row0) → col2 values: 20,30,10.
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'2': {'v': 10}, '3': {'v': 'Z'}},
        '1': {'2': {'v': 20}, '3': {'v': 'A'}},
        '2': {'2': {'v': 30}, '3': {'v': 'M'}},
      }));
      final next = sheet.sortRange(SelRange(0, 2, 2, 3), 3, asc: true);
      expect(next.cellAt(0, 3).display, 'A');
      expect(next.cellAt(1, 3).display, 'M');
      expect(next.cellAt(2, 3).display, 'Z');
      // Corresponding col2 values follow their rows.
      expect(next.cellAt(0, 2).value, 20);
      expect(next.cellAt(1, 2).value, 30);
      expect(next.cellAt(2, 2).value, 10);
    });

    test('sortRange desc keeps empty cells last', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': <String, dynamic>{}}, // empty
        '1': {'0': {'v': 3}},
        '2': {'0': {'v': 1}},
      }));
      final next = sheet.sortRange(SelRange(0, 0, 2, 0), 0, asc: false);
      // Desc: 3, 1, empty — empty must be LAST
      expect(next.cellAt(0, 0).value, 3);
      expect(next.cellAt(1, 0).value, 1);
      expect(next.cellAt(2, 0).display, ''); // empty last even in desc
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // toggleFreeze / frozen
  // ──────────────────────────────────────────────────────────────────────────
  group('freeze', () {
    test('frozen is false on a fresh sheet', () {
      expect(UniverSheet.fromWorkbook(_simpleWb()).frozen, isFalse);
    });

    test('toggleFreeze sets freeze pane', () {
      final next = UniverSheet.fromWorkbook(_simpleWb()).toggleFreeze();
      expect(next.frozen, isTrue);
      final freeze = next.toWorkbook()['sheets']['s1']['freeze'] as Map;
      expect(freeze['ySplit'], 1);
      expect(freeze['xSplit'], 1);
    });

    test('toggleFreeze removes freeze pane when already frozen', () {
      final frozen = UniverSheet.fromWorkbook(_simpleWb()).toggleFreeze();
      final unfrozen = frozen.toggleFreeze();
      expect(unfrozen.frozen, isFalse);
      final sheet = unfrozen.toWorkbook()['sheets']['s1'] as Map;
      expect(sheet.containsKey('freeze'), isFalse);
    });

    test('original unchanged after toggleFreeze (immutability)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final before = sheet.toWorkbook();
      sheet.toggleFreeze();
      expect(sheet.toWorkbook(), equals(before));
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // rangeToTsv / pasteTsv
  // ──────────────────────────────────────────────────────────────────────────
  group('rangeToTsv', () {
    test('exports 2×2 grid as TSV', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'A'}, '1': {'v': 'B'}},
        '1': {'0': {'v': 'C'}, '1': {'v': 'D'}},
      }));
      expect(sheet.rangeToTsv(SelRange(0, 0, 1, 1)), 'A\tB\nC\tD');
    });

    test('single cell TSV', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '2': {'3': {'v': 42}},
      }));
      expect(sheet.rangeToTsv(SelRange(2, 3, 2, 3)), '42');
    });
  });

  group('pasteTsv', () {
    test('basic round-trip', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final tsv = 'Hello\tWorld\nFoo\tBar';
      final next = sheet.pasteTsv(0, 0, tsv);
      expect(next.cellAt(0, 0).display, 'Hello');
      expect(next.cellAt(0, 1).display, 'World');
      expect(next.cellAt(1, 0).display, 'Foo');
      expect(next.cellAt(1, 1).display, 'Bar');
    });

    test('numeric values are coerced', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final next = sheet.pasteTsv(0, 0, '42\t3.14');
      expect(next.cellAt(0, 0).value, anyOf(42, '42'));
      final v = next.cellAt(0, 1).value;
      expect(v is num ? v : double.tryParse(v.toString()), closeTo(3.14, 0.001));
    });

    test('trailing empty line in TSV is ignored', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final next = sheet.pasteTsv(0, 0, 'A\tB\n');
      expect(next.cellAt(0, 0).display, 'A');
      expect(next.cellAt(1, 0).display, ''); // no second row
    });

    test('rangeToTsv + pasteTsv round-trip preserves values', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {
          '0': {'v': 10},
          '1': {'v': 20},
        },
        '1': {
          '0': {'v': 30},
          '1': {'v': 40},
        },
      }));
      final tsv = sheet.rangeToTsv(SelRange(0, 0, 1, 1));
      final pasted = UniverSheet.fromWorkbook(_simpleWb()).pasteTsv(0, 0, tsv);
      expect(pasted.cellAt(0, 0).display, '10');
      expect(pasted.cellAt(0, 1).display, '20');
      expect(pasted.cellAt(1, 0).display, '30');
      expect(pasted.cellAt(1, 1).display, '40');
    });

    test('original unchanged after pasteTsv (immutability)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      final before = sheet.toWorkbook();
      sheet.pasteTsv(0, 0, 'X\tY');
      expect(sheet.toWorkbook(), equals(before));
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // rawWorkbook — zero-copy read-path tests
  // ──────────────────────────────────────────────────────────────────────────
  group('rawWorkbook zero-copy read path', () {
    test('rawWorkbook identity is stable across calls (no copy per call)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb());
      // identical() verifies same object in memory — no deep copy was made.
      expect(identical(sheet.rawWorkbook, sheet.rawWorkbook), isTrue);
    });

    test('resolveStyle does not mutate the workbook (toWorkbook unchanged)', () {
      final sheet = UniverSheet.fromWorkbook(_wbWithStyles(
        styles: {
          'sid1': {'bl': 1, 'it': 1, 'cl': {'rgb': '#ff0000'}},
        },
        cellData: {
          '0': {'0': {'v': 'Hello', 's': 'sid1'}},
        },
      ));
      final before = sheet.toWorkbook();
      // Call resolveStyle multiple times to simulate per-cell grid rebuilds.
      final s1 = sheet.resolveStyle(0, 0);
      final s2 = sheet.resolveStyle(0, 0);
      final s3 = sheet.resolveStyle(1, 1); // empty cell
      // Workbook state is unchanged.
      expect(sheet.toWorkbook(), equals(before));
      // Repeated reads return consistent results.
      expect(s1.bold, isTrue);
      expect(s2.bold, isTrue);
      expect(s1.italic, isTrue);
      expect(s2.italic, isTrue);
      expect(s3.bold, isFalse);
    });

    test('linkAt does not mutate the workbook (toWorkbook unchanged)', () {
      final sheet = UniverSheet.fromWorkbook(_wbWithLinks(
        links: [
          {'id': 'l1', 'row': 0, 'column': 0, 'payload': 'https://test.com'},
        ],
      ));
      final before = sheet.toWorkbook();
      // Call linkAt multiple times.
      final u1 = sheet.linkAt(0, 0);
      final u2 = sheet.linkAt(0, 0);
      final u3 = sheet.linkAt(1, 1); // no link
      // Workbook state is unchanged.
      expect(sheet.toWorkbook(), equals(before));
      // Consistent results across calls.
      expect(u1, 'https://test.com');
      expect(u2, 'https://test.com');
      expect(u3, isNull);
    });

    test('frozen does not mutate the workbook (toWorkbook unchanged)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb()).toggleFreeze();
      final before = sheet.toWorkbook();
      // Call frozen multiple times.
      final f1 = sheet.frozen;
      final f2 = sheet.frozen;
      // Workbook state is unchanged.
      expect(sheet.toWorkbook(), equals(before));
      // Consistent results.
      expect(f1, isTrue);
      expect(f2, isTrue);
    });

    test('resolveStyle after applyStyle sees new style (rawWorkbook reflects mutation)', () {
      final sheet = UniverSheet.fromWorkbook(_simpleWb(cellData: {
        '0': {'0': {'v': 'A'}},
      }));
      // Before: not bold.
      expect(sheet.resolveStyle(0, 0).bold, isFalse);
      // After: apply bold — returns a NEW sheet.
      final next = sheet.applyStyle(SelRange(0, 0, 0, 0), {'bl': 1});
      expect(next.resolveStyle(0, 0).bold, isTrue);
      // Original sheet is still not bold (immutability).
      expect(sheet.resolveStyle(0, 0).bold, isFalse);
    });
  });
}
