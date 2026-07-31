/// Serialized text-to-speech queue.
///
/// Serialization is NOT a style choice — it is forced by the platform plugin.
/// flutter_tts on Android silently DISCARDS a `speak()` issued while another
/// utterance is in flight (`FlutterTtsPlugin.kt:304-309`), so a naive
/// per-sentence fire-and-forget loop would speak the first sentence and drop
/// every one after it. Switching to `QUEUE_ADD` is not an escape either:
/// `awaitSpeakCompletion` only functions under `QUEUE_FLUSH`, so that trades a
/// dropped-audio bug for the total loss of completion signalling.
///
/// Every await is watchdogged because the plugin never resolves its pending
/// speak result on an engine error (`FlutterTtsPlugin.kt:169-199`) — the future
/// simply hangs forever. Its `stop` handler DOES resolve it, which is the
/// escape hatch the watchdog uses.
library;

import 'dart:async';

/// Raised by a [Speaker] when the platform reports an utterance failure.
class SpeakException implements Exception {
  const SpeakException(this.message);
  final String message;
  @override
  String toString() => 'SpeakException: $message';
}

/// The slice of a TTS engine this queue needs. Exists so the queue can be
/// tested against a fake that reproduces the plugin's real pathologies.
abstract interface class Speaker {
  Future<void> awaitSpeakCompletion(bool value);
  Future<void> setSpeechRate(double rate);
  Future<bool> isLanguageAvailable(String languageTag);
  Future<void> setLanguage(String languageTag);

  /// Completes when the utterance finishes. Throws [SpeakException] on a
  /// reported failure — and, in production, may never complete at all.
  Future<void> speak(String text);

  Future<void> stop();
}

class SpeechQueue {
  SpeechQueue(this._tts);

  final Speaker _tts;
  final List<String> _queue = [];

  /// Bumped by [stop] and [dispose]. A drain loop and any late [add] carrying a
  /// stale epoch become no-ops, which is what makes barge-in prompt instead of
  /// "after the current sentence finishes".
  int _epoch = 0;
  bool _draining = false;
  bool _disposed = false;
  Completer<void>? _idle;

  /// Fired when an utterance actually begins — drives thinking → speaking.
  void Function()? onUtteranceStart;

  /// Fired when an utterance fails, so a silent assistant can be surfaced
  /// rather than looking like it answered.
  void Function(Object error)? onError;

  int get epoch => _epoch;
  bool get isBusy => _draining;

  /// One-time platform setup. Deliberately NOT per utterance — doing this on
  /// every call costs two extra method-channel round trips per sentence.
  Future<void> init() async {
    await _tts.awaitSpeakCompletion(true);
    await _tts.setSpeechRate(0.52);
  }

  /// Point the engine at the language actually recognized. Without this a reply
  /// in a language with no installed voice errors — and that error is the shape
  /// that hangs forever. Returns false when the language is unavailable.
  Future<bool> useLanguage(String languageTag) async {
    try {
      if (!await _tts.isLanguageAvailable(languageTag)) return false;
      await _tts.setLanguage(languageTag);
      return true;
    } catch (_) {
      return false;
    }
  }

  /// Enqueue one utterance. Ignored when disposed, when [epoch] is stale, or
  /// when the text is blank.
  void add(String utterance, int epoch) {
    if (_disposed || epoch != _epoch || utterance.trim().isEmpty) return;
    _queue.add(utterance);
    if (!_draining) unawaited(_drain());
  }

  /// Barge-in or a new turn: drop everything queued and silence the engine.
  Future<void> stop() async {
    _epoch++;
    _queue.clear();
    try {
      await _tts.stop();
    } catch (_) {
      // A stop that itself fails must not propagate — the caller is already
      // abandoning this turn.
    }
    // Idle is NOT completed here: a drain still awaiting its in-flight
    // utterance owns that, and completing it from two places orphaned the
    // waiter whenever stop() raced a live drain.
    if (!_draining) _completeIdle();
  }

  /// Completes once the queue is empty and nothing is speaking.
  Future<void> get idle =>
      _draining ? (_idle ??= Completer<void>()).future : Future.value();

  /// Drains whatever is queued. There is deliberately NO epoch guard on the
  /// loop: [stop] already clears the queue and bumps the epoch, so a stale
  /// [add] can never enqueue. Guarding the loop as well meant a *fresh*
  /// utterance added while the old drain was still awaiting its in-flight
  /// speak would sit in the queue with nothing left to drain it.
  Future<void> _drain() async {
    _draining = true;
    try {
      while (_queue.isNotEmpty && !_disposed) {
        final next = _queue.removeAt(0);
        onUtteranceStart?.call();
        try {
          await _tts.speak(next).timeout(
            _watchdogFor(next),
            onTimeout: () async {
              // The production hang. stop() is what releases the wedged future.
              try {
                await _tts.stop();
              } catch (_) {}
            },
          );
        } catch (e) {
          // One bad utterance must not kill the rest of the reply.
          onError?.call(e);
        }
      }
    } finally {
      _draining = false;
      _completeIdle();
    }
  }

  void _completeIdle() {
    final c = _idle;
    _idle = null;
    if (c != null && !c.isCompleted) c.complete();
  }

  /// Android speaks roughly 13 characters/second at rate 1.0; 120 ms/char is
  /// about 1.5x headroom before we assume the engine has wedged.
  static Duration _watchdogFor(String text) =>
      Duration(milliseconds: 2000 + 120 * text.length);

  Future<void> dispose() async {
    _disposed = true;
    await stop();
  }
}
