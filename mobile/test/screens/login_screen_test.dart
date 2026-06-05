import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/screens/login_screen.dart';

void main() {
  testWidgets('login screen renders fields and submit', (tester) async {
    await tester.pumpWidget(const ProviderScope(
        child: MaterialApp(home: LoginScreen())));
    expect(find.byKey(const Key('login_user')), findsOneWidget);
    expect(find.byKey(const Key('login_pass')), findsOneWidget);
    expect(find.byKey(const Key('login_submit')), findsOneWidget);
  });
}
