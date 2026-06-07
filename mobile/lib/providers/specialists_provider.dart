import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/specialist.dart';
import '../repositories/specialists_repository.dart';
import 'auth_provider.dart';

// ── Infrastructure ───────────────────────────────────────────────────────────

/// Dio-backed specialists repository — wired to the shared ApiClient.
final specialistsRepositoryProvider = Provider<SpecialistsRepository>((ref) {
  return SpecialistsRepository(
    DioSpecialistsTransport(ref.watch(apiClientProvider)),
  );
});

// ── State ────────────────────────────────────────────────────────────────────

/// UI state for the Specialists power surface.
class SpecialistsState {
  /// All specialists fetched from the server (unfiltered).
  final List<Specialist> specialists;

  /// Current search/filter query (applied client-side).
  final String query;

  /// True while the initial fetch or a pull-to-refresh is in flight.
  final bool isLoading;

  /// Non-null when the last fetch or mutation failed.
  final String? error;

  const SpecialistsState({
    this.specialists = const [],
    this.query = '',
    this.isLoading = false,
    this.error,
  });

  SpecialistsState copyWith({
    List<Specialist>? specialists,
    String? query,
    bool? isLoading,
    String? error,
    bool clearError = false,
  }) =>
      SpecialistsState(
        specialists: specialists ?? this.specialists,
        query: query ?? this.query,
        isLoading: isLoading ?? this.isLoading,
        error: clearError ? null : (error ?? this.error),
      );

  /// Specialists after applying the current [query] filter (name + display
  /// name + tools, case-insensitive). Works fully in-memory — no network call.
  List<Specialist> get filtered {
    final q = query.trim().toLowerCase();
    if (q.isEmpty) return specialists;
    return specialists.where((s) {
      return s.name.toLowerCase().contains(q) ||
          s.displayName.toLowerCase().contains(q) ||
          s.tools.any((t) => t.toLowerCase().contains(q));
    }).toList();
  }

  /// Filtered builtins (read-only), in fetch order.
  List<Specialist> get builtins =>
      filtered.where((s) => s.isBuiltin).toList();

  /// Filtered custom specialists, in fetch order.
  List<Specialist> get customs =>
      filtered.where((s) => !s.isBuiltin).toList();
}

// ── Notifier ─────────────────────────────────────────────────────────────────

/// Network-first (online) notifier for the specialist registry.
///
/// Specialist definitions live server-side, so there is no offline cache.
/// Connectivity is expected for this power-surface screen.
class SpecialistsNotifier extends StateNotifier<SpecialistsState> {
  final SpecialistsRepository _repo;

  SpecialistsNotifier(this._repo) : super(const SpecialistsState());

  /// Initial load — shows the loading skeleton, then populates from server.
  Future<void> load() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final specialists = await _repo.listSpecialists();
      state = SpecialistsState(specialists: specialists, query: state.query);
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
    }
  }

  /// Pull-to-refresh: re-fetches without resetting the query.
  Future<void> refresh() => load();

  /// Updates the in-memory search query (no network call).
  void search(String query) {
    state = state.copyWith(query: query);
  }

  /// Delete a custom specialist, optimistically removing it then persisting.
  ///
  /// Rolls back the removal and surfaces an error on failure. Builtins should
  /// never reach here (the UI gates the action), but the server is the final
  /// authority and a rejection is handled gracefully.
  Future<String?> deleteSpecialist(String name) async {
    final original = state.specialists;
    state = state.copyWith(
      clearError: true,
      specialists: original.where((s) => s.name != name).toList(),
    );
    try {
      await _repo.deleteSpecialist(name);
      return null;
    } catch (e) {
      state = state.copyWith(specialists: original, error: e.toString());
      return e.toString();
    }
  }

  void clearError() => state = state.copyWith(clearError: true);
}

// ── Provider ─────────────────────────────────────────────────────────────────

final specialistsProvider =
    StateNotifierProvider<SpecialistsNotifier, SpecialistsState>((ref) {
  return SpecialistsNotifier(ref.watch(specialistsRepositoryProvider));
});
