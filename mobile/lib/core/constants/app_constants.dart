/// Primary (locked) gateway URL. This build is PINNED to the remote-access
/// tunnel (frp + sslip.io, see docs/REMOTE_ACCESS.md) — the in-app server field
/// is hidden, so the user can never point it elsewhere. The app PREFERS this URL
/// and only falls back to [kLanFallbackBaseUrl] when it is unreachable.
const String kDefaultBaseUrl = 'https://detoxify-culinary-resonant.ngrok-free.dev';

/// Home-LAN fallback, used automatically when the tunnel is unreachable — e.g.
/// before the tunnel is deployed, or when the phone is on the home WiFi. This is
/// NOT user-editable; it only makes the locked app self-heal instead of being
/// stranded with no reachable server. `BlckIt.local` is the Mac's mDNS name.
const String kLanFallbackBaseUrl = 'http://BlckIt.local:18789';

/// Compile-time version/build, used ONLY as a fallback. The real running
/// version is read at runtime from `package_info_plus` inside the self-update
/// service ([SelfUpdateService]); these constants apply only when that read
/// fails (e.g. a non-platform test harness). Keep them in sync with the
/// `version:` field in `pubspec.yaml`.
const String kAppVersion = '1.21.5';
const int kAppBuild = 65;
