#!/usr/bin/env python3
"""TCP forwarder: 0.0.0.0:9222 → 127.0.0.1:9223.

Brave silently ignores ``--remote-debugging-address=0.0.0.0`` since
Chromium 126+ for security, so it always binds to 127.0.0.1. That makes
the host CDP port unreachable from inside the LazyClaw Docker container,
which connects via ``host.docker.internal`` → resolves to a non-loopback
gateway IP.

Workaround: run Brave on 127.0.0.1:9223 (host-only, untouched by anyone
else), and run this 30-line forwarder on the public-facing 0.0.0.0:9222.
The container connects to host.docker.internal:9222 → forwarder
→ 127.0.0.1:9223 → Brave. No new ports, no auth changes, no env tweaks.

Stdlib only — no third-party deps. Logs to stderr (captured by launchd).
"""

from __future__ import annotations

import logging
import socket
import socketserver
import sys
import threading

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 9222
TARGET_HOST = "127.0.0.1"
TARGET_PORT = 9223
BUF = 65536

logger = logging.getLogger("cdp-forwarder")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stderr,
)


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(BUF)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class CDPHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.request
        try:
            upstream = socket.create_connection(
                (TARGET_HOST, TARGET_PORT), timeout=5,
            )
        except OSError as exc:
            logger.warning("upstream %s:%d unreachable: %s",
                           TARGET_HOST, TARGET_PORT, exc)
            client.close()
            return

        # Bidirectional pipe: one thread per direction, join on first EOF.
        t = threading.Thread(target=_pipe, args=(client, upstream), daemon=True)
        t.start()
        _pipe(upstream, client)
        t.join(timeout=2)
        for s in (client, upstream):
            try:
                s.close()
            except OSError:
                pass


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    logger.info("listening on %s:%d → %s:%d",
                LISTEN_HOST, LISTEN_PORT, TARGET_HOST, TARGET_PORT)
    with ThreadedTCPServer((LISTEN_HOST, LISTEN_PORT), CDPHandler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("shutting down")
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
