import 'constants/app_constants.dart';

/// Pure update-availability check for the sideloaded APK.
///
/// The published `version.json` carries both a human version *name*
/// (e.g. `1.7.1`) and a monotonically increasing *build* number. Build numbers
/// are monotonic by design, so they — not the human name — are the source of
/// truth for ordering. Comparing the name (a mere inequality) would offer an
/// OLDER server version as an "update" (a downgrade) whenever the names differ;
/// we instead gate strictly on the build number.
///
/// An empty/absent server version is treated as "no update" so a missing or
/// malformed `version.json` can't raise a false alarm.
bool isUpdateAvailable({
  required String latestVersion,
  required int latestBuild,
  String currentVersion = kAppVersion,
  int currentBuild = kAppBuild,
}) {
  if (latestVersion.isEmpty) return false; // no/empty manifest → no update
  return latestBuild > currentBuild;
}
