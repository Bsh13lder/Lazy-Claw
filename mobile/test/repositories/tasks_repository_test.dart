import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/models/task.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeTransport implements TasksTransport {
  String? lastPath;
  Map<String, dynamic>? lastBody;
  Map<String, dynamic>? lastQueryParams;
  String? lastMethod;

  /// The response to return from the next call.
  Map<String, dynamic> response;

  _FakeTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    lastMethod = 'GET';
    lastPath = path;
    lastQueryParams = queryParams;
    return response;
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    lastMethod = 'POST';
    lastPath = path;
    lastBody = body;
    return response;
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    lastMethod = 'DELETE';
    lastPath = path;
    return response;
  }
}

// ── Fixtures ───────────────────────────────────────────────────────────────

Map<String, dynamic> _taskJson({
  String id = 't1',
  String title = 'Test task',
  String status = 'todo',
}) =>
    {
      'id': id,
      'user_id': 'u1',
      'title': title,
      'description': null,
      'category': null,
      'priority': 'medium',
      'status': status,
      'owner': 'user',
      'due_date': null,
      'reminder_at': null,
      'recurring': null,
      'tags': null,
      'nag_count': 0,
      'created_at': '2026-06-05T10:00:00Z',
      'completed_at': null,
      'steps': null,
      'allocated_budget': null,
    };

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  group('TasksRepository.listTasks', () {
    test('GET /api/tasks and parses task list', () async {
      final t = _FakeTransport({
        'tasks': [_taskJson(id: 'a1', title: 'Task A'), _taskJson(id: 'a2', title: 'Task B')],
        'count': 2,
      });
      final repo = TasksRepository(t);
      final tasks = await repo.listTasks();
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/tasks');
      expect(tasks, hasLength(2));
      expect(tasks[0].id, 'a1');
      expect(tasks[1].title, 'Task B');
    });

    test('passes owner query param', () async {
      final t = _FakeTransport({'tasks': [], 'count': 0});
      await TasksRepository(t).listTasks(owner: 'user');
      expect(t.lastQueryParams, containsPair('owner', 'user'));
    });

    test('passes status query param', () async {
      final t = _FakeTransport({'tasks': [], 'count': 0});
      await TasksRepository(t).listTasks(status: 'done');
      expect(t.lastQueryParams, containsPair('status', 'done'));
    });

    test('sends no queryParams when all are null', () async {
      final t = _FakeTransport({'tasks': [], 'count': 0});
      await TasksRepository(t).listTasks();
      expect(t.lastQueryParams, isNull);
    });

    test('returns empty list when tasks key is missing', () async {
      final t = _FakeTransport({});
      final tasks = await TasksRepository(t).listTasks();
      expect(tasks, isEmpty);
    });
  });

  group('TasksRepository.createTask', () {
    test('POST /api/tasks with title and returns Task', () async {
      final t = _FakeTransport({'task': _taskJson(id: 'new1', title: 'Buy milk')});
      final repo = TasksRepository(t);
      final task = await repo.createTask('Buy milk');
      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/tasks');
      expect(t.lastBody, containsPair('title', 'Buy milk'));
      expect(task, isA<Task>());
      expect(task.id, 'new1');
    });

    test('includes optional fields when provided', () async {
      final t = _FakeTransport({'task': _taskJson()});
      await TasksRepository(t).createTask(
        'Book dentist',
        priority: 'high',
        dueDate: '2026-06-20',
        category: 'health',
      );
      expect(t.lastBody, containsPair('priority', 'high'));
      expect(t.lastBody, containsPair('due_date', '2026-06-20'));
      expect(t.lastBody, containsPair('category', 'health'));
    });

    test('falls back gracefully when server omits task wrapper', () async {
      // Some endpoints return the task directly (no outer "task" key).
      final t = _FakeTransport(_taskJson(id: 'flat'));
      final task = await TasksRepository(t).createTask('Flat response');
      expect(task.id, 'flat');
    });
  });

  group('TasksRepository.completeTask', () {
    test('POST /api/tasks/{id}/complete', () async {
      final t = _FakeTransport({'status': 'ok', 'id': 't99'});
      await TasksRepository(t).completeTask('t99');
      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/tasks/t99/complete');
    });
  });

  group('TasksRepository.deleteTask', () {
    test('DELETE /api/tasks/{id}', () async {
      final t = _FakeTransport({'status': 'deleted', 'id': 't55'});
      await TasksRepository(t).deleteTask('t55');
      expect(t.lastMethod, 'DELETE');
      expect(t.lastPath, '/api/tasks/t55');
    });
  });
}
