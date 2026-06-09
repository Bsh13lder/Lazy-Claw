"""Tests for the SSRF guard (lazyclaw.browser.ssrf_guard).

Uses IP literals + ``localhost`` (resolved locally) so no external DNS is hit.
"""

from __future__ import annotations

from lazyclaw.browser.ssrf_guard import (
    is_blocked_ssrf_target,
    is_metadata_or_linklocal,
)


# ── STRICT policy (content-triggered fetches: scraper / web_search) ──────────


def test_strict_blocks_cloud_metadata():
    assert is_blocked_ssrf_target("http://169.254.169.254/latest/meta-data/")
    assert is_blocked_ssrf_target("http://metadata.google.internal/")
    assert is_blocked_ssrf_target("http://100.100.100.200/")


def test_strict_blocks_loopback_private_and_local_names():
    assert is_blocked_ssrf_target("http://127.0.0.1:18789/api/internal")
    assert is_blocked_ssrf_target("http://10.0.0.5/")
    assert is_blocked_ssrf_target("http://172.16.0.1/")
    assert is_blocked_ssrf_target("http://192.168.1.1/")
    assert is_blocked_ssrf_target("http://localhost:5432/")
    assert is_blocked_ssrf_target("http://host.docker.internal:9222/")
    assert is_blocked_ssrf_target("http://[::1]/")


def test_strict_allows_public_literals():
    assert not is_blocked_ssrf_target("https://1.1.1.1/")
    assert not is_blocked_ssrf_target("https://8.8.8.8/")


def test_strict_ignores_unresolvable_and_empty():
    # No host / unparseable → not our concern (the fetch fails on its own).
    assert not is_blocked_ssrf_target("not-a-url")
    assert not is_blocked_ssrf_target("")


# ── MINIMAL policy (user-driven navigation) ──────────────────────────────────


def test_minimal_blocks_metadata_and_linklocal_only():
    assert is_metadata_or_linklocal("http://169.254.169.254/")
    assert is_metadata_or_linklocal("http://metadata.google.internal/")
    assert is_metadata_or_linklocal("http://[fe80::1]/")


def test_minimal_allows_user_localhost_and_lan_dev():
    # The human may legitimately open their own dev server / LAN box.
    assert not is_metadata_or_linklocal("http://localhost:3000/")
    assert not is_metadata_or_linklocal("http://127.0.0.1:3000/")
    assert not is_metadata_or_linklocal("http://192.168.1.50:8080/")
    assert not is_metadata_or_linklocal("https://example.com/")
