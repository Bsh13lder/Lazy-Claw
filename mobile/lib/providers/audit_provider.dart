import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../repositories/audit_repository.dart';
import 'auth_provider.dart';

// ── Infrastructure providers ───────────────────────────────────────────────

final auditRepositoryProvider = Provider<AuditRepository>((ref) {
  return AuditRepository(DioAuditTransport(ref.watch(apiClientProvider)));
});

// ── State ──────────────────────────────────────────────────────────────────

class AuditState {
  final List<AuditEntry> entries;
  final bool isLoading;
  final String? error;

  /// The currently selected action filter. Null means "all".
  final String? activeFilter;

  const AuditState({
    this.entries = const [],
    this.isLoading = false,
    this.error,
    this.activeFilter,
  });

  AuditState copyWith({
    List<AuditEntry>? entries,
    bool? isLoading,
    String? error,
    bool clearError = false,
    String? activeFilter,
    bool clearFilter = false,
  }) =>
      AuditState(
        entries: entries ?? this.entries,
        isLoading: isLoading ?? this.isLoading,
        error: clearError ? null : (error ?? this.error),
        activeFilter:
            clearFilter ? null : (activeFilter ?? this.activeFilter),
      );

  /// Entries filtered by [activeFilter] (client-side subset after a full fetch).
  List<AuditEntry> get filteredEntries {
    if (activeFilter == null) return entries;
    return entries
        .where((e) => e.action == activeFilter)
        .toList(growable: false);
  }
}

// ── Notifier ───────────────────────────────────────────────────────────────

class AuditNotifier extends StateNotifier<AuditState> {
  final AuditRepository _repo;

  AuditNotifier(this._repo) : super(const AuditState());

  /// Initial load — fetches the most-recent 200 entries (server default).
  Future<void> load() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final entries = await _repo.listAudit(limit: 200);
      state = state.copyWith(entries: entries, isLoading: false);
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
    }
  }

  /// Pull-to-refresh.
  Future<void> refresh() => load();

  /// Apply or toggle a status filter. Passing the same [action] twice clears it.
  void setFilter(String action) {
    if (state.activeFilter == action) {
      state = state.copyWith(clearFilter: true);
    } else {
      state = state.copyWith(activeFilter: action);
    }
  }

  void clearFilter() => state = state.copyWith(clearFilter: true);
}

// ── Provider ───────────────────────────────────────────────────────────────

final auditProvider =
    StateNotifierProvider<AuditNotifier, AuditState>((ref) {
  return AuditNotifier(ref.watch(auditRepositoryProvider));
});
