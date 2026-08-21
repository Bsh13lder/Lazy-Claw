/// Rendering the formatting the backend can now write.
///
/// The agent gained styles, column/row-level formatting, merges and geometry;
/// this pins the three places the phone had to change to actually SHOW them:
///
///  1. **The style cascade.** `resolveStyle` read only the cell's own `s`, so a
///     sheet formatted BY COLUMN — the natural way to format a table, and what
///     the web toolbar produces from a column selection — rendered unstyled.
///  2. **Merges.** `mergeData` was read nowhere, so a merged title banner drew
///     as separate boxed cells.
///  3. **Implicit width.** With no stored `columnData`, every column fell back
///     to one viewport-fit width, so on a phone anything longer than a word was
///     ellipsised regardless of what was in it.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/documents/sheet_grid.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_model.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_ops.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_parse.dart';

Map<String, dynamic> _wb({
  Map<String, dynamic>? styles,
  Map<String, dynamic>? sheetExtras,
  Map<String, dynamic>? cellData,
  Map<String, dynamic>? workbookExtras,
}) =>
    {
      'id': 'wb',
      'sheetOrder': ['sh'],
      'styles': styles ?? <String, dynamic>{},
      ...?workbookExtras,
      'sheets': {
        'sh': {
          'id': 'sh',
          'name': 'Sheet1',
          'rowCount': 100,
          'columnCount': 10,
          'cellData': cellData ??
              {
                '0': {'0': {'v': 'Item'}, '1': {'v': 'Cost'}},
                '1': {'0': {'v': 'Rent'}, '1': {'v': 1200}},
              },
          ...?sheetExtras,
        },
      },
    };

/// Width of the rendered cell whose text is [text].
Future<double> _cellWidth(WidgetTester tester, UniverSheet sheet, String text,
    {double viewport = 800}) async {
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
      body: SizedBox(
        width: viewport,
        height: 600,
        child: SheetEditorGrid(
          sheet: sheet,
          rows: 6,
          cols: 4,
          sel: null,
          viewportWidth: viewport,
          onTapCell: (_, __) {},
          onExtendSelection: (_, __) {},
          onStartSelection: (_, __) {},
          onHeaderAction: (_, __) {},
        ),
      ),
    ),
  ));
  await tester.pump();
  final container = tester.widget<Container>(
    find.ancestor(of: find.text(text), matching: find.byType(Container)).first,
  );
  return (container.constraints?.maxWidth) ??
      tester.getSize(find.ancestor(
        of: find.text(text), matching: find.byType(Container),
      ).first).width;
}

void main() {
  group('style cascade', () {
    test('a column-level style applies to every cell in that column', () {
      final sheet = UniverSheet.fromWorkbook(_wb(
        styles: {'s-col': {'bl': 1}},
        sheetExtras: {
          'columnData': {'1': {'s': 's-col'}},
        },
      ));
      expect(sheet.resolveStyle(0, 1).bold, isTrue);
      expect(sheet.resolveStyle(1, 1).bold, isTrue);
      expect(sheet.resolveStyle(0, 0).bold, isFalse, reason: 'column A is not styled');
    });

    test('a row-level style applies across that row', () {
      final sheet = UniverSheet.fromWorkbook(_wb(
        styles: {'s-row': {'it': 1}},
        sheetExtras: {
          'rowData': {'0': {'s': 's-row'}},
        },
      ));
      expect(sheet.resolveStyle(0, 0).italic, isTrue);
      expect(sheet.resolveStyle(1, 0).italic, isFalse);
    });

    test('the cell wins over the row, which wins over the column', () {
      final sheet = UniverSheet.fromWorkbook(_wb(
        styles: {
          's-col': {'cl': {'rgb': '#111111'}},
          's-row': {'cl': {'rgb': '#222222'}},
          's-cell': {'cl': {'rgb': '#333333'}},
        },
        sheetExtras: {
          'columnData': {'0': {'s': 's-col'}},
          'rowData': {'0': {'s': 's-row'}},
        },
        cellData: {
          '0': {'0': {'v': 'x', 's': 's-cell'}, '1': {'v': 'y'}},
          '1': {'0': {'v': 'z'}},
        },
      ));
      expect(sheet.resolveStyle(0, 0).color, '#333333', reason: 'cell wins');
      expect(sheet.resolveStyle(0, 1).color, '#222222', reason: 'row beats nothing');
      expect(sheet.resolveStyle(1, 0).color, '#111111', reason: 'column applies');
    });

    test('layers merge field-by-field rather than replacing wholesale', () {
      final sheet = UniverSheet.fromWorkbook(_wb(
        styles: {
          's-col': {'bg': {'rgb': '#EEEEEE'}},
          's-cell': {'bl': 1},
        },
        sheetExtras: {
          'columnData': {'0': {'s': 's-col'}},
        },
        cellData: {
          '0': {'0': {'v': 'x', 's': 's-cell'}},
        },
      ));
      final view = sheet.resolveStyle(0, 0);
      expect(view.bold, isTrue, reason: 'from the cell');
      expect(view.bgColor, '#EEEEEE', reason: 'from the column — not clobbered');
    });

    test('worksheet and workbook defaultStyle sit at the bottom', () {
      final sheet = UniverSheet.fromWorkbook(_wb(
        styles: {'s-wb': {'tb': 3}, 's-ws': {'it': 1}},
        workbookExtras: {'defaultStyle': 's-wb'},
        sheetExtras: {'defaultStyle': 's-ws'},
      ));
      final view = sheet.resolveStyle(0, 0);
      expect(view.wrap, isTrue, reason: 'workbook default');
      expect(view.italic, isTrue, reason: 'worksheet default');
    });

    test('an inline style map is honoured as well as a registry id', () {
      final sheet = UniverSheet.fromWorkbook(_wb(
        cellData: {
          '0': {'0': {'v': 'x', 's': {'bl': 1}}},
        },
      ));
      expect(sheet.resolveStyle(0, 0).bold, isTrue);
    });

    test('an unstyled cell is still empty', () {
      expect(UniverSheet.fromWorkbook(_wb()).resolveStyle(0, 0),
          CellStyleView.empty);
    });
  });

  group('merges', () {
    final sheet = UniverSheet.fromWorkbook(_wb(sheetExtras: {
      'mergeData': [
        {'startRow': 0, 'startColumn': 0, 'endRow': 0, 'endColumn': 2},
      ],
    }));

    test('mergeAt finds the rect from any member cell', () {
      expect(sheet.mergeAt(0, 0), isNotNull);
      expect(sheet.mergeAt(0, 2), isNotNull);
      expect(sheet.mergeAt(0, 3), isNull);
      expect(sheet.mergeAt(1, 0), isNull);
    });

    test('the rect uses inclusive end indices', () {
      expect(sheet.mergeAt(0, 1), {
        'startRow': 0, 'startColumn': 0, 'endRow': 0, 'endColumn': 2,
      });
    });

    test('a malformed rect is ignored rather than thrown on', () {
      final broken = UniverSheet.fromWorkbook(_wb(sheetExtras: {
        'mergeData': [
          {'startRow': 'x'},
          {'startRow': 0, 'startColumn': 0, 'endRow': 0, 'endColumn': 1},
        ],
      }));
      expect(broken.mergeAt(0, 1), isNotNull);
    });

    testWidgets('the anchor spans its merged columns', (tester) async {
      final merged = UniverSheet.fromWorkbook(_wb(
        sheetExtras: {
          'mergeData': [
            {'startRow': 0, 'startColumn': 0, 'endRow': 0, 'endColumn': 2},
          ],
        },
        cellData: {
          '0': {'0': {'v': 'Banner'}},
          '1': {'0': {'v': 'Rent'}, '1': {'v': 1200}},
        },
      ));
      final plain = UniverSheet.fromWorkbook(_wb(cellData: {
        '0': {'0': {'v': 'Banner'}},
        '1': {'0': {'v': 'Rent'}, '1': {'v': 1200}},
      }));

      final mergedW = await _cellWidth(tester, merged, 'Banner');
      final plainW = await _cellWidth(tester, plain, 'Banner');
      expect(mergedW, greaterThan(plainW),
          reason: 'the merged anchor must cover its span');
    });
  });

  group('implicit column width', () {
    testWidgets('a long text column grows past the viewport-fit default',
        (tester) async {
      // A phone-width viewport: the fit default lands at the 88px floor, so a
      // long value has to widen the column or it is ellipsised.
      final wide = UniverSheet.fromWorkbook(_wb(cellData: {
        '0': {'0': {'v': 'Groceries, household and cleaning supplies'}},
      }));
      final narrow = UniverSheet.fromWorkbook(_wb(cellData: {
        '0': {'0': {'v': 'Ab'}},
      }));
      final wideW = await _cellWidth(tester, wide,
          'Groceries, household and cleaning supplies', viewport: 411);
      final narrowW = await _cellWidth(tester, narrow, 'Ab', viewport: 411);
      expect(wideW, greaterThan(narrowW));
    });

    testWidgets('it never narrows a column below the fit default',
        (tester) async {
      final sheet = UniverSheet.fromWorkbook(_wb(cellData: {
        '0': {'0': {'v': 'Ab'}},
      }));
      final width = await _cellWidth(tester, sheet, 'Ab', viewport: 800);
      expect(width, greaterThanOrEqualTo(88.0),
          reason: 'short content must not shrink a column below tappable');
    });

    testWidgets('an explicit stored width still wins', (tester) async {
      final sheet = UniverSheet.fromWorkbook(_wb(
        sheetExtras: {
          'columnData': {'0': {'w': 200.0}},
        },
        cellData: {
          '0': {'0': {'v': 'Ab'}},
        },
      ));
      expect(await _cellWidth(tester, sheet, 'Ab'), closeTo(200.0, 0.5));
    });
  });
}
