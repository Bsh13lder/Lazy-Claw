/// [LocalLlmEngine] backed by `llamadart` (bundled llama.cpp, arm64-v8a).
///
/// CPU-only by design: llama.cpp's Android Vulkan path is known to emit garbage
/// on some phones, and llamadart itself defaults Android to CPU — so we pin
/// `GpuBackend.cpu` + `gpuLayers: 0` for deterministic output. `useMmap: true`
/// is required because the model is read from FUSE-emulated external storage,
/// where llama.cpp's default direct-I/O path fails with EINVAL.
///
/// One model per engine instance: switching models disposes the old
/// [LlamaEngine] and creates a fresh one (llama.cpp's loader is single-shot).
library;

import 'package:llamadart/llamadart.dart';

import 'local_llm_engine.dart';

/// llama.cpp-backed on-device engine. Not safe for concurrent [generate]/[load]
/// calls — the chat layer serializes turns, and [_busy] guards against reentry.
class LlamadartEngine implements LocalLlmEngine {
  LlamaEngine? _engine;
  String? _loadedModelId;
  bool _busy = false;

  /// Context window. Modest on purpose — a 4B Q4 model on a phone is happiest
  /// well under its theoretical max, and a smaller KV cache saves RAM.
  static const int _contextSize = 4096;

  /// Cap per reply so a runaway generation can't pin the CPU indefinitely.
  static const int _maxTokens = 1024;

  @override
  bool get isLoaded => _engine != null && _loadedModelId != null;

  @override
  String? get loadedModelId => _loadedModelId;

  @override
  Future<void> load(String modelId, String absolutePath) async {
    if (_loadedModelId == modelId && _engine != null) return;
    if (_busy) {
      throw const LocalLlmException('Engine is busy; try again in a moment');
    }
    _busy = true;
    try {
      await unload(); // switching models: drop the previous one first
      final engine = LlamaEngine(LlamaBackend());
      await engine.loadModel(
        absolutePath,
        modelParams: const ModelParams(
          contextSize: _contextSize,
          gpuLayers: 0, // CPU only
          preferredBackend: GpuBackend.cpu,
          useMmap: true, // required for FUSE external-storage reads
        ),
      );
      _engine = engine;
      _loadedModelId = modelId;
    } catch (e) {
      _engine = null;
      _loadedModelId = null;
      throw LocalLlmException('Failed to load model $modelId', e);
    } finally {
      _busy = false;
    }
  }

  @override
  Stream<String> generate(
    List<LocalLlmMessage> messages, {
    String? systemPrompt,
  }) async* {
    final engine = _engine;
    if (engine == null) {
      throw const LocalLlmException('No model loaded');
    }
    final llamaMessages = <LlamaChatMessage>[
      if (systemPrompt != null && systemPrompt.isNotEmpty)
        LlamaChatMessage(role: 'system', content: systemPrompt),
      for (final m in messages)
        LlamaChatMessage(role: m.role, content: m.content),
    ];
    try {
      await for (final chunk in engine.create(
        llamaMessages,
        params: const GenerationParams(maxTokens: _maxTokens, temp: 0.7),
      )) {
        if (chunk.choices.isEmpty) continue;
        final delta = chunk.choices.first.delta.content;
        if (delta != null && delta.isNotEmpty) yield delta;
      }
    } catch (e) {
      throw LocalLlmException('Generation failed', e);
    }
  }

  @override
  Future<void> unload() async {
    final engine = _engine;
    _engine = null;
    _loadedModelId = null;
    if (engine != null) {
      try {
        await engine.dispose();
      } catch (_) {
        // best-effort cleanup; a failed dispose must not wedge a reload
      }
    }
  }
}
