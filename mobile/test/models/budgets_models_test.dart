import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/expense.dart';

void main() {
  // ── Project.fromJson ──────────────────────────────────────────────────────

  group('Project.fromJson', () {
    test('parses a complete well-typed response', () {
      final json = {
        'id': 'proj1',
        'name': 'Marketing',
        'name_key': 'marketing',
        'budget': 1000.0,
        'currency': 'USD',
        'status': 'active',
        'description': 'Marketing budget',
        'lazybrain_note_id': 'note1',
        'spent': 350.0,
        'remaining': 650.0,
      };
      final p = Project.fromJson(json);
      expect(p.id, 'proj1');
      expect(p.name, 'Marketing');
      expect(p.nameKey, 'marketing');
      expect(p.budget, 1000.0);
      expect(p.currency, 'USD');
      expect(p.status, 'active');
      expect(p.description, 'Marketing budget');
      expect(p.lazybrainNoteId, 'note1');
      expect(p.spent, 350.0);
      expect(p.remaining, 650.0);
    });

    test('tolerates missing optional fields', () {
      final p = Project.fromJson({'id': 'p2', 'name': 'Minimal'});
      expect(p.id, 'p2');
      expect(p.name, 'Minimal');
      expect(p.budget, 0.0);
      expect(p.currency, 'USD');
      expect(p.status, 'active');
      expect(p.description, isNull);
      expect(p.spent, isNull);
      expect(p.remaining, isNull);
    });

    test('tolerates int budget sent from server', () {
      final p = Project.fromJson({'id': 'p3', 'name': 'IntBudget', 'budget': 500});
      expect(p.budget, 500.0);
    });

    test('tolerates budget sent as string', () {
      final p = Project.fromJson({'id': 'p4', 'name': 'StrBudget', 'budget': '750.5'});
      expect(p.budget, 750.5);
    });

    test('tolerates all nulls without throwing', () {
      final p = Project.fromJson({
        'id': null,
        'name': null,
        'budget': null,
        'currency': null,
        'status': null,
        'description': null,
        'spent': null,
        'remaining': null,
      });
      expect(p.id, '');
      expect(p.name, '');
      expect(p.budget, 0.0);
      expect(p.currency, 'USD');
      expect(p.status, 'active');
    });

    test('spentFraction returns 0 when budget is 0', () {
      final p = Project.fromJson({'id': 'p5', 'name': 'Zero', 'budget': 0, 'spent': 100.0});
      expect(p.spentFraction, 0.0);
    });

    test('spentFraction is capped at 1.0 when over budget', () {
      final p = Project.fromJson({'id': 'p6', 'name': 'Over', 'budget': 100, 'spent': 200.0});
      expect(p.spentFraction, 1.0);
    });

    test('spentFraction returns correct ratio', () {
      final p = Project.fromJson({'id': 'p7', 'name': 'Half', 'budget': 200, 'spent': 100.0});
      expect(p.spentFraction, closeTo(0.5, 0.001));
    });

    test('spentFraction returns 0 when spent is null', () {
      final p = Project.fromJson({'id': 'p8', 'name': 'NoSpent', 'budget': 200});
      expect(p.spentFraction, 0.0);
    });

    test('isArchived returns true for archived status', () {
      final p = Project.fromJson({'id': 'p9', 'name': 'Old', 'status': 'archived'});
      expect(p.isArchived, isTrue);
    });

    test('isArchived returns false for active', () {
      final p = Project.fromJson({'id': 'p10', 'name': 'New', 'status': 'active'});
      expect(p.isArchived, isFalse);
    });

    test('copyWith creates new instance with updated fields', () {
      final original = Project.fromJson({'id': 'orig', 'name': 'Original', 'budget': 100.0});
      final updated = original.copyWith(name: 'Updated', budget: 200.0);
      expect(updated.name, 'Updated');
      expect(updated.budget, 200.0);
      expect(updated.id, 'orig'); // unchanged
      expect(original.name, 'Original'); // immutable
    });

    test('equality is id-based', () {
      final a = Project.fromJson({'id': 'same', 'name': 'A'});
      final b = Project.fromJson({'id': 'same', 'name': 'B'});
      expect(a, equals(b));
    });

    test('toJson round-trips name and budget', () {
      final p = Project.fromJson({
        'id': 'rt1',
        'name': 'Round trip',
        'budget': 500.0,
        'currency': 'EUR',
        'status': 'active',
      });
      final json = p.toJson();
      expect(json['id'], 'rt1');
      expect(json['name'], 'Round trip');
      expect(json['budget'], 500.0);
      expect(json['currency'], 'EUR');
    });
  });

  // ── Expense.fromJson ──────────────────────────────────────────────────────

  group('Expense.fromJson', () {
    test('parses a complete well-typed response', () {
      final json = {
        'id': 'exp1',
        'project_id': 'proj1',
        'task_id': null,
        'amount': 99.99,
        'currency': 'USD',
        'description': 'Office supplies',
        'vendor': 'Amazon',
        'notes': 'Bulk order',
        'spent_at': '2026-06-01T10:00:00Z',
        'status': 'posted',
        'recurring_expense_id': null,
        'lazybrain_note_id': null,
        'project_name': 'Marketing',
      };
      final e = Expense.fromJson(json);
      expect(e.id, 'exp1');
      expect(e.projectId, 'proj1');
      expect(e.taskId, isNull);
      expect(e.amount, 99.99);
      expect(e.currency, 'USD');
      expect(e.description, 'Office supplies');
      expect(e.vendor, 'Amazon');
      expect(e.notes, 'Bulk order');
      expect(e.spentAt, '2026-06-01T10:00:00Z');
      expect(e.status, 'posted');
      expect(e.projectName, 'Marketing');
    });

    test('tolerates missing optional fields', () {
      final e = Expense.fromJson({'id': 'e2', 'project_id': 'p1', 'amount': 10.0});
      expect(e.id, 'e2');
      expect(e.projectId, 'p1');
      expect(e.amount, 10.0);
      expect(e.currency, 'USD');
      expect(e.status, 'posted');
      expect(e.vendor, isNull);
      expect(e.description, isNull);
    });

    test('tolerates int amount from server', () {
      final e = Expense.fromJson({'id': 'e3', 'project_id': 'p1', 'amount': 50});
      expect(e.amount, 50.0);
    });

    test('tolerates amount as string', () {
      final e = Expense.fromJson({'id': 'e4', 'project_id': 'p1', 'amount': '25.5'});
      expect(e.amount, 25.5);
    });

    test('tolerates all nulls without throwing', () {
      final e = Expense.fromJson({
        'id': null,
        'project_id': null,
        'amount': null,
        'currency': null,
        'description': null,
        'vendor': null,
        'status': null,
      });
      expect(e.id, '');
      expect(e.projectId, '');
      expect(e.amount, 0.0);
      expect(e.currency, 'USD');
      expect(e.status, 'posted');
    });

    test('isVoid returns true for void status', () {
      final e = Expense.fromJson(
          {'id': 'e5', 'project_id': 'p1', 'amount': 10.0, 'status': 'void'});
      expect(e.isVoid, isTrue);
    });

    test('isVoid returns false for posted', () {
      final e = Expense.fromJson(
          {'id': 'e6', 'project_id': 'p1', 'amount': 10.0, 'status': 'posted'});
      expect(e.isVoid, isFalse);
    });

    test('displayDescription returns vendor when description is null', () {
      final e = Expense.fromJson(
          {'id': 'e7', 'project_id': 'p1', 'amount': 10.0, 'vendor': 'Acme'});
      expect(e.displayDescription, 'Acme');
    });

    test('displayDescription returns description when present', () {
      final e = Expense.fromJson({
        'id': 'e8',
        'project_id': 'p1',
        'amount': 10.0,
        'description': 'My desc',
        'vendor': 'Vendor',
      });
      expect(e.displayDescription, 'My desc');
    });

    test('displayDescription returns empty string when both null', () {
      final e = Expense.fromJson(
          {'id': 'e9', 'project_id': 'p1', 'amount': 10.0});
      expect(e.displayDescription, '');
    });

    test('equality is id-based', () {
      final a = Expense.fromJson(
          {'id': 'same', 'project_id': 'p1', 'amount': 10.0});
      final b = Expense.fromJson(
          {'id': 'same', 'project_id': 'p2', 'amount': 99.0});
      expect(a, equals(b));
    });

    test('toJson round-trips amount and description', () {
      final e = Expense.fromJson({
        'id': 'rt1',
        'project_id': 'proj1',
        'amount': 42.5,
        'description': 'Round trip',
        'currency': 'EUR',
        'status': 'posted',
      });
      final json = e.toJson();
      expect(json['id'], 'rt1');
      expect(json['amount'], 42.5);
      expect(json['description'], 'Round trip');
      expect(json['currency'], 'EUR');
    });
  });
}
