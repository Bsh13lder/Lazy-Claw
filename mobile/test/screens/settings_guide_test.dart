// Widget tests for the Settings → Guide "Tips & shortcuts" tile + dialog.
//
// `SettingsGuideTile` is the testable body extracted from `SettingsScreen`
// (mirrors `SettingsUpdateTile` in settings_update_button_test.dart): a
// tappable list tile that opens an `LzDialog` listing `GuideStepsList`'s 6
// in-app tips. Pumping the full `SettingsScreen` would drag in
// auth/eco/permissions/secure-storage platform channels that have no test
// implementation, so this extracted widget is the deterministic seam.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/settings_screen.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

void main() {
  Widget host(Widget child) => MaterialApp(
        theme: buildAppTheme(),
        home: Scaffold(body: child),
      );

  testWidgets(
    'renders the keyed Guide tile with its lightbulb icon and label',
    (tester) async {
      await tester.pumpWidget(host(const SettingsGuideTile()));

      expect(find.byKey(const Key('settings-guide-tile')), findsOneWidget);
      expect(find.text('Tips & shortcuts'), findsOneWidget);
      expect(find.byIcon(Icons.lightbulb_outline), findsOneWidget);
    },
  );

  testWidgets(
    'tapping the tile opens a dialog listing all 6 tip steps verbatim',
    (tester) async {
      await tester.pumpWidget(host(const SettingsGuideTile()));

      await tester.tap(find.byKey(const Key('settings-guide-tile')));
      await tester.pumpAndSettle();

      // Dialog title + the tile's own label both render "Tips & shortcuts".
      expect(find.text('Tips & shortcuts'), findsNWidgets(2));
      for (final step in GuideStepsList.steps) {
        expect(find.text(step), findsOneWidget);
      }

      await tester.tap(find.text('Got it'));
      await tester.pumpAndSettle();
      expect(find.byType(AlertDialog), findsNothing);
    },
  );

  testWidgets('GuideStepsList renders all 6 steps standalone', (
    tester,
  ) async {
    await tester.pumpWidget(host(const GuideStepsList()));

    expect(GuideStepsList.steps, hasLength(6));
    for (final step in GuideStepsList.steps) {
      expect(find.text(step), findsOneWidget);
    }
  });
}
