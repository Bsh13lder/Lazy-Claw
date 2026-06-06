// Widget tests for the auto-surfaced "Update available" tile in Settings.
//
// The tile (`SettingsUpdateTile`) is the testable body extracted from
// `SettingsScreen`: it watches `updateAvailableProvider` and renders a prominent
// accent banner when a newer build is pending, or nothing when it is not. Both
// states are exercised here in isolation — pumping the full `SettingsScreen`
// would drag in auth/eco/permissions/secure-storage platform channels that have
// no test implementation, so the body widget is the deterministic seam.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/self_update.dart';
import 'package:lazyclaw_mobile/screens/settings_screen.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

void main() {
  Widget host(UpdateInfo? pending) => ProviderScope(
        overrides: [
          updateAvailableProvider.overrideWith((ref) => pending),
        ],
        child: MaterialApp(
          theme: buildAppTheme(),
          home: const Scaffold(body: SettingsUpdateTile()),
        ),
      );

  testWidgets(
    'surfaces the keyed update tile when an update is available',
    (tester) async {
      await tester.pumpWidget(
        host(const UpdateInfo(version: '1.9.0', build: 99)),
      );
      await tester.pump();

      // The auto-surfaced tile is present and unambiguously targetable by key.
      expect(
        find.byKey(const Key('settings-update-available')),
        findsOneWidget,
      );
      // It reads the pending version and offers an "Update" affordance.
      expect(find.text('Update available'), findsOneWidget);
      expect(find.textContaining('1.9.0'), findsOneWidget);
      expect(find.text('Update'), findsOneWidget);
    },
  );

  testWidgets(
    'renders nothing when no update is available',
    (tester) async {
      await tester.pumpWidget(host(null));
      await tester.pump();

      // The auto-surfaced tile is absent; only its key distinguishes it from the
      // always-present manual "Check for update" tile in the real screen.
      expect(
        find.byKey(const Key('settings-update-available')),
        findsNothing,
      );
      expect(find.text('Update available'), findsNothing);
    },
  );
}
