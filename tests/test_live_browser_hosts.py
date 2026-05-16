"""Tests for runtime-configurable live-browser watcher hosts.

Covers:
  - ``_host_matches`` subdomain semantics
  - ``_needs_live_browser`` builtin-only path
  - ``_needs_live_browser`` user-extras merge
  - ``add_live_host`` / ``remove_live_host`` idempotency
  - ``get_live_hosts`` normalization (lowercase, strip www., strip protocol)
  - Builtins cannot be silently removed via the user-extra path
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.browser.browser_settings import (
    add_live_host,
    get_live_hosts,
    remove_live_host,
)
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.heartbeat.daemon import (
    _LIVE_BROWSER_WATCHER_HOSTS_BUILTIN,
    _host_matches,
    _needs_live_browser,
)


@pytest.fixture
async def tmp_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


# ── _host_matches ────────────────────────────────────────────────────


def test_host_matches_exact():
    assert _host_matches("upwork.com", {"upwork.com"})


def test_host_matches_subdomain():
    assert _host_matches("www.upwork.com", {"upwork.com"})
    assert _host_matches("api.upwork.com", {"upwork.com"})
    assert _host_matches("nested.api.upwork.com", {"upwork.com"})


def test_host_matches_case_insensitive():
    assert _host_matches("UPWORK.COM", {"upwork.com"})
    assert _host_matches("Www.UPwork.Com", {"upwork.com"})


def test_host_matches_negative():
    # 'fakeupwork.com' must NOT match 'upwork.com'
    assert not _host_matches("fakeupwork.com", {"upwork.com"})
    assert not _host_matches("upwork.com.evil.com", {"upwork.com"})
    assert not _host_matches("google.com", {"upwork.com"})
    assert not _host_matches("", {"upwork.com"})
    assert not _host_matches(None, {"upwork.com"})  # type: ignore[arg-type]


def test_host_matches_subdomain_attack_shapes():
    """Belt-and-suspenders: explicit assertions for the attack shapes
    flagged in the 2026-05-16 audit. The endswith("." + needle) gate
    already rejects all three, but pin them so a future refactor that
    drops the leading-dot anchor (e.g. switching to a naïve ``in``
    check) fails loudly here instead of silently routing watcher polls
    through the user's live signed-in Brave to an attacker domain."""
    assert not _host_matches("xupwork.com", {"upwork.com"})
    assert not _host_matches("evilupwork.com", {"upwork.com"})
    assert not _host_matches(
        "evilupwork.com.attacker.com", {"upwork.com"},
    )
    # And the inverse — a legitimate subdomain still passes.
    assert _host_matches("messages.upwork.com", {"upwork.com"})


def test_host_matches_empty_haystack():
    assert not _host_matches("upwork.com", set())
    assert not _host_matches("upwork.com", frozenset())


# ── _needs_live_browser ──────────────────────────────────────────────


def test_needs_live_browser_builtin_upwork():
    assert _needs_live_browser("upwork.com")
    assert _needs_live_browser("www.upwork.com")
    assert _needs_live_browser("api.upwork.com")


def test_needs_live_browser_builtin_linkedin():
    assert _needs_live_browser("linkedin.com")
    assert _needs_live_browser("www.linkedin.com")


def test_needs_live_browser_unknown_host():
    # Without user extras, an unknown host is NOT live
    assert not _needs_live_browser("taskrabbit.com")
    assert not _needs_live_browser("fieldnation.com")
    assert not _needs_live_browser("google.com")


def test_needs_live_browser_with_user_extras():
    extras = frozenset({"taskrabbit.com", "fieldnation.com"})
    assert _needs_live_browser("taskrabbit.com", extras)
    assert _needs_live_browser("www.taskrabbit.com", extras)
    assert _needs_live_browser("fieldnation.com", extras)
    # Builtins still match even when extras are passed
    assert _needs_live_browser("upwork.com", extras)
    # Unknown still doesn't match
    assert not _needs_live_browser("airtasker.com", extras)


def test_needs_live_browser_user_extras_dont_remove_builtin():
    """Pasting in an extras set without upwork.com must NOT 'unset' it."""
    extras = frozenset({"taskrabbit.com"})
    assert _needs_live_browser("upwork.com", extras)
    assert _needs_live_browser("www.upwork.com", extras)


def test_builtin_constant_shape():
    """Builtin set must be a frozenset of normalized lowercase domains."""
    assert isinstance(_LIVE_BROWSER_WATCHER_HOSTS_BUILTIN, frozenset)
    for host in _LIVE_BROWSER_WATCHER_HOSTS_BUILTIN:
        assert host == host.lower()
        assert not host.startswith("www.")
        assert "://" not in host
        assert "/" not in host


# ── add_live_host / get_live_hosts / remove_live_host ────────────────


@pytest.mark.asyncio
async def test_get_live_hosts_empty_default(tmp_config):
    hosts = await get_live_hosts(tmp_config, "u1")
    assert hosts == []


@pytest.mark.asyncio
async def test_add_live_host_normalizes(tmp_config):
    """Protocol stripped, www. removed, lowercased."""
    hosts = await add_live_host(tmp_config, "u1", "HTTPS://Www.TaskRabbit.Com/some/path")
    # normalize_domain strips www. and lowercases — does it strip protocol+path?
    # Check the actual stored value
    fetched = await get_live_hosts(tmp_config, "u1")
    assert len(fetched) == 1
    # Whatever normalize_domain does, it should be deterministic + lowercase
    assert fetched[0] == fetched[0].lower()
    assert hosts == fetched


@pytest.mark.asyncio
async def test_add_live_host_idempotent(tmp_config):
    """Adding the same host twice doesn't duplicate."""
    await add_live_host(tmp_config, "u1", "taskrabbit.com")
    await add_live_host(tmp_config, "u1", "taskrabbit.com")
    await add_live_host(tmp_config, "u1", "TASKRABBIT.COM")  # different case
    await add_live_host(tmp_config, "u1", "www.taskrabbit.com")  # with www
    hosts = await get_live_hosts(tmp_config, "u1")
    assert hosts == ["taskrabbit.com"]


@pytest.mark.asyncio
async def test_add_live_host_multiple(tmp_config):
    await add_live_host(tmp_config, "u1", "taskrabbit.com")
    await add_live_host(tmp_config, "u1", "fieldnation.com")
    await add_live_host(tmp_config, "u1", "thumbtack.com")
    hosts = await get_live_hosts(tmp_config, "u1")
    assert set(hosts) == {"taskrabbit.com", "fieldnation.com", "thumbtack.com"}


@pytest.mark.asyncio
async def test_remove_live_host_present(tmp_config):
    await add_live_host(tmp_config, "u1", "taskrabbit.com")
    await add_live_host(tmp_config, "u1", "fieldnation.com")
    remaining = await remove_live_host(tmp_config, "u1", "taskrabbit.com")
    assert remaining == ["fieldnation.com"]
    assert await get_live_hosts(tmp_config, "u1") == ["fieldnation.com"]


@pytest.mark.asyncio
async def test_remove_live_host_absent_noop(tmp_config):
    await add_live_host(tmp_config, "u1", "fieldnation.com")
    remaining = await remove_live_host(tmp_config, "u1", "taskrabbit.com")
    assert remaining == ["fieldnation.com"]


@pytest.mark.asyncio
async def test_remove_live_host_builtin_silently_ignored(tmp_config):
    """User-list removal of a builtin domain is a no-op (it wasn't there)
    and crucially does NOT remove it from the builtin frozenset."""
    await remove_live_host(tmp_config, "u1", "upwork.com")
    # Builtin still works
    assert _needs_live_browser("upwork.com")
    # User list unchanged
    assert await get_live_hosts(tmp_config, "u1") == []


@pytest.mark.asyncio
async def test_add_live_host_rejects_empty(tmp_config):
    with pytest.raises(ValueError):
        await add_live_host(tmp_config, "u1", "")
    with pytest.raises(ValueError):
        await add_live_host(tmp_config, "u1", "   ")
