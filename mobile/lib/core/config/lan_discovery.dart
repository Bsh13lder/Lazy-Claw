import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../constants/app_constants.dart';

/// One network interface as the sweep sees it: kernel name + IPv4 addresses.
/// A record (not `NetworkInterface`) so tests can fabricate interfaces without
/// touching `dart:io` platform internals.
typedef SweepInterface = ({String name, List<String> ipv4});

/// Lists the device's interfaces. Injectable so [LanDiscovery.sweep] can be
/// unit-tested without real network hardware.
typedef InterfaceLister = Future<List<SweepInterface>> Function();

/// Probes ONE candidate LAN address for a live lazyclaw gateway. Injectable
/// for tests; the production probe is TCP-connect + identity health check.
typedef LanHostProbe = Future<bool> Function(String ip);

/// A full LAN sweep. Returns base URLs of CONFIRMED lazyclaw gateways —
/// callers may trust entries without re-probing.
typedef LanSweep = Future<List<String>> Function();

/// Last-resort discovery of the self-hosted gateway on the phone's own
/// subnets — the recovery path for "DHCP moved the Mac and every known
/// candidate is dead" (and for the laptop-tethered-to-phone-hotspot case,
/// where the server lives on the hotspot AP subnet).
///
/// Pattern per ecosystem practice (Home Assistant tooling, `network_tools`,
/// `lan_scanner`): enumerate own IPv4 interfaces, sweep each /24 with bounded
/// parallel TCP connects, then confirm identity over HTTP so a stranger
/// listening on the port is never adopted. Requires only the INTERNET
/// permission — no multicast lock, no location.
class LanDiscovery {
  /// Per-peer TCP connect gate. Short — a silent host on a healthy LAN fails
  /// fast, and the whole /24 finishes in ~2–3s at [defaultConcurrency].
  static const Duration connectTimeout = Duration(milliseconds: 400);

  /// Timeout for the HTTP identity confirmation of a TCP-open peer.
  static const Duration identityTimeout = Duration(seconds: 2);

  /// Parallel connect budget. 48–64 is the established safe band on Android —
  /// higher risks file-descriptor pressure and OEM WiFi SYN throttling.
  static const int defaultConcurrency = 48;

  /// Interface-name prefixes worth sweeping: WiFi (`wlan`), hotspot AP
  /// (`ap`/`swlan`), ethernet (`eth`/`en`), USB tether (`rndis`/`ncm`/`usb`)
  /// and bridges. A whitelist — cellular data interfaces (`rmnet`/`ccmni`/
  /// `pdp`) must NEVER be swept: their subnet is carrier CGNAT space and a
  /// sweep there is pointless mobile traffic.
  static const List<String> _sweepablePrefixes = [
    'wlan',
    'ap',
    'swlan',
    'eth',
    'en',
    'rndis',
    'ncm',
    'usb',
    'bridge',
  ];

  /// True when [name] is an interface whose subnet may contain the gateway.
  static bool isSweepableInterface(String name) {
    final n = name.toLowerCase();
    return _sweepablePrefixes.any(n.startsWith);
  }

  /// The /24 peers of [ownIp] (1–254, excluding [ownIp] itself), or empty when
  /// [ownIp] is not a valid RFC1918 private address — public, loopback and
  /// link-local ranges are never swept. Dart exposes no prefix length, so /24
  /// is assumed (the safe home/hotspot default).
  static List<String> peersOf(String ownIp) {
    final parts = ownIp.split('.');
    if (parts.length != 4) return const [];
    final octets = <int>[];
    for (final p in parts) {
      final v = int.tryParse(p);
      if (v == null || v < 0 || v > 255) return const [];
      octets.add(v);
    }
    if (!_isPrivate(octets)) return const [];
    final prefix = '${octets[0]}.${octets[1]}.${octets[2]}';
    return [
      for (var host = 1; host <= 254; host++)
        if (host != octets[3]) '$prefix.$host',
    ];
  }

  static bool _isPrivate(List<int> o) {
    if (o[0] == 10) return true;
    if (o[0] == 192 && o[1] == 168) return true;
    if (o[0] == 172 && o[1] >= 16 && o[1] <= 31) return true;
    return false;
  }

  /// Sweeps every sweepable interface's /24 for a lazyclaw gateway on [port].
  ///
  /// Peers are probed in batches of [concurrency]; the sweep stops launching
  /// new batches after the first confirmed hit. NEVER throws — a failing
  /// lister yields an empty result and a throwing probe reads as unreachable.
  static Future<List<String>> sweep({
    InterfaceLister? lister,
    LanHostProbe? hostProbe,
    int concurrency = defaultConcurrency,
    int port = kGatewayPort,
  }) async {
    final listInterfaces = lister ?? _listInterfaces;
    final probe = hostProbe ?? ((String ip) => _probeHost(ip, port));

    List<SweepInterface> interfaces;
    try {
      interfaces = await listInterfaces();
    } catch (_) {
      return const [];
    }

    // Collect peers across interfaces, deduping subnets two interfaces share.
    final peers = <String>[];
    final seen = <String>{};
    for (final itf in interfaces) {
      if (!isSweepableInterface(itf.name)) continue;
      for (final ip in itf.ipv4) {
        for (final peer in peersOf(ip)) {
          if (seen.add(peer)) peers.add(peer);
        }
      }
    }

    final found = <String>[];
    for (var i = 0; i < peers.length && found.isEmpty; i += concurrency) {
      final end =
          (i + concurrency) > peers.length ? peers.length : i + concurrency;
      final batch = peers.sublist(i, end);
      final hits = await Future.wait(batch.map((ip) async {
        try {
          return await probe(ip) ? ip : null;
        } catch (_) {
          return null; // A throwing probe reads as unreachable.
        }
      }));
      found.addAll([
        for (final ip in hits)
          if (ip != null) 'http://$ip:$port',
      ]);
    }
    return found;
  }

  /// The production sweep, wired into `ServerConfig.lanSweep` by `main()` (the
  /// composition root — unit tests never run `main()`, so resolution in tests
  /// can never fire a real network sweep by accident).
  static Future<List<String>> sweepAllInterfaces() => sweep();

  static Future<List<SweepInterface>> _listInterfaces() async {
    final list = await NetworkInterface.list(
      includeLoopback: false,
      type: InternetAddressType.IPv4,
    );
    return [
      for (final itf in list)
        (name: itf.name, ipv4: [for (final a in itf.addresses) a.address]),
    ];
  }

  /// Production per-peer probe: a cheap TCP connect gate first, then a full
  /// identity confirmation so a stranger on the port is never adopted.
  static Future<bool> _probeHost(String ip, int port) async {
    Socket socket;
    try {
      socket = await Socket.connect(ip, port, timeout: connectTimeout);
    } catch (_) {
      return false;
    }
    socket.destroy(); // Close immediately — the HTTP probe opens its own.
    return identityProbe('http://$ip:$port');
  }

  /// GETs `/api/health` on [baseUrl] and requires a 2xx AND a JSON body with
  /// `"status": "ok"` — the lazyclaw gateway's health shape. Stricter than the
  /// plain reachability probe because sweep candidates are UNKNOWN hosts.
  /// Never throws.
  static Future<bool> identityProbe(String baseUrl) async {
    final client = HttpClient()..connectionTimeout = identityTimeout;
    try {
      final uri = Uri.parse('$baseUrl/api/health');
      final request = await client.getUrl(uri).timeout(identityTimeout);
      final response = await request.close().timeout(identityTimeout);
      if (response.statusCode < 200 || response.statusCode >= 300) {
        await response
            .drain<void>()
            .timeout(identityTimeout, onTimeout: () {});
        return false;
      }
      final body = await response
          .transform(utf8.decoder)
          .join()
          .timeout(identityTimeout);
      final decoded = jsonDecode(body);
      return decoded is Map && decoded['status'] == 'ok';
    } catch (_) {
      return false;
    } finally {
      client.close(force: true);
    }
  }
}
