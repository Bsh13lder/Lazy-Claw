import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/skill.dart';

Map<String, dynamic> _skillJson({
  String id = 's1',
  String name = 'web_search',
  String description = 'Searches the web',
  String skillType = 'builtin',
  bool enabled = true,
  String? category,
}) =>
    {
      'id': id,
      'name': name,
      'description': description,
      'skill_type': skillType,
      'enabled': enabled,
      'category': category,
    };

void main() {
  group('Skill.fromJson', () {
    test('parses all required fields', () {
      final s = Skill.fromJson(_skillJson());
      expect(s.id, 's1');
      expect(s.name, 'web_search');
      expect(s.description, 'Searches the web');
      expect(s.skillType, 'builtin');
      expect(s.enabled, isTrue);
      expect(s.category, isNull);
    });

    test('parses optional category', () {
      final s = Skill.fromJson(_skillJson(category: 'research'));
      expect(s.category, 'research');
    });

    test('enabled defaults false when null', () {
      final json = _skillJson();
      json.remove('enabled');
      final s = Skill.fromJson(json);
      expect(s.enabled, isFalse);
    });

    test('enabled parses int 1 as true', () {
      final json = _skillJson();
      json['enabled'] = 1;
      expect(Skill.fromJson(json).enabled, isTrue);
    });

    test('enabled parses int 0 as false', () {
      final json = _skillJson();
      json['enabled'] = 0;
      expect(Skill.fromJson(json).enabled, isFalse);
    });

    test('enabled parses string "true" as true', () {
      final json = _skillJson();
      json['enabled'] = 'true';
      expect(Skill.fromJson(json).enabled, isTrue);
    });

    test('skill_type defaults to "other" when missing', () {
      final json = _skillJson();
      json.remove('skill_type');
      expect(Skill.fromJson(json).skillType, 'other');
    });

    test('tolerates null name and description', () {
      final json = {
        'id': 'x',
        'skill_type': 'mcp',
        'enabled': false,
      };
      final s = Skill.fromJson(json);
      expect(s.name, '');
      expect(s.description, '');
    });
  });

  group('Skill.displayCategory', () {
    test('returns category when set', () {
      final s = Skill.fromJson(_skillJson(skillType: 'instruction', category: 'productivity'));
      expect(s.displayCategory, 'productivity');
    });

    test('falls back to skillType when category is null', () {
      final s = Skill.fromJson(_skillJson(skillType: 'instruction'));
      expect(s.displayCategory, 'instruction');
    });
  });

  group('Skill.copyWith', () {
    test('creates new instance with updated enabled', () {
      final original = Skill.fromJson(_skillJson(enabled: true));
      final updated = original.copyWith(enabled: false);
      expect(updated.enabled, isFalse);
      expect(original.enabled, isTrue); // immutable — original unchanged
      expect(updated.id, original.id);
    });

    test('preserves unmodified fields', () {
      final original = Skill.fromJson(_skillJson(name: 'browser', category: 'web'));
      final copy = original.copyWith(enabled: false);
      expect(copy.name, 'browser');
      expect(copy.category, 'web');
    });
  });

  group('Skill equality', () {
    test('equal when ids match', () {
      final a = Skill.fromJson(_skillJson(id: 'abc'));
      final b = Skill.fromJson(_skillJson(id: 'abc', name: 'different'));
      expect(a, equals(b));
    });

    test('not equal when ids differ', () {
      final a = Skill.fromJson(_skillJson(id: 'x'));
      final b = Skill.fromJson(_skillJson(id: 'y'));
      expect(a, isNot(equals(b)));
    });
  });

  group('Skill.toJson', () {
    test('round-trips through fromJson', () {
      final original = Skill.fromJson(_skillJson(category: 'core'));
      final roundTripped = Skill.fromJson(original.toJson());
      expect(roundTripped.id, original.id);
      expect(roundTripped.name, original.name);
      expect(roundTripped.category, original.category);
      expect(roundTripped.enabled, original.enabled);
    });
  });
}
