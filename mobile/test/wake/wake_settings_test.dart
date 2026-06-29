import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/wake/wake_service.dart';
import 'package:lazyclaw_mobile/wake/wake_settings.dart';

class _FakeService implements WakeService {
  bool running = false;
  int starts = 0, stops = 0;
  @override
  Future<bool> start() async {
    running = true;
    starts++;
    return true;
  }

  @override
  Future<void> stop() async {
    running = false;
    stops++;
  }

  @override
  Future<bool> isRunning() async => running;
}

class _MemStore implements WakeStore {
  String? v;
  @override
  Future<String?> read() async => v;
  @override
  Future<void> write(String value) async => v = value;
}

void main() {
  test('enabling starts the service and persists; disabling stops it', () async {
    final svc = _FakeService();
    final store = _MemStore();
    final c = WakeEnabledController(svc, store);

    await c.set(true);
    expect(c.debugState, true);
    expect(svc.starts, 1);
    expect(store.v, 'true');

    await c.set(false);
    expect(c.debugState, false);
    expect(svc.stops, 1);
    expect(store.v, 'false');
  });

  test('restores persisted enabled state', () async {
    final store = _MemStore()..v = 'true';
    final svc = _FakeService();
    final c = WakeEnabledController(svc, store);
    await c.restore();
    expect(c.debugState, true);
  });

  test('reverts to off when the service cannot arm', () async {
    final store = _MemStore();
    final c = WakeEnabledController(_DenyService(), store);
    await c.set(true);
    expect(c.debugState, false);
  });
}

class _DenyService implements WakeService {
  @override
  Future<bool> start() async => false;
  @override
  Future<void> stop() async {}
  @override
  Future<bool> isRunning() async => false;
}
