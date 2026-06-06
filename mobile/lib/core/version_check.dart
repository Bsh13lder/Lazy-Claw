import 'constants/app_constants.dart';

/// Pure update-availability check for the sideloaded APK.
///
/// The published `version.json` carries both a human version *name*
/// (e.g. `1.7.1`) and a monotonically increasing *build* number. The old
/// check compared only the name, so a build-only bump (`+12 → +13`) was
/// invisible. We now treat an update as available when EITHER the name
/// differs OR the published build is strictly higher than the installed one.
///
/// An empty/absent server version is treated as "no update" so a missing or
/// malformed `version.json` can't raise a false alarm.
bool isUpdateAvailable({
  required String latestVersion,
  required int latestBuild,
  String currentVersion = kAppVersion,
  int currentBuild = kAppBuild,
}) {
  if (latestVersion.isEmpty) return false;
  if (latestVersion != currentVersion) return true;
  return latestBuild > currentBuild;
}
