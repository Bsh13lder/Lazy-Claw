import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/ui/app_theme.dart';
import 'package:lazyclaw_mobile/ui/components/lz_banner.dart';

Widget _host(Widget child) =>
    MaterialApp(theme: buildAppTheme(), home: Scaffold(body: child));

void main() {
  group('LzBanner', () {
    testWidgets('offline variant shows the default message + cloud icon',
        (tester) async {
      await tester.pumpWidget(_host(const LzBanner.offline()));
      expect(find.text('Computer offline — changes will sync'), findsOneWidget);
      expect(find.byIcon(Icons.cloud_off_outlined), findsOneWidget);
    });

    testWidgets('error variant shows its message + error icon', (tester) async {
      await tester.pumpWidget(_host(const LzBanner.error(message: 'Boom')));
      expect(find.text('Boom'), findsOneWidget);
      expect(find.byIcon(Icons.error_outline), findsOneWidget);
    });

    testWidgets('info variant shows its message + info icon', (tester) async {
      await tester.pumpWidget(_host(const LzBanner.info(message: 'Heads up')));
      expect(find.text('Heads up'), findsOneWidget);
      expect(find.byIcon(Icons.info_outline), findsOneWidget);
    });

    testWidgets('renders a trailing action when provided', (tester) async {
      await tester.pumpWidget(_host(
        LzBanner.info(
          message: 'Update available',
          action: TextButton(onPressed: () {}, child: const Text('Update')),
        ),
      ));
      expect(find.text('Update'), findsOneWidget);
    });
  });
}
