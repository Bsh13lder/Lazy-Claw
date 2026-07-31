/// Catalog of the on-device GGUF models the LazyClaw app can run locally.
///
/// These are the SAME files the on-device keyboard (`private-ai-keyboard`)
/// downloads — identical Hugging Face URLs and filename stems — so that a model
/// pushed once over USB can be shared between the app and the keyboard (see the
/// Phase B/C plan). The app never bundles a model; a `.gguf` is either pushed
/// from the Mac over USB or downloaded on-device as a fallback, then loaded from
/// an absolute file path by the local LLM engine.
///
/// Only ONE model is ever loaded at a time (user-switchable) — this is a
/// catalog of choices, not a set that runs concurrently.
library;

/// A single downloadable / pushable on-device model.
class LocalModel {
  const LocalModel({
    required this.id,
    required this.displayName,
    required this.license,
    required this.sizeBytesApprox,
    required this.url,
    required this.fileName,
    this.recommended = false,
  });

  /// Stable id and on-disk filename stem — kept identical to the keyboard's
  /// `ModelCatalog.kt` so a single pushed file is recognized by both apps.
  final String id;

  /// Human-friendly name shown in the Local Models screen.
  final String displayName;

  /// SPDX-style license string (informational). Permissive only (CLAUDE.md).
  final String license;

  /// Approximate download size in bytes — for the size label and the
  /// free-space pre-check before a download/push.
  final int sizeBytesApprox;

  /// Direct Hugging Face `resolve` URL to the `.gguf` (on-device fallback
  /// download when no USB push is available).
  final String url;

  /// The `.gguf` filename as stored on disk (matches the keyboard's stem).
  final String fileName;

  /// Highlighted as the suggested default in the UI.
  final bool recommended;

  /// Human-readable size, e.g. "2.3 GB".
  String get sizeLabel {
    const gb = 1024 * 1024 * 1024;
    return '${(sizeBytesApprox / gb).toStringAsFixed(1)} GB';
  }
}

/// The curated set — mirrors `private-ai-keyboard/.../ModelCatalog.kt` exactly
/// (same ids, filenames, and unsloth URLs) so the pushed file is cross-app.
const List<LocalModel> kLocalModels = [
  LocalModel(
    id: 'qwen3-4b-instruct-2507-q4_k_m',
    displayName: 'Qwen3 4B Instruct 2507',
    license: 'Apache-2.0',
    sizeBytesApprox: 2497281120, // ~2.33 GB
    url:
        'https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
    fileName: 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
    recommended: true,
  ),
  // Gemma 4 E2B: ~4.65B params total but only ~2B active per token, so it
  // generates faster than a dense 4B. Q4_0 rather than a K-quant on purpose —
  // it is the only quant `ggml-org` publishes for this model, AND it is the
  // quant llama.cpp's Adreno OpenCL kernels are tuned for.
  //
  // Needs no llamadart upgrade: the `gemma4` architecture landed upstream at
  // tag b8637, and llamadart 0.8.5 bundles b9744.
  LocalModel(
    id: 'gemma-4-e2b-it-q4_0',
    displayName: 'Gemma 4 E2B Instruct',
    license: 'Apache-2.0',
    sizeBytesApprox: 2841481184, // ~2.65 GB (verified via the HF API)
    url:
        'https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_0.gguf',
    fileName: 'gemma-4-E2B-it-Q4_0.gguf',
  ),
  LocalModel(
    id: 'phi-4-mini-instruct-q4_k_m',
    displayName: 'Phi-4 mini Instruct',
    license: 'MIT',
    sizeBytesApprox: 2491874272, // ~2.32 GB
    url:
        'https://huggingface.co/unsloth/Phi-4-mini-instruct-GGUF/resolve/main/Phi-4-mini-instruct-Q4_K_M.gguf',
    fileName: 'Phi-4-mini-instruct-Q4_K_M.gguf',
  ),
];

/// Look up a catalog entry by id, or null when unknown.
LocalModel? localModelById(String id) {
  for (final m in kLocalModels) {
    if (m.id == id) return m;
  }
  return null;
}
