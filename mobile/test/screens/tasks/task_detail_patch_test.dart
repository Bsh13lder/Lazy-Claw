// Pure tests for the Save payload's three-way rules (null = untouched,
// '' = force clear, value = set). These used to be reachable only by driving
// the whole detail sheet; getting one wrong silently deletes user data on an
// unrelated edit, so each rule is now asserted directly.

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_detail_patch.dart';

TaskDetailPatch build({
  String title = 'T',
  String description = '',
  String priority = 'medium',
  String? composedDue,
  List<String> tags = const [],
  String? originalTagsJson,
  String budgetText = '',
  double? originalBudget,
  String? nextSteps,
  String? originalSteps,
  bool categoryTouched = false,
  String? category,
  bool recurrenceTouched = false,
  String? nextCron,
  bool recurUntilTouched = false,
  String? recurUntil,
  String? reminderArg,
}) => buildTaskDetailPatch(
  title: title,
  description: description,
  priority: priority,
  composedDue: composedDue,
  tags: tags,
  originalTagsJson: originalTagsJson ?? jsonEncode(tags),
  budgetText: budgetText,
  originalBudget: originalBudget,
  nextSteps: nextSteps,
  originalSteps: originalSteps,
  categoryTouched: categoryTouched,
  category: category,
  recurrenceTouched: recurrenceTouched,
  nextCron: nextCron,
  recurUntilTouched: recurUntilTouched,
  recurUntil: recurUntil,
  reminderArg: reminderArg,
);

void main() {
  group('tags', () {
    test('unchanged tags are NOT written (no churn on a title-only edit)', () {
      expect(build(tags: const ['work']).tags, isNull);
    });

    test('changed tags ride as the JSON-array string the cache carries', () {
      final patch = build(
        tags: const ['work', 'home'],
        originalTagsJson: '["work"]',
      );
      expect(jsonDecode(patch.tags!), ['work', 'home']);
    });

    test('emptying a tagged task writes "[]" (a deliberate clear)', () {
      expect(build(tags: const [], originalTagsJson: '["work"]').tags, '[]');
    });
  });

  group('allocated budget', () {
    test('an empty field on a task that HAD one clears it', () {
      final patch = build(budgetText: '', originalBudget: 250);
      expect(patch.clearAllocatedBudget, isTrue);
      expect(patch.allocatedBudget, isNull);
    });

    test('an empty field on a task with none is a no-op', () {
      final patch = build(budgetText: '');
      expect(patch.clearAllocatedBudget, isFalse);
      expect(patch.allocatedBudget, isNull);
    });

    test('a different number sets it', () {
      expect(build(budgetText: '250').allocatedBudget, 250.0);
    });

    test('the SAME number is not re-written', () {
      final patch = build(budgetText: '250', originalBudget: 250);
      expect(patch.allocatedBudget, isNull);
      expect(patch.clearAllocatedBudget, isFalse);
    });

    test('unparseable text neither sets nor clears', () {
      final patch = build(budgetText: 'abc', originalBudget: 250);
      expect(patch.allocatedBudget, isNull);
      expect(patch.clearAllocatedBudget, isFalse);
    });
  });

  group('steps', () {
    test('unchanged steps are not written', () {
      expect(build(nextSteps: '[]', originalSteps: '[]').steps, isNull);
    });

    test('removing every sub-task writes "" (force clear), never null', () {
      expect(build(nextSteps: null, originalSteps: '[{"id":"s"}]').steps, '');
    });
  });

  group('category', () {
    test('untouched leaves the column alone', () {
      expect(build(category: 'Home').category, isNull);
    });

    test('"No project" force-clears with ""', () {
      expect(build(categoryTouched: true).category, '');
    });

    test('a pick sets the name', () {
      expect(build(categoryTouched: true, category: 'Home').category, 'Home');
    });
  });

  group('recurring + recur_until', () {
    test('untouched recurrence preserves an unknown/custom cron', () {
      expect(build(nextCron: '0 9 * * *').recurring, isNull);
    });

    test('a pick sends the computed cron', () {
      final patch = build(recurrenceTouched: true, nextCron: '0 9 * * *');
      expect(patch.recurring, '0 9 * * *');
    });

    test('"does not repeat" sends the "" clear sentinel', () {
      expect(build(recurrenceTouched: true).recurring, '');
    });

    test('clearing the recurrence also clears an orphaned end date', () {
      final patch = build(recurrenceTouched: true, recurUntil: '2026-12-01');
      expect(patch.recurring, '');
      expect(patch.recurUntil, '');
    });

    test('clearing a recurrence that had NO end date churns nothing', () {
      expect(build(recurrenceTouched: true).recurUntil, isNull);
    });

    test('an untouched end date is not written', () {
      final patch = build(
        recurrenceTouched: true,
        nextCron: '0 9 * * *',
        recurUntil: '2026-12-01',
      );
      expect(patch.recurUntil, isNull);
    });

    test('touching Ends → Never sends ""', () {
      final patch = build(
        recurrenceTouched: true,
        nextCron: '0 9 * * *',
        recurUntilTouched: true,
      );
      expect(patch.recurUntil, '');
    });
  });

  group('due date', () {
    test('a removed due date rides as "" so the clear actually syncs', () {
      expect(build(composedDue: null).dueDate, '');
    });

    test('a set due date rides verbatim', () {
      expect(build(composedDue: '2026-06-10').dueDate, '2026-06-10');
    });
  });

  test('reminderArg is passed through untouched', () {
    expect(build(reminderArg: null).reminderAt, isNull);
    expect(build(reminderArg: '').reminderAt, '');
    expect(
      build(reminderArg: '2026-06-10T09:00:00').reminderAt,
      '2026-06-10T09:00:00',
    );
  });
}
