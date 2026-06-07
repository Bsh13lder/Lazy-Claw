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

  group('expenseRangeBounds', () {
    test('week is today + the previous six days (inclusive)', () {
      final now = DateTime(2026, 6, 15, 14, 30); // Mon-ish, mid-month
      final b = expenseRangeBounds(ExpenseRange.week, now: now);
      expect(b.start, DateTime(2026, 6, 9));
      expect(b.end, DateTime(2026, 6, 15));
    });

    test('week underflow rolls into the previous month', () {
      final now = DateTime(2026, 6, 3);
      final b = expenseRangeBounds(ExpenseRange.week, now: now);
      expect(b.start, DateTime(2026, 5, 28));
      expect(b.end, DateTime(2026, 6, 3));
    });

    test('month is the 1st through the last day of the calendar month', () {
      final b = expenseRangeBounds(ExpenseRange.month, now: DateTime(2026, 2, 9));
      expect(b.start, DateTime(2026, 2, 1));
      expect(b.end, DateTime(2026, 2, 28)); // 2026 is not a leap year
    });

    test('custom uses the date portions of the supplied bounds', () {
      final b = expenseRangeBounds(
        ExpenseRange.custom,
        customStart: DateTime(2026, 6, 1, 9, 0),
        customEnd: DateTime(2026, 6, 7, 23, 59),
      );
      expect(b.start, DateTime(2026, 6, 1));
      expect(b.end, DateTime(2026, 6, 7));
    });

    test('custom tolerates a reversed pair by swapping', () {
      final b = expenseRangeBounds(
        ExpenseRange.custom,
        customStart: DateTime(2026, 6, 7),
        customEnd: DateTime(2026, 6, 1),
      );
      expect(b.start, DateTime(2026, 6, 1));
      expect(b.end, DateTime(2026, 6, 7));
    });

    test('custom with a missing bound falls back to the current month', () {
      final now = DateTime(2026, 6, 15);
      final b = expenseRangeBounds(ExpenseRange.custom, now: now);
      expect(b.start, DateTime(2026, 6, 1));
      expect(b.end, DateTime(2026, 6, 30));
    });
  });

  group('filterByRange', () {
    final now = DateTime(2026, 6, 15);
    final june1 = _expense('a', 'p1', 10, spentAt: '2026-06-01');
    final june9 = _expense('b', 'p1', 20, spentAt: '2026-06-09');
    final june15 = _expense('c', 'p1', 30, spentAt: '2026-06-15T08:00:00');
    final may31 = _expense('d', 'p1', 40, spentAt: '2026-05-31');
    final july1 = _expense('e', 'p1', 50, spentAt: '2026-07-01');
    final undated = _expense('f', 'p1', 99); // no spent_at

    final all = [june1, june9, june15, may31, july1, undated];

    test('month keeps only the current calendar month', () {
      final out = filterByRange(all, ExpenseRange.month, now: now);
      expect(out.map((e) => e.id), containsAll(['a', 'b', 'c']));
      expect(out.map((e) => e.id), isNot(contains('d'))); // May
      expect(out.map((e) => e.id), isNot(contains('e'))); // July
      expect(out.map((e) => e.id), isNot(contains('f'))); // undated
    });

    test('week keeps the 7-day window ending today (boundaries inclusive)', () {
      final out = filterByRange(all, ExpenseRange.week, now: now);
      // window: Jun 9 .. Jun 15 inclusive
      expect(out.map((e) => e.id), containsAll(['b', 'c']));
      expect(out.map((e) => e.id), isNot(contains('a'))); // Jun 1 < Jun 9
      expect(out.map((e) => e.id), isNot(contains('d')));
      expect(out.map((e) => e.id), isNot(contains('e')));
    });

    test('week includes both inclusive boundary days', () {
      final start = _expense('s', 'p1', 1, spentAt: '2026-06-09');
      final end = _expense('t', 'p1', 1, spentAt: '2026-06-15');
      final out = filterByRange([start, end], ExpenseRange.week, now: now);
      expect(out.length, 2);
    });

    test('custom filters to the picked window (inclusive)', () {
      final out = filterByRange(
        all,
        ExpenseRange.custom,
        customStart: DateTime(2026, 5, 31),
        customEnd: DateTime(2026, 6, 1),
      );
      expect(out.map((e) => e.id), containsAll(['a', 'd']));
      expect(out.length, 2);
    });

    test('undated rows are always excluded', () {
      expect(filterByRange([undated], ExpenseRange.month, now: now), isEmpty);
      expect(filterByRange([undated], ExpenseRange.week, now: now), isEmpty);
    });

    test('empty input yields empty output', () {
      expect(filterByRange(const [], ExpenseRange.month, now: now), isEmpty);
    });
  });

  group('rangeTotal', () {
    test('sums live amounts and skips void rows', () {
      final list = [
        _expense('a', 'p1', 10),
        _expense('b', 'p1', 5.5),
        _expense('c', 'p1', 999, status: 'void'),
      ];
      expect(rangeTotal(list), closeTo(15.5, 0.001));
      expect(rangeTotal(const []), 0);
    });
  });

  group('expenseRangeLabel', () {
    test('week and month produce readable labels', () {
      expect(expenseRangeLabel(ExpenseRange.week), 'Last 7 days');
      expect(expenseRangeLabel(ExpenseRange.month, now: DateTime(2026, 6, 1)),
          'June');
    });

    test('custom shows the date span, with year only when it differs', () {
      expect(
        expenseRangeLabel(
          ExpenseRange.custom,
          now: DateTime(2026, 6, 15),
          customStart: DateTime(2026, 6, 1),
          customEnd: DateTime(2026, 6, 7),
        ),
        'Jun 1 – Jun 7',
      );
      expect(
        expenseRangeLabel(
          ExpenseRange.custom,
          now: DateTime(2026, 6, 15),
          customStart: DateTime(2025, 12, 30),
          customEnd: DateTime(2026, 1, 2),
        ),
        'Dec 30, 2025 – Jan 2',
      );
    });

    test('custom with a missing bound falls back to the month name', () {
      expect(
        expenseRangeLabel(ExpenseRange.custom, now: DateTime(2026, 6, 1)),
        'June',
      );
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
