import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';

void main() {
  group('Task.fromJson', () {
    test('parses a complete well-typed response', () {
      final json = {
        'id': 'abc123',
        'user_id': 'user1',
        'title': 'Buy milk',
        'description': 'Full fat',
        'category': 'errands',
        'priority': 'high',
        'status': 'todo',
        'owner': 'user',
        'due_date': '2026-06-10',
        'reminder_at': null,
        'recurring': null,
        'tags': null,
        'nag_count': 0,
        'created_at': '2026-06-05T10:00:00Z',
        'completed_at': null,
        'last_error': null,
        'attempt_count': null,
        'last_attempted_at': null,
        'trace_session_id': null,
        'lazybrain_note_id': null,
        'steps': null,
        'allocated_budget': null,
      };
      final task = Task.fromJson(json);
      expect(task.id, 'abc123');
      expect(task.userId, 'user1');
      expect(task.title, 'Buy milk');
      expect(task.description, 'Full fat');
      expect(task.category, 'errands');
      expect(task.priority, 'high');
      expect(task.status, 'todo');
      expect(task.owner, 'user');
      expect(task.dueDate, '2026-06-10');
      expect(task.nagCount, 0);
      expect(task.isDone, isFalse);
    });

    test('isDone returns true when status is done', () {
      final task = Task.fromJson({
        'id': 't1',
        'user_id': 'u1',
        'title': 'Done task',
        'status': 'done',
        'priority': 'low',
        'owner': 'user',
        'nag_count': 0,
        'created_at': '2026-06-05T00:00:00Z',
      });
      expect(task.isDone, isTrue);
    });

    test('tolerates missing optional fields', () {
      final task = Task.fromJson({
        'id': 't2',
        'title': 'Minimal',
        'created_at': '2026-06-05T00:00:00Z',
      });
      expect(task.id, 't2');
      expect(task.userId, '');
      expect(task.priority, 'medium');
      expect(task.status, 'todo');
      expect(task.owner, 'user');
      expect(task.nagCount, 0);
      expect(task.isDone, isFalse);
    });

    test('tolerates int nag_count sent as string', () {
      final task = Task.fromJson({
        'id': 't3',
        'title': 'Nag test',
        'nag_count': '5',
        'created_at': '2026-06-05T00:00:00Z',
      });
      expect(task.nagCount, 5);
    });

    test('tolerates allocated_budget as int', () {
      final task = Task.fromJson({
        'id': 't4',
        'title': 'Budget test',
        'allocated_budget': 100,
        'nag_count': 0,
        'created_at': '2026-06-05T00:00:00Z',
      });
      expect(task.allocatedBudget, 100.0);
    });

    test('parses all null optional fields without error', () {
      final task = Task.fromJson({
        'id': 't5',
        'user_id': null,
        'title': null,
        'description': null,
        'category': null,
        'priority': null,
        'status': null,
        'owner': null,
        'due_date': null,
        'reminder_at': null,
        'recurring': null,
        'tags': null,
        'nag_count': null,
        'created_at': null,
        'completed_at': null,
        'last_error': null,
        'allocated_budget': null,
      });
      expect(task.id, 't5');
      expect(task.nagCount, 0); // default
    });

    test('copyWith creates a new instance with updated fields', () {
      final original = Task.fromJson({
        'id': 'orig',
        'title': 'Original',
        'status': 'todo',
        'priority': 'low',
        'owner': 'user',
        'nag_count': 0,
        'created_at': '2026-06-05T00:00:00Z',
      });
      final updated = original.copyWith(status: 'done');
      expect(updated.status, 'done');
      expect(updated.title, 'Original'); // unchanged
      expect(original.status, 'todo'); // immutable — original not mutated
    });

    test('equality is id-based', () {
      final a = Task.fromJson({'id': 'same', 'title': 'A', 'nag_count': 0, 'created_at': ''});
      final b = Task.fromJson({'id': 'same', 'title': 'B', 'nag_count': 0, 'created_at': ''});
      expect(a, equals(b));
    });

    test('toJson round-trips title and status', () {
      final task = Task.fromJson({
        'id': 'rt1',
        'title': 'Round trip',
        'status': 'in_progress',
        'priority': 'urgent',
        'owner': 'agent',
        'nag_count': 2,
        'created_at': '2026-06-05T00:00:00Z',
      });
      final json = task.toJson();
      expect(json['title'], 'Round trip');
      expect(json['status'], 'in_progress');
      expect(json['nag_count'], 2);
    });

    // ── recur_until (the recurring series' end) ──────────────────────────────

    test('fromJson reads recur_until and toJson round-trips it', () {
      final task = Task.fromJson({
        'id': 'ru1',
        'title': 'Water plants',
        'recurring': '0 9 * * *',
        'recur_until': '2026-09-30',
        'nag_count': 0,
        'created_at': '2026-06-05T00:00:00Z',
      });
      expect(task.recurUntil, '2026-09-30');
      expect(task.toJson()['recur_until'], '2026-09-30');
    });

    test('recurUntil is null when recur_until is absent or null', () {
      final absent = Task.fromJson({
        'id': 'ru2',
        'title': 'Forever',
        'nag_count': 0,
        'created_at': '2026-06-05T00:00:00Z',
      });
      expect(absent.recurUntil, isNull);
      expect(absent.toJson()['recur_until'], isNull);
    });

    test('copyWith sets recurUntil without mutating the original', () {
      final original = Task.fromJson({
        'id': 'ru3',
        'title': 'Original',
        'recurring': '0 9 * * 1',
        'nag_count': 0,
        'created_at': '2026-06-05T00:00:00Z',
      });
      final updated = original.copyWith(recurUntil: '2027-01-01');
      expect(updated.recurUntil, '2027-01-01');
      expect(original.recurUntil, isNull); // immutable — original not mutated
      // A copyWith that doesn't touch it preserves the value.
      expect(updated.copyWith(title: 'Renamed').recurUntil, '2027-01-01');
    });
  });
}
