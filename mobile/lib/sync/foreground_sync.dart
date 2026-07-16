import 'dart:async';

import 'package:flutter/widgets.dart';

/// Keeps offline-first data fresh while the app is in the FOREGROUND.
///
/// Background refresh (app closed) is handled separately by the Workmanager
/// job. This fills the gap while the app is OPEN: it re-arms a periodic timer
/// that fires [onSync] every [interval], and also syncs immediately whenever
/// the app returns to the foreground (`resumed`). When the app leaves the
/// foreground (paused/inactive/detached/hidden) the timer is cancelled so no
/// work runs while backgrounded.
///
/// This scheduler owns NO sync logic of its own — it just invokes the injected
/// [onSync] callback. The caller wires that to the task/note/budget providers.
class ForegroundSyncScheduler with WidgetsBindingObserver {
  ForegroundSyncScheduler({
    required Future<void> Function() onSync,
    Duration interval = const Duration(minutes: 30),
  })  : _onSync = onSync,
        _interval = interval;

  final Future<void> Function() _onSync;
  final Duration _interval;

  Timer? _timer;

  /// Registers the lifecycle observer and arms the periodic timer.
  void start() {
    WidgetsBinding.instance.addObserver(this);
    _arm();
  }

  /// Removes the lifecycle observer and cancels the periodic timer.
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _timer?.cancel();
    _timer = null;
  }

  /// (Re)starts the periodic timer, cancelling any existing one first.
  void _arm() {
    _timer?.cancel();
    _timer = Timer.periodic(_interval, (_) => _onSync());
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      debugPrint(
        'ForegroundSync: app resumed — triggering sync + re-arming '
        '${_interval.inMinutes}m timer',
      );
      _onSync();
      _arm();
    } else {
      debugPrint(
        'ForegroundSync: app $state — cancelling periodic sync timer',
      );
      _timer?.cancel();
    }
  }
}
