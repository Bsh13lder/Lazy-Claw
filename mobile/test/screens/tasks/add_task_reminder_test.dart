// Widget tests for the add-task sheet's reminder lead-time integration:
//   * the REMIND picker stays hidden until the task has a due TIME,
//   * once a time is present, the global default lead is pre-selected,
//   * a date-only due shows the muted "Reminds at … on the due date" hint.
//
// The sheet is a ConsumerStatefulWidget (it watches the default-reminder-time
// pref to LABEL the date-only hint), so it's pumped inside a [ProviderScope].
// The pref provider stays in its loading state here, so the label falls back to
// the built-in default (09:00) — exactly the production fallback.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/reminder_lead.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_task_sheet.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

void main() {
  Widget host({ReminderLead defaultLead = ReminderLead.min30}) => ProviderScope(
    child: MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: SingleChildScrollView(
          child: AddTaskSheet(defaultLead: defaultLead),
        ),
      ),
    ),
  );

  testWidgets('REMIND picker is hidden until a due time is set', (
    tester,
  ) async {
    await tester.pumpWidget(host());
    // No time yet → no reminder section.
    expect(find.text('REMIND'), findsNothing);

    // A date-only token does NOT surface the picker.
    await tester.enterText(find.byType(TextField).first, 'Buy milk today');
    await tester.pump();
    expect(find.text('REMIND'), findsNothing);
  });

  testWidgets('a date-only due shows the muted default-time reminder hint', (
    tester,
  ) async {
    await tester.pumpWidget(host());
    await tester.enterText(find.byType(TextField).first, 'Buy milk tomorrow');
    await tester.pump();

    // No timed REMIND picker, but the reassurance hint at the default time.
    expect(find.text('REMIND'), findsNothing);
    expect(find.text('Reminds at 9:00 AM on the due date'), findsOneWidget);
  });

  testWidgets('a parsed time surfaces the picker with the global default '
      'pre-selected', (tester) async {
    await tester.pumpWidget(host(defaultLead: ReminderLead.min30));

    await tester.enterText(find.byType(TextField).first, 'Buy milk 5pm');
    await tester.pump();

    expect(find.text('REMIND'), findsOneWidget);
    // The default (30 min before) chip is selected; None is not.
    expect(
      find.byWidgetPredicate(
        (w) => w is LzChip && w.label == '30 min before' && w.selected,
      ),
      findsOneWidget,
    );
    expect(
      find.byWidgetPredicate(
        (w) => w is LzChip && w.label == 'None' && w.selected,
      ),
      findsNothing,
    );
  });

  testWidgets('a different global default is honoured', (tester) async {
    await tester.pumpWidget(host(defaultLead: ReminderLead.hour1));

    await tester.enterText(find.byType(TextField).first, 'Standup 9am');
    await tester.pump();

    expect(
      find.byWidgetPredicate(
        (w) => w is LzChip && w.label == '1 hour before' && w.selected,
      ),
      findsOneWidget,
    );
  });
}
