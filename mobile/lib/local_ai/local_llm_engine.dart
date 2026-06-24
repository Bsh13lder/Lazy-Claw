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
  /// [systemPrompt], when set, is prepended as a system turn. Throws
  /// [LocalLlmException] if no model is loaded.
  Stream<String> generate(
    List<LocalLlmMessage> messages, {
    String? systemPrompt,
  });

  /// Free the loaded model and its native resources. Safe to call when idle.
  Future<void> unload();
}
