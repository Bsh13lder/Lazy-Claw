/// Reproduction + regression for the "NEW sheet → black + stuck" bug.
///
/// On-device, creating a brand-NEW sheet goes black and hangs, while opening an
/// EXISTING sheet works. The differentiator: a brand-new sheet is LOCAL-ONLY —
/// it lives in the on-device cache as a valid blank `blankWorkbook` dirty row,
/// but it is NOT on the server yet, so the editor's network
/// `getPayload('/api/sheets/<id>')` returns a 404 (an existing sheet 200s).
///
/// ROOT CAUSE the tests pin down: the editor's loading state is rendered with a
/// shimmer skeleton whose [AnimationController] is `..repeat()` — an INFINITE
/// animation. While `_loading == true`, that skeleton is on screen; on the dark
/// theme it reads as a black, stuck screen. So the editor MUST flip `_loading`
/// to false from the on-device cache (the blank workbook is already there)
/// WITHOUT waiting on the network — and a 404 on the network revalidation must
/// never leave the editor stuck in the skeleton state.
///
/// Harness mirrors `expenses_range_filter_test.dart`:
///   • ALL async work runs inside `tester.runAsync()` (sqflite_ffi uses real
///     timers that FakeAsync never advances → a fake-clock `pump(Duration)`
///     would deadlock on the DB read).
///   • `pumpAndSettle` is avoided (LzSkeleton shimmers forever); a fixed
///     `Future.delayed` + `pump()` loop drains microtasks instead.
///   • reachability is a no-op probe (no platform channel).
///   • the fake transport throws the PRODUCTION 404 shape
///     (`DioException(error: ApiError(404))` — per the CLAUDE.md lesson).
library;

import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/document_cache_dao.dart';
import 'package:lazyclaw_mobile/providers/documents_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart'
    show appDatabaseProvider, reachabilityProvider;
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';
import 'package:lazyclaw_mobile/screens/documents/blank_doc.dart';
import 'package:lazyclaw_mobile/screens/documents/sheet_editor_screen.dart';
import 'package:lazyclaw_mobile/sync/reachability.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

/// No-op connectivity probe — keeps [Reachability] off the OS EventChannel.
class _FakeProbe implements ConnectivityProbe {
  @override
  Stream<bool> get onChanged => const Stream<bool>.empty();
  @override
  Future<bool> hasLink() async => true;
  @override
  Future<bool> pingHost() async => true;
}

/// A real 404 DioException of the production shape: the `DioDocumentsTransport`
/// surfaces a 4xx as a `DioException(error: ApiError(status))`.
DioException _notFoundDio() {
  final req = RequestOptions(path: '/api/sheets/local-1');
  return DioException(
    requestOptions: req,
    type: DioExceptionType.badResponse,
    response: Response(requestOptions: req, statusCode: 404),
    error: ApiError(404, 'Not found'),
  );
}

/// Models a brand-new, LOCAL-ONLY sheet: the GET of the doc throws 404 (it's not
/// on the server yet); list + /changes are empty so a background sync can't
/// clobber the local-first create.
class _NotOnServerTransport implements DocumentsTransport {
  int getDocCalls = 0;

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    if (path.contains('/changes')) {
      return const {
        'items': [],
        'deleted': [],
        'server_time': '2026-06-23T12:00:00Z'
      };
    }
    if (RegExp(r'/api/(sheets|docs|pdf)$').hasMatch(path)) {
      return const {'sheets': [], 'docs': [], 'files': []};
    }
    // The doc GET (`/api/sheets/<id>`) — local-only sheet isn't on the server.
    getDocCalls++;
    throw _notFoundDio();
  }

  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async =>
      {
        'sheet': {
          'id': b['id'] ?? 'srv',
          'name': b['name'],
          'updated_at': '2026-06-23T10:00:00Z'
        },
      };
  @override
  Future<Map<String, dynamic>> putJson(String p, Map<String, dynamic> b) async =>
      {
        'sheet': {'id': 'x', 'updated_at': '2026-06-23T12:00:00Z'}
      };
  @override
  Future<Map<String, dynamic>> patchJson(String p, Map<String, dynamic> b) async =>
      const {};
  @override
  Future<Map<String, dynamic>> deleteJson(String p) async =>
      {'status': 'deleted'};
  @override
  Future<Map<String, dynamic>> uploadFile(String p, File f) async => const {};
  @override
  Future<List<int>> getBytes(String p) async => const [];
  @override
  Future<List<int>> postBytes(String p, Map<String, dynamic> b) async =>
      const [];
}

Future<Database> _openMemDb() => databaseFactoryFfi.openDatabase(
      'file:newsheetmem${_dbCounter++}?mode=memory&cache=shared',
      options: OpenDatabaseOptions(
        version: kAppDbVersion,
        singleInstance: false,
        onCreate: (db, _) => createAppDbSchema(db),
      ),
    );

({ProviderContainer container, _NotOnServerTransport transport}) _scope(
    Database db) {
  final transport = _NotOnServerTransport();
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

/// Drain real-timer DB/provider work + UI rebuilds without `pumpAndSettle`
/// (the repeating shimmer skeleton never lets it settle). MUST run inside
/// `tester.runAsync()`.
Future<void> _pump(WidgetTester tester) async {
  await tester.pump();
  for (var i = 0; i < 15; i++) {
    await Future<void>.delayed(const Duration(milliseconds: 30));
    await tester.pump();
  }
}

void main() {
  setUpAll(sqfliteFfiInit);

  testWidgets(
      'NEW local-only sheet (cache blank + getPayload 404) renders the grid, '
      'not an infinite skeleton', (tester) async {
    await tester.runAsync(() async {
      final db = await _openMemDb();
      final s = _scope(db);
      addTearDown(s.container.dispose);

      // Seed the cache exactly like createBlank: a fresh blank dirty row.
      final dao = DocumentCacheDao(db);
      const id = 'local-1';
      await dao.applyLocalCreate(
        kind: 'sheets',
        id: id,
        name: 'Fresh sheet',
        payloadJson: jsonEncode(blankWorkbook('Fresh sheet')),
      );

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: s.container,
          child: const MaterialApp(
            home: SheetEditorScreen(id: id, name: 'Fresh sheet'),
          ),
        ),
      );
      await _pump(tester);

      // No uncaught exception escaped the build/load.
      expect(tester.takeException(), isNull);

      // The 404 revalidation DID fire (proves we modelled the local-only case).
      expect(s.transport.getDocCalls, greaterThan(0));

      // The loading skeleton must be GONE (loading flipped false from the cache).
      expect(find.byType(LzSkeleton), findsNothing,
          reason: 'editor stuck on the infinite shimmer skeleton (black/stuck)');

      // The grid actually rendered (column headers visible) — editor is alive.
      expect(find.text('A'), findsOneWidget);
      expect(find.text('B'), findsOneWidget);
    });
  });

  testWidgets(
      'NEW local-only sheet survives the background sync firing concurrently',
      (tester) async {
    await tester.runAsync(() async {
      final db = await _openMemDb();
      final s = _scope(db);
      addTearDown(s.container.dispose);

      // Drive the create through the REAL provider so `unawaited(_syncThenMerge())`
      // (the background sync) fires concurrently with the editor's cache read —
      // the exact race the on-device flow produces.
      final notifier =
          s.container.read(documentsListProvider(DocKind.sheets).notifier);
      final meta = await notifier.createBlank('Concurrent sheet');
      expect(meta, isNotNull);

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: s.container,
          child: MaterialApp(
            home: SheetEditorScreen(id: meta!.id, name: meta.name),
          ),
        ),
      );
      await _pump(tester);

      expect(tester.takeException(), isNull);
      expect(find.byType(LzSkeleton), findsNothing);
      expect(find.text('A'), findsOneWidget);
    });
  });

  testWidgets(
      'NEW local-only sheet renders + is editable at a phone-sized viewport',
      (tester) async {
    // A real-device-sized surface (Pixel-ish) — exercises the grid layout +
    // selection (toolbar/formula bar) the way the phone does, surfacing any
    // layout exception that would show as a black screen on-device.
    tester.view.physicalSize = const Size(1080, 2340);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.runAsync(() async {
      final db = await _openMemDb();
      final s = _scope(db);
      addTearDown(s.container.dispose);

      final dao = DocumentCacheDao(db);
      const id = 'local-1';
      await dao.applyLocalCreate(
        kind: 'sheets',
        id: id,
        name: 'Fresh sheet',
        payloadJson: jsonEncode(blankWorkbook('Fresh sheet')),
      );

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: s.container,
          child: const MaterialApp(
            home: SheetEditorScreen(id: id, name: 'Fresh sheet'),
          ),
        ),
      );
      await _pump(tester);

      expect(tester.takeException(), isNull);
      expect(find.byType(LzSkeleton), findsNothing);
      expect(find.text('A'), findsOneWidget);

      // Tap a cell → selection state adds the toolbar + a populated formula bar.
      // This is the first real interaction a user does on a brand-new sheet.
      await tester.tap(find.text('A'));
      await _pump(tester);
      expect(tester.takeException(), isNull);
      expect(find.byType(TextField), findsWidgets);
    });
  });

  // ── The actual ROOT-CAUSE regression ──────────────────────────────────────
  //
  // If the on-device cache read THROWS (a transient SQLite lock while the
  // background sync isolate holds the DB), `_load` must NOT abort with
  // `_loading` still true — that strands the editor on the infinite shimmer
  // skeleton (a black, stuck screen). It must degrade to the network path and,
  // when that also fails for a local-only doc (404), surface a retryable error
  // — never an eternal skeleton.
  testWidgets(
      'cache read THROWS + 404 -> retryable error, NOT a stuck skeleton',
      (tester) async {
    await tester.runAsync(() async {
      // A DAO whose DB is closed → every getDoc() throws DatabaseException.
      final brokenDb = await _openMemDb();
      await brokenDb.close();
      final brokenDao = DocumentCacheDao(brokenDb);

      final transport = _NotOnServerTransport();
      final container = ProviderContainer(overrides: [
        documentCacheDaoProvider.overrideWithValue(brokenDao),
        documentsRepositoryProvider
            .overrideWithValue(DocumentsRepository(transport)),
      ]);
      addTearDown(container.dispose);

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: const MaterialApp(
            home: SheetEditorScreen(id: 'local-1', name: 'Fresh sheet'),
          ),
        ),
      );
      await _pump(tester);

      // The cache-read exception must have been swallowed, not crashed the load.
      expect(tester.takeException(), isNull);
      // NOT stuck on the infinite skeleton…
      expect(find.byType(LzSkeleton), findsNothing,
          reason: 'cache-read throw stranded the editor on the skeleton');
      // …and a retryable error is shown instead.
      expect(find.byType(LzErrorState), findsOneWidget);
    });
  });
}
