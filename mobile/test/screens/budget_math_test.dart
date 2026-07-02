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
  bool isFavorite = false,
}) =>
    Expense.fromJson({
      'id': id,
      'project_id': projectId,
      'amount': amount,
      'currency': currency,
      'status': status,
      'spent_at': spentAt,
      'is_favorite': isFavorite,
    });

Project _fav(String id, {double budget = 0, String currency = 'USD'}) =>
    Project.fromJson({
      'id': id,
      'name': id,
      'budget': budget,
      'currency': currency,
      'is_favorite': true,
    });

void main() {
  group('heroTotals (favorites-scoped headline)', () {
    test('scopes the hero to favorite projects when any project is starred', () {
      // Mirrors the reported screenshot: Personal (★) 3360, ClubBay (☆) 2032.
      final projects = [
        _fav('personal', budget: 0), // starred, no budget
        _project('clubbay', budget: 2800), // not starred
      ];
      final expenses = [
        _expense('e1', 'personal', 3360),
        _expense('e2', 'clubbay', 2032),
      ];
      final totals = heroTotals(projects, expenses);
      // Only the favorite (personal) — NOT the 5392 grand total.
      expect(totals.totalSpent, 3360);
      expect(totals.totalBudget, 0); // personal has no budget
    });

    test('sums MULTIPLE favorites (and only favorites)', () {
      final projects = [
        _fav('a', budget: 100),
        _fav('b', budget: 200),
        _project('c', budget: 999), // not starred → excluded
      ];
      final expenses = [
        _expense('e1', 'a', 10),
        _expense('e2', 'b', 20),
        _expense('e3', 'c', 500), // excluded
      ];
      final totals = heroTotals(projects, expenses);
      expect(totals.totalSpent, 30);
      expect(totals.totalBudget, 300);
    });

    test('falls back to ALL projects when nothing is starred', () {
      final projects = [_project('a', budget: 100), _project('b', budget: 50)];
      final expenses = [_expense('e1', 'a', 30), _expense('e2', 'b', 20)];
      final totals = heroTotals(projects, expenses);
      expect(totals.totalSpent, 50);
      expect(totals.totalBudget, 150);
    });

    test('hasFavoriteProjects reflects any non-archived starred project', () {
      expect(hasFavoriteProjects([_project('a')]), isFalse);
      expect(hasFavoriteProjects([_fav('a'), _project('b')]), isTrue);
    });
  });

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
    test('today is a single day (start == end == the calendar date)', () {
      final b = expenseRangeBounds(
        ExpenseRange.today,
        now: DateTime(2026, 6, 15, 14, 30),
      );
      expect(b.start, DateTime(2026, 6, 15));
      expect(b.end, DateTime(2026, 6, 15));
    });

    test('all is a wide-open window (1970 → ref year + 100)', () {
      final b = expenseRangeBounds(ExpenseRange.all, now: DateTime(2026, 6, 15));
      expect(b.start, DateTime(1970, 1, 1));
      expect(b.end, DateTime(2126, 12, 31));
    });

    test('month honors a positive/negative monthOffset', () {
      final now = DateTime(2026, 6, 15);
      final prev = expenseRangeBounds(ExpenseRange.month, now: now, monthOffset: -1);
      expect(prev.start, DateTime(2026, 5, 1));
      expect(prev.end, DateTime(2026, 5, 31));
      final next = expenseRangeBounds(ExpenseRange.month, now: now, monthOffset: 1);
      expect(next.start, DateTime(2026, 7, 1));
      expect(next.end, DateTime(2026, 7, 31));
    });

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

  group('monthBounds', () {
    test('offset 0 is the reference month, 1st → last day', () {
      final b = monthBounds(DateTime(2026, 6, 15));
      expect(b.start, DateTime(2026, 6, 1));
      expect(b.end, DateTime(2026, 6, 30));
    });

    test('negative offset steps to the previous month', () {
      final b = monthBounds(DateTime(2026, 6, 15), monthOffset: -1);
      expect(b.start, DateTime(2026, 5, 1));
      expect(b.end, DateTime(2026, 5, 31));
    });

    test('positive offset steps to the next month', () {
      final b = monthBounds(DateTime(2026, 6, 15), monthOffset: 1);
      expect(b.start, DateTime(2026, 7, 1));
      expect(b.end, DateTime(2026, 7, 31));
    });

    test('stepping back across the year boundary rolls into December', () {
      final b = monthBounds(DateTime(2026, 1, 10), monthOffset: -1);
      expect(b.start, DateTime(2025, 12, 1));
      expect(b.end, DateTime(2025, 12, 31));
    });

    test('stepping forward across the year boundary rolls into January', () {
      final b = monthBounds(DateTime(2026, 12, 10), monthOffset: 1);
      expect(b.start, DateTime(2027, 1, 1));
      expect(b.end, DateTime(2027, 1, 31));
    });

    test('leap February resolves to the 29th', () {
      final direct = monthBounds(DateTime(2024, 2, 10));
      expect(direct.start, DateTime(2024, 2, 1));
      expect(direct.end, DateTime(2024, 2, 29));
      // And the same via an offset from January.
      final viaOffset = monthBounds(DateTime(2024, 1, 10), monthOffset: 1);
      expect(viaOffset.end, DateTime(2024, 2, 29));
    });

    test('non-leap February resolves to the 28th', () {
      final b = monthBounds(DateTime(2026, 2, 10));
      expect(b.end, DateTime(2026, 2, 28));
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

    test('today keeps only rows dated on the current day', () {
      final out = filterByRange(all, ExpenseRange.today, now: now);
      expect(out.map((e) => e.id), ['c']); // only Jun 15
    });

    test('all keeps every dated row (past + future), dropping only undated', () {
      final out = filterByRange(all, ExpenseRange.all, now: now);
      expect(out.map((e) => e.id), containsAll(['a', 'b', 'c', 'd', 'e']));
      expect(out.map((e) => e.id), isNot(contains('f'))); // undated still out
      expect(out.length, 5);
    });

    test('month + monthOffset selects the shifted calendar month', () {
      // Step back one month → May 2026 → only the May 31 row matches.
      final out = filterByRange(
        all,
        ExpenseRange.month,
        now: now,
        monthOffset: -1,
      );
      expect(out.map((e) => e.id), ['d']);
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

    test('today and all have fixed labels', () {
      expect(expenseRangeLabel(ExpenseRange.today), 'Today');
      expect(expenseRangeLabel(ExpenseRange.all), 'All time');
    });

    test('month label shifts with monthOffset (year omitted in ref year)', () {
      final now = DateTime(2026, 6, 15);
      expect(expenseRangeLabel(ExpenseRange.month, now: now), 'June');
      expect(expenseRangeLabel(ExpenseRange.month, now: now, monthOffset: -1),
          'May');
      // Forward within the same year stays year-less.
      expect(expenseRangeLabel(ExpenseRange.month, now: now, monthOffset: 6),
          'December');
    });

    test('month label adds the year when the step crosses the year boundary',
        () {
      final now = DateTime(2026, 6, 15);
      expect(expenseRangeLabel(ExpenseRange.month, now: now, monthOffset: -6),
          'December 2025');
      expect(
        expenseRangeLabel(ExpenseRange.month,
            now: DateTime(2026, 1, 10), monthOffset: -1),
        'December 2025',
      );
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

  group('starredExpenseTotal (Money "★ Starred only" subtotal)', () {
    test('sums only favorited, non-void expenses', () {
      final expenses = [
        _expense('e1', 'p1', 10, isFavorite: true),
        _expense('e2', 'p1', 5.5, isFavorite: true),
        _expense('e3', 'p1', 100), // not starred → excluded
        _expense('e4', 'p1', 99, isFavorite: true, status: 'void'), // void out
      ];
      expect(starredExpenseTotal(expenses), closeTo(15.5, 0.001));
    });

    test('is 0 when nothing is starred', () {
      final expenses = [
        _expense('e1', 'p1', 10),
        _expense('e2', 'p1', 5),
      ];
      expect(starredExpenseTotal(expenses), 0.0);
    });

    test('is 0 for an empty list', () {
      expect(starredExpenseTotal(const <Expense>[]), 0.0);
    });

    test('scopes to a single currency when one is given', () {
      final expenses = [
        _expense('e1', 'p1', 10, isFavorite: true, currency: 'USD'),
        _expense('e2', 'p1', 20, isFavorite: true, currency: 'EUR'),
        _expense('e3', 'p1', 5, isFavorite: true, currency: 'USD'),
      ];
      expect(starredExpenseTotal(expenses, currency: 'USD'), closeTo(15, 0.001));
      expect(starredExpenseTotal(expenses, currency: 'EUR'), closeTo(20, 0.001));
      // No currency filter → sums every starred row regardless of currency.
      expect(starredExpenseTotal(expenses), closeTo(35, 0.001));
    });
  });
}

Project _projectWithSpent({required double budget, required double spent}) =>
    Project.fromJson(
        {'id': 'p', 'name': 'p', 'budget': budget, 'spent': spent});
