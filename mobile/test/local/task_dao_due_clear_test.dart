// Regression tests for clearing a task's due date on mobile (bug T2).
//
// On the phone, clearing a due date + Save was a silent no-op: the detail
// sheet sent Dart `null` for a cleared due date, which the DAO's null-aware
// patch treats as "field untouched" (same as every other unchanged field). A
// cleared due date must instead ride an explicit clear sentinel so it (a) nulls
// the local cache row and (b) is INCLUDED in the outbox patch so the clear
// SYNCS to the server.
//
// These exercise the DAO against a real in-memory SQLite (FFI) with the
// production schema — same harness as task_dao_test.dart — because the fix
// lives in the DAO/model clear contract, not in the (widget-only) sheet.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

/// Isolated in-memory DB per call (mirrors task_dao_test.dart::_freshDao).
Future<TaskDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:dueclearmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return TaskDao(db);
}

/// Create a task carrying a due date, then drain the create op so the next
/// readOutbox() returns only the update op under test.
Future<TaskDao> _daoWithDatedTask(String id) async {
  final dao = await _freshDao();
  await dao.applyLocalCreate('Dated task', id: id, dueDate: '2026-06-09');
  for (final o in await dao.readOutbox()) {
    await dao.deleteOutboxItem(o.seq);
  }
  return dao;
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('applyLocalUpdate — clearing due_date (bug T2)', () {
    test(
      'the "" clear sentinel nulls the cache row AND rides the outbox patch',
      () async {
        final dao = await _daoWithDatedTask('t-clear');

        final updated = await dao.applyLocalUpdate('t-clear', dueDate: '');

        // (a) The returned Task and the cached row are both cleared.
        expect(updated, isNotNull);
        expect(updated!.dueDate, isNull, reason: 'copyWith must clear the value');
        final row = await dao.getById('t-clear');
        expect(row!.dueDate, isNull, reason: 'cache row must be cleared');

        // (b) The clear is a real change in the outbox, not a dropped key, so it
        // syncs to the server (server clears due_date on an empty value).
        final outbox = await dao.readOutbox();
        expect(outbox, hasLength(1));
        expect(outbox.single.op, OutboxOp.update);
        expect(
          outbox.single.payload.containsKey('due_date'),
          isTrue,
          reason: 'a cleared due_date must be included in the patch, not omitted',
        );
        expect(outbox.single.payload['due_date'], '');
      },
    );

    test(
      'passing null leaves due_date untouched and omits it from the patch',
      () async {
        final dao = await _daoWithDatedTask('t-keep');

        // A title-only edit (dueDate stays null) must NOT clear the due date.
        final updated = await dao.applyLocalUpdate('t-keep', title: 'renamed');

        expect(updated!.dueDate, '2026-06-09');
        expect((await dao.getById('t-keep'))!.dueDate, '2026-06-09');
        final outbox = await dao.readOutbox();
        expect(outbox.single.payload.containsKey('due_date'), isFalse);
      },
    );

    test('a non-empty value still sets due_date normally', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('No due yet', id: 't-set');
      for (final o in await dao.readOutbox()) {
        await dao.deleteOutboxItem(o.seq);
      }

      final updated = await dao.applyLocalUpdate('t-set', dueDate: '2026-07-01');

      expect(updated!.dueDate, '2026-07-01');
      expect((await dao.getById('t-set'))!.dueDate, '2026-07-01');
      expect((await dao.readOutbox()).single.payload['due_date'], '2026-07-01');
    });
  });
}
