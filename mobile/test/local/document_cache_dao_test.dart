import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/document_cache_dao.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

/// Real in-memory SQLite (ffi) with the production schema so the read-through
/// document cache DAO is verified against the actual engine.
Future<DocumentCacheDao> _freshDao({
  String Function()? now,
  int budgetBytes = kDocumentCacheBudgetBytes,
}) async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:doccachemem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return DocumentCacheDao(db, now: now, budgetBytes: budgetBytes);
}

void main() {
  setUpAll(() {
    sqfliteFfiInit();
  });

  group('payload round-trip', () {
    test('stores and reads back a sheet/doc Univer payload', () async {
      final dao = await _freshDao();
      final payload = jsonEncode({
        'id': 's1',
        'sheets': {'A': {'name': 'Sheet1'}},
      });
      await dao.putDoc(
        kind: 'sheets',
        id: 's1',
        name: 'Budget',
        payloadJson: payload,
        updatedAt: '2026-06-09T10:00:00Z',
      );

      final got = await dao.getDoc('sheets', 's1');
      expect(got, isNotNull);
      expect(got!.name, 'Budget');
      expect(got.payloadJson, payload);
      expect(got.bytes, isNull);
      expect(got.updatedAt, '2026-06-09T10:00:00Z');
    });

    test('stores and reads back PDF bytes', () async {
      final dao = await _freshDao();
      final bytes = List<int>.generate(256, (i) => i % 256);
      await dao.putDoc(kind: 'pdf', id: 'p1', name: 'Invoice', bytes: bytes);

      final got = await dao.getDoc('pdf', 'p1');
      expect(got, isNotNull);
      expect(got!.bytes, equals(bytes));
      expect(got.payloadJson, isNull);
    });

    test('getDoc returns null when absent', () async {
      final dao = await _freshDao();
      expect(await dao.getDoc('sheets', 'nope'), isNull);
    });

    test('putDoc upserts — second write replaces name + payload', () async {
      final dao = await _freshDao();
      await dao.putDoc(kind: 'docs', id: 'd1', name: 'Old', payloadJson: '{"v":1}');
      await dao.putDoc(kind: 'docs', id: 'd1', name: 'New', payloadJson: '{"v":2}');
      final got = await dao.getDoc('docs', 'd1');
      expect(got!.name, 'New');
      expect(got.payloadJson, '{"v":2}');
    });

    test('same id under different kinds are independent rows', () async {
      final dao = await _freshDao();
      await dao.putDoc(kind: 'sheets', id: 'x', name: 'S', payloadJson: '{"s":1}');
      await dao.putDoc(kind: 'docs', id: 'x', name: 'D', payloadJson: '{"d":1}');
      expect((await dao.getDoc('sheets', 'x'))!.name, 'S');
      expect((await dao.getDoc('docs', 'x'))!.name, 'D');
    });

    test('deleteDoc removes only that row', () async {
      final dao = await _freshDao();
      await dao.putDoc(kind: 'sheets', id: 'a', name: 'A', payloadJson: '{}');
      await dao.putDoc(kind: 'sheets', id: 'b', name: 'B', payloadJson: '{}');
      await dao.deleteDoc('sheets', 'a');
      expect(await dao.getDoc('sheets', 'a'), isNull);
      expect(await dao.getDoc('sheets', 'b'), isNotNull);
    });
  });

  group('list cache', () {
    test('putList + getList round-trips items; null when absent', () async {
      final dao = await _freshDao();
      expect(await dao.getList('sheets'), isNull);

      final items = [
        {'id': 's1', 'name': 'One', 'updated_at': '2026-06-09T10:00:00Z'},
        {'id': 's2', 'name': 'Two'},
      ];
      await dao.putList('sheets', items);

      final got = await dao.getList('sheets');
      expect(got, isNotNull);
      expect(got!.length, 2);
      expect(got[0]['id'], 's1');
      expect(got[1]['name'], 'Two');
      // Other kinds stay independent / empty.
      expect(await dao.getList('docs'), isNull);
    });
  });

  group('LRU eviction by byte budget', () {
    test('evicts the oldest cached row when over budget', () async {
      var t = 0;
      // Tiny budget so the 2nd insert pushes the 1st out.
      final dao = await _freshDao(
        now: () => '2026-06-09T00:00:${(t++).toString().padLeft(2, '0')}Z',
        budgetBytes: 300,
      );
      final big = 'x' * 200; // ~200 bytes each
      await dao.putDoc(kind: 'sheets', id: 'old', name: 'old', payloadJson: big);
      await dao.putDoc(kind: 'sheets', id: 'new', name: 'new', payloadJson: big);

      expect(await dao.getDoc('sheets', 'old'), isNull, reason: 'oldest evicted');
      expect(await dao.getDoc('sheets', 'new'), isNotNull);
      expect(await dao.totalBytes(), lessThanOrEqualTo(300));
    });
  });
}
