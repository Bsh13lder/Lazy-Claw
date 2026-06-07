import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/specialist.dart';
import 'package:lazyclaw_mobile/repositories/specialists_repository.dart';

// ── Fake transport ───────────────────────────────────────────────────────────

class _FakeTransport implements SpecialistsTransport {
  String? lastMethod;
  String? lastPath;

  Map<String, dynamic> response;
  Object? errorToThrow;

  _FakeTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    if (errorToThrow != null) throw errorToThrow!;
    lastMethod = 'GET';
    lastPath = path;
    return response;
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    if (errorToThrow != null) throw errorToThrow!;
    lastMethod = 'DELETE';
    lastPath = path;
    return response;
  }
}

// ── Fixtures ─────────────────────────────────────────────────────────────────

Map<String, dynamic> _specialistJson({
  String name = 'browser_specialist',
  String displayName = 'Browser Specialist',
  List<String> tools = const ['browser'],
  String? model = 'smart',
  bool includeScraper = true,
  bool isBuiltin = true,
}) =>
    {
      'name': name,
      'display_name': displayName,
      'system_prompt': 'prompt body',
      'tools': tools,
      'model': model,
      'include_scraper': includeScraper,
      'is_builtin': isBuiltin,
    };

// ── Tests ────────────────────────────────────────────────────────────────────

void main() {
  group('SpecialistsRepository.listSpecialists', () {
    test('GET /api/specialists and parses the list', () async {
      final t = _FakeTransport({
        'ok': true,
        'specialists': [
          _specialistJson(name: 'a', isBuiltin: true),
          _specialistJson(name: 'b', isBuiltin: false),
        ],
      });
      final repo = SpecialistsRepository(t);
      final list = await repo.listSpecialists();

      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/specialists');
      expect(list, hasLength(2));
      expect(list[0], isA<Specialist>());
      expect(list[0].name, 'a');
      expect(list[0].isBuiltin, isTrue);
      expect(list[1].isBuiltin, isFalse);
    });

    test('returns empty list when specialists key is missing', () async {
      final list = await SpecialistsRepository(_FakeTransport({'ok': true}))
          .listSpecialists();
      expect(list, isEmpty);
    });

    test('returns empty list when specialists is not a list', () async {
      final list = await SpecialistsRepository(
              _FakeTransport({'specialists': 'bad'}))
          .listSpecialists();
      expect(list, isEmpty);
    });

    test('skips non-Map entries gracefully', () async {
      final t = _FakeTransport({
        'specialists': [_specialistJson(name: 'ok'), 'nope', 7],
      });
      final list = await SpecialistsRepository(t).listSpecialists();
      expect(list, hasLength(1));
      expect(list.first.name, 'ok');
    });

    test('propagates transport errors', () async {
      final t = _FakeTransport({})..errorToThrow = Exception('network');
      expect(
        () => SpecialistsRepository(t).listSpecialists(),
        throwsException,
      );
    });
  });

  group('SpecialistsRepository.deleteSpecialist', () {
    test('DELETE /api/specialists/{name}', () async {
      final t = _FakeTransport({'ok': true});
      await SpecialistsRepository(t).deleteSpecialist('my_specialist');
      expect(t.lastMethod, 'DELETE');
      expect(t.lastPath, '/api/specialists/my_specialist');
    });

    test('URL-encodes the name in the path', () async {
      final t = _FakeTransport({'ok': true});
      await SpecialistsRepository(t).deleteSpecialist('a b/c');
      expect(t.lastPath, '/api/specialists/a%20b%2Fc');
    });

    test('propagates server errors to caller', () async {
      final t = _FakeTransport({})..errorToThrow = Exception('forbidden');
      expect(
        () => SpecialistsRepository(t).deleteSpecialist('x'),
        throwsException,
      );
    });
  });
}
