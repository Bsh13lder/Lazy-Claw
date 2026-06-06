import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/version_check.dart';

void main() {
  group('isUpdateAvailable', () {
    test('same name and build → no update', () {
      expect(
        isUpdateAvailable(
          latestVersion: '1.7.1',
          latestBuild: 13,
          currentVersion: '1.7.1',
          currentBuild: 13,
        ),
        isFalse,
      );
    });

    test('newer version name → update', () {
      expect(
        isUpdateAvailable(
          latestVersion: '1.7.1',
          latestBuild: 13,
          currentVersion: '1.7.0',
          currentBuild: 12,
        ),
        isTrue,
      );
    });

    test('same name but higher build → update (the silent-bump case)', () {
      expect(
        isUpdateAvailable(
          latestVersion: '1.7.0',
          latestBuild: 13,
          currentVersion: '1.7.0',
          currentBuild: 12,
        ),
        isTrue,
      );
    });

    test('empty/unknown server version → no update (avoid false alarm)', () {
      expect(
        isUpdateAvailable(
          latestVersion: '',
          latestBuild: 0,
          currentVersion: '1.7.1',
          currentBuild: 13,
        ),
        isFalse,
      );
    });

    test('older server build than installed → no update (no downgrade nag)', () {
      expect(
        isUpdateAvailable(
          latestVersion: '1.7.1',
          latestBuild: 12,
          currentVersion: '1.7.1',
          currentBuild: 13,
        ),
        isFalse,
      );
    });
  });
}
