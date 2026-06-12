/// Tests for the sheet hyperlink UI:
///   (a) Commit a bare URL → linkAt returns it.
///   (b) Commit `[Docs](https://d.io)` → cell display "Docs" + link stored.
///   (c) Link chip appears when a linked cell is selected; Remove clears it.
///   (d) Insert-link dialog validation rejects `ftp://x`.
///   (e) Toolbar has the insertLink button (Icons.link).
library;

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/documents_provider.dart';
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';
import 'package:lazyclaw_mobile/screens/documents/sheet_editor_screen.dart';
import 'package:lazyclaw_mobile/screens/documents/sheet_toolbar.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_model.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_ops.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_parse.dart';

// ── Fake transport ────────────────────────────────────────────────────────────

/// Fake transport with a single plain-text cell "Hello" at A1.
class _FakeTransport implements DocumentsTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path) async => {
        'id': 'wb-link',
        'name': 'Links',
        'payload': {
          'sheetOrder': ['s1'],
          'sheets': {
            's1': {
              'name': 'Sheet1',
              'cellData': {
                '0': {
                  '0': {'v': 'Hello'},
                },
              },
            },
          },
        },
      };

  @override
  Future<Map<String, dynamic>> putJson(
      String path, Map<String, dynamic> body) async =>
      {'ok': true};

  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) async =>
      {};

  @override
  Future<Map<String, dynamic>> patchJson(
      String path, Map<String, dynamic> body) async =>
      {};

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => {};

  @override
  Future<Map<String, dynamic>> uploadFile(String p, File f) async => {};

  @override
  Future<List<int>> getBytes(String path) async => const [];

  @override
  Future<List<int>> postBytes(String p, Map<String, dynamic> b) async =>
      const [];
}

// ── Wrapper ───────────────────────────────────────────────────────────────────

ProviderScope _wrapEditor() {
  return ProviderScope(
    overrides: [
      documentsRepositoryProvider
          .overrideWithValue(DocumentsRepository(_FakeTransport())),
    ],
    child: const MaterialApp(
      home: SheetEditorScreen(id: 'wb-link', name: 'Links'),
    ),
  );
}

// ── Pure model tests (no Flutter pumpWidget) ──────────────────────────────────

void main() {
  // ─────────────────────────────────────────────────────────────────────────────
  // (a) Auto-convert: bare URL typed in formula bar → linkAt returns the URL
  // ─────────────────────────────────────────────────────────────────────────────
  group('auto-convert on commit', () {
    test('(a) bare URL → linkAt returns the URL', () {
      // Build a minimal UniverSheet and manually simulate the commit logic.
      final wb = {
        'sheetOrder': ['s1'],
        'sheets': {
          's1': {
            'name': 'Sheet1',
            'cellData': <String, dynamic>{},
          },
        },
      };
      var sheet = UniverSheet.fromWorkbook(wb);

      // Simulate what _commit does: setCell then setLink for a bare URL.
      const url = 'https://example.com';
      sheet = sheet.setCell(0, 0, value: url);
      sheet = sheet.setLink(0, 0, url);

      expect(sheet.linkAt(0, 0), url);
    });

    test('(b) markdown [Docs](url) → display "Docs" + link stored', () {
      final wb = {
        'sheetOrder': ['s1'],
        'sheets': {
          's1': {
            'name': 'Sheet1',
            'cellData': <String, dynamic>{},
          },
        },
      };
      var sheet = UniverSheet.fromWorkbook(wb);

      const display = 'Docs';
      const url = 'https://d.io';

      // Simulate commit: set display text + link.
      sheet = sheet.setCell(0, 0, value: display);
      sheet = sheet.setLink(0, 0, url, display: display);

      expect(sheet.cellAt(0, 0).display, display);
      expect(sheet.linkAt(0, 0), url);
    });
  });

  // ─────────────────────────────────────────────────────────────────────────────
  // (c) Link chip appears when linked cell selected; Remove clears it
  // ─────────────────────────────────────────────────────────────────────────────
  group('link chip widget', () {
    testWidgets('(c) chip appears after typing a URL and committing',
        (tester) async {
      await tester.pumpWidget(_wrapEditor());
      await tester.pumpAndSettle();

      // Tap A1 (Hello cell).
      await tester.tap(find.text('Hello'));
      await tester.pumpAndSettle();

      // Type a URL into the formula bar.
      final formulaField = find.byType(TextField).first;
      await tester.enterText(formulaField, 'https://example.com');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();

      // The link chip row should appear (shows "Open" action).
      expect(find.text('Open'), findsOneWidget);
      expect(find.text('Edit'), findsOneWidget);
      expect(find.text('Remove'), findsOneWidget);
    });

    testWidgets('(c) Remove button clears the link chip', (tester) async {
      await tester.pumpWidget(_wrapEditor());
      await tester.pumpAndSettle();

      // Select A1 and type a URL.
      await tester.tap(find.text('Hello'));
      await tester.pumpAndSettle();

      final formulaField = find.byType(TextField).first;
      await tester.enterText(formulaField, 'https://remove.me');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();

      // Chip should be visible.
      expect(find.text('Remove'), findsOneWidget);

      // Tap Remove.
      await tester.tap(find.text('Remove'));
      await tester.pumpAndSettle();

      // Chip should be gone.
      expect(find.text('Remove'), findsNothing);
      expect(find.text('Open'), findsNothing);
    });
  });

  // ─────────────────────────────────────────────────────────────────────────────
  // (d) Insert-link dialog validation rejects ftp://x
  // ─────────────────────────────────────────────────────────────────────────────
  group('insert-link dialog validation', () {
    testWidgets('(d) dialog rejects ftp:// URL with inline error',
        (tester) async {
      await tester.pumpWidget(_wrapEditor());
      await tester.pumpAndSettle();

      // Select a cell so the toolbar appears.
      await tester.tap(find.text('Hello'));
      await tester.pumpAndSettle();

      // Tap the link icon button in the toolbar.
      final linkBtn = find.byIcon(Icons.link);
      expect(linkBtn, findsOneWidget);
      await tester.tap(linkBtn);
      await tester.pumpAndSettle();

      // The bottom sheet should be open — find the URL field and enter ftp://.
      // The URL TextField is after the display TextField in the sheet.
      final urlField = find.byType(TextField).last;
      await tester.enterText(urlField, 'ftp://x');

      // Tap Save.
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();

      // Should show an inline error.
      expect(
        find.text('URL must start with http:// or https://'),
        findsOneWidget,
      );
    });

    testWidgets('(d) dialog accepts https:// URL and saves', (tester) async {
      await tester.pumpWidget(_wrapEditor());
      await tester.pumpAndSettle();

      await tester.tap(find.text('Hello'));
      await tester.pumpAndSettle();

      await tester.tap(find.byIcon(Icons.link));
      await tester.pumpAndSettle();

      // Enter a valid URL.
      final urlField = find.byType(TextField).last;
      await tester.enterText(urlField, 'https://valid.example.com');

      // Tap Save.
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();

      // Sheet dismisses and chip should now appear.
      expect(find.text('Open'), findsOneWidget);
    });
  });

  // ─────────────────────────────────────────────────────────────────────────────
  // (e) Toolbar has the insertLink button (Icons.link)
  // ─────────────────────────────────────────────────────────────────────────────
  group('toolbar insertLink button', () {
    testWidgets('(e) SheetToolbar renders the link icon', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: SheetToolbar(
            anchorStyle: const CellStyleView(),
            canUndo: false,
            canRedo: false,
            onAction: (_) {},
            onTextColor: (_) {},
            onFillColor: (_) {},
            onNumberFormat: (_) {},
          ),
        ),
      ));

      expect(find.byIcon(Icons.link), findsOneWidget);
    });

    testWidgets('(e) tapping link icon fires insertLink action',
        (tester) async {
      SheetToolbarAction? fired;
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: SheetToolbar(
            anchorStyle: const CellStyleView(),
            canUndo: false,
            canRedo: false,
            onAction: (a) => fired = a,
            onTextColor: (_) {},
            onFillColor: (_) {},
            onNumberFormat: (_) {},
          ),
        ),
      ));

      await tester.tap(find.byIcon(Icons.link));
      await tester.pump();

      expect(fired, SheetToolbarAction.insertLink);
    });
  });
}
