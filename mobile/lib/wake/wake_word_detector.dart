/// Turns a stream of Vosk recognition results into debounced [WakeEvent]s when
/// the configured wake phrase is heard. Pure logic — no audio, no platform.
library;

import 'dart:async';

import 'wake_event.dart';
import 'wake_recognizer.dart';

class WakeWordDetector {
  WakeWordDetector(
    this._recognizer, {
    String phrase = 'hey lazy',
    Duration debounce = const Duration(seconds: 2),
    DateTime Function() clock = DateTime.now,
  })  : _phrase = phrase.toLowerCase().trim(),
        _debounce = debounce,
        _clock = clock;

  final WakeRecognizer _recognizer;
  final String _phrase;
  final Duration _debounce;
  final DateTime Function() _clock;

  final _wakes = StreamController<WakeEvent>.broadcast();
  StreamSubscription<String>? _sub;
  DateTime? _lastFired;

  Stream<WakeEvent> get wakes => _wakes.stream;

  Future<void> start() async {
    _sub ??= _recognizer.results.listen(_onResult);
    await _recognizer.start();
  }

  Future<void> stop() async {
    await _recognizer.stop();
    await _sub?.cancel();
    _sub = null;
  }

  void _onResult(String json) {
    final text = parseVoskText(json)?.toLowerCase().trim();
    if (text == null || !text.contains(_phrase)) return;
    final now = _clock();
    if (_lastFired != null && now.difference(_lastFired!) < _debounce) return;
    _lastFired = now;
    _wakes.add(WakeEvent(now));
  }

  Future<void> dispose() async {
    await stop();
    await _wakes.close();
  }
}
