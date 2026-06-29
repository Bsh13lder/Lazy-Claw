/// "Hey Lazy" tiered voice assistant.
///
/// Pipeline: mic → speech-to-text → tier router → reply → text-to-speech. Plain
/// chat stays on the on-device LLM ([LocalLlmEngine]); tool/action/internet
/// turns escalate to the real server ([CloudTurns]), gated by a first-hop
/// consent. The screen drives this controller and ensures a model is loaded.
library;

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:speech_to_text/speech_recognition_result.dart';
import 'package:speech_to_text/speech_to_text.dart' as stt;

import '../local_ai/local_ai_providers.dart';
import '../local_ai/local_llm_engine.dart';
import 'assistant_backend_mode.dart';
import 'assistant_router.dart';
import 'assistant_settings_providers.dart';
import 'assistant_state.dart';
import 'cloud_turn_client.dart';

// Re-export the view-state types so existing call-sites that import the
// controller keep resolving AssistantState/AssistantPhase/TurnSource.
export 'assistant_state.dart';

class LazyAssistantController extends StateNotifier<AssistantState> {
  LazyAssistantController(
    this._engine,
    this._cloud,
    this._readMode,
    this._readOnDeviceOnly, {
    AssistantRouter router = const AssistantRouter(),
    Future<bool> Function()? ensureCloud,
    Future<bool> Function()? ensureLocalModel,
    bool Function()? readConfirmCloud,
    bool Function()? readConsentGiven,
    void Function()? markConsentGiven,
  })  : _router = router,
        _ensureCloud = ensureCloud,
        _ensureLocalModel = ensureLocalModel,
        _readConfirmCloud = readConfirmCloud,
        _readConsentGiven = readConsentGiven,
        _markConsentGiven = markConsentGiven,
        super(const AssistantState());

  final LocalLlmEngine _engine;
  final CloudTurns _cloud;
  final AssistantBackendMode Function() _readMode;
  final bool Function() _readOnDeviceOnly;
  final AssistantRouter _router;
  final Future<bool> Function()? _ensureCloud;
  // Loads the on-device model on demand (it loads in parallel while the user is
  // still speaking) so listening never has to wait for the LLM to be resident.
  final Future<bool> Function()? _ensureLocalModel;
  // First-cloud-hop consent gate (Task 8). When confirm-cloud is on and the
  // user hasn't yet consented, the first cloud turn pauses for an explicit OK.
  final bool Function()? _readConfirmCloud;
  final bool Function()? _readConsentGiven;
  final void Function()? _markConsentGiven;
  final stt.SpeechToText _stt = stt.SpeechToText();
  final FlutterTts _tts = FlutterTts();
  bool _sttReady = false;

  /// Auto-endpoint timer: when no new words arrive for [_silenceWindow] while
  /// listening, finalize automatically — so the user never has to tap "Done"
  /// (some Android recognizers don't honor `pauseFor`). Reset on every partial.
  Timer? _silenceTimer;
  static const _silenceWindow = Duration(milliseconds: 1600);

  /// The utterance held while [AssistantPhase.awaitingCloudConsent] — the
  /// prompt the cloud turn will run once the user approves. Null otherwise.
  String? _pendingCloudPrompt;
  String? get pendingCloudPrompt => _pendingCloudPrompt;

  @visibleForTesting
  Future<void> askForTest(String text) => _ask(text);
  // Tests read the current state via the inherited StateNotifier.debugState.

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
    // Speech capture needs no LLM — start it immediately so the mic is hot the
    // instant the assistant surfaces (Google-like), even on a cold wake while
    // the on-device model is still loading. The model is ensured in _ask, only
    // when (and if) a local answer is actually needed.
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
        listenFor: const Duration(seconds: 30),
        pauseFor: const Duration(seconds: 2),
      ),
    );
    _bumpSilenceTimer();
  }

  /// The "Done" action — stop capturing and use whatever was heard.
  Future<void> finishListening() async {
    _silenceTimer?.cancel();
    _silenceTimer = null;
    await _stt.stop();
  }

  /// Restarts the silence countdown; when it elapses with no new speech we
  /// finalize automatically (hands-free, no "Done" tap).
  void _bumpSilenceTimer() {
    _silenceTimer?.cancel();
    _silenceTimer = Timer(_silenceWindow, () {
      if (state.phase == AssistantPhase.listening) finishListening();
    });
  }

  Future<void> stopSpeaking() async {
    await _tts.stop();
    state = state.copyWith(phase: AssistantPhase.idle);
  }

  void _onSpeech(SpeechRecognitionResult r) {
    if (r.finalResult) {
      _silenceTimer?.cancel();
      _silenceTimer = null;
      _ask(r.recognizedWords);
    } else if (state.phase == AssistantPhase.listening) {
      state = state.copyWith(transcript: r.recognizedWords);
      // Reset the auto-endpoint countdown each time the user keeps talking.
      if (r.recognizedWords.trim().isNotEmpty) _bumpSilenceTimer();
    }
  }

  Future<void> _ask(String text) async {
    final prompt = text.trim();
    if (prompt.isEmpty) {
      state = const AssistantState(phase: AssistantPhase.idle);
      return;
    }
    final route = _router.decide(
      utterance: prompt,
      mode: _readMode(),
      processDataOnDevice: _readOnDeviceOnly(),
    );

    // First-cloud-hop consent: pause before the very first cloud turn so the
    // user can OK leaving the phone once. We hold the prompt and surface the
    // consent phase; the screen resumes via approveCloudOnce / denyCloud.
    if (route == AssistantRoute.cloud && _needsCloudConsent()) {
      _pendingCloudPrompt = prompt;
      state = AssistantState(
        phase: AssistantPhase.awaitingCloudConsent,
        transcript: prompt,
      );
      return;
    }

    state = AssistantState(phase: AssistantPhase.thinking, transcript: prompt);

    final buf = StringBuffer();
    TurnSource source =
        route == AssistantRoute.cloud ? TurnSource.cloud : TurnSource.onDevice;
    try {
      if (route == AssistantRoute.cloud) {
        final ok = (_ensureCloud == null) ? true : await _ensureCloud();
        if (!ok) {
          // No session → degrade to local with an honest note.
          source = TurnSource.onDevice;
          await _streamLocal(prompt, buf);
        } else {
          await _streamCloud(prompt, buf);
        }
      } else {
        // Ensure the on-device model is loaded before answering locally. The
        // load was kicked off while the user was still speaking, so this is
        // usually instant; it only ever waits on a true cold start.
        final localReady = (_ensureLocalModel == null)
            ? _engine.isLoaded
            : await _ensureLocalModel();
        if (!localReady) {
          // Can't answer on-device. Escalate to the cloud when the mode allows;
          // otherwise say so plainly rather than failing silently.
          final canCloud = _readMode() != AssistantBackendMode.onlyOnDevice &&
              !_readOnDeviceOnly();
          if (canCloud &&
              (_ensureCloud == null ? true : await _ensureCloud())) {
            source = TurnSource.cloud;
            await _streamCloud(prompt, buf);
          } else {
            buf.write(canCloud
                ? "I couldn't reach the server and my on-device model isn't "
                    "ready yet — give it a moment and try again."
                : "I'm still getting my on-device model ready — give me a "
                    "second and ask again.");
          }
        } else {
          await _streamLocal(prompt, buf);
          // Optional secondary self-signal: local asked to escalate.
          if (_readMode() == AssistantBackendMode.preferOnDevice &&
              !_readOnDeviceOnly() &&
              buf.toString().contains('[[NEEDS_CLOUD]]')) {
            buf.clear();
            final ok = (_ensureCloud == null) ? true : await _ensureCloud();
            if (ok) {
              source = TurnSource.cloud;
              await _streamCloud(prompt, buf);
            }
          }
        }
      }
    } catch (e) {
      _emitError(prompt, e, source);
      return;
    }

    await _finishTurn(prompt, buf, source);
  }

  /// True when the first-cloud-hop consent gate must intervene: confirm-cloud
  /// is on AND the user hasn't yet approved a cloud hop this install.
  bool _needsCloudConsent() {
    if (!(_readConfirmCloud?.call() ?? false)) return false;
    return !(_readConsentGiven?.call() ?? false);
  }

  /// User approved the held cloud turn (once). Marks consent and runs it.
  Future<void> approveCloudOnce() async {
    final prompt = _takePendingPrompt();
    if (prompt == null) return;
    _markConsentGiven?.call();
    await _runHeldCloudTurn(prompt);
  }

  /// User declined the cloud hop. Keep it on-device with an honest spoken note.
  Future<void> denyCloud() async {
    final prompt = _takePendingPrompt();
    if (prompt == null) return;

    state = AssistantState(phase: AssistantPhase.thinking, transcript: prompt);
    final buf = StringBuffer();
    try {
      await _streamLocal(prompt, buf);
    } catch (e) {
      _emitError(prompt, e, TurnSource.onDevice);
      return;
    }
    if (buf.isEmpty) {
      buf.write(
          "Okay, keeping this on your phone — I can't reach the internet for that.");
    }
    await _finishTurn(prompt, buf, TurnSource.onDevice);
  }

  /// Runs the approved cloud turn, degrading to local if there's no session.
  Future<void> _runHeldCloudTurn(String prompt) async {
    state = AssistantState(phase: AssistantPhase.thinking, transcript: prompt);
    final buf = StringBuffer();
    try {
      final ok = (_ensureCloud == null) ? true : await _ensureCloud();
      if (!ok) {
        await _streamLocal(prompt, buf); // no session → honest local fallback
        await _finishTurn(prompt, buf, TurnSource.onDevice);
        return;
      }
      await _streamCloud(prompt, buf);
    } catch (e) {
      _emitError(prompt, e, TurnSource.cloud);
      return;
    }
    await _finishTurn(prompt, buf, TurnSource.cloud);
  }

  String? _takePendingPrompt() {
    final prompt = _pendingCloudPrompt;
    _pendingCloudPrompt = null;
    return prompt;
  }

  void _emitError(String prompt, Object e, TurnSource source) {
    state = AssistantState(
      phase: AssistantPhase.error,
      transcript: prompt,
      error: 'Something went wrong: $e',
      source: source,
    );
  }

  Future<void> _streamCloud(String prompt, StringBuffer buf) async {
    await for (final tok in _cloud.streamTurn(prompt)) {
      buf.write(tok);
      state = AssistantState(
        phase: AssistantPhase.thinking,
        transcript: prompt,
        response: buf.toString(),
        source: TurnSource.cloud,
      );
    }
  }

  /// Cleans, shows + speaks the buffered reply, then settles to idle.
  Future<void> _finishTurn(
      String prompt, StringBuffer buf, TurnSource source) async {
    final reply = _clean(buf.toString());
    if (reply.isEmpty) {
      state = AssistantState(
          phase: AssistantPhase.idle, transcript: prompt, source: source);
      return;
    }
    state = AssistantState(
      phase: AssistantPhase.speaking,
      transcript: prompt,
      response: reply,
      source: source,
    );
    await _speak(reply);
    state = AssistantState(
      phase: AssistantPhase.idle,
      transcript: prompt,
      response: reply,
      source: source,
    );
  }

  Future<void> _streamLocal(String prompt, StringBuffer buf) async {
    await for (final tok in _engine.generate(
      [LocalLlmMessage.user(prompt)],
      systemPrompt: _system,
    )) {
      buf.write(tok);
      state = AssistantState(
        phase: AssistantPhase.thinking,
        transcript: prompt,
        response: buf.toString(),
        source: TurnSource.onDevice,
      );
    }
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
    _silenceTimer?.cancel();
    _stt.cancel();
    _tts.stop();
    super.dispose();
  }
}

/// App-lifetime controller: shares the on-device engine with local-chat (one
/// model in memory), a dedicated cloud client, the tier router and the
/// first-hop consent flags.
final lazyAssistantProvider =
    StateNotifierProvider<LazyAssistantController, AssistantState>((ref) {
  final c = LazyAssistantController(
    ref.watch(localLlmEngineProvider),
    ref.watch(cloudTurnClientProvider),
    () => ref.read(assistantBackendModeProvider),
    () => ref.read(assistantOnDeviceOnlyProvider),
    ensureCloud: () => ensureAssistantSocketConnected(ref),
    ensureLocalModel: () async {
      final la = ref.read(localAiControllerProvider);
      if (la.isReady) return true;
      final id = la.activeModelId;
      if (id == null) return false; // nothing selected → can't load
      await ref.read(localAiControllerProvider.notifier).selectAndLoad(id);
      return ref.read(localAiControllerProvider).isReady;
    },
    readConfirmCloud: () => ref.read(assistantConfirmCloudProvider),
    readConsentGiven: () => ref.read(assistantFirstCloudConsentGivenProvider),
    markConsentGiven: () =>
        ref.read(assistantFirstCloudConsentGivenProvider.notifier).state = true,
  );
  ref.onDispose(c.dispose);
  return c;
});
