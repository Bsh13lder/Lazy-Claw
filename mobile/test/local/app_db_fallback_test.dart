import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';

void main() {
  sqfliteFfiInit();
  databaseFactory = databaseFactoryFfi;

  test('falls back to in-memory + degraded when file open keeps failing', () async {
    var calls = 0;
    final result = await openAppDbWithFallback(
      retries: 2,
      openImpl: () async { calls++; throw StateError('keychain locked'); },
      openInMemory: () async {
        final db = await databaseFactory.openDatabase(inMemoryDatabasePath);
        await createAppDbSchema(db);
        return db;
      },
    );
    expect(calls, 3);
    expect(result.health.isDegraded, true);
    expect(result.health.error, isA<StateError>());
    expect(await result.db.query('note_cache'), isEmpty);
  });

  test('returns ok health when file open succeeds first try', () async {
    final result = await openAppDbWithFallback(
      openImpl: () async {
        final db = await databaseFactory.openDatabase(inMemoryDatabasePath);
        await createAppDbSchema(db);
        return db;
      },
    );
    expect(result.health.isDegraded, false);
  });
}
