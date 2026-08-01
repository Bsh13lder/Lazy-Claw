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
import 'package:speech_to_text/speech_recognition_result.dart';
import 'package:speech_to_text/speech_to_text.dart' as stt;

import '../local_ai/local_ai_providers.dart';
import '../local_ai/local_llm_engine.dart';
import 'assistant_backend_mode.dart';
import 'assistant_router.dart';
import 'assistant_settings_providers.dart';
import 'assistant_state.dart';
import 'cloud_turn_client.dart';
import 'flutter_tts_speaker.dart';
import 'sentence_streamer.dart';
import 'speech_queue.dart';

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
    Speaker? speaker,
  })  : _router = router,
        _speech = SpeechQueue(speaker ?? FlutterTtsSpeaker()),
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

  /// Serialized TTS. Sentences are spoken as they are produced, so audio starts
  /// about one sentence into the reply instead of after the whole generation.
  final SpeechQueue _speech;
  bool _speechReady = false;
  bool _sttReady = false;

  /// Bumped per turn. A superseded turn's stream must not keep writing state,
  /// or stopping and asking again flickers the previous answer back on screen.
  int _turn = 0;

  /// Auto-endpoint timer: when no new words arrive for [_silenceWindow] while
  /// listening, finalize automatically — so the user never has to tap "Done"
  /// (some Android recognizers don't honor `pauseFor`). Reset on every partial.
  Timer? _silenceTimer;
  // 900ms, and it is now the ONLY endpointer: the plugin's own pauseFor and
  // its hidden 2s finalTimeout used to stack on top, giving a ~3.6s worst
  // case against Google's ~0.5-0.8s.
  static const _silenceWindow = Duration(milliseconds: 900);

  /// The utterance held while [AssistantPhase.awaitingCloudConsent] — the
  /// prompt the cloud turn will run once the user approves. Null otherwise.
  String? _pendingCloudPrompt;
  String? get pendingCloudPrompt => _pendingCloudPrompt;

  @visibleForTesting
  Future<void> askForTest(String text) => _ask(text);
  // Tests read the current state via the inherited StateNotifier.debugState.

  /// The length ceiling is stated FIRST and as a number: "concise" is not a
  /// budget, and a small model weights the last instruction it read most. Length
  /// is then enforced in three independent layers — this prompt, the token cap
  /// in [LocalGenOptions.voice], and its stop sequences.
  static const String _system =
      'You are Lazy, a voice assistant. You are being read aloud.\n'
      'Answer in ONE or TWO short sentences. Never more.\n'
      'Plain speech only: no markdown, no asterisks, no emoji, no bullet points, '
      'no headings, no stage directions.\n'
      'If you do not know, say so in one sentence.\n'
      "Reply in the user's language.\n"
      'If the answer needs the internet, live data, or my tools, reply with '
      'exactly $_sentinel and nothing else.';

  /// Escalation marker. The contract is PREFIX-ONLY on purpose: a substring test
  /// would throw away a perfectly good answer that merely mentions it.
  static const String _sentinel = '[[NEEDS_CLOUD]]';

  /// True while the buffer could still turn out to BE the sentinel, so speech
  /// can be held back until that is ruled out — usually within a chunk or two.
  static bool _sentinelStillPossible(String buffer) {
    final head = buffer.trimLeft();
    if (head.isEmpty) return true;
    return head.length >= _sentinel.length
        ? head.startsWith(_sentinel)
        : _sentinel.startsWith(head);
  }

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
    // Bullet markers survive the pair-matching regex above, so strip them too —
    // otherwise "* item" is read aloud as "asterisk item".
    t = t.replaceAll(RegExp(r'^[ \t]*[*\-•]\s+', multiLine: true), '');
    t = t.replaceAll(RegExp(r'[ \t]+'), ' ').replaceAll(RegExp(r' *\n *'), '\n');
    // Removing an inline marker leaves a space stranded before the punctuation
    // ("Sure **thing**." -> "Sure thing ."), which both reads wrong on screen
    // and makes the speech engine pause in the wrong place.
    t = t.replaceAllMapped(RegExp(r'\s+([.,!?;:…])'), (m) => m.group(1)!);
    return t.trim();
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
    // Silence any reply still playing; a new question supersedes the old one.
    await _speech.stop();
    if (!_sttReady) {
      _sttReady = await _stt.initialize(
        onError: (e) => _fail("I didn't catch that"),
        onStatus: _onSttStatus,
      );
    }
    if (!_sttReady) {
      _fail('Speech recognition is unavailable on this device');
      return;
    }
    state = const AssistantState(phase: AssistantPhase.listening);
    try {
      await _stt.listen(
        onResult: _onSpeech,
        listenOptions: stt.SpeechListenOptions(
          partialResults: true,
          cancelOnError: true,
          listenFor: const Duration(seconds: 30),
          // Ours is the only endpointer. The plugin's pauseFor always lost to
          // the shorter silence window anyway, and having two made the real
          // behaviour impossible to reason about.
          pauseFor: const Duration(seconds: 30),
        ),
      );
    } catch (e) {
      // ListenFailedException when the platform refuses the mic — the wake
      // handoff race, or an incoming call. Settle instead of hanging.
      _fail("I couldn't open the microphone — try again");
      return;
    }
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

  /// Barge-in: stop talking AND stop generating.
  ///
  /// Cancelling the engine matters as much as silencing the queue — without it
  /// an abandoned turn keeps the CPU busy to its token limit and its stream
  /// keeps overwriting `state.response`, so stopping and asking again would
  /// flicker the previous answer back on screen. The turn counter makes the
  /// abandoned stream a no-op even if it outlives this call.
  Future<void> stopSpeaking() async {
    _turn++;
    await _speech.stop();
    try {
      await _engine.cancel();
    } catch (_) {
      // Best effort: a cancel that fails must not block the user.
    }
    if (!mounted) return;
    state = state.copyWith(phase: AssistantPhase.idle);
  }

  /// Endpoint signal from the recognizer.
  ///
  /// Without this a turn where nothing was heard never delivers a final result
  /// — the plugin only notifies when a previous result exists, and listen()
  /// clears it — so the phase stayed at `listening` with a dead mic until the
  /// screen was popped. Salvages any partial rather than discarding it.
  void _onSttStatus(String status) {
    if (status != 'done' && status != 'notListening') return;
    if (state.phase != AssistantPhase.listening) return; // a final already ran
    _silenceTimer?.cancel();
    _silenceTimer = null;
    final heard = state.transcript.trim();
    if (heard.isEmpty) {
      state = const AssistantState();
      return;
    }
    unawaited(_ask(heard));
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

    final turn = ++_turn;
    await _ensureSpeechReady();
    await _speech.stop(); // silence anything still playing from a previous turn
    final epoch = _speech.epoch;

    final buf = StringBuffer();
    final seg = SentenceStreamer();
    TurnSource source =
        route == AssistantRoute.cloud ? TurnSource.cloud : TurnSource.onDevice;

    // Cloud tokens are final, so speech is armed immediately. The LOCAL branch
    // starts DISARMED: sentences are held until the reply is proven not to be
    // the escalation sentinel. That makes speaking-then-discarding structurally
    // impossible — escalation is only reachable on a turn where nothing was
    // ever spoken.
    var armed = route == AssistantRoute.cloud;
    final held = <String>[];

    void emit(String chunk) {
      if (turn != _turn) return; // superseded by a newer turn
      buf.write(chunk);
      if (!armed && !_sentinelStillPossible(buf.toString())) {
        armed = true;
        for (final h in held) {
          _speech.add(h, epoch);
        }
        held.clear();
      }
      // Clean per EMITTED SENTENCE, never per chunk: the segmenter guarantees a
      // sentence is a whole whitespace-delimited unit, so a markdown token can
      // never be split across the boundary.
      for (final sentence in seg.add(chunk)) {
        final line = _clean(sentence);
        if (line.isEmpty) continue;
        if (armed) {
          _speech.add(line, epoch);
        } else {
          held.add(line);
        }
      }
      state = AssistantState(
        phase: state.phase == AssistantPhase.speaking
            ? AssistantPhase.speaking
            : AssistantPhase.thinking,
        transcript: prompt,
        response: _clean(buf.toString()),
        source: source,
      );
    }

    try {
      if (route == AssistantRoute.cloud) {
        final ok = (_ensureCloud == null) ? true : await _ensureCloud();
        if (!ok) {
          source = TurnSource.onDevice;
          await _streamLocal(prompt, emit);
        } else {
          await _streamCloud(prompt, emit);
        }
      } else {
        final localReady = (_ensureLocalModel == null)
            ? _engine.isLoaded
            : await _ensureLocalModel();
        if (!localReady) {
          final canCloud = _readMode() != AssistantBackendMode.onlyOnDevice &&
              !_readOnDeviceOnly();
          if (canCloud &&
              (_ensureCloud == null ? true : await _ensureCloud())) {
            source = TurnSource.cloud;
            armed = true;
            await _streamCloud(prompt, emit);
          } else {
            final msg = canCloud
                ? "I couldn't reach the server and my on-device model isn't "
                    "ready yet — give it a moment and try again."
                : "I'm still getting my on-device model ready — give me a "
                    "second and ask again.";
            buf.write(msg);
            armed = true;
            _speech.add(msg, epoch);
          }
        } else {
          await _streamLocal(prompt, emit);
        }
      }
    } catch (e) {
      if (turn == _turn) _emitError(prompt, e, source, buf);
      return;
    }

    if (turn != _turn) return;

    // Escalate only if nothing was ever spoken. Prefix test, not `contains`.
    if (!armed && buf.toString().trimLeft().startsWith(_sentinel)) {
      held.clear();
      await _speech.stop();
      final ok = (_ensureCloud == null) ? true : await _ensureCloud();
      if (ok) {
        await _runHeldCloudTurn(prompt);
        return;
      }
    }

    // Speak whatever the segmenter still holds, and release anything that was
    // held back but never escalated (a short reply with no terminator).
    if (!armed) {
      for (final h in held) {
        _speech.add(h, epoch);
      }
    }
    final tail = seg.flush();
    if (tail != null) {
      final line = _clean(tail);
      if (line.isNotEmpty) _speech.add(line, epoch);
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

  /// Builds a sink that buffers, segments, cleans and speaks as tokens arrive.
  ///
  /// Used by the consent paths, which are already past the escalation decision,
  /// so speech is armed from the first token — no holding required.
  void Function(String) _armedSink(
    String prompt,
    StringBuffer buf,
    SentenceStreamer seg,
    TurnSource source,
    int turn,
    int epoch,
  ) {
    return (String chunk) {
      if (turn != _turn) return;
      buf.write(chunk);
      for (final sentence in seg.add(chunk)) {
        final line = _clean(sentence);
        if (line.isNotEmpty) _speech.add(line, epoch);
      }
      state = AssistantState(
        phase: state.phase == AssistantPhase.speaking
            ? AssistantPhase.speaking
            : AssistantPhase.thinking,
        transcript: prompt,
        response: _clean(buf.toString()),
        source: source,
      );
    };
  }

  /// Speaks the segmenter's remaining tail at end of stream.
  void _flushTail(SentenceStreamer seg, int epoch) {
    final tail = seg.flush();
    if (tail == null) return;
    final line = _clean(tail);
    if (line.isNotEmpty) _speech.add(line, epoch);
  }

  /// User declined the cloud hop. Keep it on-device with an honest spoken note.
  Future<void> denyCloud() async {
    final prompt = _takePendingPrompt();
    if (prompt == null) return;

    state = AssistantState(phase: AssistantPhase.thinking, transcript: prompt);
    final turn = ++_turn;
    await _ensureSpeechReady();
    await _speech.stop();
    final epoch = _speech.epoch;
    final buf = StringBuffer();
    final seg = SentenceStreamer();
    try {
      await _streamLocal(
          prompt, _armedSink(prompt, buf, seg, TurnSource.onDevice, turn, epoch));
    } catch (e) {
      if (turn == _turn) _emitError(prompt, e, TurnSource.onDevice, buf);
      return;
    }
    if (turn != _turn) return;
    if (buf.isEmpty) {
      const note =
          "Okay, keeping this on your phone — I can't reach the internet for that.";
      buf.write(note);
      _speech.add(note, epoch);
    } else {
      _flushTail(seg, epoch);
    }
    await _finishTurn(prompt, buf, TurnSource.onDevice);
  }

  /// Runs the approved cloud turn, degrading to local if there's no session.
  Future<void> _runHeldCloudTurn(String prompt) async {
    state = AssistantState(phase: AssistantPhase.thinking, transcript: prompt);
    final turn = ++_turn;
    await _ensureSpeechReady();
    await _speech.stop();
    final epoch = _speech.epoch;
    final buf = StringBuffer();
    final seg = SentenceStreamer();
    var source = TurnSource.cloud;
    try {
      final ok = (_ensureCloud == null) ? true : await _ensureCloud();
      if (!ok) {
        source = TurnSource.onDevice; // no session → honest local fallback
        await _streamLocal(prompt, _armedSink(prompt, buf, seg, source, turn, epoch));
      } else {
        await _streamCloud(prompt, _armedSink(prompt, buf, seg, source, turn, epoch));
      }
    } catch (e) {
      if (turn == _turn) _emitError(prompt, e, source, buf);
      return;
    }
    if (turn != _turn) return;
    _flushTail(seg, epoch);
    await _finishTurn(prompt, buf, source);
  }

  String? _takePendingPrompt() {
    final prompt = _pendingCloudPrompt;
    _pendingCloudPrompt = null;
    return prompt;
  }

  /// Keeps whatever was already produced. A cloud stream that fails after three
  /// sentences used to show nothing at all.
  void _emitError(String prompt, Object e, TurnSource source,
      [StringBuffer? partial]) {
    state = AssistantState(
      phase: AssistantPhase.error,
      transcript: prompt,
      response: partial == null ? '' : _clean(partial.toString()),
      error: 'Something went wrong: $e',
      source: source,
    );
  }

  Future<void> _streamCloud(String prompt, void Function(String) sink) async {
    await for (final tok in _cloud.streamTurn(prompt)) {
      sink(tok);
    }
  }

  /// Waits for the spoken queue to drain, then settles to idle.
  ///
  /// Speech already started mid-generation, so this no longer speaks anything —
  /// it only decides when the turn is over.
  Future<void> _finishTurn(
      String prompt, StringBuffer buf, TurnSource source) async {
    final reply = _clean(buf.toString());
    await _speech.idle;
    if (!mounted) return;
    state = AssistantState(
      phase: AssistantPhase.idle,
      transcript: prompt,
      response: reply,
      source: source,
    );
  }

  /// Lazily performs the one-time TTS setup. Doing it per utterance costs two
  /// extra platform round trips per sentence once speech is chunked.
  Future<void> _ensureSpeechReady() async {
    if (_speechReady) return;
    _speechReady = true;
    _speech.onUtteranceStart = () {
      if (!mounted) return;
      // thinking -> speaking the moment audio actually starts. Generation may
      // still be running; the phases now overlap by design.
      if (state.phase == AssistantPhase.thinking) {
        state = state.copyWith(phase: AssistantPhase.speaking);
      }
    };
    try {
      await _speech.init();
    } catch (_) {
      // A failed init must not block the turn; the queue degrades to unspoken.
    }
  }

  Future<void> _streamLocal(String prompt, void Function(String) sink) async {
    await for (final tok in _engine.generate(
      [LocalLlmMessage.user(prompt)],
      systemPrompt: _system,
      options: LocalGenOptions.voice,
    )) {
      sink(tok);
    }
  }

  void _fail(String message) {
    state = state.copyWith(phase: AssistantPhase.error, error: message);
  }

  void reset() => state = const AssistantState();

  @override
  void dispose() {
    _turn++;
    _silenceTimer?.cancel();
    _stt.cancel();
    _pendingCloudPrompt = null;
    unawaited(_speech.dispose());
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
