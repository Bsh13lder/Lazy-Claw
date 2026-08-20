// SocketRewirer — keeps the chat WebSocket pointed at the CURRENTLY
// resolved gateway.
//
// 2026-08-20 incident: a cold start seeds the base URL to the unprobed
// last-known-good host (the home LAN IP). ChatSocket got its URL from a
// one-shot `ref.read(baseUrlProvider)` at screen mount and its backoff loop
// re-dials that STORED URL forever. When the background reresolve flipped
// the provider to the Funnel, every REST reader rebuilt (apiClient WATCHES
// the provider) but nothing re-pointed the socket — so away from home the
// app polled history over HTTPS all morning while the WS dialed a dead LAN
// address eternally and the server saw zero upgrade attempts.
//
// The rewirer is the missing listener, extracted as a plain class so it is
// unit-testable without pumping ChatScreen (house style: see
// day_separator_test.dart / connect_error_test.dart).

import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_socket.dart';
import 'package:lazyclaw_mobile/chat/socket_rewire.dart';

void main() {
  // Records every URL the socket actually dials. The incoming stream stays
  // silent — dialing is all these tests observe.
  late List<String> dialed;
  late ChatSocket socket;

  ChatSocket makeSocket() => ChatSocket(
        channelFactory: (url, headers) {
          dialed.add(url);
          return FakeSink(const Stream.empty(), <String>[]);
        },
      );

  setUp(() {
    dialed = <String>[];
    socket = makeSocket();
  });

  tearDown(() => socket.dispose());

  test('re-points the socket at the ws URL derived from the new base',
      () async {
    final rewirer = SocketRewirer(
      socket: socket,
      getSessionCookie: () async => 'abc',
    );

    await rewirer.onBaseUrl('https://blckit.tail57f754.ts.net');

    expect(dialed, ['wss://blckit.tail57f754.ts.net/ws/chat']);
  });

  test('a base-URL flip dials the NEW target (the incident shape)', () async {
    final rewirer = SocketRewirer(
      socket: socket,
      getSessionCookie: () async => 'abc',
    );

    await rewirer.onBaseUrl('http://192.168.0.11:18789'); // stale LAN seed
    await rewirer.onBaseUrl('https://blckit.tail57f754.ts.net'); // reresolve

    expect(dialed.first, 'ws://192.168.0.11:18789/ws/chat');
    expect(dialed.last, 'wss://blckit.tail57f754.ts.net/ws/chat');
  });

  test('the same base twice dials only once', () async {
    final rewirer = SocketRewirer(
      socket: socket,
      getSessionCookie: () async => 'abc',
    );

    await rewirer.onBaseUrl('http://192.168.0.11:18789');
    await rewirer.onBaseUrl('http://192.168.0.11:18789');
    // Normalization must not defeat the dedupe (trailing slash).
    await rewirer.onBaseUrl('http://192.168.0.11:18789/');

    expect(dialed.length, 1);
  });

  test('missing session cookie skips the dial AND does not latch the base',
      () async {
    String? cookie;
    final rewirer = SocketRewirer(
      socket: socket,
      getSessionCookie: () async => cookie,
    );

    await rewirer.onBaseUrl('https://blckit.tail57f754.ts.net');
    expect(dialed, isEmpty, reason: 'no cookie — must not dial unauth');

    // Cookie appears (login completed) — the SAME base must now connect,
    // so a cookie-miss can never permanently suppress the rewire.
    cookie = 'abc';
    await rewirer.onBaseUrl('https://blckit.tail57f754.ts.net');
    expect(dialed, ['wss://blckit.tail57f754.ts.net/ws/chat']);
  });

  test('sends the session cookie header on the rewired dial', () async {
    Map<String, String>? seenHeaders;
    final headerSocket = ChatSocket(
      channelFactory: (url, headers) {
        seenHeaders = headers;
        return FakeSink(const Stream.empty(), <String>[]);
      },
    );
    addTearDown(headerSocket.dispose);
    final rewirer = SocketRewirer(
      socket: headerSocket,
      getSessionCookie: () async => 'abc',
    );

    await rewirer.onBaseUrl('https://blckit.tail57f754.ts.net');

    expect(seenHeaders?['Cookie'], 'session_id=abc');
  });

  test('a throwing cookie getter is swallowed and does not latch', () async {
    var shouldThrow = true;
    final rewirer = SocketRewirer(
      socket: socket,
      getSessionCookie: () async {
        if (shouldThrow) throw StateError('secure storage exploded');
        return 'abc';
      },
    );

    await rewirer.onBaseUrl('https://blckit.tail57f754.ts.net');
    expect(dialed, isEmpty);

    shouldThrow = false;
    await rewirer.onBaseUrl('https://blckit.tail57f754.ts.net');
    expect(dialed, ['wss://blckit.tail57f754.ts.net/ws/chat']);
  });
}
