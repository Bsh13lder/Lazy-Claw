import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/self_update.dart';

/// A scriptable [SelfUpdateGateway] double: returns canned server/current
/// values, or throws from [fetchVersion] when [throwOnFetch] is set.
class _FakeGateway implements SelfUpdateGateway {
  _FakeGateway({
    required this.serverJson,
    required this.current,
    this.throwOnFetch = false,
  });

  final Map<String, dynamic> serverJson;
  final ({String version, int build}) current;
  final bool throwOnFetch;

  @override
  Future<Map<String, dynamic>> fetchVersion() async {
    if (throwOnFetch) {
      throw Exception('server unreachable');
    }
    return serverJson;
  }

  @override
  Future<({String version, int build})> currentVersion() async => current;
}

void main() {
  group('SelfUpdateService.checkForUpdate', () {
    test('strictly newer published build → non-null UpdateInfo', () async {
      final service = SelfUpdateService(
        _FakeGateway(
          serverJson: {'version': '1.8.0', 'build': 14, 'sha256': 'abc'},
          current: (version: '1.7.1', build: 13),
        ),
      );

      final info = await service.checkForUpdate();

      expect(info, isNotNull);
      expect(info!.version, '1.8.0');
      expect(info.build, 14);
      expect(info.sha256, 'abc');
      expect(info.apkPath, '/api/mobile/apk');
    });

    test('server build == current build (same version) → null', () async {
      final service = SelfUpdateService(
        _FakeGateway(
          serverJson: {'version': '1.7.1', 'build': 13, 'sha256': 'abc'},
          current: (version: '1.7.1', build: 13),
        ),
      );

      expect(await service.checkForUpdate(), isNull);
    });

    test('fetchVersion throws → null (never crashes)', () async {
      final service = SelfUpdateService(
        _FakeGateway(
          serverJson: const {},
          current: (version: '1.7.1', build: 13),
          throwOnFetch: true,
        ),
      );

      expect(await service.checkForUpdate(), isNull);
    });

    test('malformed server JSON (missing version) → null', () async {
      final service = SelfUpdateService(
        _FakeGateway(
          serverJson: const {'build': 99},
          current: (version: '1.7.1', build: 13),
        ),
      );

      expect(await service.checkForUpdate(), isNull);
    });

    test('null sha256 in server JSON is tolerated', () async {
      final service = SelfUpdateService(
        _FakeGateway(
          serverJson: {'version': '1.9.0', 'build': 20},
          current: (version: '1.7.1', build: 13),
        ),
      );

      final info = await service.checkForUpdate();
      expect(info, isNotNull);
      expect(info!.sha256, isNull);
    });
  });
}
