import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_parse.dart';

void main() {
  Map<String, dynamic> wb() => {
        'id': 'wb-1',
        'name': 'Budget',
        'sheetOrder': ['s1', 's2'],
        'sheets': {
          's1': {
            'name': 'One',
            'cellData': {
              '0': {
                '0': {'v': 10},
                '1': {'f': '=A1*2', 'v': 20},
              },
            },
          },
          's2': {'name': 'Two', 'cellData': <String, dynamic>{}},
        },
      };

  test('reads value, formula, multi-sheet', () {
    final sheet = UniverSheet.fromWorkbook(wb());
    expect(sheet.sheetNames, ['One', 'Two']);
    expect(sheet.cellAt(0, 0).display, '10');
    expect(sheet.cellAt(0, 1).formula, '=A1*2');
    expect(sheet.cellAt(0, 1).display, '20'); // cached value wins for display
  });

  test('activeIndex defaults to 0 and switches immutably', () {
    final sheet = UniverSheet.fromWorkbook(wb());
    expect(sheet.activeIndex, 0);
    final two = sheet.withActiveIndex(1);
    expect(two.activeIndex, 1);
    expect(sheet.activeIndex, 0); // original untouched
    expect(two.cellAt(0, 0).display, ''); // sheet Two is empty
  });

  test('setCell returns a new workbook (immutable) with the edit', () {
    final sheet = UniverSheet.fromWorkbook({
      'sheetOrder': ['s1'],
      'sheets': {
        's1': {'name': 'One', 'cellData': <String, dynamic>{}},
      },
    });
    final next = sheet.setCell(0, 0, value: '42');
    expect(next.cellAt(0, 0).display, '42');
    expect(sheet.cellAt(0, 0).display, ''); // original untouched
    final raw = next.toWorkbook()['sheets']['s1']['cellData']['0']['0'];
    expect(raw['v'], anyOf('42', 42));
  });

  test('setCell with a formula enforces leading =', () {
    final sheet = UniverSheet.fromWorkbook({
      'sheetOrder': ['s1'],
      'sheets': {
        's1': {'name': 'One', 'cellData': <String, dynamic>{}},
      },
    });
    final next = sheet.setCell(0, 2, formula: 'SUM(A1:A2)');
    expect(next.cellAt(0, 2).formula, '=SUM(A1:A2)');
  });

  test('usedBounds reflects populated cells', () {
    final sheet = UniverSheet.fromWorkbook(wb());
    final (maxRow, maxCol) = sheet.usedBounds();
    expect(maxRow, 0);
    expect(maxCol, 1);
  });

  test('toWorkbook round-trips through fromWorkbook', () {
    final sheet = UniverSheet.fromWorkbook(wb()).setCell(2, 0, value: '99');
    final reparsed = UniverSheet.fromWorkbook(sheet.toWorkbook());
    expect(reparsed.cellAt(2, 0).display, '99');
    expect(reparsed.sheetNames, ['One', 'Two']);
  });
}
