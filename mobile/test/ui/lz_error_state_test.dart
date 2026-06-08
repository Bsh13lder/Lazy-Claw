import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/ui/app_theme.dart';
import 'package:lazyclaw_mobile/ui/components/lz_banner.dart';
import 'package:lazyclaw_mobile/ui/components/lz_error_state.dart';

Widget _host(Widget child) =>
    MaterialApp(theme: buildAppTheme(), home: Scaffold(body: child));

void main() {
  group('LzErrorState', () {
    testWidgets('renders the message and a default icon', (tester) async {
      await tester.pumpWidget(_host(
        LzErrorState(message: 'Something broke', onRetry: () {}),
      ));
      expect(find.text('Something broke'), findsOneWidget);
    });

    testWidgets('shows a Retry button that fires onRetry', (tester) async {
      var tapped = false;
      await tester.pumpWidget(_host(
        LzErrorState(message: 'Something broke', onRetry: () => tapped = true),
      ));
      expect(find.text('Retry'), findsOneWidget);
      await tester.tap(find.text('Retry'));
      await tester.pump();
      expect(tapped, isTrue);
    });

    testWidgets('uses a custom icon when provided', (tester) async {
      await tester.pumpWidget(_host(
        LzErrorState(
          message: 'Boom',
          icon: Icons.wifi_off_outlined,
          onRetry: () {},
        ),
      ));
      expect(find.byIcon(Icons.wifi_off_outlined), findsOneWidget);
    });
  });

  group('LzBanner.degraded', () {
    testWidgets('shows the degraded message', (tester) async {
      await tester.pumpWidget(_host(
        LzBanner.degraded(onRetry: () {}, onReset: () {}),
      ));
      expect(
        find.text('Local storage unavailable — data may be incomplete.'),
        findsOneWidget,
      );
    });

    testWidgets('Refresh affordance fires onRetry', (tester) async {
      var retried = false;
      await tester.pumpWidget(_host(
        LzBanner.degraded(onRetry: () => retried = true, onReset: () {}),
      ));
      expect(find.text('Refresh'), findsOneWidget);
      await tester.tap(find.text('Refresh'));
      await tester.pump();
      expect(retried, isTrue);
    });

    testWidgets('Reset affordance fires onReset', (tester) async {
      var reset = false;
      await tester.pumpWidget(_host(
        LzBanner.degraded(onRetry: () {}, onReset: () => reset = true),
      ));
      expect(find.text('Reset'), findsOneWidget);
      await tester.tap(find.text('Reset'));
      await tester.pump();
      expect(reset, isTrue);
    });
  });
}
