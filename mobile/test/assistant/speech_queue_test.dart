/// The fake here deliberately reproduces flutter_tts's two Android
/// pathologies, because a polite fake would green-light both production bugs:
///
///   1. entering speak() while another utterance is in flight THROWS, so an
///      unserialized queue fails loudly here instead of silently dropping
///      audio the way the real plugin does;
///   2. an "engine error" is a future that NEVER completes, because that is
///      literally what the plugin does — a fake that threw instead would
///      green-light a queue with no watchdog and ship the production hang.
library;

import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/assistant/speech_queue.dart';

class _RecordingSpeaker implements Speaker {
  _RecordingSpeaker({
    this.delay = const Duration(milliseconds: 10),
    this.throwOn = const {},
    this.hangOn = const {},
    this.languageAvailable = true,
  });

  final Duration delay;

  /// Utterances the engine reports as failed.
  final Set<String> throwOn;

  /// Utterances whose future never completes — the real error shape.
  final Set<String> hangOn;
  final bool languageAvailable;

  final List<String> spoken = [];
  final List<String> languages = [];
  int stopCalls = 0;
  bool _inFlight = false;

  @override
  Future<void> awaitSpeakCompletion(bool value) async {}
  @override
  Future<void> setSpeechRate(double rate) async {}
  @override
  Future<bool> isLanguageAvailable(String tag) async => languageAvailable;
  @override
  Future<void> setLanguage(String tag) async => languages.add(tag);

  @override
  Future<void> speak(String text) async {
    if (_inFlight) {
      throw StateError(
          'overlapping speak("$text") — the real plugin would DROP this');
    }
    _inFlight = true;
    spoken.add(text);
    try {
      if (hangOn.contains(text)) {
        await Completer<void>().future; // never completes
      }
      await Future<void>.delayed(delay);
      if (throwOn.contains(text)) throw const SpeakException('engine failed');
    } finally {
      _inFlight = false;
    }
  }

  @override
  Future<void> stop() async {
    stopCalls++;
    _inFlight = false;
  }
}

void main() {
  test('serializes utterances — never overlaps', () async {
    final tts = _RecordingSpeaker();
    final q = SpeechQueue(tts);
    await q.init();

    q..add('A', q.epoch)..add('B', q.epoch)..add('C', q.epoch);
    await q.idle;

    expect(tts.spoken, ['A', 'B', 'C']);
  });

  test('barge-in drops the rest of the queue', () async {
    final tts = _RecordingSpeaker(delay: const Duration(milliseconds: 40));
    final q = SpeechQueue(tts);
    await q.init();

    q..add('A', q.epoch)..add('B', q.epoch)..add('C', q.epoch);
    await Future<void>.delayed(const Duration(milliseconds: 10));
    await q.stop();
    await q.idle;

    expect(tts.spoken, ['A'], reason: 'B and C must never be spoken');
    expect(tts.stopCalls, greaterThanOrEqualTo(1));
  });

  test('one failed utterance does not kill the queue', () async {
    final tts = _RecordingSpeaker(throwOn: {'B'});
    final q = SpeechQueue(tts);
    final errors = <Object>[];
    q.onError = errors.add;
    await q.init();

    q..add('A', q.epoch)..add('B', q.epoch)..add('C', q.epoch);
    await q.idle;

    expect(tts.spoken, ['A', 'B', 'C']);
    expect(errors, hasLength(1));
  });

  test('a hung utterance is watchdogged and the queue continues', () async {
    // The production shape: FlutterTtsPlugin never resolves speakResult on an
    // engine error, so without a watchdog the assistant goes silent forever.
    final tts = _RecordingSpeaker(hangOn: {'B'});
    final q = SpeechQueue(tts);
    await q.init();

    q..add('A', q.epoch)..add('B', q.epoch)..add('C', q.epoch);
    await q.idle.timeout(const Duration(seconds: 20));

    expect(tts.spoken, ['A', 'B', 'C'], reason: 'C must still be reached');
    expect(tts.stopCalls, greaterThanOrEqualTo(1),
        reason: 'stop() is what releases the wedged future');
  });

  test('an empty reply speaks nothing and settles immediately', () async {
    final tts = _RecordingSpeaker();
    final q = SpeechQueue(tts);
    await q.init();

    q..add('', q.epoch)..add('   ', q.epoch);
    await q.idle;

    expect(tts.spoken, isEmpty);
  });

  test('a stale epoch is ignored', () async {
    final tts = _RecordingSpeaker();
    final q = SpeechQueue(tts);
    await q.init();

    final stale = q.epoch;
    await q.stop(); // bumps the epoch
    q.add('late arrival', stale);
    await q.idle;

    expect(tts.spoken, isEmpty);
  });

  test('dispose mid-queue stops speaking and ignores later adds', () async {
    final tts = _RecordingSpeaker(delay: const Duration(milliseconds: 40));
    final q = SpeechQueue(tts);
    await q.init();

    q..add('A', q.epoch)..add('B', q.epoch);
    await Future<void>.delayed(const Duration(milliseconds: 10));
    await q.dispose();
    q.add('C', q.epoch);
    await Future<void>.delayed(const Duration(milliseconds: 80));

    expect(tts.spoken, ['A']);
  });

  test('onUtteranceStart fires for the first utterance', () async {
    final tts = _RecordingSpeaker();
    final q = SpeechQueue(tts);
    var starts = 0;
    q.onUtteranceStart = () => starts++;
    await q.init();

    q..add('A', q.epoch)..add('B', q.epoch);
    await q.idle;

    expect(starts, 2);
  });

  group('language selection', () {
    test('sets the language when available', () async {
      final tts = _RecordingSpeaker();
      final q = SpeechQueue(tts);
      expect(await q.useLanguage('es-ES'), isTrue);
      expect(tts.languages, ['es-ES']);
    });

    test('reports false when unavailable, and does not set it', () async {
      // Without this check a reply in an uninstalled language hits the engine
      // error path — which is the shape that hangs forever.
      final tts = _RecordingSpeaker(languageAvailable: false);
      final q = SpeechQueue(tts);
      expect(await q.useLanguage('ka-GE'), isFalse);
      expect(tts.languages, isEmpty);
    });
  });
}
