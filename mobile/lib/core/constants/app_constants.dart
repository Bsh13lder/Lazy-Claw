/// Default gateway when the user hasn't set one. Uses the self-hosted Mac's
/// mDNS name (`BlckIt.local`) rather than a numeric IP so it survives DHCP / IP
/// changes on the LAN. (Override per-install via Settings → Server URL.)
const String kDefaultBaseUrl = 'http://BlckIt.local:18789';

/// Compile-time version/build, used ONLY as a fallback. The real running
/// version is read at runtime from `package_info_plus` inside the self-update
/// service ([SelfUpdateService]); these constants apply only when that read
/// fails (e.g. a non-platform test harness). Keep them in sync with the
/// `version:` field in `pubspec.yaml`.
const String kAppVersion = '1.8.0';
const int kAppBuild = 21;
const String kSecureBaseUrlKey = 'lazyclaw_base_url';
