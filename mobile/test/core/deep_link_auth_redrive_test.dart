// Verifies the COLD-START auth-redirect safety net added to main.dart.
//
// The bug: a widget/shortcut can fire the deep link while auth is still
// `loading`/`unauthenticated`. The first navigation can then be undone by the
// auth redirect (→ /login → /home) before the destination screen mounts,
// stranding a still-set pending action on Home. The fix RE-DRIVES navigation
// when auth settles to `authenticated`, so the destination finally mounts (and
// then drains the action).
//
// This test mirrors main.dart's two pendingActionProvider/authProvider listeners
// against a tiny fake router so the load-bearing re-drive logic is exercised
// without the heavy real screen graph.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/actions/app_actions.dart';

enum _Auth { loading, authed, unauthed }

final _authProvider = StateProvider<_Auth>((_) => _Auth.loading);

/// Records every navigation target — stands in for GoRouter.go.
class _NavSpy {
  final List<String> targets = [];
  void go(String t) => targets.add(t);
}

/// Mirrors main.dart's [_navigateForAction] + the two listeners.
class _AppHarness extends ConsumerWidget {
  const _AppHarness(this.nav);
  final _NavSpy nav;

  void _navigateForAction(WidgetRef ref, AppAction action) {
    nav.go(routeForAction(action));
    if (action == AppAction.chat) {
      ref.read(pendingActionProvider.notifier).state = null;
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    ref.listen<AppAction?>(pendingActionProvider, (_, next) {
      if (next == null) return;
      _navigateForAction(ref, next);
    });
    ref.listen<_Auth>(_authProvider, (prev, next) {
      if (prev == _Auth.authed) return;
      if (next != _Auth.authed) return;
      final pending = ref.read(pendingActionProvider);
      if (pending == null) return;
      final still = ref.read(pendingActionProvider);
      if (still != null) _navigateForAction(ref, still);
    });
    return const SizedBox.shrink();
  }
}

void main() {
  testWidgets(
      'pending action set during auth=loading is (re)driven once authed',
      (tester) async {
    final container = ProviderContainer();
    addTearDown(container.dispose);
    final nav = _NavSpy();

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(home: _AppHarness(nav)),
      ),
    );

    // Cold start: auth still loading, deep link fires.
    expect(container.read(_authProvider), _Auth.loading);
    container.read(pendingActionProvider.notifier).state = AppAction.addTask;
    await tester.pump();

    // The first listener navigates immediately (best effort)…
    expect(nav.targets, ['/tasks']);

    // …simulate the redirect churn undoing it (auth flips unauthed→back) — the
    // action is intentionally NOT cleared (only `chat` self-clears), so it's
    // still pending and ready to be re-driven.
    container.read(_authProvider.notifier).state = _Auth.unauthed;
    await tester.pump();
    expect(container.read(pendingActionProvider), AppAction.addTask);

    // Auth finally settles → the safety net re-drives the still-pending action.
    container.read(_authProvider.notifier).state = _Auth.authed;
    await tester.pump();
    expect(nav.targets, ['/tasks', '/tasks']);
  });

  testWidgets('no re-drive when nothing is pending at auth-settle',
      (tester) async {
    final container = ProviderContainer();
    addTearDown(container.dispose);
    final nav = _NavSpy();

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(home: _AppHarness(nav)),
      ),
    );

    container.read(_authProvider.notifier).state = _Auth.authed;
    await tester.pump();

    expect(nav.targets, isEmpty);
  });

  testWidgets('chat action self-clears and is NOT re-driven at settle',
      (tester) async {
    final container = ProviderContainer();
    addTearDown(container.dispose);
    final nav = _NavSpy();

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(home: _AppHarness(nav)),
      ),
    );

    container.read(pendingActionProvider.notifier).state = AppAction.chat;
    await tester.pump();
    // chat navigates + self-clears.
    expect(nav.targets, ['/chat']);
    expect(container.read(pendingActionProvider), isNull);

    // Auth settles — nothing pending, so no spurious second nav.
    container.read(_authProvider.notifier).state = _Auth.authed;
    await tester.pump();
    expect(nav.targets, ['/chat']);
  });
}
