/// The headline regression: audio must start while generation is still running.
///
/// Before this, `_finishTurn` ran only after the stream closed, so
/// time-to-first-audio was the entire generation — up to minutes on a phone.
/// The first test here fails on that implementation.
library;

import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/assistant/assistant_backend_mode.dart';
import 'package:lazyclaw_mobile/assistant/cloud_turn_client.dart';
import 'package:lazyclaw_mobile/assistant/lazy_assistant_controller.dart';
import 'package:lazyclaw_mobile/assistant/speech_queue.dart';
import 'package:lazyclaw_mobile/local_ai/local_llm_engine.dart';

/// Records the exact sequence of spoken utterances, and reproduces the two
/// flutter_tts pathologies so an unserialised or unwatchdogged queue fails here
/// rather than in the user's hand.
class _RecordingSpeaker implements Speaker {
  final List<String> spoken = [];
  int stopCalls = 0;
  bool _inFlight = false;

  @override
  Future<void> awaitSpeakCompletion(bool value) async {}
  @override
  Future<void> setSpeechRate(double rate) async {}
  @override
  Future<bool> isLanguageAvailable(String tag) async => true;
  @override
  Future<void> setLanguage(String tag) async {}

  @override
  Future<void> speak(String text) async {
    if (_inFlight) {
      throw StateError('overlapping speak("$text") — production would DROP it');
    }
    _inFlight = true;
    spoken.add(text);
    await Future<void>.delayed(const Duration(milliseconds: 1));
    _inFlight = false;
  }

  @override
  Future<void> stop() async {
    stopCalls++;
    _inFlight = false;
  }
}

/// Engine driven by the test, so the reply can be held open mid-generation.
class _DrivenEngine implements LocalLlmEngine {
  final StreamController<String> controller = StreamController<String>();
  int cancelCount = 0;

  @override
  bool get isLoaded => true;
  @override
  String? get loadedModelId => 'fake';
  @override
  Future<void> load(String id, String path) async {}
  @override
  Future<void> unload() async {}
  @override
  Future<void> cancel() async => cancelCount++;
  @override
  Stream<String> generate(
    List<LocalLlmMessage> m, {
    String? systemPrompt,
    LocalGenOptions options = const LocalGenOptions(),
  }) =>
      controller.stream;
}

class _UnusedCloud implements CloudTurns {
  @override
  Stream<String> streamTurn(String t) => const Stream<String>.empty();
}

LazyAssistantController _build(_DrivenEngine engine, _RecordingSpeaker speaker) =>
    LazyAssistantController(
      engine,
      _UnusedCloud(),
      () => AssistantBackendMode.onlyOnDevice,
      () => true,
      speaker: speaker,
    );

Future<void> _settle() async {
  for (var i = 0; i < 12; i++) {
    await Future<void>.delayed(Duration.zero);
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('speaks the first sentence BEFORE generation finishes', () async {
    final engine = _DrivenEngine();
    final speaker = _RecordingSpeaker();
    final c = _build(engine, speaker);

    unawaited(c.askForTest('what is the capital'));
    await _settle();

    // Emit a complete first sentence plus a following word, so the segmenter's
    // mandatory lookahead is satisfied and it can cut.
    engine.controller.add('Sure, the capital is Madrid. ');
    engine.controller.add('It ');
    await _settle();

    expect(engine.controller.isClosed, isFalse,
        reason: 'generation must still be open — that is the whole point');
    expect(speaker.spoken, isNotEmpty,
        reason: 'audio must start before the reply is complete');
    expect(speaker.spoken.first, 'Sure, the capital is Madrid.');

    await engine.controller.close();
    await _settle();
  });

  test('markdown is cleaned per sentence, identically across chunk splits',
      () async {
    Future<List<String>> run(List<String> chunks) async {
      final engine = _DrivenEngine();
      final speaker = _RecordingSpeaker();
      final c = _build(engine, speaker);
      // Await the turn itself rather than a fixed number of microtask yields:
      // under full-suite load a yield count is a race, not a wait.
      final turn = c.askForTest('q');
      await _settle();
      for (final ch in chunks) {
        engine.controller.add(ch);
      }
      await engine.controller.close();
      await turn;
      return speaker.spoken;
    }

    final whole = await run(['Sure **thing**. Done here. ']);
    final split = await run(['Sure **th', 'ing**. Done ', 'here. ']);

    expect(whole.first, 'Sure thing.');
    expect(split, whole,
        reason: 'a chunk boundary inside markdown must not change the output');
  });

  test('an empty reply speaks nothing and still settles', () async {
    final engine = _DrivenEngine();
    final speaker = _RecordingSpeaker();
    final c = _build(engine, speaker);

    final turn = c.askForTest('q');
    await _settle();
    await engine.controller.close();
    await turn;

    expect(speaker.spoken, isEmpty);
  });

  test('barge-in cancels generation as well as speech', () async {
    final engine = _DrivenEngine();
    final speaker = _RecordingSpeaker();
    final c = _build(engine, speaker);

    unawaited(c.askForTest('q'));
    await _settle();
    engine.controller.add('First sentence here. ');
    await _settle();

    await c.stopSpeaking();
    await _settle();

    expect(engine.cancelCount, greaterThan(0),
        reason: 'an abandoned turn must not keep the CPU busy to its cap');
    expect(speaker.stopCalls, greaterThan(0));

    // Tokens from the superseded turn must not resurface on screen.
    final before = c.debugState.response;
    engine.controller.add('Late token that should be ignored. ');
    await _settle();
    expect(c.debugState.response, before);

    await engine.controller.close();
  });
}
