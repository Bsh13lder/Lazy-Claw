/// Primary (locked) gateway URL. This build is PINNED to the self-hosted
/// remote-access front door (DuckDNS + Caddy/Let's Encrypt over a forwarded
/// router port — see docs/REMOTE_ACCESS.md). Fully self-owned: TLS terminates
/// on the Mac, no third party decrypts traffic. The in-app server field is
/// hidden, so the user can never point it elsewhere. The app PREFERS this URL
/// and only falls back to [kLanFallbackBaseUrl] when it is unreachable.
const String kDefaultBaseUrl = 'https://lazyclaw.duckdns.org:8443';

/// Home-LAN fallback, used automatically when the tunnel is unreachable — e.g.
/// before the tunnel is deployed, or when the phone is on the home WiFi. This is
/// NOT user-editable; it only makes the locked app self-heal instead of being
/// stranded with no reachable server. `BlckIt.local` is the Mac's mDNS name.
const String kLanFallbackBaseUrl = 'http://BlckIt.local:18789';

/// Second home-LAN fallback: the Mac's LAN IP directly. Used when the platform's
/// HTTP client can't resolve the `BlckIt.local` mDNS name (Android's `.local`
/// resolution is unreliable inside Dart's HttpClient, even when the OS shell can
/// resolve it). Assumes the Mac keeps this DHCP address (pin it with a router
/// reservation). Only reached on the home network — off-home it simply won't
/// answer and the app moves on.
const String kLanFallbackIpBaseUrl = 'http://192.168.0.12:18789';

/// Every URL the ONE self-hosted server is reachable as. The runtime gateway
/// rotates among these (DuckDNS front door ↔ mDNS name ↔ LAN IP) as the network
/// changes, so anything host-keyed (the offline auth cache) must treat them as
/// the SAME server — otherwise a host-flip forces a spurious re-login. Keep this
/// in lock-step with `ServerConfig._candidates`.
const List<String> kServerAliases = <String>[
  kDefaultBaseUrl,
  kLanFallbackBaseUrl,
  kLanFallbackIpBaseUrl,
];

/// Compile-time version/build, used ONLY as a fallback. The real running
/// version is read at runtime from `package_info_plus` inside the self-update
/// service ([SelfUpdateService]); these constants apply only when that read
/// fails (e.g. a non-platform test harness). Keep them in sync with the
/// `version:` field in `pubspec.yaml`.
const String kAppVersion = '1.21.22';
const int kAppBuild = 81;
