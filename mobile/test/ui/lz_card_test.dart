import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/ui/app_theme.dart';
import 'package:lazyclaw_mobile/ui/components/lz_card.dart';

Widget _host(Widget child) =>
    MaterialApp(theme: buildAppTheme(), home: Scaffold(body: child));

void main() {
  group('LzCard', () {
    testWidgets('renders its child', (tester) async {
      await tester.pumpWidget(_host(
        const LzCard(child: Text('content')),
      ));
      expect(find.text('content'), findsOneWidget);
    });

    testWidgets('is tappable when onTap is given', (tester) async {
      var tapped = false;
      await tester.pumpWidget(_host(
        LzCard(onTap: () => tapped = true, child: const Text('tap me')),
      ));
      expect(find.byType(InkWell), findsOneWidget);
      await tester.tap(find.text('tap me'));
      await tester.pump();
      expect(tapped, isTrue);
    });

    testWidgets('has no InkWell when not tappable', (tester) async {
      await tester.pumpWidget(_host(
        const LzCard(child: Text('static')),
      ));
      expect(find.byType(InkWell), findsNothing);
    });

    testWidgets('builds with a gradient fill', (tester) async {
      await tester.pumpWidget(_host(
        LzCard(
          gradient: const LinearGradient(colors: [Colors.red, Colors.blue]),
          child: const Text('grad'),
        ),
      ));
      expect(find.text('grad'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });
}
