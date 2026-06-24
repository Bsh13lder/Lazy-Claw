/// Provider-level tests for the documents LIST notifier as a sync surface:
///   • A doc CREATED on web (server-only, surfaced via /changes) appears in the
///     mobile list after a load/refresh tick — the "web edits don't show on
///     Flutter" fix.
///   • An offline-first create enqueues + surfaces instantly in the list.
///   • A server tombstone (pulled via /changes) removes the doc from the list.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/document_cache_dao.dart';
import 'package:lazyclaw_mobile/providers/documents_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart'
    show appDatabaseProvider, reachabilityProvider;
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';
import 'package:lazyclaw_mobile/sync/reachability.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

/// A no-op connectivity probe so [Reachability] never opens the real OS
/// connectivity EventChannel (which needs a full TestWidgetsFlutterBinding).
class _FakeProbe implements ConnectivityProbe {
  @override
  Stream<bool> get onChanged => const Stream<bool>.empty();
  @override
  Future<bool> hasLink() async => true;
  @override
  Future<bool> pingHost() async => true;
}

/// A transport whose plain list endpoint is EMPTY but whose /changes feed
/// returns server deltas — modelling a doc made on the web that the bare list
/// hasn't surfaced yet but the delta feed has.
class _ChangesTransport implements DocumentsTransport {
  final List<Map<String, dynamic>> changeItems;
  final List<String> deletedIds;

  _ChangesTransport({
    this.changeItems = const [],
    this.deletedIds = const [],
  });

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    if (path.contains('/changes')) {
      return {
        'items': changeItems,
        'deleted': deletedIds,
        'server_time': '2026-06-20T12:00:00Z',
      };
    }
    return const {'sheets': [], 'docs': [], 'files': []};
  }

  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async =>
      {'sheet': {'id': b['id'] ?? 'srv', 'name': b['name'], 'updated_at': '2026-06-20T10:00:00Z'}};
  @override
  Future<Map<String, dynamic>> putJson(String p, Map<String, dynamic> b) async =>
      {'sheet': {'id': 'x', 'updated_at': '2026-06-20T12:00:00Z'}};
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

Future<ProviderContainer> _container(DocumentsTransport transport) async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:doclistmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return ProviderContainer(overrides: [
    appDatabaseProvider.overrideWithValue(db),
    documentsRepositoryProvider
        .overrideWithValue(DocumentsRepository(transport)),
    // Avoid the real connectivity EventChannel in unit tests by swapping the
    // probe the real Reachability builds on for a no-op one.
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

  test('a web-created doc (only in /changes) surfaces in the mobile list',
      () async {
    final transport = _ChangesTransport(changeItems: [
      {
        'id': 'web1',
        'name': 'Made on web',
        'payload': {'a': 1},
        'updated_at': '2026-06-20T11:00:00Z',
      }
    ]);
    final c = await _container(transport);
    final notifier = c.read(documentsListProvider(DocKind.sheets).notifier);

    await notifier.load(); // load → sync (pull /changes) → merge

    final ids = c.read(documentsListProvider(DocKind.sheets)).items.map((m) => m.id);
    expect(ids, contains('web1'));

    // And it landed in the cache (so it persists offline too).
    final dao = DocumentCacheDao(c.read(appDatabaseProvider));
    expect((await dao.listDocs('sheets')).map((d) => d.id), contains('web1'));
    c.dispose();
  });

  test('an offline-first create surfaces in the list instantly + enqueues',
      () async {
    final transport = _ChangesTransport();
    final c = await _container(transport);
    final notifier = c.read(documentsListProvider(DocKind.sheets).notifier);

    final meta = await notifier.createBlank('My offline sheet');
    expect(meta, isNotNull);

    final ids =
        c.read(documentsListProvider(DocKind.sheets)).items.map((m) => m.id);
    expect(ids, contains(meta!.id));

    // The create is queued for push.
    final dao = DocumentCacheDao(c.read(appDatabaseProvider));
    final queue = await dao.readOutbox();
    expect(queue.any((o) => o.op == DocOutboxOp.create && o.docId == meta.id),
        isTrue);
    c.dispose();
  });

  test('a server tombstone pulled via /changes removes the doc from the list',
      () async {
    // Seed a cached doc, then a /changes feed that tombstones it.
    final transport = _ChangesTransport(deletedIds: ['gone1']);
    final c = await _container(transport);
    final dao = DocumentCacheDao(c.read(appDatabaseProvider));
    await dao.putServerDoc(kind: 'sheets', id: 'gone1', name: 'Doomed');

    final notifier = c.read(documentsListProvider(DocKind.sheets).notifier);
    await notifier.refresh(); // sync → pull tombstone → merge

    final ids =
        c.read(documentsListProvider(DocKind.sheets)).items.map((m) => m.id);
    expect(ids, isNot(contains('gone1')));
    c.dispose();
  });
}
