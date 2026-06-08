// Widget tests for the reminder lead-time picker: preset chips report the
// right ReminderLead, and the "Custom…" chip reveals number+unit inputs that
// emit a custom lead.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/reminder_lead.dart';
import 'package:lazyclaw_mobile/screens/tasks/reminder_lead_picker.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

void main() {
  // Host that owns the value + records every onChanged emission, re-rendering
  // the picker with the latest value (mirrors how the sheets drive it).
  Widget host(List<ReminderLead> sink, {ReminderLead initial = ReminderLead.none}) {
    return MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: StatefulBuilder(
          builder: (ctx, setState) {
            var value = sink.isEmpty ? initial : sink.last;
            return SingleChildScrollView(
              child: ReminderLeadPicker(
                value: value,
                onChanged: (v) => setState(() => sink.add(v)),
              ),
            );
          },
        ),
      ),
    );
  }

  testWidgets('renders all preset chips + Custom', (tester) async {
    await tester.pumpWidget(host([]));
    expect(find.text('None'), findsOneWidget);
    expect(find.text('At time'), findsOneWidget);
    expect(find.text('10 min before'), findsOneWidget);
    expect(find.text('30 min before'), findsOneWidget);
    expect(find.text('1 hour before'), findsOneWidget);
    expect(find.text('1 day before'), findsOneWidget);
    expect(find.text('Custom…'), findsOneWidget);
  });

  testWidgets('tapping a preset reports that lead', (tester) async {
    final sink = <ReminderLead>[];
    await tester.pumpWidget(host(sink));

    await tester.tap(find.text('1 hour before'));
    await tester.pump();
    expect(sink.last, ReminderLead.hour1);

    await tester.tap(find.text('30 min before'));
    await tester.pump();
    expect(sink.last, ReminderLead.min30);

    await tester.tap(find.text('None'));
    await tester.pump();
    expect(sink.last, ReminderLead.none);
  });

  testWidgets('Custom… reveals the number field and emits a custom lead',
      (tester) async {
    final sink = <ReminderLead>[];
    await tester.pumpWidget(host(sink));

    // Initially hidden.
    expect(find.byKey(const Key('reminder-custom-value')), findsNothing);

    await tester.tap(find.text('Custom…'));
    await tester.pump();
    // Custom inputs revealed (default text 15 → emits a 15-min lead).
    expect(find.byKey(const Key('reminder-custom-value')), findsOneWidget);
    expect(sink.last, const ReminderLead(Duration(minutes: 15)));

    // Type a custom value (minutes by default).
    await tester.enterText(
        find.byKey(const Key('reminder-custom-value')), '45');
    await tester.pump();
    expect(sink.last, const ReminderLead(Duration(minutes: 45)));
    expect(sink.last.isCustom, isTrue);

    // Switch the unit to hours.
    await tester.tap(find.text('Hr'));
    await tester.pump();
    expect(sink.last, const ReminderLead(Duration(hours: 45)));
  });

  testWidgets('opening Custom on an existing custom value seeds the inputs',
      (tester) async {
    final sink = <ReminderLead>[];
    await tester.pumpWidget(
        host(sink, initial: const ReminderLead(Duration(hours: 2))));

    // Custom inputs are shown immediately (value.isCustom), seeded to 2 / Hr.
    final field = tester.widget<TextField>(
        find.byKey(const Key('reminder-custom-value')));
    expect(field.controller!.text, '2');
    expect(find.text('Hr'), findsOneWidget);
  });
}
