import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/ui/app_theme.dart';
import 'package:lazyclaw_mobile/ui/components/lz_button.dart';

Widget _host(Widget child) =>
    MaterialApp(theme: buildAppTheme(), home: Scaffold(body: Center(child: child)));

void main() {
  group('LzButton', () {
    testWidgets('renders its label', (tester) async {
      await tester.pumpWidget(_host(
        LzButton.primary(label: 'Save', onPressed: () {}),
      ));
      expect(find.text('Save'), findsOneWidget);
    });

    testWidgets('fires onPressed when enabled', (tester) async {
      var taps = 0;
      await tester.pumpWidget(_host(
        LzButton.primary(label: 'Tap', onPressed: () => taps++),
      ));
      await tester.tap(find.text('Tap'));
      await tester.pump();
      expect(taps, 1);
    });

    testWidgets('does not fire when onPressed is null (disabled)',
        (tester) async {
      await tester.pumpWidget(_host(
        const LzButton.primary(label: 'Disabled', onPressed: null),
      ));
      await tester.tap(find.text('Disabled'));
      await tester.pump();
      // No exception, nothing to assert beyond a clean tap on a disabled button.
      expect(tester.takeException(), isNull);
    });

    testWidgets('shows a spinner and swallows taps while loading',
        (tester) async {
      var taps = 0;
      await tester.pumpWidget(_host(
        LzButton.primary(label: 'Go', onPressed: () => taps++, loading: true),
      ));
      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      expect(find.text('Go'), findsNothing);
      await tester.tap(find.byType(LzButton));
      await tester.pump();
      expect(taps, 0);
    });

    testWidgets('renders a leading icon when provided', (tester) async {
      await tester.pumpWidget(_host(
        LzButton.primary(label: 'Add', icon: Icons.add, onPressed: () {}),
      ));
      expect(find.byIcon(Icons.add), findsOneWidget);
    });

    testWidgets('all four variants build', (tester) async {
      await tester.pumpWidget(_host(
        Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            LzButton.primary(label: 'P', onPressed: () {}),
            LzButton.secondary(label: 'S', onPressed: () {}),
            LzButton.ghost(label: 'G', onPressed: () {}),
            LzButton.danger(label: 'D', onPressed: () {}),
          ],
        ),
      ));
      expect(find.byType(LzButton), findsNWidgets(4));
      expect(tester.takeException(), isNull);
    });
  });
}
