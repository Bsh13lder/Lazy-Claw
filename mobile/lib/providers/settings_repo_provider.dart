import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../repositories/settings_repository.dart';
import 'auth_provider.dart';

// ── Repository provider ────────────────────────────────────────────────────

/// Provides a [SettingsRepository] backed by the shared [ApiClient].
///
/// Follows the same pattern as [authRepositoryProvider] — the repository is
/// a thin transport wrapper; async state lives in the notifiers below.
final settingsRepositoryProvider = Provider<SettingsRepository>((ref) =>
    SettingsRepository(DioSettingsTransport(ref.watch(apiClientProvider))));

// ── ECO mode notifier ──────────────────────────────────────────────────────

class EcoState {
  final AsyncValue<EcoSettings> value;
  final bool saving;

  const EcoState({required this.value, this.saving = false});

  EcoState copyWith({AsyncValue<EcoSettings>? value, bool? saving}) =>
      EcoState(
        value: value ?? this.value,
        saving: saving ?? this.saving,
      );
}

class EcoNotifier extends StateNotifier<EcoState> {
  final SettingsRepository _repo;

  EcoNotifier(this._repo)
      : super(const EcoState(value: AsyncValue.loading())) {
    _load();
  }

  Future<void> _load() async {
    try {
      final eco = await _repo.getEco();
      state = state.copyWith(value: AsyncValue.data(eco));
    } catch (e, st) {
      state = state.copyWith(value: AsyncValue.error(e, st));
    }
  }

  Future<String?> setMode(String mode) async {
    state = state.copyWith(saving: true);
    try {
      final eco = await _repo.setEco(mode);
      state = state.copyWith(value: AsyncValue.data(eco), saving: false);
      return null;
    } catch (e) {
      state = state.copyWith(saving: false);
      return e is SettingsException ? e.message : e.toString();
    }
  }

  void reload() {
    state = state.copyWith(value: const AsyncValue.loading());
    _load();
  }
}

final ecoProvider =
    StateNotifierProvider.autoDispose<EcoNotifier, EcoState>((ref) =>
        EcoNotifier(ref.watch(settingsRepositoryProvider)));

// ── Operating-mode notifier (general.agent_mode) ───────────────────────────

class AgentModeState {
  /// The current operating-mode value ('chat' | 'ask' | 'plan' | 'auto').
  final AsyncValue<String> value;
  final bool saving;

  const AgentModeState({required this.value, this.saving = false});

  AgentModeState copyWith({AsyncValue<String>? value, bool? saving}) =>
      AgentModeState(
        value: value ?? this.value,
        saving: saving ?? this.saving,
      );
}

class AgentModeNotifier extends StateNotifier<AgentModeState> {
  final SettingsRepository _repo;

  AgentModeNotifier(this._repo)
      : super(const AgentModeState(value: AsyncValue.loading())) {
    _load();
  }

  Future<void> _load() async {
    try {
      final general = await _repo.getGeneral();
      state = state.copyWith(value: AsyncValue.data(general.agentMode));
    } catch (e, st) {
      state = state.copyWith(value: AsyncValue.error(e, st));
    }
  }

  /// Persist a new operating mode. Returns an error message on failure, or
  /// null on success.
  Future<String?> setMode(String mode) async {
    state = state.copyWith(saving: true);
    try {
      final general = await _repo.setAgentMode(mode);
      state =
          state.copyWith(value: AsyncValue.data(general.agentMode), saving: false);
      return null;
    } catch (e) {
      state = state.copyWith(saving: false);
      return e is SettingsException ? e.message : e.toString();
    }
  }

  void reload() {
    state = state.copyWith(value: const AsyncValue.loading());
    _load();
  }
}

final agentModeProvider =
    StateNotifierProvider.autoDispose<AgentModeNotifier, AgentModeState>((ref) =>
        AgentModeNotifier(ref.watch(settingsRepositoryProvider)));

// ── Permissions notifier ───────────────────────────────────────────────────

class PermState {
  final AsyncValue<PermissionsSettings> value;
  final Set<String> savingCategories;

  const PermState({
    required this.value,
    this.savingCategories = const {},
  });

  PermState copyWith({
    AsyncValue<PermissionsSettings>? value,
    Set<String>? savingCategories,
  }) =>
      PermState(
        value: value ?? this.value,
        savingCategories: savingCategories ?? this.savingCategories,
      );
}

class PermissionsNotifier extends StateNotifier<PermState> {
  final SettingsRepository _repo;

  PermissionsNotifier(this._repo)
      : super(const PermState(value: AsyncValue.loading())) {
    _load();
  }

  Future<void> _load() async {
    try {
      final perms = await _repo.getPermissions();
      state = state.copyWith(value: AsyncValue.data(perms));
    } catch (e, st) {
      state = state.copyWith(value: AsyncValue.error(e, st));
    }
  }

  Future<String?> setCategory(String category, String level) async {
    final saving = {...state.savingCategories, category};
    state = state.copyWith(savingCategories: saving);
    try {
      final perms = await _repo.setCategoryPermission(category, level);
      final done = {...state.savingCategories}..remove(category);
      state = state.copyWith(
          value: AsyncValue.data(perms), savingCategories: done);
      return null;
    } catch (e) {
      final done = {...state.savingCategories}..remove(category);
      state = state.copyWith(savingCategories: done);
      return e is SettingsException ? e.message : e.toString();
    }
  }

  void reload() {
    state = state.copyWith(value: const AsyncValue.loading());
    _load();
  }
}

final permissionsProvider =
    StateNotifierProvider.autoDispose<PermissionsNotifier, PermState>((ref) =>
        PermissionsNotifier(ref.watch(settingsRepositoryProvider)));
