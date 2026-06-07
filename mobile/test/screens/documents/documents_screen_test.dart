import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/documents_provider.dart';
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';
import 'package:lazyclaw_mobile/screens/documents/documents_screen.dart';
import 'package:lazyclaw_mobile/ui/components/lz_chip.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

// ── Fake transport — routes GETs by path so all three sub-tabs can load ──────
//
// NOTE: DocumentsScreen uses an IndexedStack, so all three sub-tab lists build
// (and load) eagerly. Assertions that must distinguish the ACTIVE tab key off
// the kind-aware FAB icon (Icons.add for sheets/docs, upload_file for PDF)
// rather than list text (which is present in the tree regardless of which tab
// is on top).

class _FakeTransport implements DocumentsTransport {
  final List<Map<String, dynamic>> sheets;
  final List<Map<String, dynamic>> docs;
  final List<Map<String, dynamic>> files;

  _FakeTransport({
    this.sheets = const [],
    this.docs = const [],
    this.files = const [],
  });

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    if (path == '/api/sheets') return {'sheets': sheets};
    if (path == '/api/docs') return {'docs': docs};
    if (path == '/api/pdf') return {'files': files};
    return const {};
  }

  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async => const {};
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => const {};
  @override
  Future<Map<String, dynamic>> uploadFile(String p, File f) async => const {};
  @override
  Future<List<int>> getBytes(String path) async => const [];
}

Widget _host(_FakeTransport transport) {
  return ProviderScope(
    overrides: [
      documentsRepositoryProvider
          .overrideWithValue(DocumentsRepository(transport)),
    ],
    child: MaterialApp(
      theme: buildAppTheme(),
      home: const DocumentsScreen(),
    ),
  );
}

void main() {
  group('DocumentsScreen — structure', () {
    testWidgets('renders the "Documents" title and three sub-tab chips',
        (tester) async {
      await tester.pumpWidget(_host(_FakeTransport()));
      await tester.pump();

      expect(find.text('Documents'), findsOneWidget);
      expect(find.byType(LzChip), findsNWidgets(3));
      expect(find.widgetWithText(LzChip, 'Sheets'), findsOneWidget);
      expect(find.widgetWithText(LzChip, 'Docs'), findsOneWidget);
      expect(find.widgetWithText(LzChip, 'PDF'), findsOneWidget);
    });

    testWidgets('defaults to the Sheets tab (create FAB shows +)',
        (tester) async {
      // Seed every kind so no empty-state action icons (which include an
      // upload_file glyph for the hidden PDF tab) pollute the icon assertions.
      await tester.pumpWidget(_host(_FakeTransport(
        sheets: [
          {'id': 's1', 'name': 'S'}
        ],
        docs: [
          {'id': 'd1', 'name': 'D'}
        ],
        files: [
          {'id': 'p1', 'name': 'P.pdf'}
        ],
      )));
      await tester.pumpAndSettle();

      expect(find.byIcon(Icons.add), findsOneWidget);
      expect(find.byIcon(Icons.upload_file_rounded), findsNothing);
    });
  });

  group('DocumentsScreen — list', () {
    testWidgets('lists sheets returned by the repo', (tester) async {
      await tester.pumpWidget(_host(_FakeTransport(sheets: [
        {'id': 's1', 'name': 'Budget 2026'},
        {'id': 's2', 'name': 'Roadmap'},
      ])));
      await tester.pumpAndSettle();

      expect(find.text('Budget 2026'), findsOneWidget);
      expect(find.text('Roadmap'), findsOneWidget);
    });

    testWidgets('shows the Sheets empty state when no sheets exist',
        (tester) async {
      // Seed docs+files so ONLY the Sheets list is empty (the other two
      // IndexedStack children render lists, not empty states).
      await tester.pumpWidget(_host(_FakeTransport(
        docs: [
          {'id': 'd1', 'name': 'Doc'}
        ],
        files: [
          {'id': 'p1', 'name': 'F.pdf'}
        ],
      )));
      await tester.pumpAndSettle();

      expect(find.textContaining('No sheets yet'), findsOneWidget);
    });
  });

  group('DocumentsScreen — sub-tab switch', () {
    testWidgets('tapping PDF switches the active tab (FAB → import)',
        (tester) async {
      await tester.pumpWidget(_host(_FakeTransport(
        files: [
          {'id': 'p1', 'name': 'Invoice.pdf', 'pages': 2}
        ],
      )));
      await tester.pumpAndSettle();

      // Sheets active → create FAB.
      expect(find.byIcon(Icons.add), findsOneWidget);
      expect(find.byIcon(Icons.upload_file_rounded), findsNothing);

      await tester.tap(find.widgetWithText(LzChip, 'PDF'));
      await tester.pumpAndSettle();

      // PDF active → import FAB, and the PDF item is visible.
      expect(find.byIcon(Icons.add), findsNothing);
      expect(find.byIcon(Icons.upload_file_rounded), findsOneWidget);
      expect(find.text('Invoice.pdf'), findsOneWidget);
    });
  });
}
