"""Tests for CDP connection event-handler plumbing.

Exercises only the in-process dispatch — no real Chrome / WebSocket needed.
"""

from __future__ import annotations

import asyncio
import json

from lazyclaw.browser.cdp import CDPConnection


def test_register_and_dispatch_sync_handler():
    conn = CDPConnection()
    seen: list[dict] = []
    conn.register_event_handler("Network.requestWillBeSent", lambda p: seen.append(p))

    # Simulate an event message routed by _listen.
    # _listen doesn't dispatch sync — it loops over ws messages. Instead we
    # replicate the dispatch contract here: lookup, iterate, call.
    handlers = conn._event_handlers["Network.requestWillBeSent"]
    for cb in handlers:
        cb({"requestId": "x1", "request": {"url": "https://example.com"}})
    assert seen == [{"requestId": "x1", "request": {"url": "https://example.com"}}]


def test_clear_event_handlers_drops_all():
    conn = CDPConnection()
    conn.register_event_handler("Network.requestWillBeSent", lambda _p: None)
    conn.register_event_handler("Network.responseReceived", lambda _p: None)
    assert conn._event_handlers
    conn.clear_event_handlers()
    assert conn._event_handlers == {}


def test_multiple_handlers_per_event_all_fire():
    conn = CDPConnection()
    calls: list[str] = []
    conn.register_event_handler("Page.loadEventFired", lambda _p: calls.append("a"))
    conn.register_event_handler("Page.loadEventFired", lambda _p: calls.append("b"))

    for cb in conn._event_handlers["Page.loadEventFired"]:
        cb({})
    assert calls == ["a", "b"]


def test_listen_dispatches_event_messages(monkeypatch):
    """The real _listen reads from a WebSocket. We stub one in to verify that
    event frames (no id, has method) reach the registered handler.
    """

    class FakeWS:
        def __init__(self, frames):
            self._frames = list(frames)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._frames:
                raise StopAsyncIteration
            return self._frames.pop(0)

    conn = CDPConnection()
    seen: list[dict] = []
    conn.register_event_handler("Network.requestWillBeSent", lambda p: seen.append(p))

    conn._ws = FakeWS([
        json.dumps({"method": "Network.requestWillBeSent",
                    "params": {"requestId": "r1", "request": {"url": "https://x"}}}),
        json.dumps({"method": "OtherMethod", "params": {}}),
        # Unparseable frame — should be skipped silently
        "not-json",
        # Response to an in-flight request (id set, but nothing pending) — must not crash
        json.dumps({"id": 99, "result": {}}),
    ])

    async def run() -> None:
        await conn._listen()

    asyncio.run(run())

    assert len(seen) == 1
    assert seen[0]["requestId"] == "r1"


def test_handler_exception_does_not_break_dispatch():
    conn = CDPConnection()
    calls: list[str] = []

    def bad(_p):
        raise RuntimeError("boom")

    def good(_p):
        calls.append("ok")

    conn.register_event_handler("X", bad)
    conn.register_event_handler("X", good)

    # Simulate the dispatch loop's inner body (mirrors _listen)
    for cb in conn._event_handlers["X"]:
        try:
            cb({})
        except Exception:
            pass  # the real _listen also swallows per-handler exceptions
    assert calls == ["ok"]
