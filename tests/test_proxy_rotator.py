"""Tests for ProxyRotator — cyclic / random / sticky + health tracking."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "mcp-scraper"))

from mcp_scraper.proxy_rotator import ProxyRotator  # noqa: E402


# ── Cyclic ─────────────────────────────────────────────────────────────

def test_cyclic_round_robin_order():
    r = ProxyRotator(["p1", "p2", "p3"])
    assert [r.next() for _ in range(6)] == ["p1", "p2", "p3", "p1", "p2", "p3"]


def test_single_proxy_returns_same_proxy():
    r = ProxyRotator(["only"])
    assert [r.next() for _ in range(3)] == ["only", "only", "only"]


def test_empty_pool_raises():
    with pytest.raises(ValueError, match="non-empty"):
        ProxyRotator([])


def test_unknown_strategy_raises():
    with pytest.raises(ValueError, match="Unknown strategy"):
        ProxyRotator(["p1"], strategy="explosive")  # type: ignore[arg-type]


# ── Random ─────────────────────────────────────────────────────────────

def test_random_only_returns_known_proxies():
    r = ProxyRotator(["a", "b", "c"], strategy="random")
    seen = {r.next() for _ in range(50)}
    assert seen.issubset({"a", "b", "c"})
    assert len(seen) >= 2  # vanishingly unlikely to be just 1 over 50 trials


# ── Sticky ─────────────────────────────────────────────────────────────

def test_sticky_per_host_consistency():
    r = ProxyRotator(["p1", "p2", "p3"], strategy="sticky")
    a = r.next(host="example.com")
    b = r.next(host="example.com")
    c = r.next(host="example.com")
    assert a == b == c, "Same host must reuse the pinned proxy"


def test_sticky_different_hosts_can_get_different_proxies():
    r = ProxyRotator(["p1", "p2", "p3"], strategy="sticky")
    pin1 = r.next(host="alpha.com")
    pin2 = r.next(host="beta.com")
    # Hosts may share a proxy by coincidence, but the assignments are tracked
    stats = r.stats()
    assert "alpha.com" in stats["sticky_assignments"]
    assert "beta.com" in stats["sticky_assignments"]
    assert stats["sticky_assignments"]["alpha.com"] == pin1
    assert stats["sticky_assignments"]["beta.com"] == pin2


def test_sticky_without_host_falls_back_to_cyclic():
    r = ProxyRotator(["p1", "p2"], strategy="sticky")
    seq = [r.next() for _ in range(4)]  # no host arg
    assert seq == ["p1", "p2", "p1", "p2"]


# ── Health: blacklist after strike threshold ───────────────────────────

def test_three_strikes_blacklists_proxy():
    r = ProxyRotator(["p1", "p2", "p3"])
    r.mark_bad("p1", reason="429")
    r.mark_bad("p1", reason="429")
    # Two strikes: p1 still healthy
    assert r.stats()["healthy"] == 3
    r.mark_bad("p1", reason="429")
    # Third strike: blacklisted
    assert r.stats()["healthy"] == 2
    in_cooldown = [x["proxy"] for x in r.stats()["in_cooldown"]]
    assert "p1" in in_cooldown


def test_blacklisted_proxy_skipped_in_rotation():
    r = ProxyRotator(["p1", "p2", "p3"])
    for _ in range(3):
        r.mark_bad("p1")
    seq = [r.next() for _ in range(6)]
    assert "p1" not in seq, "Blacklisted proxy must be skipped"
    assert set(seq) == {"p2", "p3"}


def test_mark_good_resets_strikes():
    r = ProxyRotator(["p1", "p2"])
    r.mark_bad("p1")
    r.mark_bad("p1")
    r.mark_good("p1")
    # Should take 3 MORE strikes to blacklist now
    r.mark_bad("p1")
    r.mark_bad("p1")
    assert r.stats()["healthy"] == 2  # still healthy
    r.mark_bad("p1")
    assert r.stats()["healthy"] == 1  # now blacklisted


def test_cooldown_expires_and_proxy_returns():
    r = ProxyRotator(["p1"])
    for _ in range(3):
        r.mark_bad("p1")
    # Mock time to jump past the 60s cooldown
    original = time.monotonic
    with patch("mcp_scraper.proxy_rotator.time.monotonic",
               return_value=original() + 1000):
        assert r.stats()["healthy"] == 1  # back in service


def test_all_blacklisted_raises_runtime_error():
    """When every proxy is in cooldown, .next() raises so the caller
    can pause instead of slamming the dead pool."""
    r = ProxyRotator(["p1", "p2"])
    for proxy in ("p1", "p2"):
        for _ in range(3):
            r.mark_bad(proxy)
    with pytest.raises(RuntimeError, match="cooldown"):
        r.next()


def test_mark_bad_on_unknown_proxy_is_silent():
    """Caller passes a proxy not in the pool — log+ignore, don't crash."""
    r = ProxyRotator(["p1"])
    r.mark_bad("not-in-pool")  # must not raise
    assert r.stats()["healthy"] == 1


# ── stats() snapshot ───────────────────────────────────────────────────

def test_stats_includes_all_required_keys():
    r = ProxyRotator(["p1", "p2"], strategy="cyclic")
    s = r.stats()
    assert s["strategy"] == "cyclic"
    assert s["total"] == 2
    assert s["healthy"] == 2
    assert s["in_cooldown"] == []
    assert s["sticky_assignments"] == {}
