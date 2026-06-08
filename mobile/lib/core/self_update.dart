import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:package_info_plus/package_info_plus.dart';

import '../providers/auth_provider.dart';
import 'api/api_client.dart';
import 'constants/app_constants.dart';
import 'version_check.dart';

/// Describes a published APK that is strictly newer than what is installed.
class UpdateInfo {
  final String version;
  final int build;
  final String? sha256;
  final String apkPath;

  const UpdateInfo({
    required this.version,
    required this.build,
    this.sha256,
    this.apkPath = '/api/mobile/apk',
  });
}

/// Testable seam over the network + the platform's running version.
///
/// Splitting these two reads behind an interface lets the unit tests drive
/// [SelfUpdateService] with canned values (no Dio, no platform channels).
abstract class SelfUpdateGateway {
  /// GET `/api/mobile/version` → the raw JSON map.
  Future<Map<String, dynamic>> fetchVersion();

  /// The actually-running app version + build (from `package_info_plus`).
  Future<({String version, int build})> currentVersion();
}

/// Compares the published `version.json` against the running build and, when a
/// strictly-newer build is available, returns an [UpdateInfo] describing it.
class SelfUpdateService {
  SelfUpdateService(this._gw);

  final SelfUpdateGateway _gw;

  /// Returns [UpdateInfo] when a strictly-newer build is published, else null.
  ///
  /// Never throws: an unreachable / malformed server, or any platform error,
  /// resolves to `null` so the caller can treat "no update" and "couldn't
  /// check" uniformly without a crash.
  Future<UpdateInfo?> checkForUpdate() async {
    try {
      final json = await _gw.fetchVersion();
      final current = await _gw.currentVersion();

      // Validate at the boundary — never trust the server payload's shape.
      final latestVersion = json['version']?.toString() ?? '';
      final latestBuild = (json['build'] as num?)?.toInt() ?? 0;
      final sha256 = json['sha256']?.toString();

      final available = isUpdateAvailable(
        latestVersion: latestVersion,
        latestBuild: latestBuild,
        currentVersion: current.version,
        currentBuild: current.build,
      );
      if (!available) return null;

      final normalizedSha = sha256 == null || sha256.isEmpty ? null : sha256;
      if (normalizedSha == null) {
        // Not fatal — the server normally provides a hash. But without one the
        // installer can't verify integrity, so surface it for diagnosis.
        debugPrint(
          'self_update: server published an update without a sha256 — '
          'install integrity will not be verified',
        );
      }

      return UpdateInfo(
        version: latestVersion,
        build: latestBuild,
        sha256: normalizedSha,
      );
    } catch (_) {
      // Server unreachable / malformed JSON / platform error → treat as "none".
      return null;
    }
  }
}

/// Concrete [SelfUpdateGateway] backed by the [ApiClient] and `package_info_plus`.
///
/// Falls back to the compile-time [kAppVersion]/[kAppBuild] constants only if
/// `PackageInfo` can't be read (e.g. in a non-platform test harness).
class ApiSelfUpdateGateway implements SelfUpdateGateway {
  ApiSelfUpdateGateway(this._client);

  final ApiClient _client;

  @override
  Future<Map<String, dynamic>> fetchVersion() {
    return _client.get<Map<String, dynamic>>('/api/mobile/version');
  }

  @override
  Future<({String version, int build})> currentVersion() async {
    try {
      final info = await PackageInfo.fromPlatform();
      final build = int.tryParse(info.buildNumber) ?? kAppBuild;
      final version = info.version.isNotEmpty ? info.version : kAppVersion;
      return (version: version, build: build);
    } catch (_) {
      return (version: kAppVersion, build: kAppBuild);
    }
  }
}

/// The self-update service, wired to the live [ApiClient].
final selfUpdateServiceProvider = Provider<SelfUpdateService>((ref) {
  return SelfUpdateService(ApiSelfUpdateGateway(ref.watch(apiClientProvider)));
});

/// Holds the pending update for the UI banner; `null` = none.
///
/// Seeded by the Settings "Check for update" action (and any startup check).
final updateAvailableProvider = StateProvider<UpdateInfo?>((ref) => null);
