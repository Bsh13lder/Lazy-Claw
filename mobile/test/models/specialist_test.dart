import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/specialist.dart';

void main() {
  group('Specialist.fromJson', () {
    test('parses the full envelope shape', () {
      final s = Specialist.fromJson({
        'name': 'browser_specialist',
        'display_name': 'Browser Specialist',
        'system_prompt': 'You are a browser automation specialist.',
        'tools': ['browser', 'web_search'],
        'model': 'smart',
        'include_scraper': true,
        'is_builtin': true,
      });
      expect(s.name, 'browser_specialist');
      expect(s.displayName, 'Browser Specialist');
      expect(s.systemPrompt, 'You are a browser automation specialist.');
      expect(s.tools, ['browser', 'web_search']);
      expect(s.model, 'smart');
      expect(s.includeScraper, isTrue);
      expect(s.isBuiltin, isTrue);
    });

    test('falls back to name when display_name is empty', () {
      final s = Specialist.fromJson({
        'name': 'code_specialist',
        'display_name': '',
        'system_prompt': 'body',
      });
      expect(s.displayName, 'code_specialist');
    });

    test('null model stays null (not the string "null")', () {
      final s = Specialist.fromJson({
        'name': 'x',
        'system_prompt': 'b',
        'model': null,
      });
      expect(s.model, isNull);
    });

    test('defaults tools to empty list when missing or wrong type', () {
      final a = Specialist.fromJson({'name': 'a', 'system_prompt': 'b'});
      final b = Specialist.fromJson(
          {'name': 'b', 'system_prompt': 'b', 'tools': 'not_a_list'});
      expect(a.tools, isEmpty);
      expect(b.tools, isEmpty);
    });

    test('coerces non-bool include_scraper / is_builtin', () {
      final s = Specialist.fromJson({
        'name': 'x',
        'system_prompt': 'b',
        'include_scraper': 'true',
        'is_builtin': 1,
      });
      expect(s.includeScraper, isTrue);
      expect(s.isBuiltin, isTrue);
    });

    test('defaults flags to false when absent', () {
      final s = Specialist.fromJson({'name': 'x', 'system_prompt': 'b'});
      expect(s.includeScraper, isFalse);
      expect(s.isBuiltin, isFalse);
    });
  });

  group('Specialist value semantics', () {
    test('equality is by name', () {
      const a = Specialist(name: 'x', displayName: 'X', systemPrompt: 'a');
      const b = Specialist(name: 'x', displayName: 'Y', systemPrompt: 'b');
      const c = Specialist(name: 'z', displayName: 'X', systemPrompt: 'a');
      expect(a, equals(b));
      expect(a, isNot(equals(c)));
      expect(a.hashCode, b.hashCode);
    });

    test('copyWith overrides only the given fields', () {
      const s = Specialist(
        name: 'x',
        displayName: 'X',
        systemPrompt: 'a',
        tools: ['t1'],
        includeScraper: true,
      );
      final u = s.copyWith(displayName: 'New');
      expect(u.displayName, 'New');
      expect(u.name, 'x');
      expect(u.tools, ['t1']);
      expect(u.includeScraper, isTrue);
    });
  });
}
