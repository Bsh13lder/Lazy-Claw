import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/core/config/base_url_override_store.dart';
import 'package:lazyclaw_mobile/core/config/server_config.dart';
import 'package:lazyclaw_mobile/screens/login_screen.dart';

void main() {
  testWidgets('login screen renders fields and submit', (tester) async {
    await tester.pumpWidget(
        const ProviderScope(child: MaterialApp(home: LoginScreen())));
    expect(find.byKey(const Key('login_user')), findsOneWidget);
    expect(find.byKey(const Key('login_pass')), findsOneWidget);
    expect(find.byKey(const Key('login_submit')), findsOneWidget);
  });

  testWidgets('server escape hatch pins a user-typed URL (unbricks login)',
      (tester) async {
    final saved = ServerConfig.overrideStore;
    ServerConfig.overrideStore = InMemoryBaseUrlOverrideStore();
    addTearDown(() => ServerConfig.overrideStore = saved);

    await tester.pumpWidget(
        const ProviderScope(child: MaterialApp(home: LoginScreen())));

    // Collapsed by default — it must not clutter the normal login.
    expect(find.byKey(const Key('login_server')), findsNothing);

    // Reveal the field, type the reachable LAN IP, save.
    await tester.tap(find.byKey(const Key('login_toggle_server')));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const Key('login_server')), 'http://192.168.0.12:18789');
    await tester.tap(find.byKey(const Key('login_use_server')));
    await tester.pumpAndSettle();

    // Persisted as the manual override so the next login request uses it, and
    // the user gets confirmation.
    expect(await ServerConfig.loadOverride(), 'http://192.168.0.12:18789');
    expect(find.textContaining('Now using'), findsOneWidget);
  });
}
