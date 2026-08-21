/// The ACTUAL create journey, end to end: tap the FAB → name it → land in a
/// rendered editor.
///
/// Every earlier documents test mounted an editor DIRECTLY with a pre-seeded
/// cache. That skipped the whole path the user actually walks — the FAB, the
/// name dialog, `createBlank`, and the `Navigator.push` — which is exactly
/// where "created a document, got a black screen" would live.
///
/// Uses a REAL on-device DB (sqflite_ffi) and the REAL providers so
/// `createBlank`'s local-first write, its outbox enqueue and the background
/// sync all run for real. Only the network transport is faked.
library;

import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_quill/flutter_quill.dart'
    show FlutterQuillLocalizations, QuillEditor;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/providers/documents_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart'
    show appDatabaseProvider, reachabilityProvider;
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';
import 'package:lazyclaw_mobile/screens/documents/doc_editor_screen.dart';
import 'package:lazyclaw_mobile/screens/documents/documents_screen.dart';
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

/// Models a REACHABLE server that simply doesn't have the brand-new document
/// yet — the outbox create hasn't drained by the time the editor's
/// revalidation fires. This is the on-device timing, not an offline case.
class _ServerWithoutTheNewDoc implements DocumentsTransport {
  final List<String> getPaths = [];

  /// Ids the server has accepted (populated by the outbox `create` push).
  final Set<String> known = {};

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    getPaths.add(path);
    if (path.contains('/changes')) {
      return const {'items': [], 'server_time': '2026-08-21T12:00:00Z'};
    }
    if (RegExp(r'/api/(sheets|docs|pdf)$').hasMatch(path)) {
      return const {'sheets': [], 'docs': [], 'files': []};
    }
    final id = path.split('/').last;
    if (!known.contains(id)) {
      final req = RequestOptions(path: path);
      throw DioException(
        requestOptions: req,
        type: DioExceptionType.badResponse,
        response: Response(requestOptions: req, statusCode: 404),
        error: ApiError(404, 'Not found'),
      );
    }
    return {'id': id, 'name': 'Doc', 'payload': const {}, 'updated_at': 'x'};
  }

  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async {
    final id = (b['id'] ?? 'srv').toString();
    known.add(id);
    final row = {'id': id, 'name': b['name'], 'updated_at': '2026-08-21T12:00:00Z'};
    return {'sheet': row, 'doc': row};
  }

  @override
  Future<Map<String, dynamic>> putJson(String p, Map<String, dynamic> b) async =>
      {'sheet': {'updated_at': 'y'}, 'doc': {'updated_at': 'y'}};
  @override
  Future<Map<String, dynamic>> patchJson(String p, Map<String, dynamic> b) async =>
      const {};
  @override
  Future<Map<String, dynamic>> deleteJson(String p) async => const {};
  @override
  Future<Map<String, dynamic>> uploadFile(String p, File f) async => const {};
  @override
  Future<List<int>> getBytes(String p) async => const [];
  @override
  Future<List<int>> postBytes(String p, Map<String, dynamic> b) async => const [];
}

Future<Database> _openMemDb() => databaseFactoryFfi.openDatabase(
      'file:createjourney${_dbCounter++}?mode=memory&cache=shared',
      options: OpenDatabaseOptions(
        version: kAppDbVersion,
        singleInstance: false,
        onCreate: (db, _) => createAppDbSchema(db),
      ),
    );

ProviderContainer _scope(Database db, DocumentsTransport transport) =>
    ProviderContainer(overrides: [
      appDatabaseProvider.overrideWithValue(db),
      documentsRepositoryProvider
          .overrideWithValue(DocumentsRepository(transport)),
      reachabilityProvider.overrideWith((ref) {
        final reach = Reachability(_FakeProbe());
        ref.onDispose(reach.dispose);
        return reach;
      }),
    ]);

Future<void> _pump(WidgetTester tester, {int ticks = 20}) async {
  await tester.pump();
  for (var i = 0; i < ticks; i++) {
    await Future<void>.delayed(const Duration(milliseconds: 30));
    await tester.pump();
  }
}

Widget _host(ProviderContainer container) => UncontrolledProviderScope(
      container: container,
      child: MaterialApp(
        localizationsDelegates: FlutterQuillLocalizations.localizationsDelegates,
        supportedLocales: FlutterQuillLocalizations.supportedLocales,
        home: const DocumentsScreen(),
      ),
    );

/// Walk the create flow: FAB → name dialog → Create.
Future<void> _create(WidgetTester tester, String name) async {
  await tester.tap(find.byType(FloatingActionButton));
  await _pump(tester, ticks: 6);
  await tester.enterText(find.byType(TextField).first, name);
  await _pump(tester, ticks: 4);
  await tester.tap(find.text('Create'));
  await _pump(tester, ticks: 25);
}

void main() {
  setUpAll(sqfliteFfiInit);

  testWidgets('creating a SHEET lands on a rendered grid, not a blank screen',
      (tester) async {
    tester.view.physicalSize = const Size(1080, 2340);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.runAsync(() async {
      final db = await _openMemDb();
      final transport = _ServerWithoutTheNewDoc();
      final container = _scope(db, transport);
      addTearDown(container.dispose);

      await tester.pumpWidget(_host(container));
      await _pump(tester);

      await _create(tester, 'My budget');

      expect(tester.takeException(), isNull,
          reason: 'the create journey threw');
      expect(find.byType(SheetEditorScreen), findsOneWidget,
          reason: 'never navigated into the editor');
      expect(find.byType(LzSkeleton), findsNothing,
          reason: 'stuck on the shimmer — reads as a black screen');
      // Column headers prove the grid actually painted.
      expect(find.text('A'), findsOneWidget);
      expect(find.text('B'), findsOneWidget);
    });
  });

  testWidgets('creating a DOC lands on a rendered editor, not a blank screen',
      (tester) async {
    tester.view.physicalSize = const Size(1080, 2340);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.runAsync(() async {
      final db = await _openMemDb();
      final container = _scope(db, _ServerWithoutTheNewDoc());
      addTearDown(container.dispose);

      await tester.pumpWidget(_host(container));
      await _pump(tester);

      // Switch to the Docs sub-tab first.
      await tester.tap(find.text('Docs'));
      await _pump(tester, ticks: 6);

      await _create(tester, 'My notes');

      expect(tester.takeException(), isNull);
      expect(find.byType(DocEditorScreen), findsOneWidget,
          reason: 'never navigated into the doc editor');
      expect(find.byType(LzSkeleton), findsNothing);
      expect(find.byType(QuillEditor), findsOneWidget,
          reason: 'the editor body did not paint');
    });
  });

  testWidgets('the new sheet survives the outbox push completing',
      (tester) async {
    // After the create pushes, the server DOES know the id — the editor's
    // revalidation then returns a payload. If that payload is empty the grid
    // must not be wiped to blank.
    await tester.runAsync(() async {
      final db = await _openMemDb();
      final transport = _ServerWithoutTheNewDoc();
      final container = _scope(db, transport);
      addTearDown(container.dispose);

      await tester.pumpWidget(_host(container));
      await _pump(tester);
      await _create(tester, 'Pushed sheet');
      // Let the outbox drain and the revalidation land.
      await _pump(tester, ticks: 30);

      expect(tester.takeException(), isNull);
      expect(find.byType(LzSkeleton), findsNothing);
      expect(find.text('A'), findsOneWidget,
          reason: 'an empty server payload wiped the grid');
    });
  });
}
