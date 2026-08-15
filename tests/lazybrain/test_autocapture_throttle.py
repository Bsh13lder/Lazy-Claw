"""Auto-capture hourly cap — the write-side half of memory-store hygiene.

Auto-capture fires on EVERY user message (``runtime/agent.py:~7986``,
fire-and-forget), which is how the store reached 93.5% auto-captured notes
(2026-08-14 audit). The archive sweep walks old noise out of recall; this
cap stops a chatty hour from minting it in the first place.

Design pins asserted here:
  * per-user (one user's chatter can't throttle another's)
  * monotonic rolling window (not wall clock — NTP steps / suspend safe)
  * ``capture_text_with_llm`` consumes exactly ONE slot per message, and a
    throttled message never pays for the 1-3s worker-LLM round trip
  * env override, including the "0 disables" escape hatch
"""
from __future__ import annotations

import pytest

from lazyclaw.lazybrain import auto_capture

_ENV = "LAZYCLAW_AUTOCAPTURE_HOURLY_CAP"


@pytest.fixture(autouse=True)
def _clean_throttle(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    auto_capture.reset_capture_throttle()
    yield
    auto_capture.reset_capture_throttle()


# ─── Cap resolution ───────────────────────────────────────────────────────


def test_default_cap_is_twelve():
    assert auto_capture.autocapture_hourly_cap() == 12
    assert auto_capture.DEFAULT_AUTOCAPTURE_HOURLY_CAP == 12


def test_env_override(monkeypatch):
    monkeypatch.setenv(_ENV, "3")
    assert auto_capture.autocapture_hourly_cap() == 3


def test_env_read_per_call_not_cached_at_import(monkeypatch):
    """Flipping the knob must take effect without a process restart."""
    monkeypatch.setenv(_ENV, "5")
    assert auto_capture.autocapture_hourly_cap() == 5
    monkeypatch.setenv(_ENV, "7")
    assert auto_capture.autocapture_hourly_cap() == 7


def test_unparseable_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(_ENV, "not-a-number")
    assert auto_capture.autocapture_hourly_cap() == 12


def test_blank_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(_ENV, "   ")
    assert auto_capture.autocapture_hourly_cap() == 12


# ─── Window behaviour ─────────────────────────────────────────────────────


def test_cap_enforced(monkeypatch):
    monkeypatch.setenv(_ENV, "3")
    assert [auto_capture._consume_capture_budget("u1") for _ in range(5)] == [
        True, True, True, False, False,
    ]


def test_window_resets_after_an_hour(monkeypatch):
    """Budget refills once ``_CAPTURE_WINDOW_SECONDS`` elapse."""
    monkeypatch.setenv(_ENV, "2")
    clock = {"t": 1000.0}
    monkeypatch.setattr(auto_capture.time, "monotonic", lambda: clock["t"])

    assert auto_capture._consume_capture_budget("u1") is True
    assert auto_capture._consume_capture_budget("u1") is True
    assert auto_capture._consume_capture_budget("u1") is False

    # 59m59s later — still the same window.
    clock["t"] = 1000.0 + auto_capture._CAPTURE_WINDOW_SECONDS - 1
    assert auto_capture._consume_capture_budget("u1") is False

    # One hour after the window START — fresh budget.
    clock["t"] = 1000.0 + auto_capture._CAPTURE_WINDOW_SECONDS
    assert auto_capture._consume_capture_budget("u1") is True
    assert auto_capture._consume_capture_budget("u1") is True
    assert auto_capture._consume_capture_budget("u1") is False


def test_window_is_fixed_not_sliding(monkeypatch):
    """The window is anchored on its FIRST capture, so a steady drip can't
    keep pushing the reset out forever."""
    monkeypatch.setenv(_ENV, "2")
    clock = {"t": 0.0}
    monkeypatch.setattr(auto_capture.time, "monotonic", lambda: clock["t"])

    assert auto_capture._consume_capture_budget("u1") is True
    clock["t"] = 3000.0
    assert auto_capture._consume_capture_budget("u1") is True
    clock["t"] = 3599.0
    assert auto_capture._consume_capture_budget("u1") is False
    clock["t"] = 3600.0
    assert auto_capture._consume_capture_budget("u1") is True


def test_throttle_is_per_user(monkeypatch):
    monkeypatch.setenv(_ENV, "1")
    assert auto_capture._consume_capture_budget("u1") is True
    assert auto_capture._consume_capture_budget("u1") is False
    assert auto_capture._consume_capture_budget("u2") is True
    assert auto_capture._consume_capture_budget("u3") is True


def test_zero_cap_disables_throttle(monkeypatch):
    monkeypatch.setenv(_ENV, "0")
    assert all(auto_capture._consume_capture_budget("u1") for _ in range(100))


def test_negative_cap_disables_throttle(monkeypatch):
    monkeypatch.setenv(_ENV, "-1")
    assert all(auto_capture._consume_capture_budget("u1") for _ in range(50))


def test_reset_clears_single_user(monkeypatch):
    monkeypatch.setenv(_ENV, "1")
    auto_capture._consume_capture_budget("u1")
    auto_capture._consume_capture_budget("u2")
    auto_capture.reset_capture_throttle("u1")
    assert auto_capture._consume_capture_budget("u1") is True
    assert auto_capture._consume_capture_budget("u2") is False


def test_window_dict_stays_bounded(monkeypatch):
    """Module-level dict keyed by user_id must not grow forever in a
    multi-user deployment (same class of bug as
    ``heartbeat/daemon._last_watcher_context``, 2026-06-10 finding L3)."""
    monkeypatch.setenv(_ENV, "5")
    for i in range(auto_capture._MAX_THROTTLE_USERS + 50):
        auto_capture._consume_capture_budget(f"u{i}")
    assert len(auto_capture._capture_windows) <= auto_capture._MAX_THROTTLE_USERS


def test_expired_windows_are_pruned(monkeypatch):
    monkeypatch.setenv(_ENV, "5")
    clock = {"t": 0.0}
    monkeypatch.setattr(auto_capture.time, "monotonic", lambda: clock["t"])
    for i in range(10):
        auto_capture._consume_capture_budget(f"old-{i}")
    assert len(auto_capture._capture_windows) == 10

    clock["t"] = auto_capture._CAPTURE_WINDOW_SECONDS + 1
    auto_capture._consume_capture_budget("newcomer")
    assert auto_capture._capture_windows.keys() == {"newcomer"}


# ─── Entry-point integration ──────────────────────────────────────────────


async def test_capture_text_skips_silently_when_capped(monkeypatch):
    """Over cap: returns ``[]`` without ever reaching the persistence layer."""
    monkeypatch.setenv(_ENV, "1")
    calls: list[str] = []

    async def _boom(*_a, **_kw):
        calls.append("persist")
        return []

    monkeypatch.setattr(auto_capture, "_persist", _boom)

    text = "TIL that Redis uses LRU eviction by default."
    assert await auto_capture.capture_text(None, "u1", text) == []
    assert calls == ["persist"], "first call should have run the regex tier"

    assert await auto_capture.capture_text(None, "u1", text) == []
    assert calls == ["persist"], "second call must be skipped before _persist"


async def test_llm_tier_consumes_one_slot_per_message(monkeypatch):
    """``capture_text_with_llm`` must not double-count through its inner
    regex pass — 3 messages under a cap of 3 all get through."""
    monkeypatch.setenv(_ENV, "3")

    async def _persist(_cfg, _uid, captures, _trace, _src):
        return ["id"] if captures else []

    monkeypatch.setattr(auto_capture, "_persist", _persist)

    text = "TIL that Redis uses LRU eviction by default."
    for _ in range(3):
        assert await auto_capture.capture_text_with_llm(
            None, "u1", text, object()
        ) == ["id"]
    # 4th is over cap.
    assert await auto_capture.capture_text_with_llm(
        None, "u1", text, object()
    ) == []


async def test_capped_message_never_calls_the_worker_llm(monkeypatch):
    """The expensive half must be gated too, not just the regex tier."""
    monkeypatch.setenv(_ENV, "1")
    router_calls: list[int] = []

    class _Router:
        async def chat(self, *_a, **_kw):
            router_calls.append(1)
            return None

    async def _persist(*_a, **_kw):
        return []

    monkeypatch.setattr(auto_capture, "_persist", _persist)

    # No regex hit + >=40 chars → the LLM fallback would normally fire.
    text = "the quick brown fox jumped over the extremely lazy dog again"
    await auto_capture.capture_text_with_llm(None, "u1", text, _Router())
    assert len(router_calls) == 1

    await auto_capture.capture_text_with_llm(None, "u1", text, _Router())
    assert len(router_calls) == 1, "throttled message still hit the worker LLM"
