// Widget tests for the add-task sheet's reminder lead-time integration:
//   * the REMIND picker stays hidden until the task has a due TIME,
//   * once a time is present, the global default lead is pre-selected.
//
// The sheet is a plain StatefulWidget (no providers), so it's pumped directly
// with an injected [defaultLead].

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/reminder_lead.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_task_sheet.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

void main() {
  Widget host({ReminderLead defaultLead = ReminderLead.min30}) => MaterialApp(
        theme: buildAppTheme(),
        home: Scaffold(
          body: SingleChildScrollView(
            child: AddTaskSheet(defaultLead: defaultLead),
          ),
        ),
      );

  testWidgets('REMIND picker is hidden until a due time is set',
      (tester) async {
    await tester.pumpWidget(host());
    // No time yet → no reminder section.
    expect(find.text('REMIND'), findsNothing);

    // A date-only token does NOT surface the picker.
    await tester.enterText(find.byType(TextField).first, 'Buy milk today');
    await tester.pump();
    expect(find.text('REMIND'), findsNothing);
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
