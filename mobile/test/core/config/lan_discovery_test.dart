// Covers [LanDiscovery] — the last-resort LAN sweep that finds the self-hosted
// gateway when EVERY known candidate is dead (the "DHCP moved the Mac" case):
//   * interface whitelist: WiFi / hotspot-AP / USB-tether / ethernet interfaces
//     are swept; cellular data interfaces (rmnet/ccmni/pdp) and tunnels NEVER
//     are — sweeping a carrier CGNAT subnet is pointless traffic;
//   * /24 peer enumeration: private-range own-IP only, excludes the phone's own
//     address, never link-local or public ranges;
//   * sweep orchestration: probes every peer of every sweepable interface,
//     dedups subnets shared by two interfaces, stops early after a confirmed
//     hit, and NEVER throws (a failing lister/probe yields an empty result).
//
// All I/O is faked: an injected interface lister and an injected host probe —
// no sockets, no real network.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/config/lan_discovery.dart';

void main() {
  group('isSweepableInterface', () {
    test('WiFi, hotspot AP, USB-tether and ethernet interfaces are sweepable',
        () {
      for (final name in [
        'wlan0',
        'wlan1',
        'ap0',
        'swlan0',
        'eth0',
        'en0',
        'rndis0',
        'ncm0',
        'usb0',
        'bridge100',
      ]) {
        expect(LanDiscovery.isSweepableInterface(name), isTrue,
            reason: '$name should be sweepable');
      }
    });

    test('cellular data, loopback and tunnel interfaces are NEVER swept', () {
      for (final name in [
        'rmnet0',
        'rmnet_data1',
        'ccmni0',
        'pdp_ip0',
        'lo',
        'tun0',
        'clat4',
        'dummy0',
      ]) {
        expect(LanDiscovery.isSweepableInterface(name), isFalse,
            reason: '$name must not be swept');
      }
    });

    test('matching is case-insensitive', () {
      expect(LanDiscovery.isSweepableInterface('WLAN0'), isTrue);
      expect(LanDiscovery.isSweepableInterface('RMNET0'), isFalse);
    });
  });

  group('peersOf', () {
    test('enumerates the /24 excluding the own address', () {
      final peers = LanDiscovery.peersOf('192.168.0.12');
      expect(peers.length, 253);
      expect(peers, isNot(contains('192.168.0.12')));
      expect(peers, contains('192.168.0.1'));
      expect(peers, contains('192.168.0.254'));
      expect(peers.every((p) => p.startsWith('192.168.0.')), isTrue);
    });

    test('accepts all three RFC1918 private ranges', () {
      expect(LanDiscovery.peersOf('10.20.30.7'), isNotEmpty);
      expect(LanDiscovery.peersOf('172.16.4.2'), isNotEmpty);
      expect(LanDiscovery.peersOf('172.31.255.9'), isNotEmpty);
      expect(LanDiscovery.peersOf('192.168.230.101'), isNotEmpty);
    });

    test('rejects public, link-local, loopback and out-of-range addresses', () {
      expect(LanDiscovery.peersOf('84.125.160.61'), isEmpty);
      expect(LanDiscovery.peersOf('169.254.5.5'), isEmpty);
      expect(LanDiscovery.peersOf('127.0.0.1'), isEmpty);
      expect(LanDiscovery.peersOf('172.32.1.1'), isEmpty);
      expect(LanDiscovery.peersOf('172.15.1.1'), isEmpty);
    });

    test('rejects garbage input instead of throwing', () {
      expect(LanDiscovery.peersOf('not-an-ip'), isEmpty);
      expect(LanDiscovery.peersOf(''), isEmpty);
      expect(LanDiscovery.peersOf('192.168.0'), isEmpty);
      expect(LanDiscovery.peersOf('192.168.0.999'), isEmpty);
    });
  });

  group('sweep', () {
    test('finds the gateway on a sweepable interface subnet', () async {
      final found = await LanDiscovery.sweep(
        lister: () async => [
          (name: 'wlan0', ipv4: ['192.168.0.12']),
        ],
        hostProbe: (ip) async => ip == '192.168.0.15',
      );
      expect(found, ['http://192.168.0.15:18789']);
    });

    test('never probes peers of a cellular interface', () async {
      final probed = <String>[];
      final found = await LanDiscovery.sweep(
        lister: () async => [
          (name: 'rmnet_data1', ipv4: ['10.133.7.4']),
        ],
        hostProbe: (ip) async {
          probed.add(ip);
          return true;
        },
      );
      expect(found, isEmpty);
      expect(probed, isEmpty);
    });

    test('sweeps the hotspot AP interface (the laptop-tethered-to-phone case)',
        () async {
      final found = await LanDiscovery.sweep(
        lister: () async => [
          (name: 'rmnet0', ipv4: ['10.133.7.4']),
          (name: 'ap0', ipv4: ['192.168.230.1']),
        ],
        hostProbe: (ip) async => ip == '192.168.230.101',
      );
      expect(found, ['http://192.168.230.101:18789']);
    });

    test('dedups a subnet shared by two interfaces (each peer probed once)',
        () async {
      final probed = <String>[];
      await LanDiscovery.sweep(
        lister: () async => [
          (name: 'wlan0', ipv4: ['192.168.0.12']),
          (name: 'wlan1', ipv4: ['192.168.0.13']),
        ],
        hostProbe: (ip) async {
          probed.add(ip);
          return false;
        },
      );
      expect(probed.length, probed.toSet().length,
          reason: 'no peer is probed twice');
    });

    test('stops launching probes after the first confirmed hit', () async {
      var probeCount = 0;
      await LanDiscovery.sweep(
        lister: () async => [
          (name: 'wlan0', ipv4: ['192.168.0.12']),
        ],
        // The hit lands in the very first batch, so the sweep must finish well
        // before all 253 peers have been probed.
        hostProbe: (ip) async {
          probeCount++;
          return ip == '192.168.0.1';
        },
        concurrency: 16,
      );
      expect(probeCount, lessThan(64),
          reason: 'sweep keeps probing whole subnet after a confirmed hit');
    });

    test('returns empty when nothing answers', () async {
      final found = await LanDiscovery.sweep(
        lister: () async => [
          (name: 'wlan0', ipv4: ['192.168.0.12']),
        ],
        hostProbe: (ip) async => false,
      );
      expect(found, isEmpty);
    });

    test('a throwing lister yields empty instead of propagating', () async {
      final found = await LanDiscovery.sweep(
        lister: () async => throw StateError('no interfaces'),
        hostProbe: (ip) async => true,
      );
      expect(found, isEmpty);
    });

    test('a throwing probe is treated as unreachable, sweep continues',
        () async {
      final found = await LanDiscovery.sweep(
        lister: () async => [
          (name: 'wlan0', ipv4: ['192.168.0.12']),
        ],
        hostProbe: (ip) async =>
            ip == '192.168.0.15' ? true : throw StateError('boom'),
      );
      expect(found, ['http://192.168.0.15:18789']);
    });
  });
}
