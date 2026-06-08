import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/skill.dart';
import 'package:lazyclaw_mobile/providers/skills_provider.dart';
import 'package:lazyclaw_mobile/repositories/skills_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeTransport implements SkillsTransport {
  List<Map<String, dynamic>> skills;
  bool failNext;

  _FakeTransport({this.skills = const [], this.failNext = false});

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    if (failNext) throw Exception('network_failure');
    return {'skills': skills};
  }

  @override
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    if (failNext) throw Exception('patch_failure');
    return {'status': 'ok'};
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────

Map<String, dynamic> _skillJson({
  String id = 's1',
  String name = 'web_search',
  String skillType = 'builtin',
  bool enabled = true,
  String? category,
}) =>
    {
      'id': id,
      'name': name,
      'description': 'A test skill',
      'skill_type': skillType,
      'enabled': enabled,
      'category': category,
    };

SkillsNotifier _notifier(SkillsTransport transport) =>
    SkillsNotifier(SkillsRepository(transport));

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  group('SkillsState', () {
    group('.filtered', () {
      final skills = [
        Skill.fromJson(_skillJson(id: '1', name: 'web_search', skillType: 'builtin')),
        Skill.fromJson(_skillJson(id: '2', name: 'send_email', skillType: 'instruction')),
        Skill.fromJson(_skillJson(id: '3', name: 'browser_read', skillType: 'builtin')),
      ];

      test('returns all skills when query is empty', () {
        const state = SkillsState();
        expect(
          state.copyWith(skills: skills).filtered,
          hasLength(3),
        );
      });

      test('filters by name substring (case-insensitive)', () {
        final state = SkillsState(skills: skills, query: 'email');
        expect(state.filtered, hasLength(1));
        expect(state.filtered.first.name, 'send_email');
      });

      test('filters by category/skillType', () {
        final state = SkillsState(skills: skills, query: 'instruction');
        expect(state.filtered, hasLength(1));
        expect(state.filtered.first.skillType, 'instruction');
      });

      test('returns empty list when no match', () {
        final state = SkillsState(skills: skills, query: 'zzz_no_match');
        expect(state.filtered, isEmpty);
      });
    });

    group('.grouped', () {
      test('groups skills by displayCategory', () {
        final skills = [
          Skill.fromJson(_skillJson(id: '1', skillType: 'builtin')),
          Skill.fromJson(_skillJson(id: '2', skillType: 'instruction')),
          Skill.fromJson(_skillJson(id: '3', skillType: 'builtin')),
        ];
        final state = SkillsState(skills: skills);
        final grouped = state.grouped;
        expect(grouped['builtin'], hasLength(2));
        expect(grouped['instruction'], hasLength(1));
      });

      test('prefers explicit category over skillType for grouping', () {
        final skills = [
          Skill.fromJson(_skillJson(id: '1', skillType: 'instruction', category: 'productivity')),
          Skill.fromJson(_skillJson(id: '2', skillType: 'instruction')),
        ];
        final state = SkillsState(skills: skills);
        final grouped = state.grouped;
        expect(grouped.containsKey('productivity'), isTrue);
        expect(grouped.containsKey('instruction'), isTrue);
      });

      test('respects active query when grouping', () {
        final skills = [
          Skill.fromJson(_skillJson(id: '1', name: 'browser_read', skillType: 'builtin')),
          Skill.fromJson(_skillJson(id: '2', name: 'send_email', skillType: 'instruction')),
        ];
        final state = SkillsState(skills: skills, query: 'browser');
        final grouped = state.grouped;
        expect(grouped.containsKey('builtin'), isTrue);
        expect(grouped.containsKey('instruction'), isFalse);
      });

      test('returns empty map when filtered is empty', () {
        final state = SkillsState(
          skills: [Skill.fromJson(_skillJson())],
          query: 'zzz_no_match',
        );
        expect(state.grouped, isEmpty);
      });
    });

    group('copyWith immutability', () {
      test('original state is unchanged after copyWith', () {
        const original = SkillsState(isLoading: false);
        final updated = original.copyWith(isLoading: true);
        expect(original.isLoading, isFalse);
        expect(updated.isLoading, isTrue);
      });

      test('error is cleared when not explicitly set', () {
        const state = SkillsState(error: 'boom');
        final cleared = state.copyWith(error: null);
        expect(cleared.error, isNull);
      });
    });
  });

  group('SkillsNotifier.load', () {
    test('sets isLoading then populates skills', () async {
      final transport = _FakeTransport(
        skills: [_skillJson(id: 'x1'), _skillJson(id: 'x2')],
      );
      final n = _notifier(transport);

      await n.load();

      expect(n.state.isLoading, isFalse);
      expect(n.state.skills, hasLength(2));
      expect(n.state.error, isNull);
    });

    test('surfaces error and clears loading on transport failure', () async {
      final transport = _FakeTransport(failNext: true);
      final n = _notifier(transport);

      await n.load();

      expect(n.state.isLoading, isFalse);
      expect(n.state.error, isNotNull);
      expect(n.state.skills, isEmpty);
    });
  });

  group('SkillsNotifier.search', () {
    test('updates query without hitting the network', () async {
      final transport = _FakeTransport(
        skills: [_skillJson(id: '1', name: 'browser_read')],
      );
      final n = _notifier(transport);
      await n.load();

      n.search('browser');

      expect(n.state.query, 'browser');
      expect(n.state.filtered, hasLength(1));
    });

    test('empty query shows all skills', () async {
      final transport = _FakeTransport(
        skills: [_skillJson(id: '1'), _skillJson(id: '2')],
      );
      final n = _notifier(transport);
      await n.load();

      n.search('skill_a');
      expect(n.state.filtered, isEmpty);

      n.search('');
      expect(n.state.filtered, hasLength(2));
    });
  });

  group('SkillsNotifier.toggleEnabled', () {
    test('optimistically flips enabled state', () async {
      final transport = _FakeTransport(
        skills: [_skillJson(id: 't1', enabled: true)],
      );
      final n = _notifier(transport);
      await n.load();

      // Start the toggle without awaiting — observe optimistic state.
      final future = n.toggleEnabled('t1', enabled: false);
      expect(n.state.skills.first.enabled, isFalse);

      await future;
      // After PATCH success, optimistic state is kept.
      expect(n.state.skills.first.enabled, isFalse);
    });

    test('rolls back on PATCH failure', () async {
      final transport = _FakeTransport(
        skills: [_skillJson(id: 'r1', enabled: true)],
      );
      final n = _notifier(transport);
      await n.load();
      transport.failNext = true;

      await n.toggleEnabled('r1', enabled: false);

      // Rolled back to original value.
      expect(n.state.skills.first.enabled, isTrue);
      expect(n.state.error, isNotNull);
    });

    test('does not affect other skills in the list', () async {
      final transport = _FakeTransport(
        skills: [
          _skillJson(id: 'a1', name: 'A', enabled: true),
          _skillJson(id: 'a2', name: 'B', enabled: true),
        ],
      );
      final n = _notifier(transport);
      await n.load();

      await n.toggleEnabled('a1', enabled: false);

      expect(n.state.skills.firstWhere((s) => s.id == 'a1').enabled, isFalse);
      expect(n.state.skills.firstWhere((s) => s.id == 'a2').enabled, isTrue);
    });
  });

  group('SkillsNotifier.clearError', () {
    test('clears a non-null error', () async {
      final transport = _FakeTransport(failNext: true);
      final n = _notifier(transport);
      await n.load();
      expect(n.state.error, isNotNull);

      n.clearError();
      expect(n.state.error, isNull);
    });
  });

  group('SkillsNotifier.refresh', () {
    test('re-fetches from server', () async {
      final transport = _FakeTransport(
        skills: [_skillJson(id: 'r1')],
      );
      final n = _notifier(transport);
      await n.load();
      expect(n.state.skills, hasLength(1));

      // Add a skill to the fake and refresh.
      transport.skills = [_skillJson(id: 'r1'), _skillJson(id: 'r2')];
      await n.refresh();
      expect(n.state.skills, hasLength(2));
    });
  });
}
