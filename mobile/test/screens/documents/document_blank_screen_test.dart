/// Regressions for the "created a document → black/blank screen" class.
///
/// Four distinct ways a document editor could end up showing NOTHING on the
/// `#0D0D0D` scaffold, each with no message and no way out:
///
///  1. **Payload-less cache row.** After an import (and after any `/changes`
///     pull that carries metadata only) the cache holds a row with `payload =
///     NULL`. `CachedDoc.payload` maps that to `const {}`, the editor parsed it
///     into a workbook with NO worksheets, cleared `_error`, cleared `_loading`
///     — and if the follow-up network read then failed, the user was stranded on
///     a blank grid forever. A payload-less row must count as a cache MISS.
///
///  2. **Silent `SizedBox.shrink()`.** Both editors returned an empty box when
///     the model was null but nothing was loading and no error was set.
///
///  3. **Parse throwing out of `_load`.** The cache-branch parse sat outside any
///     `try`, and `_load` runs from `addPostFrameCallback` — so a throw became an
///     unhandled async error with `_loading` still true → the shimmer skeleton
///     forever, which on the dark theme reads as a black screen.
///
///  4. **PDF render failure.** `PdfDocument.openData` on a non-PDF blob left
///     `PdfViewPinch` painting nothing over a near-black `ColoredBox`.
///
/// Harness mirrors `sheet_editor_new_sheet_test.dart`: everything inside
/// `tester.runAsync()` (sqflite_ffi uses real timers), no `pumpAndSettle` (the
/// shimmer never settles), and the fake transport throws the PRODUCTION
/// `DioException(error: ApiError(...))` shape.
library;

import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_quill/flutter_quill.dart' show FlutterQuillLocalizations;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/document_cache_dao.dart';
import 'package:lazyclaw_mobile/providers/documents_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart'
    show appDatabaseProvider, reachabilityProvider;
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';
import 'package:lazyclaw_mobile/screens/documents/doc_editor_screen.dart';
import 'package:lazyclaw_mobile/screens/documents/pdf_viewer_screen.dart';
import 'package:lazyclaw_mobile/screens/documents/sheet_editor_screen.dart';
import 'package:lazyclaw_mobile/sync/reachability.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

class _FakeProbe implements ConnectivityProbe {
  @override
  Stream<bool> get onChanged => const Stream<bool>.empty();
  @override
  Future<bool> hasLink() async => true;
  @override
  Future<bool> pingHost() async => true;
}

DioException _notFoundDio(String path) {
  final req = RequestOptions(path: path);
  return DioException(
    requestOptions: req,
    type: DioExceptionType.badResponse,
    response: Response(requestOptions: req, statusCode: 404),
    error: ApiError(404, 'Not found'),
  );
}

/// Every document GET 404s; list + /changes are empty. Models "the row is in the
/// local cache but the server read fails" — offline, session hiccup, or a
/// local-first create whose outbox push hasn't landed.
class _DocGetFailsTransport implements DocumentsTransport {
  int getDocCalls = 0;
  int getBytesCalls = 0;

  /// When set, `getBytes` returns these instead of throwing (PDF cases).
  List<int>? bytes;

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    if (path.contains('/changes')) {
      return const {
        'items': [],
        'deleted': [],
        'server_time': '2026-08-21T12:00:00Z',
      };
    }
    if (RegExp(r'/api/(sheets|docs|pdf)$').hasMatch(path)) {
      return const {'sheets': [], 'docs': [], 'files': []};
    }
    getDocCalls++;
    throw _notFoundDio(path);
  }

  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async =>
      {'sheet': {'id': b['id'] ?? 'srv'}, 'doc': {'id': b['id'] ?? 'srv'}};
  @override
  Future<Map<String, dynamic>> putJson(String p, Map<String, dynamic> b) async =>
      const {};
  @override
  Future<Map<String, dynamic>> patchJson(String p, Map<String, dynamic> b) async =>
      const {};
  @override
  Future<Map<String, dynamic>> deleteJson(String p) async => const {};
  @override
  Future<Map<String, dynamic>> uploadFile(String p, File f) async => const {};
  @override
  Future<List<int>> getBytes(String p) async {
    getBytesCalls++;
    final b = bytes;
    if (b == null) throw _notFoundDio(p);
    return b;
  }

  @override
  Future<List<int>> postBytes(String p, Map<String, dynamic> b) async =>
      const [];
}

Future<Database> _openMemDb() => databaseFactoryFfi.openDatabase(
      'file:blankscreenmem${_dbCounter++}?mode=memory&cache=shared',
      options: OpenDatabaseOptions(
        version: kAppDbVersion,
        singleInstance: false,
        onCreate: (db, _) => createAppDbSchema(db),
      ),
    );

({ProviderContainer container, _DocGetFailsTransport transport}) _scope(
    Database db) {
  final transport = _DocGetFailsTransport();
  final container = ProviderContainer(overrides: [
    appDatabaseProvider.overrideWithValue(db),
    documentsRepositoryProvider
        .overrideWithValue(DocumentsRepository(transport)),
    reachabilityProvider.overrideWith((ref) {
      final reach = Reachability(_FakeProbe());
      ref.onDispose(reach.dispose);
      return reach;
    }),
  ]);
  return (container: container, transport: transport);
}

Future<void> _pump(WidgetTester tester) async {
  await tester.pump();
  for (var i = 0; i < 15; i++) {
    await Future<void>.delayed(const Duration(milliseconds: 30));
    await tester.pump();
  }
}

/// The screen must never be featureless: either real content, or an error state
/// the user can act on. Never an empty box, never an endless shimmer.
void _expectNotBlank(WidgetTester tester, {required String because}) {
  expect(tester.takeException(), isNull, reason: because);
  expect(find.byType(LzSkeleton), findsNothing,
      reason: 'stuck on the infinite shimmer skeleton — $because');
  final hasError = find.byType(LzErrorState).evaluate().isNotEmpty;
  final hasEmpty = find.byType(LzEmptyState).evaluate().isNotEmpty;
  expect(hasError || hasEmpty, isTrue,
      reason: 'blank screen with no message and no retry — $because');
}

void main() {
  setUpAll(sqfliteFfiInit);

  // ── 1. Payload-less cache row ──────────────────────────────────────────────

  testWidgets(
      'sheet cached with NO payload + failing network shows a retryable error, '
      'not a silently blank grid', (tester) async {
    await tester.runAsync(() async {
      final db = await _openMemDb();
      final s = _scope(db);
      addTearDown(s.container.dispose);

      // Exactly what `_cacheMeta` writes after an import: metadata, no payload.
      await DocumentCacheDao(db).putServerDoc(
        kind: 'sheets',
        id: 'meta-only',
        name: 'Imported sheet',
        updatedAt: '2026-08-21T10:00:00Z',
      );

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: s.container,
          child: const MaterialApp(
            home: SheetEditorScreen(id: 'meta-only', name: 'Imported sheet'),
          ),
        ),
      );
      await _pump(tester);

      expect(s.transport.getDocCalls, greaterThan(0),
          reason: 'a payload-less row must not short-circuit the network read');
      _expectNotBlank(tester, because: 'payload-less cache row, sheet');
    });
  });

  testWidgets(
      'doc cached with NO payload + failing network shows a retryable error',
      (tester) async {
    await tester.runAsync(() async {
      final db = await _openMemDb();
      final s = _scope(db);
      addTearDown(s.container.dispose);

      await DocumentCacheDao(db).putServerDoc(
        kind: 'docs',
        id: 'meta-only',
        name: 'Imported doc',
        updatedAt: '2026-08-21T10:00:00Z',
      );

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: s.container,
          child: MaterialApp(
            localizationsDelegates:
                FlutterQuillLocalizations.localizationsDelegates,
            supportedLocales: FlutterQuillLocalizations.supportedLocales,
            home: const DocEditorScreen(id: 'meta-only', name: 'Imported doc'),
          ),
        ),
      );
      await _pump(tester);

      _expectNotBlank(tester, because: 'payload-less cache row, doc');
    });
  });

  // ── 2 + 3. A corrupt cached payload must not strand the editor ─────────────

  testWidgets('sheet with a MALFORMED cached payload never goes blank',
      (tester) async {
    await tester.runAsync(() async {
      final db = await _openMemDb();
      final s = _scope(db);
      addTearDown(s.container.dispose);

      // A payload that decodes but is nonsense for a workbook: `sheets` is a
      // list where the parser expects a map, `sheetOrder` holds a map.
      await DocumentCacheDao(db).putServerDoc(
        kind: 'sheets',
        id: 'garbage',
        name: 'Corrupt sheet',
        payloadJson: jsonEncode({
          'sheets': ['not', 'a', 'map'],
          'sheetOrder': {'also': 'wrong'},
        }),
        updatedAt: '2026-08-21T10:00:00Z',
      );

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: s.container,
          child: const MaterialApp(
            home: SheetEditorScreen(id: 'garbage', name: 'Corrupt sheet'),
          ),
        ),
      );
      await _pump(tester);

      // Either it parsed into a usable grid, or it surfaced an error — but the
      // load must never throw out of the post-frame callback.
      expect(tester.takeException(), isNull,
          reason: 'parse threw out of _load → _loading stuck true');
      expect(find.byType(LzSkeleton), findsNothing);
    });
  });

  testWidgets('doc with a MALFORMED cached payload never goes blank',
      (tester) async {
    await tester.runAsync(() async {
      final db = await _openMemDb();
      final s = _scope(db);
      addTearDown(s.container.dispose);

      await DocumentCacheDao(db).putServerDoc(
        kind: 'docs',
        id: 'garbage',
        name: 'Corrupt doc',
        payloadJson: jsonEncode({
          'body': {
            // `dataStream` must be a String; paragraphs must be a list of maps.
            'dataStream': {'nope': true},
            'paragraphs': 'not-a-list',
            'textRuns': 42,
          },
        }),
        updatedAt: '2026-08-21T10:00:00Z',
      );

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: s.container,
          child: MaterialApp(
            localizationsDelegates:
                FlutterQuillLocalizations.localizationsDelegates,
            supportedLocales: FlutterQuillLocalizations.supportedLocales,
            home: const DocEditorScreen(id: 'garbage', name: 'Corrupt doc'),
          ),
        ),
      );
      await _pump(tester);

      expect(tester.takeException(), isNull,
          reason: 'deltaFromUniver threw out of _load → _loading stuck true');
      expect(find.byType(LzSkeleton), findsNothing);
    });
  });

  // ── 4. PDF ─────────────────────────────────────────────────────────────────

  testWidgets('PDF whose bytes are not a PDF shows a message, not a black void',
      (tester) async {
    await tester.runAsync(() async {
      final db = await _openMemDb();
      final s = _scope(db);
      addTearDown(s.container.dispose);

      // Server answers, but with something pdfium cannot open.
      s.transport.bytes = utf8.encode('this is definitely not a PDF');

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: s.container,
          child: const MaterialApp(
            home: PdfViewerScreen(id: 'broken', name: 'Broken.pdf'),
          ),
        ),
      );
      await _pump(tester);

      _expectNotBlank(tester, because: 'undecodable PDF bytes');
    });
  });

  testWidgets('PDF whose fetch fails shows a retryable error', (tester) async {
    await tester.runAsync(() async {
      final db = await _openMemDb();
      final s = _scope(db);
      addTearDown(s.container.dispose);

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: s.container,
          child: const MaterialApp(
            home: PdfViewerScreen(id: 'missing', name: 'Missing.pdf'),
          ),
        ),
      );
      await _pump(tester);

      _expectNotBlank(tester, because: 'PDF fetch failed');
    });
  });
}
