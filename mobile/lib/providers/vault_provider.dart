import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../repositories/vault_repository.dart';
import 'auth_provider.dart';

// ── Infrastructure provider ────────────────────────────────────────────────

final vaultRepositoryProvider = Provider<VaultRepository>((ref) {
  return VaultRepository(DioVaultTransport(ref.watch(apiClientProvider)));
});

// ── State ──────────────────────────────────────────────────────────────────

class VaultState {
  final List<VaultEntry> entries;
  final bool isLoading;
  final bool isSubmitting;
  final String? error;

  const VaultState({
    this.entries = const [],
    this.isLoading = false,
    this.isSubmitting = false,
    this.error,
  });

  VaultState copyWith({
    List<VaultEntry>? entries,
    bool? isLoading,
    bool? isSubmitting,
    String? error,
    bool clearError = false,
  }) =>
      VaultState(
        entries: entries ?? this.entries,
        isLoading: isLoading ?? this.isLoading,
        isSubmitting: isSubmitting ?? this.isSubmitting,
        error: clearError ? null : (error ?? this.error),
      );
}

// ── Notifier ───────────────────────────────────────────────────────────────

class VaultNotifier extends StateNotifier<VaultState> {
  final VaultRepository _repo;

  VaultNotifier(this._repo) : super(const VaultState());

  Future<void> load() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final entries = await _repo.listSecrets();
      state = state.copyWith(entries: entries, isLoading: false);
    } catch (e) {
      state = state.copyWith(
        isLoading: false,
        error: e.toString(),
      );
    }
  }

  Future<void> refresh() => load();

  /// Adds (or replaces) a vault secret. Returns true on success.
  Future<bool> addSecret(String name, String value) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      await _repo.addSecret(name, value);
      // Reload the list to reflect the server state.
      final entries = await _repo.listSecrets();
      state = state.copyWith(entries: entries, isSubmitting: false);
      return true;
    } catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.toString());
      return false;
    }
  }

  /// Deletes a vault entry by name. Returns true on success.
  Future<bool> deleteSecret(String name) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      await _repo.deleteSecret(name);
      // Optimistic removal — avoids a round-trip for the list read.
      final updated = state.entries
          .where((e) => e.name != name)
          .toList(growable: false);
      state = state.copyWith(entries: updated, isSubmitting: false);
      return true;
    } catch (e) {
      state = state.copyWith(isSubmitting: false, error: e.toString());
      return false;
    }
  }

  void clearError() => state = state.copyWith(clearError: true);
}

// ── Provider ───────────────────────────────────────────────────────────────

final vaultProvider = StateNotifierProvider<VaultNotifier, VaultState>((ref) {
  return VaultNotifier(ref.watch(vaultRepositoryProvider));
});
