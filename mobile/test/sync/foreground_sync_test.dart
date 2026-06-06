import 'package:fake_async/fake_async.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/sync/foreground_sync.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('fires onSync every interval while running', () {
    fakeAsync((async) {
      var n = 0;
      final s = ForegroundSyncScheduler(
        onSync: () async => n++, interval: const Duration(minutes: 30));
      s.start();
      async.elapse(const Duration(minutes: 91));
      expect(n, 3);
      s.dispose();
    });
  });

  test('syncs on resume and cancels timer on pause', () {
    fakeAsync((async) {
      var n = 0;
      final s = ForegroundSyncScheduler(onSync: () async => n++);
      s.start();
      s.didChangeAppLifecycleState(AppLifecycleState.paused);
      async.elapse(const Duration(minutes: 60));
      expect(n, 0);
      s.didChangeAppLifecycleState(AppLifecycleState.resumed);
      expect(n, 1);
      s.dispose();
    });
  });
}
