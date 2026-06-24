/// Unit tests for the client-side blank Univer builders that fix the
/// "new sheet/doc is empty + uneditable" regression. These mirror the server
/// builders `lazyclaw/sheets/snapshot.py:blank_workbook` and
/// `lazyclaw/docs/snapshot.py:blank_document` and must parse back into an
/// editable structure (≥1 worksheet / one paragraph).
library;

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/documents/blank_doc.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_parse.dart';

void main() {
  group('blankWorkbook', () {
    test('builds a single editable worksheet (mirrors blank_workbook)', () {
      final wb = blankWorkbook('Budget');

      expect(wb['name'], 'Budget');
      expect(wb['appVersion'], '0.1.0');
      expect(wb['locale'], 'enUS');
      expect(wb['styles'], isEmpty);

      final order = wb['sheetOrder'] as List;
      expect(order, hasLength(1), reason: 'exactly one sheet in tab order');

      final sheets = wb['sheets'] as Map;
      expect(sheets, hasLength(1));
      final shId = order.first as String;
      final sheet = sheets[shId] as Map;
      expect(sheet['id'], shId);
      expect(sheet['name'], 'Sheet1');
      expect(sheet['rowCount'], kDefaultRows);
      expect(sheet['columnCount'], kDefaultCols);
      expect(sheet['cellData'], isEmpty);
    });

    test('matches server DEFAULT_ROWS / DEFAULT_COLS (1000 × 20)', () {
      expect(kDefaultRows, 1000);
      expect(kDefaultCols, 20);
    });

    test('internal ids are fresh, prefixed, and distinct from the row id', () {
      final a = blankWorkbook('A');
      final b = blankWorkbook('B');
      expect((a['id'] as String).startsWith('wb-'), isTrue);
      expect(((a['sheetOrder'] as List).first as String).startsWith('sh-'),
          isTrue);
      // Fresh ids per call (no shared singleton).
      expect(a['id'], isNot(b['id']));
    });

    test('blank / whitespace name falls back to "Untitled sheet"', () {
      expect(blankWorkbook('')['name'], 'Untitled sheet');
      expect(blankWorkbook('   ')['name'], 'Untitled sheet');
      expect(blankWorkbook('  Trimmed  ')['name'], 'Trimmed');
    });

    test('parses (via parseSheetGrid) into a named, empty grid', () {
      final grid = parseSheetGrid(blankWorkbook('New'));
      expect(grid.name, 'Sheet1');
      // A fresh sheet has no populated cells → empty (trimmed) grid, but the
      // sheet itself exists (name resolved, not the fallback path).
      expect(grid.isEmpty, isTrue);
    });

    test('UniverSheet.fromWorkbook yields ≥1 editable worksheet', () {
      final sheet = UniverSheet.fromWorkbook(blankWorkbook('Editable'));
      expect(sheet.sheetNames, isNotEmpty);
      expect(sheet.sheetNames.first, 'Sheet1');
      // Editable: a setCell round-trips into the active worksheet.
      final edited = sheet.setCell(0, 0, value: 'hi');
      expect(edited.cellAt(0, 0).display, 'hi');
    });

    test('survives a JSON encode/decode round-trip (the outbox path)', () {
      final wb = blankWorkbook('Round');
      final decoded =
          jsonDecode(jsonEncode(wb)) as Map<String, dynamic>;
      final sheet = UniverSheet.fromWorkbook(decoded);
      expect(sheet.sheetNames, isNotEmpty);
    });
  });

  group('blankDocument', () {
    test('builds one empty paragraph (mirrors blank_document)', () {
      final doc = blankDocument('Notes');

      expect(doc['name'], 'Notes');
      expect(doc['documentStyle'], isEmpty);

      final body = doc['body'] as Map;
      // dataStream = PARAGRAPH_BREAK ("\r") + SECTION_BREAK ("\n").
      expect(body['dataStream'], '\r\n');
      expect(body['dataStream'], kParagraphBreak + kSectionBreak);

      final paras = body['paragraphs'] as List;
      expect(paras, hasLength(1));
      expect((paras.first as Map)['startIndex'], 0);

      expect(body['textRuns'], isEmpty);

      final breaks = body['sectionBreaks'] as List;
      expect(breaks, hasLength(1));
      expect((breaks.first as Map)['startIndex'], 1);
    });

    test('id is fresh + "doc-" prefixed', () {
      final a = blankDocument('A');
      final b = blankDocument('B');
      expect((a['id'] as String).startsWith('doc-'), isTrue);
      expect(a['id'], isNot(b['id']));
    });

    test('blank / whitespace name falls back to "Untitled doc"', () {
      expect(blankDocument('')['name'], 'Untitled doc');
      expect(blankDocument('   ')['name'], 'Untitled doc');
      expect(blankDocument('  Doc  ')['name'], 'Doc');
    });

    test('parses (via parseDocParagraphs) into a single empty paragraph', () {
      final paras = parseDocParagraphs(blankDocument('Empty'));
      expect(paras, hasLength(1));
      expect(paras.first, '');
    });

    test('survives a JSON encode/decode round-trip', () {
      final doc = blankDocument('Round');
      final decoded = jsonDecode(jsonEncode(doc)) as Map<String, dynamic>;
      expect(parseDocParagraphs(decoded), const ['']);
    });
  });
}
