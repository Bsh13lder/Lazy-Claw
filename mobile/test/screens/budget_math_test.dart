import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/expense.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/screens/expenses/budget_math.dart';
import 'package:lazyclaw_mobile/ui/tokens/colors.dart';

Project _project(
  String id, {
  double budget = 0,
  String currency = 'USD',
}) =>
    Project.fromJson(
        {'id': id, 'name': id, 'budget': budget, 'currency': currency});

Expense _expense(
  String id,
  String projectId,
  double amount, {
  String currency = 'USD',
  String status = 'posted',
  String? spentAt,
}) =>
    Expense.fromJson({
      'id': id,
      'project_id': projectId,
      'amount': amount,
      'currency': currency,
      'status': status,
      'spent_at': spentAt,
    });

void main() {
  group('spentForProject', () {
    test('sums only matching, non-void expenses', () {
      final expenses = [
        _expense('e1', 'p1', 10),
        _expense('e2', 'p1', 5.5),
        _expense('e3', 'p2', 100), // other project
        _expense('e4', 'p1', 99, status: 'void'), // void excluded
      ];
      expect(spentForProject('p1', expenses), closeTo(15.5, 0.001));
      expect(spentForProject('p2', expenses), 100);
      expect(spentForProject('missing', expenses), 0);
    });
  });

  group('remainingForProject', () {
    test('is budget minus spent and can go negative', () {
      final p = _project('p1', budget: 100);
      expect(remainingForProject(p, 30), 70);
      expect(remainingForProject(p, 130), -30);
    });
  });

  group('BudgetTotals.from', () {
    test('empty inputs yield the empty totals', () {
      final t = BudgetTotals.from(const [], const []);
      expect(t.totalSpent, 0);
      expect(t.totalBudget, 0);
      expect(t.monthSpent, 0);
      expect(t.hasBudget, isFalse);
      expect(t.multiCurrency, isFalse);
    });

    test('single currency: totals + remaining + fraction', () {
      final projects = [_project('p1', budget: 200), _project('p2', budget: 100)];
      final expenses = [
        _expense('e1', 'p1', 50),
        _expense('e2', 'p2', 70),
      ];
      final t = BudgetTotals.from(projects, expenses);
      expect(t.currency, 'USD');
      expect(t.totalBudget, 300);
      expect(t.totalSpent, 120);
      expect(t.remaining, 180);
      expect(t.fraction, closeTo(0.4, 0.001));
      expect(t.percentUsed, 40);
      expect(t.overBudget, isFalse);
      expect(t.multiCurrency, isFalse);
    });

    test('over budget is detected', () {
      final projects = [_project('p1', budget: 100)];
      final expenses = [_expense('e1', 'p1', 150)];
      final t = BudgetTotals.from(projects, expenses);
      expect(t.overBudget, isTrue);
      expect(t.fraction, 1.0); // clamped
      expect(t.remaining, -50);
    });

    test('void expenses are excluded from totals', () {
      final projects = [_project('p1', budget: 100)];
      final expenses = [
        _expense('e1', 'p1', 30),
        _expense('e2', 'p1', 999, status: 'void'),
      ];
      final t = BudgetTotals.from(projects, expenses);
      expect(t.totalSpent, 30);
    });

    test('orphan expense (no live project) still counts toward total spend', () {
      final projects = [_project('p1', budget: 100)];
      final expenses = [
        _expense('e1', 'p1', 40),
        _expense('e2', 'ghost', 25), // project not in list
      ];
      final t = BudgetTotals.from(projects, expenses);
      expect(t.totalSpent, 65);
      expect(t.totalBudget, 100);
    });

    test('mixed currency: headline scoped to dominant, others disclosed', () {
      // Two EUR projects, one USD project → EUR dominates.
      final projects = [
        _project('p1', budget: 100, currency: 'EUR'),
        _project('p2', budget: 50, currency: 'EUR'),
        _project('p3', budget: 999, currency: 'USD'),
      ];
      final expenses = [
        _expense('e1', 'p1', 30, currency: 'EUR'),
        _expense('e2', 'p3', 500, currency: 'USD'),
      ];
      final t = BudgetTotals.from(projects, expenses);
      expect(t.currency, 'EUR');
      expect(t.totalBudget, 150); // only EUR projects
      expect(t.totalSpent, 30); // only EUR expenses (USD not summed in)
      expect(t.multiCurrency, isTrue);
      expect(t.otherCurrencyCount, 1);
    });

    test('monthSpent counts only the current calendar month', () {
      final now = DateTime(2026, 6, 15);
      final projects = [_project('p1', budget: 1000)];
      final expenses = [
        _expense('e1', 'p1', 40, spentAt: '2026-06-02'),
        _expense('e2', 'p1', 10, spentAt: '2026-06-30T23:00:00Z'),
        _expense('e3', 'p1', 99, spentAt: '2026-05-31'), // previous month
        _expense('e4', 'p1', 7), // no date → not counted in month
      ];
      final t = BudgetTotals.from(projects, expenses, now: now);
      expect(t.totalSpent, 156); // all live expenses
      expect(t.monthSpent, 50); // only June rows
    });
  });

  group('AppColors.trafficLight buckets (the bar colors)', () {
    test('<70% is success, 70-90% is warn, >=90% is error', () {
      expect(AppColors.trafficLight(0.0), AppColors.success);
      expect(AppColors.trafficLight(0.69), AppColors.success);
      expect(AppColors.trafficLight(0.70), AppColors.warn);
      expect(AppColors.trafficLight(0.89), AppColors.warn);
      expect(AppColors.trafficLight(0.90), AppColors.error);
      expect(AppColors.trafficLight(1.0), AppColors.error);
    });
  });

  group('Project.spentFraction', () {
    test('derives clamped fraction from spent/budget', () {
      expect(_projectWithSpent(budget: 200, spent: 50).spentFraction,
          closeTo(0.25, 0.001));
      expect(_projectWithSpent(budget: 100, spent: 250).spentFraction, 1.0);
      expect(_projectWithSpent(budget: 0, spent: 50).spentFraction, 0.0);
    });
  });
}

Project _projectWithSpent({required double budget, required double spent}) =>
    Project.fromJson(
        {'id': 'p', 'name': 'p', 'budget': budget, 'spent': spent});
