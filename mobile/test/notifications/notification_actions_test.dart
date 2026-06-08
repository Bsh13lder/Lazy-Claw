import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/notifications/notification_actions.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

/// Real in-memory SQLite (ffi) with the production schema — the completion path
/// is verified against the actual engine, not a hand-rolled fake DB.
Future<TaskDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:notifmemdb${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return TaskDao(db);
}

NotificationResponse _response({String? actionId, String? payload}) =>
    NotificationResponse(
      notificationResponseType:
          NotificationResponseType.selectedNotificationAction,
      actionId: actionId,
      payload: payload,
    );

void main() {
  setUpAll(() {
    sqfliteFfiInit();
  });

  group('taskIdFromPayload (pure)', () {
    test('extracts the id from a task:<id> payload', () {
      expect(taskIdFromPayload('task:abc-123'), 'abc-123');
    });

    test('trims surrounding whitespace on both the payload and the id', () {
      expect(taskIdFromPayload('  task:  abc-123  '), 'abc-123');
    });

    test('returns null for a non-task payload (e.g. a server "chat" payload)',
        () {
      expect(taskIdFromPayload('chat'), isNull);
      expect(taskIdFromPayload('notification:42'), isNull);
    });

    test('returns null for a null payload', () {
      expect(taskIdFromPayload(null), isNull);
    });

    test('returns null for an empty / prefix-only / blank-id payload', () {
      expect(taskIdFromPayload(''), isNull);
      expect(taskIdFromPayload('task:'), isNull);
      expect(taskIdFromPayload('task:   '), isNull);
    });

    test('keeps an id that itself contains a colon (only the first split)', () {
      // A UUID never contains a colon, but be robust: only the leading
      // `task:` prefix is stripped; the remainder is the id verbatim.
      expect(taskIdFromPayload('task:a:b'), 'a:b');
    });

    test('does NOT match a payload that merely contains "task:" mid-string', () {
      expect(taskIdFromPayload('not-a-task:123'), isNull);
    });
  });

  group('isTaskDoneAction (pure)', () {
    test('true only for the task_done actionId', () {
      expect(isTaskDoneAction(_response(actionId: kTaskDoneActionId)), isTrue);
    });

    test('false for a different / absent actionId (a plain body tap)', () {
      expect(isTaskDoneAction(_response(actionId: 'something_else')), isFalse);
      expect(isTaskDoneAction(_response(actionId: null)), isFalse);
    });

    test('the action id constant is the stable wire value "task_done"', () {
      // The native AndroidNotificationAction id and this constant must match,
      // so pin the literal — a rename would silently break background taps.
      expect(kTaskDoneActionId, 'task_done');
    });
  });

  // The full completeTaskFromPayload() opens the encrypted on-device DB via
  // platform channels (path_provider + secure_storage), which aren't available
  // in a plain unit test. Here we verify the COMPLETION MUTATION it delegates to
  // — TaskDao.applyLocalComplete, fed the parsed id — produces exactly the state
  // the in-app complete does: status=done, completed_at set, dirty, and a
  // `complete` op on the OUTBOX so it syncs.
  group('completion mutation (parser + DAO, the killed-app path)', () {
    test('marks the task done and enqueues a complete op', () async {
      final dao = await _freshDao();
      final task = await dao.applyLocalCreate('Water the plants');
      // Clear the create from the outbox so we assert only on the complete.
      final created = await dao.readOutbox();
      for (final item in created) {
        await dao.deleteOutboxItem(item.seq);
      }

      final id = taskIdFromPayload('task:${task.id}');
      expect(id, task.id);

      final completed = await dao.applyLocalComplete(id!);
      expect(completed, isNotNull);
      expect(completed!.status, 'done');
      expect(completed.completedAt, isNotNull);

      final stored = await dao.getById(task.id);
      expect(stored!.status, 'done');
      expect(stored.isDone, isTrue);

      // It must be dirty (un-pushed) and queued so the next sync pushes it.
      expect(await dao.dirtyIds(), contains(task.id));
      final outbox = await dao.readOutbox();
      expect(outbox, hasLength(1));
      expect(outbox.first.op, OutboxOp.complete);
      expect(outbox.first.entityId, task.id);
      expect(outbox.first.payload['id'], task.id);
    });

    test('a payload for a non-existent task is a safe no-op (no mutation)',
        () async {
      final dao = await _freshDao();
      final id = taskIdFromPayload('task:ghost-id');
      final result = await dao.applyLocalComplete(id!);
      expect(result, isNull);
      expect(await dao.readOutbox(), isEmpty);
    });

    test('a non-task payload yields no id, so nothing is completed', () async {
      final dao = await _freshDao();
      final task = await dao.applyLocalCreate('Stay open');
      // A 'chat' (server-notification) payload parses to null → no completion.
      expect(taskIdFromPayload('chat'), isNull);
      final stored = await dao.getById(task.id);
      expect(stored!.status, 'todo'); // untouched
    });
  });
}
