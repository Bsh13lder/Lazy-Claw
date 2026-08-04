// Pure money math behind the task detail sheet's BUDGET control.
//
// No widgets, no providers, no DB — every rule here is asserted directly
// (the widget tests in task_detail_budget_test.dart only prove the wiring).
//
// The summary/overspent groups MOVED here verbatim from
// task_detail_budget_test.dart when the helpers were extracted into
// task_budget_math.dart; they are the same assertions, not new ones.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_budget_math.dart';

void main() {
  group('taskBudgetSummaryLabel', () {
    test('nothing allocated and nothing spent reads as an invitation', () {
      expect(taskBudgetSummaryLabel(null, 0, 'USD'), 'No budget yet');
    });

    test('spend with no allocation shows only the spend', () {
      expect(taskBudgetSummaryLabel(null, 12.5, 'EUR'), 'Spent €12.50');
    });

    test('both present read side by side', () {
      expect(
        taskBudgetSummaryLabel(250, 40, 'USD'),
        'Allocated \$250 · Spent \$40',
      );
    });

    test('an allocation with nothing spent still shows a zero spend', () {
      expect(
        taskBudgetSummaryLabel(250, 0, 'USD'),
        'Allocated \$250 · Spent \$0',
      );
    });
  });

  group('taskBudgetOverspent', () {
    test('is false without an allocation, however much was spent', () {
      expect(taskBudgetOverspent(null, 900), isFalse);
    });

    test('is false at exactly the allocation', () {
      expect(taskBudgetOverspent(100, 100), isFalse);
    });

    test('is true past the allocation', () {
      expect(taskBudgetOverspent(100, 100.01), isTrue);
    });
  });

  group('formatTaskAllocation', () {
    test('a whole number drops the trailing .0', () {
      expect(formatTaskAllocation(250), '250');
    });

    test('a fractional amount keeps its cents', () {
      expect(formatTaskAllocation(250.5), '250.5');
    });

    test('round-trips through double.parse (the field feeds the save path)', () {
      expect(double.parse(formatTaskAllocation(1234.25)), 1234.25);
    });
  });

  group('nextDraftAllocation', () {
    test('a parsed number becomes the new draft', () {
      expect(nextDraftAllocation('350', 300), 350);
    });

    test('an empty field means the user is CLEARING the allocation', () {
      expect(nextDraftAllocation('', 300), isNull);
      expect(nextDraftAllocation('   ', 300), isNull);
    });

    test(
      'a half-typed number keeps the previous draft — the readout must not '
      'flicker to "No budget yet" mid-word. ("3." already parses to 3.0 in '
      'Dart, so the transient cases are the ones that DON\'T parse.)',
      () {
        expect(nextDraftAllocation('-', 300), 300);
        expect(nextDraftAllocation('1e', 300), 300);
        expect(nextDraftAllocation('3.', 300), 3);
      },
    );

    test('junk keeps the previous draft rather than adopting it', () {
      expect(nextDraftAllocation('abc', 300), 300);
      expect(nextDraftAllocation('-5', 300), 300);
      expect(nextDraftAllocation('abc', null), isNull);
    });
  });

  group('taskAllocationError', () {
    test('an empty field is VALID — it is the clear sentinel', () {
      expect(taskAllocationError(''), isNull);
      expect(taskAllocationError('  '), isNull);
    });

    test('a plain number is valid', () {
      expect(taskAllocationError('250'), isNull);
      expect(taskAllocationError('250.75'), isNull);
    });

    test('non-numeric input is rejected', () {
      expect(taskAllocationError('abc'), kTaskAllocationInvalidError);
      expect(taskAllocationError('Infinity'), kTaskAllocationInvalidError);
    });

    test('a negative allocation is rejected', () {
      expect(taskAllocationError('-1'), kTaskAllocationNegativeError);
    });

    test('an absurd allocation is rejected rather than stored', () {
      expect(
        taskAllocationError('${kTaskAllocationMax * 10}'),
        kTaskAllocationOverflowError,
      );
    });
  });

  group('previewTaskTopUp', () {
    test('ADDS to the current allocation (300 + 50 = 350)', () {
      final preview = previewTaskTopUp('50', 300);
      expect(preview.error, isNull);
      expect(preview.total, 350);
    });

    test('on a task with NO allocation it DEFINES one', () {
      final preview = previewTaskTopUp('50', null);
      expect(preview.error, isNull);
      expect(preview.total, 50);
    });

    test('rounds to cents so float noise never reaches the field', () {
      expect(previewTaskTopUp('0.1', 0.2).total, 0.3);
    });

    test('an empty amount is rejected', () {
      expect(previewTaskTopUp('', 300).error, kTaskTopUpEmptyError);
      expect(previewTaskTopUp('   ', 300).total, isNull);
    });

    test('a non-numeric amount is rejected', () {
      expect(previewTaskTopUp('abc', 300).error, kTaskTopUpInvalidError);
      expect(previewTaskTopUp('1e400', 300).error, kTaskTopUpInvalidError);
    });

    test('a negative or zero top-up is rejected — a top-up only ADDS', () {
      expect(previewTaskTopUp('-5', 300).error, kTaskTopUpNonPositiveError);
      expect(previewTaskTopUp('0', 300).error, kTaskTopUpNonPositiveError);
    });

    test('a top-up that would overflow the allocation is rejected', () {
      final preview = previewTaskTopUp('$kTaskAllocationMax', kTaskAllocationMax);
      expect(preview.error, kTaskTopUpOverflowError);
      expect(preview.total, isNull);
    });

    test('an invalid preview carries NO total — nothing can be committed', () {
      expect(previewTaskTopUp('abc', 300).total, isNull);
    });
  });

  group('taskTopUpPreviewLabel', () {
    test('shows the new total AND what it grew from', () {
      expect(
        taskTopUpPreviewLabel(300, 350, 'USD'),
        'New total \$350 (was \$300)',
      );
    });

    test('with no current allocation there is no "was"', () {
      expect(taskTopUpPreviewLabel(null, 50, 'USD'), 'New total \$50');
    });

    test('with nothing valid typed yet it shows the baseline', () {
      expect(taskTopUpPreviewLabel(300, null, 'USD'), 'Allocated \$300');
      expect(taskTopUpPreviewLabel(null, null, 'USD'), 'No allocation yet');
    });
  });
}
