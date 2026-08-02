// Local DAO ops for a task's comment thread (v12): applyLocalAddComment /
// applyLocalDeleteComment. Same real in-memory SQLite (FFI) harness as
// task_dao_test.dart — the DAO logic is verified against the actual engine.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/comment.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

/// Isolated in-memory DB per call (mirrors task_dao_test.dart::_freshDao).
Future<TaskDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:commentsmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return TaskDao(db);
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('applyLocalAddComment / applyLocalDeleteComment', () {
    test('applyLocalAddComment appends, dirties, enqueues comment_add',
        () async {
      final dao = await _freshDao();
      final created = await dao.applyLocalCreate('with thread');
      final c = TaskComment(
          id: 'c-t1', ts: '2026-08-02T10:00:00Z', author: 'user', text: 'hi');
      final updated = await dao.applyLocalAddComment(created.id, c);
      expect(updated!.taskComments.map((x) => x.text), ['hi']);
      expect(await dao.dirtyIds(), contains(created.id));
      final ops = await dao.readOutbox();
      final op = ops.lastWhere((o) => o.op == OutboxOp.commentAdd);
      expect(op.entityId, created.id);
      expect(op.payload['comment']['id'], 'c-t1');
    });

    test('applyLocalDeleteComment removes and enqueues comment_delete',
        () async {
      final dao = await _freshDao();
      final created = await dao.applyLocalCreate('t');
      await dao.applyLocalAddComment(
          created.id,
          TaskComment(
              id: 'c-t2',
              ts: '2026-08-02T10:00:00Z',
              author: 'user',
              text: 'bye'));
      final after = await dao.applyLocalDeleteComment(created.id, 'c-t2');
      expect(after!.taskComments, isEmpty);
      final ops = await dao.readOutbox();
      expect(ops.last.op, OutboxOp.commentDelete);
      expect(ops.last.payload['comment_id'], 'c-t2');
    });

    test(
        'deleting the only comment clears the cache column to NULL, not just '
        'the in-memory object', () async {
      final dao = await _freshDao();
      final created = await dao.applyLocalCreate('solo comment');
      await dao.applyLocalAddComment(
          created.id,
          TaskComment(
              id: 'c-solo',
              ts: '2026-08-02T10:00:00Z',
              author: 'user',
              text: 'only one'));

      final after = await dao.applyLocalDeleteComment(created.id, 'c-solo');
      expect(after!.taskComments, isEmpty);
      expect(after.comments, isNull,
          reason: 'the returned Task must carry a cleared (null) comments '
              'field, not a leftover value from copyWith');

      // Re-read from the DB directly — proves the column was actually
      // written as NULL, not merely left unchanged on the returned object
      // (Task.copyWith(comments: null) cannot clear; a bug here would leave
      // the stale JSON sitting in the row while the caller's Task looked
      // clean).
      final reread = await dao.getById(created.id);
      expect(reread!.taskComments, isEmpty);
      expect(reread.comments, isNull,
          reason: 'task_cache.comments must be a real SQL NULL after '
              'deleting the last comment');
    });

    test('add to a missing task returns null and enqueues nothing', () async {
      final dao = await _freshDao();
      expect(
          await dao.applyLocalAddComment(
              'ghost',
              TaskComment(id: 'c-x', ts: '', author: 'user', text: 'x')),
          isNull);
    });
  });
}
