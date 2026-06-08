import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/ui/app_theme.dart';
import 'package:lazyclaw_mobile/ui/components/lz_bottom_nav.dart';

const _destinations = <LzNavDestination>[
  LzNavDestination(
      label: 'Home', icon: Icons.home_outlined, activeIcon: Icons.home),
  LzNavDestination(
      label: 'Chat',
      icon: Icons.chat_bubble_outline,
      activeIcon: Icons.chat_bubble),
  LzNavDestination(
      label: 'Tasks',
      icon: Icons.check_circle_outline,
      activeIcon: Icons.check_circle),
  LzNavDestination(
      label: 'Money',
      icon: Icons.account_balance_wallet_outlined,
      activeIcon: Icons.account_balance_wallet),
  LzNavDestination(
      label: 'Notes', icon: Icons.notes_outlined, activeIcon: Icons.notes),
  LzNavDestination(
      label: 'Settings',
      icon: Icons.settings_outlined,
      activeIcon: Icons.settings),
];

Widget _host({required int index, required ValueChanged<int> onSelected}) {
  return MaterialApp(
    theme: buildAppTheme(),
    home: Scaffold(
      bottomNavigationBar: LzBottomNav(
        destinations: _destinations,
        currentIndex: index,
        onSelected: onSelected,
      ),
    ),
  );
}

void main() {
  group('LzBottomNav', () {
    testWidgets('renders all six destination labels', (tester) async {
      await tester.pumpWidget(_host(index: 0, onSelected: (_) {}));
      for (final d in _destinations) {
        expect(find.text(d.label), findsOneWidget);
      }
    });

    testWidgets('reports the tapped index', (tester) async {
      int? selected;
      await tester.pumpWidget(_host(index: 0, onSelected: (i) => selected = i));
      await tester.tap(find.text('Tasks'));
      await tester.pumpAndSettle();
      expect(selected, 2);
    });

    testWidgets('active destination shows its active icon', (tester) async {
      await tester.pumpWidget(_host(index: 0, onSelected: (_) {}));
      await tester.pumpAndSettle();
      // Home is selected → its active (filled) icon is rendered.
      expect(find.byIcon(Icons.home), findsOneWidget);
      // Chat is not selected → its outline icon is rendered.
      expect(find.byIcon(Icons.chat_bubble_outline), findsOneWidget);
    });

    testWidgets('marks the selected item via Semantics', (tester) async {
      await tester.pumpWidget(_host(index: 1, onSelected: (_) {}));
      await tester.pumpAndSettle();
      final chatSemantics = tester.getSemantics(find.text('Chat'));
      expect(chatSemantics.hasFlag(SemanticsFlag.isSelected), isTrue);
    });
  });
}
