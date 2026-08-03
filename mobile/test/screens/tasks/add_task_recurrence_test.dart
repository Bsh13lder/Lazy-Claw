// Widget tests for the add-task sheet's recurrence wiring:
//   * the REPEAT picker is always present,
//   * typing "every day" pre-selects Daily and surfaces a smart chip,
//   * tapping a repeat option + submitting returns a 5-field cron on the result
//     record (anchored to the composed due date),
//   * no recurrence → a null `recurring`.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/recurrence.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_task_sheet.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

void main() {
  // Host that opens the real showAddTaskSheet helper and captures its result so
  // we can assert the cron the sheet composed.
  ({String? recurring, String? dueDate})? captured;

  Widget host() => ProviderScope(
    child: MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: Center(
          child: Builder(
            builder: (ctx) => ElevatedButton(
              onPressed: () async {
                final r = await showAddTaskSheet(ctx);
                captured = r == null
                    ? null
                    : (recurring: r.recurring, dueDate: r.dueDate);
              },
              child: const Text('open'),
            ),
          ),
        ),
      ),
    ),
  );

  setUp(() => captured = null);

  testWidgets('the REPEAT picker is shown in the add sheet', (tester) async {
    await tester.pumpWidget(host());
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.text('REPEAT'), findsOneWidget);
    expect(find.byKey(const ValueKey('recurrence-opt-daily')), findsOneWidget);
  });

  testWidgets('typing "every day" surfaces a smart recurrence chip', (
    tester,
  ) async {
    await tester.pumpWidget(host());
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, 'standup every day');
    await tester.pump();

    // The "SMART DETECTED" recurrence chip echoes the parsed value.
    expect(
      find.byWidgetPredicate(
        (w) => w is LzChip && w.label == 'Daily' && w.selected,
      ),
      findsWidgets,
    );
  });

  testWidgets('tapping Daily + submit returns a daily cron', (tester) async {
    await tester.pumpWidget(host());
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, 'water plants');
    await tester.pump();

    await tester.ensureVisible(
      find.byKey(const ValueKey('recurrence-opt-daily')),
    );
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('recurrence-opt-daily')));
    await tester.pump();

    // The submit affordance is the floating square, which is anchored to
    // the sheet's viewport — no ensureVisible() needed, and that it is
    // always hit-testable is exactly the point of it.
    await tester.tap(find.byKey(kAddTaskSubmitKey));
    await tester.pumpAndSettle();

    // No due time → default 09:00 daily cron.
    expect(captured?.recurring, '0 9 * * *');
  });

  testWidgets('"every monday 5pm" composes a weekly-Monday cron at 17:00', (
    tester,
  ) async {
    await tester.pumpWidget(host());
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    await tester.enterText(
      find.byType(TextField).first,
      '1:1 every monday 5pm',
    );
    await tester.pump();

    // The submit affordance is the floating square, which is anchored to
    // the sheet's viewport — no ensureVisible() needed, and that it is
    // always hit-testable is exactly the point of it.
    await tester.tap(find.byKey(kAddTaskSubmitKey));
    await tester.pumpAndSettle();

    expect(captured?.recurring, '0 17 * * 1');
    // The due date carries the implied next-Monday + 17:00 time.
    expect(captured?.dueDate, endsWith('T17:00:00'));
    expect(
      recurrenceFromCron(captured?.recurring),
      Recurrence.weekly.copyWith(weekday: DateTime.monday),
    );
  });

  testWidgets('no recurrence vocab and no pick → null recurring', (
    tester,
  ) async {
    await tester.pumpWidget(host());
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, 'buy milk tomorrow');
    await tester.pump();

    // The submit affordance is the floating square, which is anchored to
    // the sheet's viewport — no ensureVisible() needed, and that it is
    // always hit-testable is exactly the point of it.
    await tester.tap(find.byKey(kAddTaskSubmitKey));
    await tester.pumpAndSettle();

    expect(captured, isNotNull);
    expect(captured?.recurring, isNull);
  });
}
