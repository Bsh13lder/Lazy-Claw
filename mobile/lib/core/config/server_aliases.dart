import '../constants/app_constants.dart';

/// Runtime registry of every URL known to address the ONE self-hosted server.
///
/// The static [kServerAliases] cover the compile-time candidates (DuckDNS /
/// mDNS / pinned LAN IP / USB loopback), but a LAN-sweep DISCOVERED host
/// (e.g. `http://192.168.0.15:18789` after DHCP moved the Mac) is only known
/// at runtime. Anything host-keyed — the offline auth cache above all — must
/// treat a discovered host as the same server, otherwise the automatic switch
/// to it force-logs the user out.
///
/// Discovered hosts are persisted by `ServerConfig.rememberDiscovered` and
/// re-seeded here at startup (`ServerConfig.seedDynamicAliases`), so alias
/// checks stay SYNCHRONOUS (the auth-cache decode path is sync).
class ServerAliasRegistry {
  static final Set<String> _dynamic = <String>{};

  /// Register one runtime-discovered URL as an alias of the self-hosted box.
  static void add(String url) => _dynamic.add(_normalize(url));

  /// Register many at once (startup seed from the persisted discovered list).
  static void seed(Iterable<String> urls) => urls.forEach(add);

  /// Replace the dynamic set with EXACTLY [urls] — called after the persisted
  /// discovered list is re-saved, so an evicted host also stops being an alias
  /// (the registry honors the same cap as the store).
  static void syncDynamic(Iterable<String> urls) {
    _dynamic
      ..clear()
      ..addAll(urls.map(_normalize));
  }

  /// True when [url] is a known address of the ONE self-hosted server —
  /// compile-time candidate or runtime-discovered. A foreign URL is never an
  /// alias, so a cached identity still cannot unlock against another server.
  static bool isAlias(String url) {
    final n = _normalize(url);
    return kServerAliases.contains(n) || _dynamic.contains(n);
  }

  /// Clear runtime aliases (test isolation only).
  static void resetForTests() => _dynamic.clear();

  /// Same shape as `ServerConfig.normalizeBaseUrl`, duplicated locally so this
  /// registry has no import cycle with the resolver.
  static String _normalize(String raw) {
    var v = raw.trim();
    if (!v.startsWith('http://') && !v.startsWith('https://')) {
      v = 'http://$v';
    }
    if (v.endsWith('/')) v = v.substring(0, v.length - 1);
    return v;
  }
}
