import '../core/config/server_config.dart';
import 'chat_socket.dart';

/// Keeps the chat WebSocket pointed at the CURRENTLY resolved gateway.
///
/// 2026-08-20 incident: a cold start seeds the base URL to the unprobed
/// last-known-good host (the home LAN IP, remembered from the previous
/// evening). ChatScreen wired the socket with a one-shot
/// `ref.read(baseUrlProvider)` at mount, and [ChatSocket]'s backoff loop
/// re-dials that stored URL forever. When the post-first-frame reresolve
/// flipped the provider to the Funnel, every REST reader rebuilt
/// (`apiClientProvider` WATCHES the provider) but nothing re-pointed the
/// socket — so away from home the app polled history over HTTPS all
/// morning while the WS dialed a dead LAN address eternally and the
/// server saw zero upgrade attempts.
///
/// This class is that missing link: feed it every base-URL value (the
/// screen wires it to a `ref.listenManual(baseUrlProvider, …)`) and it
/// re-connects the socket to `wsUrlFor(base)` whenever the base actually
/// changed. [ChatSocket.connect] already handles the rest — a same-URL
/// call on a live channel is a no-op, a changed URL tears down and
/// re-dials.
class SocketRewirer {
  final ChatSocket socket;

  /// Reads the current session cookie, or `null` when signed out. Injected
  /// so the class is unit-testable without an ApiClient (house style).
  final Future<String?> Function() getSessionCookie;

  /// The last base successfully handed to [ChatSocket.connect]. A dial we
  /// SKIPPED (no cookie, storage error) must not latch — otherwise one bad
  /// read would suppress the rewire for that base forever.
  String? _lastBase;

  SocketRewirer({required this.socket, required this.getSessionCookie});

  /// Re-points the socket when [base] differs from the last wired base.
  /// Never throws — a failing cookie read just leaves the socket as-is
  /// (the next change or resume retries).
  Future<void> onBaseUrl(String base) async {
    final normalized = ServerConfig.normalizeBaseUrl(base);
    if (normalized == _lastBase) return;
    String? cookie;
    try {
      cookie = await getSessionCookie();
    } catch (_) {
      return;
    }
    if (cookie == null) return;
    _lastBase = normalized;
    try {
      await socket.connect(
        ServerConfig.wsUrlFor(normalized),
        cookie: 'session_id=$cookie',
      );
    } catch (_) {
      _lastBase = null; // a failed dial must not suppress a retry
    }
  }
}
