/// [Speaker] backed by the flutter_tts plugin.
///
/// Kept apart from [SpeechQueue] so the queue stays a pure unit that can be
/// tested against a fake reproducing the plugin's real pathologies — silently
/// dropping an overlapping utterance, and never resolving its future on an
/// engine error.
library;

import 'package:flutter_tts/flutter_tts.dart';

import 'speech_queue.dart';

class FlutterTtsSpeaker implements Speaker {
  FlutterTtsSpeaker([FlutterTts? tts]) : _tts = tts ?? FlutterTts();

  final FlutterTts _tts;

  @override
  Future<void> awaitSpeakCompletion(bool value) =>
      _tts.awaitSpeakCompletion(value);

  @override
  Future<void> setSpeechRate(double rate) => _tts.setSpeechRate(rate);

  @override
  Future<bool> isLanguageAvailable(String languageTag) async {
    // The plugin returns a dynamic bool on Android and can throw for a
    // malformed tag; a language we can't confirm is treated as unavailable so
    // the caller falls back rather than hitting the engine's error path — which
    // is the shape that hangs forever.
    try {
      final result = await _tts.isLanguageAvailable(languageTag);
      return result == true;
    } catch (_) {
      return false;
    }
  }

  @override
  Future<void> setLanguage(String languageTag) => _tts.setLanguage(languageTag);

  @override
  Future<void> speak(String text) async {
    await _tts.speak(text);
  }

  @override
  Future<void> stop() => _tts.stop();
}
