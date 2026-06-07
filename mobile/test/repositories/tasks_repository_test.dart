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
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    lastMethod = 'PATCH';
    lastPath = path;
    lastBody = body;
    return response;
  }

  @override
  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    lastMethod = 'PUT';
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

  group('TasksRepository.createTask with client id', () {
    test('sends the client id in the body for idempotent replay', () async {
      final t = _FakeTransport({'task': _taskJson(id: 'cid')});
      await TasksRepository(t).createTask('Idempotent', id: 'cid');
      expect(t.lastBody, containsPair('id', 'cid'));
    });
  });

  group('TasksRepository.updateTask', () {
    test('PATCH /api/tasks/{id} with the patch body', () async {
      final t = _FakeTransport({'task': _taskJson(id: 'u1')});
      await TasksRepository(t).updateTask('u1', {'title': 'New', 'status': 'todo'});
      expect(t.lastMethod, 'PATCH');
      expect(t.lastPath, '/api/tasks/u1');
      expect(t.lastBody, containsPair('title', 'New'));
      expect(t.lastBody, containsPair('status', 'todo'));
    });
  });

  group('TasksRepository.setSteps', () {
    test('PUT /api/tasks/{id}/steps with the {steps:[...]} body', () async {
      final t = _FakeTransport({'steps': []});
      await TasksRepository(t).setSteps('u1', [
        {'id': 's-1', 'title': 'Sub one', 'done': false},
        {'id': 's-2', 'title': 'Sub two', 'done': true},
      ]);
      expect(t.lastMethod, 'PUT');
      expect(t.lastPath, '/api/tasks/u1/steps');
      final steps = (t.lastBody!['steps'] as List).cast<Map>();
      expect(steps, hasLength(2));
      expect(steps[0]['title'], 'Sub one');
      expect(steps[1]['done'], true);
    });
  });

  group('TasksRepository.fetchChanges', () {
    test('GET /api/tasks/changes and maps tasks/deleted/now', () async {
      final t = _FakeTransport({
        'tasks': [
          {..._taskJson(id: 'c1', title: 'Changed'), 'updated_at': '2026-06-05T11:00:00Z'},
        ],
        'deleted': ['gone1', 'gone2'],
        'now': '2026-06-05T12:00:00Z',
      });
      final changes = await TasksRepository(t).fetchChanges(
          since: '2026-06-05T10:00:00Z');
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/tasks/changes');
      expect(t.lastQueryParams, containsPair('since', '2026-06-05T10:00:00Z'));
      expect(changes.tasks, hasLength(1));
      expect(changes.tasks.first.task.id, 'c1');
      expect(changes.tasks.first.updatedAt, '2026-06-05T11:00:00Z');
      expect(changes.deleted, ['gone1', 'gone2']);
      expect(changes.now, '2026-06-05T12:00:00Z');
    });

    test('omits ?since when cursor is null (full snapshot)', () async {
      final t = _FakeTransport({'tasks': [], 'deleted': [], 'now': 'x'});
      await TasksRepository(t).fetchChanges();
      expect(t.lastQueryParams, isNull);
    });

    test('tolerates missing deleted/now keys', () async {
      final t = _FakeTransport({'tasks': []});
      final changes = await TasksRepository(t).fetchChanges();
      expect(changes.deleted, isEmpty);
      expect(changes.now, '');
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
