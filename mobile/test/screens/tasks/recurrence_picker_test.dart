// Widget tests for the visual REPEAT picker:
//   * renders the six options + reflects the current selection,
//   * tapping an option reports the matching Recurrence,
//   * selecting Weekly reveals the weekday sub-row and pins the day,
//   * a custom (non-authored) cron shows a read-only "Repeats" chip.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/recurrence.dart';
import 'package:lazyclaw_mobile/screens/tasks/recurrence_picker.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

void main() {
  Widget host(
    Recurrence value,
    ValueChanged<Recurrence> onChanged, {
    int? anchorWeekday,
  }) => MaterialApp(
    theme: buildAppTheme(),
    home: Scaffold(
      body: SingleChildScrollView(
        child: RecurrencePicker(
          value: value,
          onChanged: onChanged,
          anchorWeekday: anchorWeekday,
        ),
      ),
    ),
  );

  testWidgets('renders all six options', (tester) async {
    await tester.pumpWidget(host(Recurrence.none, (_) {}));
    for (final kind in [
      RecurrenceKind.none,
      RecurrenceKind.daily,
      RecurrenceKind.weekdays,
      RecurrenceKind.weekly,
      RecurrenceKind.monthly,
      RecurrenceKind.yearly,
    ]) {
      expect(
        find.byKey(ValueKey('recurrence-opt-${kind.name}')),
        findsOneWidget,
      );
    }
  });

  testWidgets('tapping Daily reports a daily Recurrence', (tester) async {
    Recurrence? got;
    await tester.pumpWidget(host(Recurrence.none, (r) => got = r));
    await tester.tap(find.byKey(const ValueKey('recurrence-opt-daily')));
    await tester.pump();
    expect(got, Recurrence.daily);
  });

  testWidgets(
    'selecting Weekly reveals the weekday row and seeds from anchor',
    (tester) async {
      Recurrence? got;
      await tester.pumpWidget(
        host(
          Recurrence.none,
          (r) => got = r,
          anchorWeekday: DateTime.wednesday,
        ),
      );
      // No weekday row until weekly is active.
      expect(find.byKey(const ValueKey('recurrence-weekday-1')), findsNothing);

      await tester.tap(find.byKey(const ValueKey('recurrence-opt-weekly')));
      await tester.pump();

      // Reported a weekly recurrence seeded with the anchor weekday.
      expect(got?.kind, RecurrenceKind.weekly);
      expect(got?.weekday, DateTime.wednesday);
    },
  );

  testWidgets('the weekday row pins a specific day', (tester) async {
    Recurrence? got;
    await tester.pumpWidget(
      host(
        Recurrence(RecurrenceKind.weekly, weekday: DateTime.monday),
        (r) => got = r,
      ),
    );
    // Tap Friday (weekday 5).
    await tester.tap(find.byKey(const ValueKey('recurrence-weekday-5')));
    await tester.pump();
    expect(got?.kind, RecurrenceKind.weekly);
    expect(got?.weekday, DateTime.friday);
  });

  testWidgets('the current option chip reads as selected', (tester) async {
    await tester.pumpWidget(host(Recurrence.monthly, (_) {}));
    final chip = tester.widget<LzChip>(
      find.byKey(const ValueKey('recurrence-opt-monthly')),
    );
    expect(chip.selected, isTrue);
    final daily = tester.widget<LzChip>(
      find.byKey(const ValueKey('recurrence-opt-daily')),
    );
    expect(daily.selected, isFalse);
  });

  testWidgets('a custom cron renders a read-only Repeats chip', (tester) async {
    await tester.pumpWidget(host(Recurrence.custom, (_) {}));
    expect(find.byKey(const ValueKey('recurrence-opt-custom')), findsOneWidget);
    final chip = tester.widget<LzChip>(
      find.byKey(const ValueKey('recurrence-opt-custom')),
    );
    expect(chip.label, 'Repeats');
    expect(chip.onTap, isNull, reason: 'custom chip is read-only');
  });
}
