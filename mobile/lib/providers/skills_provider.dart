import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/skill.dart';
import '../repositories/skills_repository.dart';
import 'auth_provider.dart';

// ── Infrastructure ─────────────────────────────────────────────────────────

/// Dio-backed skills repository — wired to the shared ApiClient.
final skillsRepositoryProvider = Provider<SkillsRepository>((ref) {
  return SkillsRepository(DioSkillsTransport(ref.watch(apiClientProvider)));
});

// ── State ──────────────────────────────────────────────────────────────────

/// UI state for the Skills power surface.
class SkillsState {
  /// All skills fetched from the server (unfiltered).
  final List<Skill> skills;

  /// Current search/filter query (applied client-side).
  final String query;

  /// True while the initial fetch or a pull-to-refresh is in flight.
  final bool isLoading;

  /// Non-null when the last fetch or toggle failed.
  final String? error;

  const SkillsState({
    this.skills = const [],
    this.query = '',
    this.isLoading = false,
    this.error,
  });

  SkillsState copyWith({
    List<Skill>? skills,
    String? query,
    bool? isLoading,
    String? error,
  }) =>
      SkillsState(
        skills: skills ?? this.skills,
        query: query ?? this.query,
        isLoading: isLoading ?? this.isLoading,
        error: error,
      );

  /// Skills after applying the current [query] filter (name + description,
  /// case-insensitive). Works fully in-memory — no network call needed.
  List<Skill> get filtered {
    final q = query.trim().toLowerCase();
    if (q.isEmpty) return skills;
    return skills.where((s) {
      return s.name.toLowerCase().contains(q) ||
          s.description.toLowerCase().contains(q) ||
          s.displayCategory.toLowerCase().contains(q);
    }).toList();
  }

  /// Skills grouped by [Skill.displayCategory] (ordered by first appearance).
  /// Only includes the [filtered] list.
  Map<String, List<Skill>> get grouped {
    final result = <String, List<Skill>>{};
    for (final skill in filtered) {
      result.putIfAbsent(skill.displayCategory, () => []).add(skill);
    }
    return result;
  }
}

// ── Notifier ───────────────────────────────────────────────────────────────

class SkillsNotifier extends StateNotifier<SkillsState> {
  final SkillsRepository _repo;

  SkillsNotifier(this._repo) : super(const SkillsState());

  /// Initial load — shows loading skeleton, then populates from server.
  Future<void> load() async {
    state = state.copyWith(isLoading: true, error: null);
    try {
      final skills = await _repo.listSkills();
      state = state.copyWith(skills: skills, isLoading: false);
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

  /// Optimistically toggle a skill's [enabled] state, then persist to server.
  ///
  /// Rolls back to the original value on failure and surfaces an error.
  Future<void> toggleEnabled(String id, {required bool enabled}) async {
    // Optimistic update.
    final original = state.skills;
    state = state.copyWith(
      error: null,
      skills: original
          .map((s) => s.id == id ? s.copyWith(enabled: enabled) : s)
          .toList(),
    );

    try {
      await _repo.setEnabled(id, enabled: enabled);
    } catch (e) {
      // Roll back.
      state = state.copyWith(skills: original, error: e.toString());
    }
  }

  void clearError() => state = state.copyWith(error: null);
}

// ── Provider ───────────────────────────────────────────────────────────────

final skillsProvider =
    StateNotifierProvider<SkillsNotifier, SkillsState>((ref) {
  return SkillsNotifier(ref.watch(skillsRepositoryProvider));
});
