/// Primary (locked) gateway URL — the self-hosted remote-access front door.
/// 2026-07-22: Vodafone moved the home line behind CGNAT, which killed the old
/// DuckDNS + Caddy port-forward (inbound is unreachable, and reverting DNS can't
/// fix it). Switched to a Tailscale Funnel OUTBOUND tunnel: only the Mac runs
/// Tailscale (nothing on the phone — the phone just opens this URL), it dials
/// OUT so neither CGNAT nor a VPN can block it, and — exactly like before — TLS
/// terminates ON THE MAC, so no third party (not even Tailscale) decrypts.
/// Still fully self-owned. The in-app server field is hidden; the app PREFERS
/// this URL and only falls back to the LAN hosts when it is unreachable.
const String kDefaultBaseUrl = 'https://blckit.tail57f754.ts.net';

/// DEAD (2026-07-22): the retired DuckDNS + router port-forward front door,
/// unreachable under Vodafone CGNAT. Kept ONLY as a server alias so a device
/// with a session cached under this host is not force-logged-out on the switch
/// to Funnel. Delete after the Funnel cutover has soaked on-device.
/// See memory: project_frp_tunnel_remote_access.
const String kLegacyDuckdnsBaseUrl = 'https://lazyclaw.duckdns.org:8443';

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
/// runtime gateway rotates among these (Funnel front door ↔ mDNS name ↔ LAN
/// IP ↔ USB loopback) as the network changes, so anything host-keyed (the
/// offline auth cache) must treat them as the SAME server — otherwise a
/// host-flip forces a spurious re-login. The live candidates are kept in
/// lock-step with `ServerConfig._candidates`; [kLegacyDuckdnsBaseUrl] is an
/// ALIAS-ONLY extra (retired host, never probed) so a session cached under the
/// old DuckDNS host survives the switch to Funnel. Runtime-DISCOVERED hosts are
/// aliased via `ServerAliasRegistry` — check aliases through it, not this list.
const List<String> kServerAliases = <String>[
  kDefaultBaseUrl,
  kLegacyDuckdnsBaseUrl,
  kLanFallbackBaseUrl,
  kLanFallbackIpBaseUrl,
  kUsbLoopbackBaseUrl,
];

/// Compile-time version/build, used ONLY as a fallback. The real running
/// version is read at runtime from `package_info_plus` inside the self-update
/// service ([SelfUpdateService]); these constants apply only when that read
/// fails (e.g. a non-platform test harness). Keep them in sync with the
/// `version:` field in `pubspec.yaml` — enforced by
/// `test/core/app_version_constants_test.dart`, because these had silently
/// drifted 34 builds behind (1.21.22+81 vs pubspec 1.22.5+115) and the
/// self-update check would then compare against the wrong build number.
const String kAppVersion = '1.23.1';
const int kAppBuild = 119;
