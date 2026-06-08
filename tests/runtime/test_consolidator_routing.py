"""A brain fan-out's consolidated reply must be delivered to the channel the
request CAME FROM.

2026-06-03 incident: a Web UI Upwork job search was AUTO-PROMOTE'd to a
background task; the consolidator factory in cli.py unconditionally returned a
Telegram notifier, so the "🧠 Consolidated" reply landed on Telegram while the
web chat got nothing. These tests pin the origin-aware routing helper.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lazyclaw.runtime.consolidator_routing import (
    choose_consolidator_callback,
    is_live_web_callback,
)


@dataclass
class _FakeWebCallback:
    """Duck-shape of gateway.routes.chat_ws.WebSocketCallback."""

    ws: object = field(default_factory=object)
    streaming_state: dict = field(default_factory=lambda: {"bg_streaming": True})
    _closed: bool = False


class _FakeTelegramNotifier:
    """Stand-in for PrefixedTelegramNotifier — NOT a web callback."""


# ── is_live_web_callback ──────────────────────────────────────────────────


def test_live_web_callback_detected() -> None:
    assert is_live_web_callback(_FakeWebCallback()) is True


def test_closed_web_callback_not_live() -> None:
    # Socket already dropped → not a delivery target; fall back to Telegram.
    assert is_live_web_callback(_FakeWebCallback(_closed=True)) is False


def test_telegram_notifier_is_not_web() -> None:
    assert is_live_web_callback(_FakeTelegramNotifier()) is False


def test_none_callback_is_not_web() -> None:
    assert is_live_web_callback(None) is False


# ── choose_consolidator_callback ──────────────────────────────────────────


def test_web_origin_reuses_socket_not_telegram() -> None:
    web_cb = _FakeWebCallback()
    built_telegram = []

    def _telegram_factory():
        notifier = _FakeTelegramNotifier()
        built_telegram.append(notifier)
        return notifier

    chosen = choose_consolidator_callback(web_cb, _telegram_factory)

    assert chosen is web_cb  # delivered back to the browser
    assert built_telegram == []  # Telegram notifier never even built


def test_telegram_origin_builds_telegram_notifier() -> None:
    tg_origin = _FakeTelegramNotifier()
    sentinel = _FakeTelegramNotifier()

    chosen = choose_consolidator_callback(tg_origin, lambda: sentinel)

    assert chosen is sentinel


def test_closed_web_socket_falls_back_to_telegram() -> None:
    sentinel = _FakeTelegramNotifier()
    chosen = choose_consolidator_callback(
        _FakeWebCallback(_closed=True), lambda: sentinel,
    )
    assert chosen is sentinel


def test_none_origin_falls_back_to_telegram() -> None:
    sentinel = _FakeTelegramNotifier()
    assert choose_consolidator_callback(None, lambda: sentinel) is sentinel
