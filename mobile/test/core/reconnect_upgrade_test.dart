// Verifies the SILENT-UPGRADE reconnect hook added to main.dart.
//
// While in offline mode (`AuthStatus.authenticatedOffline`), the app must
// revalidate the session exactly when the backend becomes reachable again
// (false→true edge on reachableProvider) — and ONLY then. Any other edge, or
// any other auth status, must NOT fire a checkSession.
//
// This test mirrors main.dart's reachable listener against a tiny fake
// reachable/auth pair (same harness idiom as deep_link_auth_redrive_test.dart)
// so the load-bearing edge logic is exercised without the heavy screen graph.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/auth_provider.dart';

final _reachable = StateProvider<bool>((_) => true);
final _status = StateProvider<AuthStatus>((_) => AuthStatus.unauthenticated);

/// Records checkSession invocations — stands in for the AuthNotifier.
class _SessionSpy {
  int checks = 0;
  void checkSession() => checks++;
}

/// Mirrors main.dart's reachableProvider listener.
class _ReconnectHarness extends ConsumerWidget {
  const _ReconnectHarness(this.spy);
  final _SessionSpy spy;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    ref.listen<bool>(_reachable, (prev, next) {
      if (prev != false || next != true) return;
      if (ref.read(_status) != AuthStatus.authenticatedOffline) return;
      spy.checkSession();
    });
    return const SizedBox.shrink();
  }
}

void main() {
  Future<(ProviderContainer, _SessionSpy)> pumpHarness(
      WidgetTester tester) async {
    final container = ProviderContainer();
    addTearDown(container.dispose);
    final spy = _SessionSpy();
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(home: _ReconnectHarness(spy)),
      ),
    );
    return (container, spy);
  }

  testWidgets('false→true edge while authenticatedOffline fires checkSession',
      (tester) async {
    final (container, spy) = await pumpHarness(tester);
    container.read(_status.notifier).state = AuthStatus.authenticatedOffline;

    container.read(_reachable.notifier).state = false;
    await tester.pump();
    expect(spy.checks, 0); // going offline never fires

    container.read(_reachable.notifier).state = true;
    await tester.pump();
    expect(spy.checks, 1); // exactly one silent upgrade
  });

  testWidgets('false→true edge while fully authenticated does NOT fire',
      (tester) async {
    final (container, spy) = await pumpHarness(tester);
    container.read(_status.notifier).state = AuthStatus.authenticated;

    container.read(_reachable.notifier).state = false;
    await tester.pump();
    container.read(_reachable.notifier).state = true;
    await tester.pump();
    expect(spy.checks, 0);
  });

  testWidgets('false→true edge while unauthenticated does NOT fire',
      (tester) async {
    final (container, spy) = await pumpHarness(tester);

    container.read(_reachable.notifier).state = false;
    await tester.pump();
    container.read(_reachable.notifier).state = true;
    await tester.pump();
    expect(spy.checks, 0);
  });

  testWidgets('repeated reconnect edges fire once per edge', (tester) async {
    final (container, spy) = await pumpHarness(tester);
    container.read(_status.notifier).state = AuthStatus.authenticatedOffline;

    for (var i = 0; i < 3; i++) {
      container.read(_reachable.notifier).state = false;
      await tester.pump();
      container.read(_reachable.notifier).state = true;
      await tester.pump();
    }
    expect(spy.checks, 3);
  });
}
