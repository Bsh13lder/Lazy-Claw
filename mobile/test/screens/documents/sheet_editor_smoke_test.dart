import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/documents_provider.dart';
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';
import 'package:lazyclaw_mobile/screens/documents/sheet_editor_screen.dart';

/// Returns a 2-sheet workbook payload from getJson; records nothing else.
class _FakeTransport implements DocumentsTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path) async => {
        'id': 'wb-1',
        'name': 'Budget',
        'payload': {
          'sheetOrder': ['s1', 's2'],
          'sheets': {
            's1': {
              'name': 'Income',
              'cellData': {
                '0': {'0': {'v': 'Rent'}, '1': {'v': 900}},
              },
            },
            's2': {'name': 'Costs', 'cellData': <String, dynamic>{}},
          },
        },
      };

  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async => {};
  @override
  Future<Map<String, dynamic>> putJson(String p, Map<String, dynamic> b) async => {};
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => {};
  @override
  Future<Map<String, dynamic>> uploadFile(String p, File f) async => {};
  @override
  Future<List<int>> getBytes(String path) async => const [];
  @override
  Future<List<int>> postBytes(String p, Map<String, dynamic> b) async => const [];
}

void main() {
  testWidgets('renders grid headers, a value, formula bar, and sheet tabs',
      (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          documentsRepositoryProvider
              .overrideWithValue(DocumentsRepository(_FakeTransport())),
        ],
        child: const MaterialApp(
          home: SheetEditorScreen(id: 'wb-1', name: 'Budget'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    // Column header letters + a populated value.
    expect(find.text('A'), findsOneWidget);
    expect(find.text('B'), findsOneWidget);
    expect(find.text('Rent'), findsOneWidget);
    expect(find.text('900'), findsOneWidget);

    // Formula bar hint (no selection yet).
    expect(find.text('Tap a cell to edit'), findsOneWidget);

    // Multi-sheet tabs (2 worksheets).
    expect(find.text('Income'), findsOneWidget);
    expect(find.text('Costs'), findsOneWidget);
  });

  testWidgets('tapping a cell loads it into the formula bar', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          documentsRepositoryProvider
              .overrideWithValue(DocumentsRepository(_FakeTransport())),
        ],
        child: const MaterialApp(
          home: SheetEditorScreen(id: 'wb-1', name: 'Budget'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('Rent'));
    await tester.pumpAndSettle();

    // The formula bar's TextField now holds the tapped cell's value.
    final field = tester.widget<TextField>(find.byType(TextField));
    expect(field.controller?.text, 'Rent');
  });
}
