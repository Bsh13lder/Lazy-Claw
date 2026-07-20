import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/sync/reachability.dart';

/// Fully controllable probe: drive the OS-link stream + pin the host-ping
/// result. Lets us assert that "internet up but host down" reads as offline.
class _FakeProbe implements ConnectivityProbe {
  final _ctrl = StreamController<bool>.broadcast();
  bool link;
  bool host;
  int pingCount = 0;

  _FakeProbe({this.link = true, this.host = true});

  @override
  Stream<bool> get onChanged => _ctrl.stream;

  @override
  Future<bool> hasLink() async => link;

  @override
  Future<bool> pingHost() async {
    pingCount++;
    return host;
  }

  void emit() => _ctrl.add(link);
  Future<void> close() => _ctrl.close();
}

void main() {
  test('reachable only when link is up AND host ping succeeds', () async {
    final probe = _FakeProbe(link: true, host: true);
    final r = Reachability(probe);
    await r.start();
    expect(r.value, isTrue);
    await r.dispose();
  });

  test('internet up but host down → NOT reachable', () async {
    final probe = _FakeProbe(link: true, host: false);
    final r = Reachability(probe);
    await r.start();
    expect(r.value, isFalse);
    await r.dispose();
  });

  test(
      'host ping is AUTHORITATIVE: reachable even when the OS reports no link '
      '(connectivity_plus false-negatives on some ROMs/hotspots must not '
      'strand the app offline while the server actually answers — 2026-07-20)',
      () async {
    final probe = _FakeProbe(link: false, host: true);
    final r = Reachability(probe);
    await r.start();
    expect(r.value, isTrue);
    expect(probe.pingCount, greaterThan(0));
    await r.dispose();
  });

  test('no OS link AND host down → offline', () async {
    final probe = _FakeProbe(link: false, host: false);
    final r = Reachability(probe);
    await r.start();
    expect(r.value, isFalse);
    await r.dispose();
  });

  test('emits on the false→true edge', () async {
    final probe = _FakeProbe(link: true, host: false);
    final r = Reachability(probe);
    await r.start();
    expect(r.value, isFalse);

    final transitions = <bool>[];
    final sub = r.reachable.listen(transitions.add);

    // Host comes back up; an OS connectivity event triggers a refresh.
    probe.host = true;
    probe.emit();
    await Future<void>.delayed(const Duration(milliseconds: 10));

    expect(transitions, contains(true));
    expect(r.value, isTrue);
    await sub.cancel();
    await r.dispose();
  });

  test('does not re-emit when value is unchanged', () async {
    final probe = _FakeProbe(link: true, host: true);
    final r = Reachability(probe);
    await r.start();

    final transitions = <bool>[];
    final sub = r.reachable.listen(transitions.add);

    probe.emit(); // still reachable → no new emission
    await Future<void>.delayed(const Duration(milliseconds: 10));
    expect(transitions, isEmpty);

    await sub.cancel();
    await r.dispose();
  });

  test('start() is idempotent', () async {
    final probe = _FakeProbe(link: true, host: true);
    final r = Reachability(probe);
    await r.start();
    final firstPings = probe.pingCount;
    await r.start(); // second call is a no-op
    expect(probe.pingCount, firstPings);
    await r.dispose();
  });
}
