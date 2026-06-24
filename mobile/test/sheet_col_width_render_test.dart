/// Widget tests proving the native grid HONORS stored per-column widths (and
/// per-row heights) instead of the old uniform scalar width, plus unit tests for
/// the width/height resolvers and the auto-fit helper.
///
/// Regression target: `_colW` used to return ONE clamped `viewportWidth/cols`
/// for every column, ignoring `columnData[col].w`. The render now reads the
/// stored width per column so a column the user (or sync) widened stays wide.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/documents/sheet_grid.dart';
import 'package:lazyclaw_mobile/screens/documents/sheet_link_ui.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_ops.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_parse.dart';

Map<String, dynamic> _workbook({
  Map<String, dynamic>? columnData,
  Map<String, dynamic>? rowData,
}) =>
    {
      'sheetOrder': ['s1'],
      'sheets': {
        's1': {
          'name': 'Sheet1',
          if (columnData != null) 'columnData': columnData,
          if (rowData != null) 'rowData': rowData,
          'cellData': {
            '0': {
              '0': {'v': 'A1'},
              '1': {'v': 'B1'},
              '2': {'v': 'C1'},
            },
          },
        },
      },
    };

/// Pump the grid alone (no editor/network), with a fixed viewport, and return
/// the rendered width of the cell showing [text].
Future<double> _renderedCellWidth(
  WidgetTester tester,
  Map<String, dynamic> wb,
  String text,
) async {
  final sheet = UniverSheet.fromWorkbook(wb);
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
      body: SizedBox(
        width: 800,
        height: 600,
        child: SheetEditorGrid(
          sheet: sheet,
          rows: 3,
          cols: 4,
          sel: null,
          viewportWidth: 800,
          onTapCell: (_, __) {},
          onExtendSelection: (_, __) {},
          onStartSelection: (_, __) {},
        ),
      ),
    ),
  ));
  await tester.pumpAndSettle();
  // The cell is a Container wrapping a Text; find the nearest sized box.
  final textFinder = find.text(text);
  expect(textFinder, findsOneWidget);
  // Walk up to the Container that sets the width (the cell box).
  final container = tester.widget<Container>(
    find.ancestor(of: textFinder, matching: find.byType(Container)).first,
  );
  final box = tester.renderObject<RenderBox>(
    find.ancestor(of: textFinder, matching: find.byType(Container)).first,
  );
  // Prefer the actual painted size.
  expect(container, isNotNull);
  return box.size.width;
}

void main() {
  group('grid honors stored per-column width', () {
    testWidgets('a column with stored w renders at that width', (tester) async {
      final wb = _workbook(columnData: {
        '0': {'w': 200.0},
      });
      final widthA = await _renderedCellWidth(tester, wb, 'A1');
      expect(widthA, closeTo(200.0, 0.5));
    });

    testWidgets('columns with different stored widths render differently',
        (tester) async {
      final wb = _workbook(columnData: {
        '0': {'w': 220.0},
        '1': {'w': 70.0},
      });
      final widthA = await _renderedCellWidth(tester, wb, 'A1');
      final widthB = await _renderedCellWidth(tester, wb, 'B1');
      expect(widthA, closeTo(220.0, 0.5));
      // 70 is below the grid's _minColW (56) but above it → kept as 70.
      expect(widthB, closeTo(70.0, 0.5));
      expect(widthA, greaterThan(widthB));
    });

    testWidgets('a column with no stored width falls back to a sane default',
        (tester) async {
      final wb = _workbook(); // no columnData
      final widthA = await _renderedCellWidth(tester, wb, 'A1');
      // Default fit for 4 cols on an 800px viewport: (800-44)/4 = 189 → clamped
      // to the 88..160 default band.
      expect(widthA, inInclusiveRange(88.0, 160.0));
    });
  });

  group('width/height resolvers', () {
    test('resolveCurrentColWidth reads stored w, else the default', () {
      final sheet = UniverSheet.fromWorkbook(_workbook(columnData: {
        '2': {'w': 133.0},
      }));
      expect(resolveCurrentColWidth(sheet, 2), 133.0);
      expect(resolveCurrentColWidth(sheet, 0), kDefaultColW);
    });

    test('resolveCurrentRowHeight reads stored h, else the default', () {
      final sheet = UniverSheet.fromWorkbook(_workbook(rowData: {
        '1': {'h': 60.0},
      }));
      expect(resolveCurrentRowHeight(sheet, 1), 60.0);
      expect(resolveCurrentRowHeight(sheet, 0), kDefaultRowH);
    });

    test('setColWidth then resolve round-trips through the workbook', () {
      final sheet = UniverSheet.fromWorkbook(_workbook());
      final next = sheet.setColWidth(0, 175.0);
      expect(resolveCurrentColWidth(next, 0), 175.0);
    });

    test('setRowHeight then resolve round-trips through the workbook', () {
      final sheet = UniverSheet.fromWorkbook(_workbook());
      final next = sheet.setRowHeight(0, 80.0);
      expect(resolveCurrentRowHeight(next, 0), 80.0);
    });
  });

  group('auto-fit', () {
    test('autoFitColWidth sizes to content, clamped, and persists', () {
      final wb = _workbook();
      // Put a long string in column 0.
      var sheet = UniverSheet.fromWorkbook(wb);
      sheet = sheet.setCell(0, 0, value: 'a really quite long cell value here');
      final w = autoFitColWidth(sheet, 0, rows: 3);
      expect(w, greaterThan(kDefaultColW));
      expect(w, lessThanOrEqualTo(320.0)); // clamped to maxW
      // Persisting via setColWidth survives a round-trip (so it survives sync).
      final saved = sheet.setColWidth(0, w);
      expect(resolveCurrentColWidth(saved, 0), w);
    });

    test('autoFitColWidth on an empty column keeps the default', () {
      final sheet = UniverSheet.fromWorkbook(_workbook());
      // Column 3 has no content.
      final w = autoFitColWidth(sheet, 3, rows: 3);
      expect(w, kDefaultColW);
    });
  });
}
