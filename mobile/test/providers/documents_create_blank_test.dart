/// Regression tests for the "new sheet/doc is empty + uneditable" bug.
///
/// `createBlank` must store a NON-NULL Univer payload in the cache row AND in
/// the outbox `create` op, so that:
///   • opening the brand-new doc (cache-first) renders an editable structure
///     (≥1 worksheet for sheets, one paragraph for docs), not a blank grid; and
///   • the outbox `create` carries a real workbook for the server to persist.
library;

import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/document_cache_dao.dart';
import 'package:lazyclaw_mobile/providers/documents_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart'
    show appDatabaseProvider, reachabilityProvider;
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_parse.dart';
import 'package:lazyclaw_mobile/sync/reachability.dart';
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

/// Transport whose list + /changes feeds are empty (so a local-first create is
/// never clobbered by a server pull during the test).
class _EmptyTransport implements DocumentsTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    if (path.contains('/changes')) {
      return const {
        'items': [],
        'deleted': [],
        'server_time': '2026-06-23T12:00:00Z',
      };
    }
    return const {'sheets': [], 'docs': [], 'files': []};
  }

  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async =>
      {
        'sheet': {'id': b['id'] ?? 'srv', 'name': b['name'], 'updated_at': '2026-06-23T10:00:00Z'},
        'doc': {'id': b['id'] ?? 'srv', 'name': b['name'], 'updated_at': '2026-06-23T10:00:00Z'},
      };
  @override
  Future<Map<String, dynamic>> putJson(String p, Map<String, dynamic> b) async =>
      {'sheet': {'id': 'x', 'updated_at': '2026-06-23T12:00:00Z'}};
  @override
  Future<Map<String, dynamic>> patchJson(String p, Map<String, dynamic> b) async => const {};
  @override
  Future<Map<String, dynamic>> deleteJson(String p) async => {'status': 'deleted'};
  @override
  Future<Map<String, dynamic>> uploadFile(String p, f) async => const {};
  @override
  Future<List<int>> getBytes(String p) async => const [];
  @override
  Future<List<int>> postBytes(String p, Map<String, dynamic> b) async => const [];
}

Future<ProviderContainer> _container() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:doccreatemem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return ProviderContainer(overrides: [
    appDatabaseProvider.overrideWithValue(db),
    documentsRepositoryProvider
        .overrideWithValue(DocumentsRepository(_EmptyTransport())),
    reachabilityProvider.overrideWith((ref) {
      final reach = Reachability(_FakeProbe());
      ref.onDispose(reach.dispose);
      return reach;
    }),
  ]);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() => sqfliteFfiInit());

  test('createBlank (sheets) stores a non-null payload → editable worksheet',
      () async {
    final c = await _container();
    final notifier = c.read(documentsListProvider(DocKind.sheets).notifier);

    final meta = await notifier.createBlank('Fresh sheet');
    expect(meta, isNotNull);

    final dao = DocumentCacheDao(c.read(appDatabaseProvider));
    final cached = await dao.getDoc('sheets', meta!.id);
    expect(cached, isNotNull);
    expect(cached!.payloadJson, isNotNull);
    expect(cached.payloadJson, isNotEmpty);

    // The whole point: opening it parses into ≥1 editable worksheet, not a
    // blank, sheet-less grid (the regression).
    final sheet = UniverSheet.fromWorkbook(cached.payload);
    expect(sheet.sheetNames, isNotEmpty);
    final edited = sheet.setCell(0, 0, value: 'x');
    expect(edited.cellAt(0, 0).display, 'x');

    c.dispose();
  });

  test('createBlank (docs) stores a non-null payload → one paragraph',
      () async {
    final c = await _container();
    final notifier = c.read(documentsListProvider(DocKind.docs).notifier);

    final meta = await notifier.createBlank('Fresh doc');
    expect(meta, isNotNull);

    final dao = DocumentCacheDao(c.read(appDatabaseProvider));
    final cached = await dao.getDoc('docs', meta!.id);
    expect(cached, isNotNull);
    expect(cached!.payloadJson, isNotNull);
    expect(cached.payloadJson, isNotEmpty);

    expect(parseDocParagraphs(cached.payload), const ['']);

    c.dispose();
  });

  test('the outbox create op carries the blank workbook payload', () async {
    final c = await _container();
    final notifier = c.read(documentsListProvider(DocKind.sheets).notifier);

    final meta = await notifier.createBlank('Queued sheet');
    expect(meta, isNotNull);

    final dao = DocumentCacheDao(c.read(appDatabaseProvider));
    final queue = await dao.readOutbox();
    final create = queue.firstWhere(
        (o) => o.op == DocOutboxOp.create && o.docId == meta!.id);
    // Without the fix this key was absent (null payload) → server got a blank.
    expect(create.payload['payload'], isNotNull);
    final wb = create.payload['payload'].toString();
    final decoded = jsonDecode(wb) as Map<String, dynamic>;
    final sheet = UniverSheet.fromWorkbook(decoded);
    expect(sheet.sheetNames, isNotEmpty);

    c.dispose();
  });

  test('round-trip: create blank → read from cache → parse → editable',
      () async {
    final c = await _container();
    final notifier = c.read(documentsListProvider(DocKind.sheets).notifier);
    final meta = await notifier.createBlank('Round trip');

    // Fresh DAO instance (proves it survives a re-read, not just in-memory).
    final dao = DocumentCacheDao(c.read(appDatabaseProvider));
    final cached = await dao.getDoc('sheets', meta!.id);
    final sheet = UniverSheet.fromWorkbook(cached!.payload);
    expect(sheet.sheetNames, isNotEmpty);

    c.dispose();
  });
}
