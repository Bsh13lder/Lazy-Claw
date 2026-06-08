import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_quill/flutter_quill.dart'
    show FlutterQuillLocalizations, QuillSimpleToolbar, QuillEditor;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/documents_provider.dart';
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';
import 'package:lazyclaw_mobile/screens/documents/doc_editor_screen.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_quill.dart';

/// Returns a doc payload (heading + numbered list) from getJson.
class _FakeTransport implements DocumentsTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    final payload = univerFromBlocks([
      DocBlock.heading('Setup', 1),
      DocBlock.number('Install deps'),
    ]);
    return {'id': 'd-1', 'name': 'Guide', 'payload': payload};
  }

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

Widget _host() => ProviderScope(
      overrides: [
        documentsRepositoryProvider
            .overrideWithValue(DocumentsRepository(_FakeTransport())),
      ],
      child: MaterialApp(
        localizationsDelegates: FlutterQuillLocalizations.localizationsDelegates,
        supportedLocales: FlutterQuillLocalizations.supportedLocales,
        home: const DocEditorScreen(id: 'd-1', name: 'Guide'),
      ),
    );

void main() {
  testWidgets('renders a Quill toolbar + editor with the doc text', (tester) async {
    await tester.pumpWidget(_host());
    await tester.pumpAndSettle();

    // The editor + toolbar mount only after the payload loads and converts to a
    // Quill document (content fidelity is covered by univer_quill_test).
    expect(find.byType(QuillSimpleToolbar), findsOneWidget);
    expect(find.byType(QuillEditor), findsOneWidget);
  });
}
