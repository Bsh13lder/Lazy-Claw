// Behaviour tests for [drainPendingAction] — the frame-order-proof deep-link
// replay primitive that fixes the "widget button opens the app but does
// nothing" bug.
//
// The bug: on cold start a widget/shortcut sets [pendingActionProvider] BEFORE
// the destination screen exists. The destination is then built as a RESULT of
// the navigation listener, so a `build`-time `ref.listen` never fires (it only
// catches changes after the screen subscribes) and a one-shot `initState`
// post-frame can mis-fire during auth-redirect churn. [drainPendingAction]
// re-arms on every build and consumes the owning action whenever the screen
// becomes visible, regardless of which frame that is.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/actions/app_actions.dart';

/// A minimal screen that owns [mine] and records every drained action. It calls
/// [drainPendingAction] from build (the production pattern) so the test can
/// exercise the exact frame-ordering the real screens use.
class _OwnerScreen extends ConsumerStatefulWidget {
  const _OwnerScreen({required this.mine, required this.drained});
  final Set<AppAction> mine;
  final List<AppAction> drained;

  @override
  ConsumerState<_OwnerScreen> createState() => _OwnerScreenState();
}

class _OwnerScreenState extends ConsumerState<_OwnerScreen> {
  @override
  Widget build(BuildContext context) {
    // Production pattern (see tasks_screen / expenses_screen): drain on every
    // build (cold start) AND re-drain when the provider changes while we're
    // alive (warm tap).
    drainPendingAction(
      ref,
      mine: widget.mine,
      isMounted: () => mounted,
      onDrained: widget.drained.add,
    );
    ref.listen<AppAction?>(pendingActionProvider, (_, next) {
      drainPendingAction(
        ref,
        mine: widget.mine,
        isMounted: () => mounted,
        onDrained: widget.drained.add,
      );
    });
    return const SizedBox.shrink();
  }
}

void main() {
  group('drainPendingAction', () {
    testWidgets(
        'COLD START: action set BEFORE the owning screen is built is still '
        'drained once the screen mounts', (tester) async {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final drained = <AppAction>[];

      // Simulate the cold-start ordering: the deep link fires first…
      container.read(pendingActionProvider.notifier).state = AppAction.addTask;

      // …THEN the destination screen mounts (router.go built it).
      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: MaterialApp(
            home: _OwnerScreen(
              mine: const {AppAction.addTask, AppAction.newNote},
              drained: drained,
            ),
          ),
        ),
      );
      await tester.pump(); // let the post-frame drain run

      expect(drained, [AppAction.addTask]);
      // Provider cleared so no other screen / rebuild re-fires it.
      expect(container.read(pendingActionProvider), isNull);
    });

    testWidgets(
        'WARM TAP: action set while the screen is already alive is drained',
        (tester) async {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final drained = <AppAction>[];

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: MaterialApp(
            home: _OwnerScreen(
              mine: const {AppAction.addExpense},
              drained: drained,
            ),
          ),
        ),
      );
      await tester.pump();
      expect(drained, isEmpty); // nothing pending yet

      // Warm widget tap arrives now.
      container.read(pendingActionProvider.notifier).state =
          AppAction.addExpense;
      await tester.pump(); // rebuild from the provider change
      await tester.pump(); // post-frame drain

      expect(drained, [AppAction.addExpense]);
      expect(container.read(pendingActionProvider), isNull);
    });

    testWidgets('a foreign action is left untouched for its owner',
        (tester) async {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final drained = <AppAction>[];

      // Expenses-owner screen, but a Tasks action is pending.
      container.read(pendingActionProvider.notifier).state = AppAction.addTask;

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: MaterialApp(
            home: _OwnerScreen(
              mine: const {AppAction.addExpense},
              drained: drained,
            ),
          ),
        ),
      );
      await tester.pump();

      expect(drained, isEmpty);
      // Untouched — the Tasks screen will drain it.
      expect(container.read(pendingActionProvider), AppAction.addTask);
    });

    testWidgets(
        'two owning screens alive at once: exactly one drains the action '
        '(no double-open)', (tester) async {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final a = <AppAction>[];
      final b = <AppAction>[];

      container.read(pendingActionProvider.notifier).state = AppAction.addTask;

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: MaterialApp(
            home: Column(
              children: [
                _OwnerScreen(mine: const {AppAction.addTask}, drained: a),
                _OwnerScreen(mine: const {AppAction.addTask}, drained: b),
              ],
            ),
          ),
        ),
      );
      await tester.pump();

      // The atomic take-at-drain-time guarantees only one consumer wins.
      expect(a.length + b.length, 1);
      expect(container.read(pendingActionProvider), isNull);
    });

    testWidgets('does nothing when no action is pending', (tester) async {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final drained = <AppAction>[];

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: MaterialApp(
            home: _OwnerScreen(
              mine: const {AppAction.addTask},
              drained: drained,
            ),
          ),
        ),
      );
      await tester.pump();

      expect(drained, isEmpty);
      expect(container.read(pendingActionProvider), isNull);
    });
  });
}
