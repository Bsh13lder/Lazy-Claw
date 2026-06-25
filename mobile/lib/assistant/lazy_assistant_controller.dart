/// "Hey Lazy" voice assistant — fully on-device.
///
/// Pipeline: microphone → speech-to-text (platform `SpeechRecognizer`) → the
/// on-device LLM ([LocalLlmEngine]) → text-to-speech (platform `TextToSpeech`).
/// Nothing leaves the phone. The screen drives this controller; model loading is
/// handled by the existing local-AI controller (the screen ensures a model is
/// ready before letting the user talk).
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:speech_to_text/speech_recognition_result.dart';
import 'package:speech_to_text/speech_to_text.dart' as stt;

import '../local_ai/local_ai_providers.dart';
import '../local_ai/local_llm_engine.dart';

enum AssistantPhase { idle, listening, thinking, speaking, error }

class AssistantState {
  const AssistantState({
    this.phase = AssistantPhase.idle,
    this.transcript = '',
    this.response = '',
    this.error,
  });

  final AssistantPhase phase;
  final String transcript; // what the user said
  final String response; // Lazy's reply (streams in while thinking)
  final String? error;

  bool get isBusy =>
      phase == AssistantPhase.listening ||
      phase == AssistantPhase.thinking ||
      phase == AssistantPhase.speaking;

  AssistantState copyWith({
    AssistantPhase? phase,
    String? transcript,
    String? response,
    String? error,
    bool clearError = false,
  }) =>
      AssistantState(
        phase: phase ?? this.phase,
        transcript: transcript ?? this.transcript,
        response: response ?? this.response,
        error: clearError ? null : (error ?? this.error),
      );
}

class LazyAssistantController extends StateNotifier<AssistantState> {
  LazyAssistantController(this._engine) : super(const AssistantState());

  final LocalLlmEngine _engine;
  final stt.SpeechToText _stt = stt.SpeechToText();
  final FlutterTts _tts = FlutterTts();
  bool _sttReady = false;

  static const String _system =
      'You are Lazy, a helpful voice assistant. Give a direct, useful, concise answer '
      'that sounds natural read aloud. Do NOT use emojis, asterisks, stage directions '
      '(like *smiles*), markdown, headings or bullet lists — just say the answer plainly. '
      'Reply in the same language as the user.';

  /// Strips roleplay actions (*smiles*), emojis and markdown so the spoken (and
  /// shown) reply is clean text — not "asterisk smiles asterisk".
  static String _clean(String text) {
    var t = text;
    t = t.replaceAll(RegExp(r'\*[^*\n]*\*'), ' '); // *smiles*, **bold**
    t = t.replaceAll(
        RegExp(
            r'[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2190}-\u{21FF}\u{2B00}-\u{2BFF}\u{FE0F}]',
            unicode: true),
        '');
    t = t.replaceAll(RegExp(r'[#`_>]'), '');
    return t.replaceAll(RegExp(r'[ \t]+'), ' ').replaceAll(RegExp(r' *\n *'), '\n').trim();
  }

  /// Tap the mic: start listening, or stop (finish talking) if already listening.
  Future<void> toggleListen() async {
    if (state.phase == AssistantPhase.listening) {
      await finishListening();
    } else {
      await startListening();
    }
  }

  Future<void> startListening() async {
    if (!_engine.isLoaded) {
      state = state.copyWith(
        phase: AssistantPhase.error,
        error: 'Load a local model first (Settings → Local AI).',
      );
      return;
    }
    await _tts.stop();
    if (!_sttReady) {
      _sttReady = await _stt.initialize(
        onError: (e) => _fail("I didn't catch that"),
        onStatus: (_) {},
      );
    }
    if (!_sttReady) {
      _fail('Speech recognition is unavailable on this device');
      return;
    }
    state = const AssistantState(phase: AssistantPhase.listening);
    await _stt.listen(
      onResult: _onSpeech,
      listenOptions: stt.SpeechListenOptions(
        partialResults: true,
        cancelOnError: true,
      ),
      listenFor: const Duration(seconds: 30),
      pauseFor: const Duration(seconds: 3),
    );
  }

  /// The "Done" action — stop capturing and use whatever was heard.
  Future<void> finishListening() async {
    await _stt.stop();
  }

  Future<void> stopSpeaking() async {
    await _tts.stop();
    state = state.copyWith(phase: AssistantPhase.idle);
  }

  void _onSpeech(SpeechRecognitionResult r) {
    if (r.finalResult) {
      _ask(r.recognizedWords);
    } else if (state.phase == AssistantPhase.listening) {
      state = state.copyWith(transcript: r.recognizedWords);
    }
  }

  Future<void> _ask(String text) async {
    final prompt = text.trim();
    if (prompt.isEmpty) {
      state = const AssistantState(phase: AssistantPhase.idle);
      return;
    }
    state = AssistantState(phase: AssistantPhase.thinking, transcript: prompt);
    final buf = StringBuffer();
    try {
      await for (final tok in _engine.generate(
        [LocalLlmMessage.user(prompt)],
        systemPrompt: _system,
      )) {
        buf.write(tok);
        state = AssistantState(
          phase: AssistantPhase.thinking,
          transcript: prompt,
          response: buf.toString(),
        );
      }
    } catch (e) {
      state = AssistantState(
        phase: AssistantPhase.error,
        transcript: prompt,
        error: 'Something went wrong: $e',
      );
      return;
    }
    final reply = _clean(buf.toString());
    if (reply.isEmpty) {
      state = AssistantState(phase: AssistantPhase.idle, transcript: prompt);
      return;
    }
    state = AssistantState(
      phase: AssistantPhase.speaking,
      transcript: prompt,
      response: reply,
    );
    await _speak(reply);
    state = AssistantState(
      phase: AssistantPhase.idle,
      transcript: prompt,
      response: reply,
    );
  }

  Future<void> _speak(String text) async {
    try {
      await _tts.awaitSpeakCompletion(true);
      await _tts.setSpeechRate(0.5);
      await _tts.speak(text);
    } catch (_) {/* best effort */}
  }

  void _fail(String message) {
    state = state.copyWith(phase: AssistantPhase.error, error: message);
  }

  void reset() => state = const AssistantState();

  @override
  void dispose() {
    _stt.cancel();
    _tts.stop();
    super.dispose();
  }
}

/// One controller for the app's lifetime, sharing the on-device engine with the
/// local-chat feature (one model in memory).
final lazyAssistantProvider =
    StateNotifierProvider<LazyAssistantController, AssistantState>((ref) {
  final c = LazyAssistantController(ref.watch(localLlmEngineProvider));
  ref.onDispose(c.dispose);
  return c;
});
