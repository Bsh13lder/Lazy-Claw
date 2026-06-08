import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_parse.dart';

// Sample Univer payloads mirror the shapes the server returns (see
// lazyclaw/sheets/snapshot.py + lazyclaw/docs/snapshot.py).

Map<String, dynamic> _workbook({
  String sheetId = 'sh-1',
  String name = 'Sheet1',
  Map<String, dynamic>? cellData,
}) =>
    {
      'id': 'wb-1',
      'name': 'Budget',
      'sheetOrder': [sheetId],
      'sheets': {
        sheetId: {
          'id': sheetId,
          'name': name,
          'rowCount': 1000,
          'columnCount': 20,
          'cellData': cellData ?? <String, dynamic>{},
        },
      },
    };

Map<String, dynamic> _doc(String dataStream) => {
      'id': 'doc-1',
      'name': 'Notes',
      'documentStyle': <String, dynamic>{},
      'body': {
        'dataStream': dataStream,
        'paragraphs': <dynamic>[],
        'textRuns': <dynamic>[],
      },
    };

void main() {
  group('parseSheetGrid', () {
    test('null payload → empty grid', () {
      final g = parseSheetGrid(null);
      expect(g.isEmpty, isTrue);
      expect(g.rows, isEmpty);
    });

    test('blank workbook (no cells) → empty grid, keeps sheet name', () {
      final g = parseSheetGrid(_workbook(name: 'MySheet'));
      expect(g.name, 'MySheet');
      expect(g.rows, isEmpty);
    });

    test('reads values (v) trimmed to used bounds', () {
      final g = parseSheetGrid(_workbook(cellData: {
        '0': {
          '0': {'v': 'Item'},
          '1': {'v': 'Qty'},
        },
        '1': {
          '0': {'v': 'Apple'},
          '1': {'v': 3},
        },
      }));
      expect(g.rowCount, 2);
      expect(g.colCount, 2);
      expect(g.rows[0], ['Item', 'Qty']);
      expect(g.rows[1], ['Apple', '3']);
    });

    test('falls back to formula (f) when no value', () {
      final g = parseSheetGrid(_workbook(cellData: {
        '0': {
          '0': {'f': '=SUM(A2:A3)'},
        },
      }));
      expect(g.rows[0][0], '=SUM(A2:A3)');
    });

    test('value wins over formula', () {
      final g = parseSheetGrid(_workbook(cellData: {
        '0': {
          '0': {'v': 42, 'f': '=6*7'},
        },
      }));
      expect(g.rows[0][0], '42');
    });

    test('sparse cells pad intervening rows/cols with empty strings', () {
      final g = parseSheetGrid(_workbook(cellData: {
        '0': {
          '0': {'v': 'A1'},
        },
        '2': {
          '3': {'v': 'D3'},
        },
      }));
      // bounds: rows 0..2, cols 0..3
      expect(g.rowCount, 3);
      expect(g.colCount, 4);
      expect(g.rows[0][0], 'A1');
      expect(g.rows[1].every((c) => c.isEmpty), isTrue);
      expect(g.rows[2][3], 'D3');
      expect(g.rows[0][3], '');
    });

    test('resolves first sheet via sheetOrder, not map order', () {
      final payload = {
        'sheetOrder': ['sh-2'],
        'sheets': {
          'sh-1': {
            'name': 'First',
            'cellData': {
              '0': {
                '0': {'v': 'wrong'}
              }
            },
          },
          'sh-2': {
            'name': 'Second',
            'cellData': {
              '0': {
                '0': {'v': 'right'}
              }
            },
          },
        },
      };
      final g = parseSheetGrid(payload);
      expect(g.name, 'Second');
      expect(g.rows[0][0], 'right');
    });

    test('tolerates Map<dynamic,dynamic> nesting from JSON decode', () {
      final payload = <String, dynamic>{
        'sheetOrder': ['sh-1'],
        'sheets': <dynamic, dynamic>{
          'sh-1': <dynamic, dynamic>{
            'name': 'S',
            'cellData': <dynamic, dynamic>{
              '0': <dynamic, dynamic>{
                '0': <dynamic, dynamic>{'v': 'ok'},
              },
            },
          },
        },
      };
      final g = parseSheetGrid(payload);
      expect(g.rows[0][0], 'ok');
    });
  });

  group('colToLetter', () {
    test('maps 0→A, 25→Z, 26→AA, 27→AB, 701→ZZ', () {
      expect(colToLetter(0), 'A');
      expect(colToLetter(25), 'Z');
      expect(colToLetter(26), 'AA');
      expect(colToLetter(27), 'AB');
      expect(colToLetter(701), 'ZZ');
    });

    test('negative → empty', () {
      expect(colToLetter(-1), '');
    });
  });

  group('parseDocParagraphs', () {
    test('null payload → single empty paragraph', () {
      expect(parseDocParagraphs(null), ['']);
    });

    test('blank document (\\r\\n) → single empty paragraph', () {
      expect(parseDocParagraphs(_doc('\r\n')), ['']);
    });

    test('splits paragraphs on carriage returns, drops terminators', () {
      final p = parseDocParagraphs(_doc('Hello\rWorld\r\n'));
      expect(p, ['Hello', 'World']);
    });

    test('single paragraph with no trailing section break', () {
      final p = parseDocParagraphs(_doc('Just one line\r'));
      expect(p, ['Just one line']);
    });

    test('strips custom-range (hyperlink) sentinel chars from visible text', () {
      //  text  — link sentinels wrap "click".
      final p = parseDocParagraphs(_doc('See click here\r\n'));
      expect(p, ['See click here']);
    });

    test('parseDocText joins paragraphs with newlines', () {
      expect(parseDocText(_doc('A\rB\rC\r\n')), 'A\nB\nC');
    });

    test('missing/empty dataStream → single empty paragraph', () {
      expect(parseDocParagraphs(_doc('')), ['']);
      expect(parseDocParagraphs({'body': <String, dynamic>{}}), ['']);
    });
  });
}
