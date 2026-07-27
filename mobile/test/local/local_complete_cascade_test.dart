// Regression tests for defect D2 — "completing a task on the phone left its
// checklist untouched".
//
// The server's complete_task cascades the checklist (every step → done) when a
// task is completed. TaskDao.applyLocalComplete only flipped status/completed_at,
// so the Tasks list showed the parent struck-through next to a stale "0/3" badge
// until a full server round-trip landed — and FOREVER while offline, which is
// exactly when the offline-first cache is the only truth the user sees.
//
// Exercised against a real in-memory SQLite (FFI) with the production schema,
// same harness as task_dao_test.dart / task_dao_due_clear_test.dart, so the
// cascade is asserted on the actual `steps` column round-trip.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/subtask.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

/// Isolated in-memory DB per call (mirrors task_dao_test.dart::_freshDao).
Future<TaskDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:completecascademem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return TaskDao(db);
}

/// Create a task (optionally with a checklist) then drain the create op so the
/// next readOutbox() returns only the ops enqueued by the complete under test.
Future<TaskDao> _daoWith(String id, {String? steps}) async {
  final dao = await _freshDao();
  await dao.applyLocalCreate('Ship the thing', id: id, steps: steps);
  for (final o in await dao.readOutbox()) {
    await dao.deleteOutboxItem(o.seq);
  }
  return dao;
}

String _steps(List<({String id, String title, bool done})> rows) =>
    serializeSubtasks([
      for (final r in rows) Subtask(id: r.id, title: r.title, done: r.done),
    ])!;

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('applyLocalComplete — subtask cascade (defect D2)', () {
    test('ticks every step in the cache row and the returned Task', () async {
      final dao = await _daoWith(
        't-cascade',
        steps: _steps([
          (id: 's1', title: 'Draft', done: false),
          (id: 's2', title: 'Review', done: true),
          (id: 's3', title: 'Send', done: false),
        ]),
      );

      final done = await dao.applyLocalComplete('t-cascade');

      // (a) The returned Task carries the cascaded checklist, so the optimistic
      // UI update paints 3/3 immediately (no server round-trip needed).
      expect(done, isNotNull);
      expect(done!.status, 'done');
      expect(subtaskProgressLabel(done.subtasks), '3/3');
      expect(done.subtasks.every((s) => s.done), isTrue);

      // (b) The cache row is cascaded too, so a cold restart while offline still
      // shows 3/3 rather than reverting to the stale count.
      final cached = await dao.getById('t-cascade');
      expect(subtaskProgressLabel(cached!.subtasks), '3/3');
      // Ids and titles are preserved — only `done` flips.
      expect(cached.subtasks.map((s) => s.id), ['s1', 's2', 's3']);
      expect(cached.subtasks.map((s) => s.title), ['Draft', 'Review', 'Send']);
    });

    test('enqueues ONLY the complete op — no racing steps update', () async {
      final dao = await _daoWith(
        't-onlyop',
        steps: _steps([(id: 's1', title: 'Draft', done: false)]),
      );

      await dao.applyLocalComplete('t-onlyop');

      final outbox = await dao.readOutbox();
      expect(
        outbox,
        hasLength(1),
        reason: 'the server cascades on its own when it applies `complete`; a '
            'second `update` op would race it',
      );
      expect(outbox.single.op, OutboxOp.complete);
      expect(outbox.single.payload, {'id': 't-onlyop'});
    });

    test('a task with no checklist keeps steps NULL (no empty list written)', () async {
      final dao = await _daoWith('t-nosteps');

      final done = await dao.applyLocalComplete('t-nosteps');

      expect(done!.status, 'done');
      expect(done.steps, isNull);
      final row = await dao.getRow('t-nosteps');
      expect(row!['steps'], isNull, reason: 'must not write "[]"');
    });

    test('an already-fully-ticked checklist is left byte-identical', () async {
      final json = _steps([
        (id: 's1', title: 'Draft', done: true),
        (id: 's2', title: 'Send', done: true),
      ]);
      final dao = await _daoWith('t-alldone', steps: json);

      await dao.applyLocalComplete('t-alldone');

      final row = await dao.getRow('t-alldone');
      expect(row!['steps'], json, reason: 'no churn when nothing changes');
    });
  });
}
