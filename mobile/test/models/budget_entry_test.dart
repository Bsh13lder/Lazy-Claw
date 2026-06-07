import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/budget_entry.dart';

void main() {
  group('BudgetEntry.fromJson', () {
    test('parses a full credit row', () {
      final e = BudgetEntry.fromJson({
        'id': 'be1',
        'project_id': 'proj1',
        'amount': 200.0,
        'currency': 'EUR',
        'source': 'client deposit',
        'kind': 'credit',
        'created_at': '2026-06-05T10:00:00Z',
      });
      expect(e.id, 'be1');
      expect(e.projectId, 'proj1');
      expect(e.amount, 200.0);
      expect(e.currency, 'EUR');
      expect(e.source, 'client deposit');
      expect(e.kind, 'credit');
      expect(e.isEdit, isFalse);
      expect(e.date, '2026-06-05');
    });

    test('flags edit rows and tolerates negative amounts', () {
      final e = BudgetEntry.fromJson({
        'id': 'be2',
        'project_id': 'proj1',
        'amount': -50,
        'kind': 'edit',
      });
      expect(e.isEdit, isTrue);
      expect(e.amount, -50.0);
    });

    test('coerces int amount to double', () {
      final e = BudgetEntry.fromJson({'id': 'x', 'amount': 75});
      expect(e.amount, isA<double>());
      expect(e.amount, 75.0);
    });

    test('defaults missing fields safely', () {
      final e = BudgetEntry.fromJson({});
      expect(e.id, '');
      expect(e.projectId, '');
      expect(e.amount, 0.0);
      expect(e.currency, 'USD');
      expect(e.source, isNull);
      expect(e.kind, 'credit');
      expect(e.date, '');
    });

    test('date is empty for a malformed timestamp', () {
      final e = BudgetEntry.fromJson({'id': 'x', 'created_at': 'nope'});
      expect(e.date, '');
    });

    test('equality is by id', () {
      final a = BudgetEntry.fromJson({'id': 'same', 'amount': 1});
      final b = BudgetEntry.fromJson({'id': 'same', 'amount': 999});
      expect(a, equals(b));
    });
  });
}
