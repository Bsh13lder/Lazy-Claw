"""Where a brain fan-out's consolidated reply is delivered.

When the brain dispatches background work (``run_background`` /
``dispatch_subagents``) the TaskRunner waits for every task to settle and
then enqueues ONE synthetic "consolidation" turn that writes the merged
reply. That turn needs a callback (delivery sink).

The bug this module fixes (2026-06-03): a request that originated from the
Web UI carries a ``WebSocketCallback``, but the consolidator factory in
``cli.py`` unconditionally returned a Telegram notifier — so a Web-UI job
search was answered with a "🧠 Consolidated" push on Telegram while the web
chat got nothing. A request made from the web must be answered in the web.

The rule is deliberately origin-aware and import-free: the WebSocketCallback
is detected by duck-typing (it owns a live ``ws`` + ``streaming_state`` and
flips ``_closed`` when the socket drops) so this runtime helper never has to
import the gateway layer.
"""

from __future__ import annotations

from typing import Callable


def is_live_web_callback(cb: object) -> bool:
    """True if ``cb`` is a Web-UI WebSocket callback with a live socket.

    Duck-typed against ``gateway.routes.chat_ws.WebSocketCallback`` to keep
    this module free of a runtime→gateway import. A callback that has already
    closed its socket (``_closed`` set) is NOT live — fall back to Telegram in
    that case so the user still gets the reply somewhere.
    """
    if cb is None:
        return False
    if not (hasattr(cb, "ws") and hasattr(cb, "streaming_state")):
        return False
    if getattr(cb, "_closed", False):
        return False
    return True


def choose_consolidator_callback(
    original_cb: object,
    telegram_factory: Callable[[], object],
) -> object:
    """Pick the delivery sink for a brain fan-out's consolidated reply.

    - Web origin (live ``WebSocketCallback``) → reuse it so the consolidated
      reply streams back to the browser the request came from.
    - Anything else (Telegram origin, null/headless callback, or a web socket
      that already closed) → a fresh Telegram "🧠 Consolidated" notifier built
      by ``telegram_factory``.

    Pure function: returns a callback, never mutates ``original_cb``.
    """
    if is_live_web_callback(original_cb):
        return original_cb
    return telegram_factory()
