// The compile-time version fallbacks must track pubspec.yaml.
//
// `SelfUpdateService` reads the real version from package_info_plus and falls
// back to these constants when that read fails. They had drifted to 1.21.22+81
// while pubspec was at 1.22.5+115 — 34 builds stale. A comment saying "keep
// them in sync" is not a mechanism; this test is.
//
// Why it matters: `isUpdateAvailable` compares `latestBuild > currentBuild`.
// On any path where package_info is unavailable the app would compare against
// build 81 and report an update that is already installed — or, if the
// constants ever ran AHEAD, hide a real one.

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/constants/app_constants.dart';

void main() {
  test('kAppVersion / kAppBuild match pubspec.yaml', () {
    final pubspec = File('pubspec.yaml').readAsStringSync();
    final match = RegExp(r'^version:\s*(\S+)\+(\d+)\s*$', multiLine: true)
        .firstMatch(pubspec);

    expect(match, isNotNull, reason: 'could not parse version: from pubspec.yaml');
    final pubspecVersion = match!.group(1)!;
    final pubspecBuild = int.parse(match.group(2)!);

    expect(
      kAppVersion,
      pubspecVersion,
      reason: 'kAppVersion drifted from pubspec.yaml — update '
          'lib/core/constants/app_constants.dart when releasing',
    );
    expect(
      kAppBuild,
      pubspecBuild,
      reason: 'kAppBuild drifted from pubspec.yaml — the self-update fallback '
          'would compare against the wrong build number',
    );
  });
}
