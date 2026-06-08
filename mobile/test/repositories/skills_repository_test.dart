import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/skill.dart';
import 'package:lazyclaw_mobile/repositories/skills_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeTransport implements SkillsTransport {
  String? lastPath;
  Map<String, dynamic>? lastBody;
  String? lastMethod;

  Map<String, dynamic> response;
  Object? errorToThrow;

  _FakeTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    if (errorToThrow != null) throw errorToThrow!;
    lastMethod = 'GET';
    lastPath = path;
    return response;
  }

  @override
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    if (errorToThrow != null) throw errorToThrow!;
    lastMethod = 'PATCH';
    lastPath = path;
    lastBody = body;
    return response;
  }
}

// ── Fixtures ───────────────────────────────────────────────────────────────

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
      'description': 'A skill',
      'skill_type': skillType,
      'enabled': enabled,
      'category': category,
    };

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  group('SkillsRepository.listSkills', () {
    test('GET /api/skills and parses skill list', () async {
      final t = _FakeTransport({
        'skills': [
          _skillJson(id: 'a1', name: 'Skill A'),
          _skillJson(id: 'a2', name: 'Skill B', enabled: false),
        ],
      });
      final repo = SkillsRepository(t);
      final skills = await repo.listSkills();

      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/skills');
      expect(skills, hasLength(2));
      expect(skills[0].id, 'a1');
      expect(skills[0].name, 'Skill A');
      expect(skills[0].enabled, isTrue);
      expect(skills[1].id, 'a2');
      expect(skills[1].enabled, isFalse);
    });

    test('returns empty list when skills key is missing', () async {
      final t = _FakeTransport({});
      final skills = await SkillsRepository(t).listSkills();
      expect(skills, isEmpty);
    });

    test('returns empty list when skills value is not a list', () async {
      final t = _FakeTransport({'skills': 'bad_value'});
      final skills = await SkillsRepository(t).listSkills();
      expect(skills, isEmpty);
    });

    test('skips non-Map entries gracefully', () async {
      final t = _FakeTransport({
        'skills': [_skillJson(id: 'ok'), 'not_a_map', 42],
      });
      final skills = await SkillsRepository(t).listSkills();
      // Only the Map entry is parsed; other types are skipped.
      expect(skills, hasLength(1));
      expect(skills.first.id, 'ok');
    });

    test('parses category from response', () async {
      final t = _FakeTransport({
        'skills': [_skillJson(id: 'c1', category: 'research')],
      });
      final skills = await SkillsRepository(t).listSkills();
      expect(skills.first.category, 'research');
    });

    test('returns Skill objects (not raw maps)', () async {
      final t = _FakeTransport({
        'skills': [_skillJson()],
      });
      final skills = await SkillsRepository(t).listSkills();
      expect(skills.first, isA<Skill>());
    });

    test('propagates transport errors', () async {
      final t = _FakeTransport({});
      t.errorToThrow = Exception('network_error');
      expect(
        () => SkillsRepository(t).listSkills(),
        throwsException,
      );
    });
  });

  group('SkillsRepository.setEnabled', () {
    test('PATCH /api/skills/{id} with enabled=true', () async {
      final t = _FakeTransport({'status': 'ok'});
      await SkillsRepository(t).setEnabled('s42', enabled: true);

      expect(t.lastMethod, 'PATCH');
      expect(t.lastPath, '/api/skills/s42');
      expect(t.lastBody, containsPair('enabled', true));
    });

    test('PATCH /api/skills/{id} with enabled=false', () async {
      final t = _FakeTransport({'status': 'ok'});
      await SkillsRepository(t).setEnabled('s99', enabled: false);

      expect(t.lastPath, '/api/skills/s99');
      expect(t.lastBody, containsPair('enabled', false));
    });

    test('encodes the id correctly in the path', () async {
      final t = _FakeTransport({'status': 'ok'});
      await SkillsRepository(t).setEnabled('my-special-skill', enabled: true);
      expect(t.lastPath, '/api/skills/my-special-skill');
    });

    test('propagates server errors to caller', () async {
      final t = _FakeTransport({});
      t.errorToThrow = Exception('server_error');
      expect(
        () => SkillsRepository(t).setEnabled('x', enabled: true),
        throwsException,
      );
    });
  });
}
