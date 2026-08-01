/// The escalation gate.
///
/// Under streaming speech there is a specific way to be badly wrong: speak a
/// complete local answer, then decide the turn needed the cloud and speak a
/// different one. The user hears two contradictory replies.
///
/// The design makes that structurally impossible rather than merely unlikely:
/// local speech starts DISARMED and is held until the reply is proven not to be
/// the escalation sentinel, so escalation is only reachable on a turn where
/// nothing was ever spoken. These tests pin that invariant.
library;

import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/assistant/assistant_backend_mode.dart';
import 'package:lazyclaw_mobile/assistant/cloud_turn_client.dart';
import 'package:lazyclaw_mobile/assistant/lazy_assistant_controller.dart';
import 'package:lazyclaw_mobile/assistant/speech_queue.dart';
import 'package:lazyclaw_mobile/local_ai/local_llm_engine.dart';

class _RecordingSpeaker implements Speaker {
  final List<String> spoken = [];
  @override
  Future<void> awaitSpeakCompletion(bool value) async {}
  @override
  Future<void> setSpeechRate(double rate) async {}
  @override
  Future<bool> isLanguageAvailable(String tag) async => true;
  @override
  Future<void> setLanguage(String tag) async {}
  @override
  Future<void> speak(String text) async => spoken.add(text);
  @override
  Future<void> stop() async {}
}

/// Yields fixed chunks, so a sentinel split across token boundaries can be
/// reproduced exactly — that is the case a naive prefix check gets wrong.
class _ChunkEngine implements LocalLlmEngine {
  _ChunkEngine(this.chunks);
  final List<String> chunks;

  @override
  bool get isLoaded => true;
  @override
  String? get loadedModelId => 'fake';
  @override
  Future<void> load(String id, String path) async {}
  @override
  Future<void> unload() async {}
  @override
  Future<void> cancel() async {}
  @override
  Stream<String> generate(
    List<LocalLlmMessage> m, {
    String? systemPrompt,
    LocalGenOptions options = const LocalGenOptions(),
  }) async* {
    for (final c in chunks) {
      yield c;
    }
  }
}

class _FakeCloud implements CloudTurns {
  _FakeCloud(this.reply);
  final String reply;
  int calls = 0;
  @override
  Stream<String> streamTurn(String t) async* {
    calls++;
    yield reply;
  }
}

Future<void> _settle() async {
  for (var i = 0; i < 15; i++) {
    await Future<void>.delayed(Duration.zero);
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  LazyAssistantController build(
    _ChunkEngine engine,
    _FakeCloud cloud,
    _RecordingSpeaker speaker,
  ) =>
      LazyAssistantController(
        engine,
        cloud,
        () => AssistantBackendMode.preferOnDevice,
        () => false, // cloud allowed
        speaker: speaker,
        ensureCloud: () async => true,
      );

  test('sentinel reply escalates and speaks NOTHING locally', () async {
    final engine = _ChunkEngine(['[[NEEDS_CLOUD]]']);
    final cloud = _FakeCloud('The cloud answer. ');
    final speaker = _RecordingSpeaker();
    final c = build(engine, cloud, speaker);

    await c.askForTest('what is the weather right now');
    await _settle();

    expect(cloud.calls, 1, reason: 'it must escalate');
    expect(speaker.spoken.any((s) => s.contains('NEEDS_CLOUD')), isFalse,
        reason: 'the sentinel must never be read aloud');
    expect(speaker.spoken, ['The cloud answer.'],
        reason: 'only the cloud reply is ever spoken');
  });

  test('sentinel split across chunks still holds the gate', () async {
    // A naive check on the first chunk alone would arm speech here.
    final engine = _ChunkEngine(['[[', 'NEEDS_', 'CLOUD]]']);
    final cloud = _FakeCloud('Cloud reply. ');
    final speaker = _RecordingSpeaker();
    final c = build(engine, cloud, speaker);

    await c.askForTest('q');
    await _settle();

    expect(cloud.calls, 1);
    expect(speaker.spoken, ['Cloud reply.']);
  });

  test('a reply that merely MENTIONS the sentinel is spoken, not escalated',
      () async {
    // The contract is prefix-only. A `contains` test would throw away this
    // perfectly good answer and replace it with a cloud round trip.
    final engine = _ChunkEngine(
        ['I cannot check that. ', 'The marker is [[NEEDS_CLOUD]] by the way. ']);
    final cloud = _FakeCloud('should not be used');
    final speaker = _RecordingSpeaker();
    final c = build(engine, cloud, speaker);

    await c.askForTest('q');
    await _settle();

    expect(cloud.calls, 0, reason: 'mid-reply mention must not escalate');
    expect(speaker.spoken.first, 'I cannot check that.');
  });

  test('a normal local reply is spoken and never escalates', () async {
    final engine = _ChunkEngine(['Madrid is the capital. ']);
    final cloud = _FakeCloud('unused');
    final speaker = _RecordingSpeaker();
    final c = build(engine, cloud, speaker);

    await c.askForTest('capital of spain');
    await _settle();

    expect(cloud.calls, 0);
    expect(speaker.spoken, ['Madrid is the capital.']);
  });

  test('nothing is ever spoken twice across the escalation boundary', () async {
    final engine = _ChunkEngine(['[[NEEDS_CLOUD]]']);
    final cloud = _FakeCloud('One. Two. ');
    final speaker = _RecordingSpeaker();
    final c = build(engine, cloud, speaker);

    await c.askForTest('q');
    await _settle();

    expect(speaker.spoken.toSet().length, speaker.spoken.length,
        reason: 'a duplicate utterance means the same text was queued twice');
  });
}
