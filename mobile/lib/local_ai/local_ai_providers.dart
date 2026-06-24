/// Riverpod wiring for the on-device local-AI feature.
///
/// Exposes the engine (singleton, disposed with the container), the model
/// store, a refreshable list of on-disk model statuses, and a small load
/// state-machine ([LocalAiController]) the UI watches to know whether a model
/// is idle / loading / ready / errored. The active model id is persisted so the
/// choice survives restarts.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../chat/chat_message.dart';
import 'llamadart_engine.dart';
import 'local_chat_controller.dart';
import 'local_llm_engine.dart';
import 'local_model.dart';
import 'local_model_downloader.dart';
import 'local_model_store.dart';

const _kActiveModelKey = 'local_ai.active_model_id';

final localModelStoreProvider =
    Provider<LocalModelStore>((_) => LocalModelStore());

/// The on-device engine — one instance for the app's lifetime; unloaded when
/// the provider container is disposed so native memory is always reclaimed.
final localLlmEngineProvider = Provider<LocalLlmEngine>((ref) {
  final engine = LlamadartEngine();
  ref.onDispose(() => engine.unload());
  return engine;
});

/// Presence/size of each catalog model on disk. autoDispose + refreshable so the
/// Local Models screen re-reads after a push/download/delete.
final localModelStatusesProvider =
    FutureProvider.autoDispose<List<LocalModelStatus>>(
  (ref) => ref.read(localModelStoreProvider).listStatuses(),
);

enum LocalAiPhase { idle, loading, ready, error }

class LocalAiState {
  const LocalAiState({
    this.activeModelId,
    this.phase = LocalAiPhase.idle,
    this.error,
  });

  final String? activeModelId;
  final LocalAiPhase phase;
  final String? error;

  bool get isReady => phase == LocalAiPhase.ready;

  LocalAiState copyWith({
    String? activeModelId,
    LocalAiPhase? phase,
    String? error,
    bool clearError = false,
  }) =>
      LocalAiState(
        activeModelId: activeModelId ?? this.activeModelId,
        phase: phase ?? this.phase,
        error: clearError ? null : (error ?? this.error),
      );
}

/// Loads the selected model into the engine and tracks the load lifecycle.
class LocalAiController extends StateNotifier<LocalAiState> {
  LocalAiController(this._engine, this._store, this._storage)
      : super(const LocalAiState()) {
    _restore();
  }

  final LocalLlmEngine _engine;
  final LocalModelStore _store;
  final FlutterSecureStorage _storage;

  Future<void> _restore() async {
    final id = await _storage.read(key: _kActiveModelKey);
    if (id != null && localModelById(id) != null) {
      state = state.copyWith(activeModelId: id);
    }
  }

  /// Persist the choice and load it into the engine (lazy — only when the user
  /// actually switches to local chat or taps "Load"). Surfaces a friendly error
  /// if the model isn't on the device yet.
  Future<void> selectAndLoad(String modelId) async {
    final model = localModelById(modelId);
    if (model == null) return;
    await _storage.write(key: _kActiveModelKey, value: modelId);
    state = state.copyWith(
        activeModelId: modelId, phase: LocalAiPhase.loading, clearError: true);
    try {
      final path = await _store.resolvePath(model);
      if (path == null) {
        state = state.copyWith(
          phase: LocalAiPhase.error,
          error: '${model.displayName} isn\'t on this device yet — '
              'push it over USB or download it first.',
        );
        return;
      }
      await _engine.load(modelId, path);
      state = state.copyWith(phase: LocalAiPhase.ready, clearError: true);
    } on LocalLlmException catch (e) {
      state = state.copyWith(phase: LocalAiPhase.error, error: e.message);
    } catch (e) {
      state = state.copyWith(
          phase: LocalAiPhase.error, error: 'Failed to load model: $e');
    }
  }

  Future<void> unload() async {
    await _engine.unload();
    state = state.copyWith(phase: LocalAiPhase.idle, clearError: true);
  }
}

final localAiControllerProvider =
    StateNotifierProvider<LocalAiController, LocalAiState>((ref) {
  return LocalAiController(
    ref.watch(localLlmEngineProvider),
    ref.watch(localModelStoreProvider),
    const FlutterSecureStorage(),
  );
});

/// Chat backed by the on-device engine. Feeds the shared [ChatReducer] with
/// synthesized frames so the streaming chat UI is reused. The system prompt
/// keeps a small local model focused and brief.
final localChatControllerProvider =
    StateNotifierProvider<LocalChatController, List<ChatMessage>>((ref) {
  return LocalChatController(
    ref.watch(localLlmEngineProvider),
    systemPrompt: 'You are LazyClaw, a concise, helpful on-device assistant. '
        'Answer directly and briefly.',
  );
});

// ── On-device download (fallback to USB push) ────────────────────────────────

final localModelDownloaderProvider = Provider<LocalModelDownloader>(
  (ref) => LocalModelDownloader(ref.watch(localModelStoreProvider)),
);

enum DownloadPhase { idle, downloading, done, error }

class LocalDownloadState {
  const LocalDownloadState({
    this.modelId,
    this.phase = DownloadPhase.idle,
    this.fraction = 0,
    this.error,
  });

  final String? modelId;
  final DownloadPhase phase;
  final double fraction; // 0..1
  final String? error;

  bool isFor(String id) => modelId == id;
}

/// Downloads one model at a time, tracking progress for the UI. Refreshes the
/// status list on completion so the card flips to "present".
class LocalDownloadController extends StateNotifier<LocalDownloadState> {
  LocalDownloadController(this._downloader, this._ref)
      : super(const LocalDownloadState());

  final LocalModelDownloader _downloader;
  final Ref _ref;

  Future<void> start(LocalModel model) async {
    if (state.phase == DownloadPhase.downloading) return;
    state = LocalDownloadState(
        modelId: model.id, phase: DownloadPhase.downloading);
    try {
      await _downloader.download(
        model,
        onProgress: (received, total) {
          if (total > 0) {
            state = LocalDownloadState(
              modelId: model.id,
              phase: DownloadPhase.downloading,
              fraction: received / total,
            );
          }
        },
      );
      state = LocalDownloadState(
          modelId: model.id, phase: DownloadPhase.done, fraction: 1);
      _ref.invalidate(localModelStatusesProvider);
    } catch (e) {
      state = LocalDownloadState(
          modelId: model.id, phase: DownloadPhase.error, error: '$e');
    }
  }

  void cancel() {
    _downloader.cancel();
    state = const LocalDownloadState();
  }
}

final localDownloadControllerProvider =
    StateNotifierProvider<LocalDownloadController, LocalDownloadState>(
  (ref) => LocalDownloadController(ref.watch(localModelDownloaderProvider), ref),
);
