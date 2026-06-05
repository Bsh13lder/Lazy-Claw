import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/ui/app_theme.dart';
import 'package:lazyclaw_mobile/ui/components/lz_empty_state.dart';

Widget _host(Widget child) =>
    MaterialApp(theme: buildAppTheme(), home: Scaffold(body: child));

void main() {
  group('LzEmptyState', () {
    testWidgets('renders icon, title and hint', (tester) async {
      await tester.pumpWidget(_host(
        const LzEmptyState(
          icon: Icons.inbox_outlined,
          title: 'Nothing yet',
          hint: 'Add your first item',
        ),
      ));
      expect(find.byIcon(Icons.inbox_outlined), findsOneWidget);
      expect(find.text('Nothing yet'), findsOneWidget);
      expect(find.text('Add your first item'), findsOneWidget);
    });

    testWidgets('omits the action button when no action is given',
        (tester) async {
      await tester.pumpWidget(_host(
        const LzEmptyState(icon: Icons.inbox_outlined, title: 'Empty'),
      ));
      expect(find.text('Add'), findsNothing);
    });

    testWidgets('shows an action button that fires onAction', (tester) async {
      var tapped = false;
      await tester.pumpWidget(_host(
        LzEmptyState(
          icon: Icons.inbox_outlined,
          title: 'Empty',
          actionLabel: 'Add item',
          actionIcon: Icons.add,
          onAction: () => tapped = true,
        ),
      ));
      expect(find.text('Add item'), findsOneWidget);
      await tester.tap(find.text('Add item'));
      await tester.pump();
      expect(tapped, isTrue);
    });
  });
}
