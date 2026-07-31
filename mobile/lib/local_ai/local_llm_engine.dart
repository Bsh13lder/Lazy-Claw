/// Engine-agnostic interface for on-device LLM inference.
///
/// The chat layer and UI depend ONLY on this interface, never on the concrete
/// llama.cpp binding — so the engine can be swapped (e.g. llamadart ↔
/// llama_cpp_dart) without touching the app, and a fake can be injected in
/// tests. The single concrete implementation today is [LlamadartEngine].
///
/// Contract: one model loaded at a time. [load] a model path, [generate] a
/// streamed reply for a conversation, [unload] to free RAM. Implementations
/// apply the model's own chat template internally (the caller passes plain
/// role/content turns, not a pre-formatted prompt).
library;

/// One conversation turn handed to the engine. Deliberately minimal — decoupled
/// from the app's UI-heavy `ChatMessage`.
class LocalLlmMessage {
  const LocalLlmMessage({required this.role, required this.content});

  /// `'system'`, `'user'`, or `'assistant'`.
  final String role;
  final String content;

  factory LocalLlmMessage.user(String content) =>
      LocalLlmMessage(role: 'user', content: content);
  factory LocalLlmMessage.assistant(String content) =>
      LocalLlmMessage(role: 'assistant', content: content);
}

/// Raised for load/generation failures so callers can surface a friendly error
/// instead of a raw native exception.
class LocalLlmException implements Exception {
  const LocalLlmException(this.message, [this.cause]);
  final String message;
  final Object? cause;
  @override
  String toString() =>
      'LocalLlmException: $message${cause != null ? ' ($cause)' : ''}';
}

/// Sampling/limit knobs for ONE generation, kept engine-agnostic so this file
/// never imports a concrete binding.
///
/// The defaults are byte-identical to what the engine used before this class
/// existed, so any caller that passes nothing keeps its previous behaviour —
/// that is what lets the voice path be tuned without shortening local chat.
class LocalGenOptions {
  const LocalGenOptions({
    this.maxTokens = 1024,
    this.temperature = 0.7,
    this.topP = 0.9,
    this.topK = 40,
    this.minP = 0.0,
    this.repeatPenalty = 1.1,
    this.stopSequences = const [],
    this.enableThinking = true,
    this.streamBatchTokens = 8,
  });

  final int maxTokens;
  final double temperature;
  final double topP;
  final int topK;
  final double minP;
  final double repeatPenalty;
  final List<String> stopSequences;

  /// Whether the model may emit a reasoning block before its answer.
  ///
  /// MUST be false for spoken replies. Gemma 4's chat template injects a
  /// `<|think|>` turn whenever this is set, and the underlying binding defaults
  /// it to true — so a voice answer would open with reasoning tokens.
  final bool enableThinking;

  /// Tokens buffered before a chunk is emitted. Lower = finer-grained stream,
  /// which is what lets the first spoken sentence start sooner.
  final int streamBatchTokens;

  /// Tuned for a SPOKEN turn: one or two sentences, low variance, no lists.
  ///
  /// The stop sequences kill bullet/numbered lists at the source — a cleanup
  /// regex can only strip the marker, not the list structure.
  static const voice = LocalGenOptions(
    maxTokens: 128,
    temperature: 0.3,
    minP: 0.05,
    repeatPenalty: 1.05,
    stopSequences: ['\n\n', '\nUser:', '\nAssistant:', '\n- ', '\n1. '],
    enableThinking: false,
    streamBatchTokens: 4,
  );

  /// Explicit name for the pre-existing defaults, for call sites that want to
  /// state "long-form reply" rather than rely on the implicit default.
  static const chat = LocalGenOptions();
}

/// On-device inference engine. Stateful: holds at most one loaded model.
abstract class LocalLlmEngine {
  /// True when a model is loaded and ready to [generate].
  bool get isLoaded;

  /// The catalog id of the currently loaded model, or null when none.
  String? get loadedModelId;

  /// Load the GGUF at [absolutePath] as model [modelId]. No-ops if [modelId] is
  /// already loaded; switches model (unload + load) otherwise. Throws
  /// [LocalLlmException] on failure.
  Future<void> load(String modelId, String absolutePath);

  /// Stream the assistant's reply token-by-token for [messages] (oldest first).
  /// [systemPrompt], when set, is prepended as a system turn. [options] tunes
  /// sampling for this call only; omitting it keeps the long-form defaults.
  /// Throws [LocalLlmException] if no model is loaded.
  Stream<String> generate(
    List<LocalLlmMessage> messages, {
    String? systemPrompt,
    LocalGenOptions options = const LocalGenOptions(),
  });

  /// Ask the running generation to stop early. Safe to call when idle; the
  /// [generate] stream simply completes. Needed for barge-in — without it an
  /// abandoned turn keeps the CPU pegged for its remaining tokens.
  Future<void> cancel();

  /// Free the loaded model and its native resources. Safe to call when idle.
  Future<void> unload();
}
