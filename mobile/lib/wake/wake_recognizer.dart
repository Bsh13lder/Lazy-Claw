/// Narrow seam over the wake-word recognizer so [WakeWordDetector] is unit
/// testable without Vosk or a microphone. The only production implementation is
/// `VoskWakeRecognizer`.
library;

import 'dart:convert';

abstract interface class WakeRecognizer {
  /// Final recognition results as Vosk JSON strings, e.g. '{"text":"hey lazy"}'.
  Stream<String> get results;
  Future<void> start();
  Future<void> stop();
}

/// Extracts the `text` field from a Vosk result JSON string; null if absent or
/// unparseable. Tolerant: never throws on malformed input.
String? parseVoskText(String json) {
  try {
    final m = jsonDecode(json);
    if (m is Map && m['text'] is String) return m['text'] as String;
  } catch (_) {/* fall through */}
  return null;
}
