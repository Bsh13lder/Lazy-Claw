/// Default gateway when the user hasn't set one. The self-hosted box on the LAN.
const String kDefaultBaseUrl = 'http://127.0.0.1:18789';

/// Compile-time version/build, used ONLY as a fallback. The real running
/// version is read at runtime from `package_info_plus` inside the self-update
/// service ([SelfUpdateService]); these constants apply only when that read
/// fails (e.g. a non-platform test harness). Keep them in sync with the
/// `version:` field in `pubspec.yaml`.
const String kAppVersion = '1.7.1';
const int kAppBuild = 13;
const String kSecureBaseUrlKey = 'lazyclaw_base_url';
