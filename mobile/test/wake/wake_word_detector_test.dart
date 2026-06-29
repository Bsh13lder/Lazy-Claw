import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/wake/wake_event.dart';
import 'package:lazyclaw_mobile/wake/wake_recognizer.dart';
import 'package:lazyclaw_mobile/wake/wake_word_detector.dart';

class _FakeRecognizer implements WakeRecognizer {
  final _ctrl = StreamController<String>.broadcast();
  bool started = false;
  void emit(String json) => _ctrl.add(json);
  @override
  Stream<String> get results => _ctrl.stream;
  @override
  Future<void> start() async => started = true;
  @override
  Future<void> stop() async => started = false;
}

void main() {
  test('fires a WakeEvent on "hey lazy", ignores other phrases', () async {
    final rec = _FakeRecognizer();
    final det = WakeWordDetector(rec);
    final got = <WakeEvent>[];
    final sub = det.wakes.listen(got.add);
    await det.start();

    rec.emit('{"text": "what time is it"}');
    rec.emit('{"text": "hey lazy"}');
    await Future<void>.delayed(Duration.zero);

    expect(got.length, 1);
    await sub.cancel();
  });

  test('debounces repeated detections within the window', () async {
    var t = DateTime(2026, 6, 26, 12, 0, 0);
    final rec = _FakeRecognizer();
    final det = WakeWordDetector(rec,
        debounce: const Duration(seconds: 2), clock: () => t);
    final got = <WakeEvent>[];
    final sub = det.wakes.listen(got.add);
    await det.start();

    rec.emit('{"text": "hey lazy"}'); // fires
    await Future<void>.delayed(Duration.zero);
    t = t.add(const Duration(milliseconds: 500));
    rec.emit('{"text": "hey lazy"}'); // within 2s → ignored
    await Future<void>.delayed(Duration.zero);
    t = t.add(const Duration(seconds: 3));
    rec.emit('{"text": "hey lazy"}'); // after window → fires
    await Future<void>.delayed(Duration.zero);

    expect(got.length, 2);
    await sub.cancel();
  });

  test('matches phrase case-insensitively and trims surrounding speech', () async {
    final rec = _FakeRecognizer();
    final det = WakeWordDetector(rec);
    final got = <WakeEvent>[];
    final sub = det.wakes.listen(got.add);
    await det.start();
    rec.emit('{"text": "  Hey Lazy  "}');
    await Future<void>.delayed(Duration.zero);
    expect(got.length, 1);
    await sub.cancel();
  });
}
