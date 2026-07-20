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
/// resolve it). This is only a HINT of where the Mac usually lives — when DHCP
/// moves the Mac, the LAN sweep (`LanDiscovery`) finds the new address and it
/// is remembered as a discovered host, so a stale value here costs one cheap
/// failed probe, not connectivity. (2026-07-19: .12 → .15 — the phone itself
/// took .12 after the hotspot experiments, which is exactly how the old
/// hard-pinned value went dead.)
const String kLanFallbackIpBaseUrl = 'http://192.168.0.15:18789';

/// The self-hosted gateway's TCP port — the LAN sweep scans for this.
const int kGatewayPort = 18789;

/// USB path: with `adb reverse tcp:18789 tcp:18789` active, the phone reaches
/// the Mac's gateway on its own loopback. Probed as the LAST-resort candidate —
/// it fails instantly (connection refused) when no adb reverse is set up, and
/// gives a guaranteed cable path when WiFi is unusable.
const String kUsbLoopbackBaseUrl = 'http://127.0.0.1:18789';

/// How many LAN-sweep discovered hosts are persisted (most-recent first).
const int kMaxDiscoveredHosts = 5;

/// Every COMPILE-TIME URL the ONE self-hosted server is reachable as. The
/// runtime gateway rotates among these (DuckDNS front door ↔ mDNS name ↔ LAN
/// IP ↔ USB loopback) as the network changes, so anything host-keyed (the
/// offline auth cache) must treat them as the SAME server — otherwise a
/// host-flip forces a spurious re-login. Keep this in lock-step with
/// `ServerConfig._candidates`. Runtime-DISCOVERED hosts are aliased via
/// `ServerAliasRegistry` — check aliases through it, not this list.
const List<String> kServerAliases = <String>[
  kDefaultBaseUrl,
  kLanFallbackBaseUrl,
  kLanFallbackIpBaseUrl,
  kUsbLoopbackBaseUrl,
];

/// Compile-time version/build, used ONLY as a fallback. The real running
/// version is read at runtime from `package_info_plus` inside the self-update
/// service ([SelfUpdateService]); these constants apply only when that read
/// fails (e.g. a non-platform test harness). Keep them in sync with the
/// `version:` field in `pubspec.yaml`.
const String kAppVersion = '1.21.22';
const int kAppBuild = 81;
