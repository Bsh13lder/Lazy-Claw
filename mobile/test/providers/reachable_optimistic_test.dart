import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/sync/reachability.dart';

/// A Reachability whose initial probe never resolves and whose stream never
/// emits — models the real boot window before the first host ping comes back.
/// Its [value] stays at the pessimistic pre-probe default of `false`; the
/// notifier must IGNORE it and start optimistic so the UI doesn't flash
/// "offline" (and fire a spurious false→true sync) at launch.
class _StuckReachability implements Reachability {
  final StreamController<bool> _controller = StreamController<bool>.broadcast();

  @override
  bool get value => false; // pre-probe pessimistic default

  @override
  Stream<bool> get reachable => _controller.stream; // never emits

  @override
  Future<void> start() => Completer<void>().future; // never completes

  @override
  Future<bool> refresh() => Completer<bool>().future; // never completes

  @override
  Future<void> dispose() async {
    if (!_controller.isClosed) await _controller.close();
  }
}

void main() {
  test('reachableProvider is optimistically true before the first probe',
      () async {
    final fake = _StuckReachability();
    final container = ProviderContainer(
      overrides: [reachabilityProvider.overrideWithValue(fake)],
    );
    addTearDown(container.dispose);
    addTearDown(fake.dispose);

    // No probe has resolved and the stream has emitted nothing, yet the
    // notifier must report reachable=true so the app boots online-first.
    expect(container.read(reachableProvider), isTrue);
  });
}
