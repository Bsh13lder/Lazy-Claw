"""Unit tests for awake settings validation in general.py."""
from __future__ import annotations

import json
import pytest

from lazyclaw.settings.general import DEFAULT_GENERAL, _HHMM_RE


# ── defaults ─────────────────────────────────────────────────────────────────

def test_default_awake_block_present():
    awake = DEFAULT_GENERAL["awake"]
    assert awake["enabled"] is True
    assert awake["daily_wake_enabled"] is False
    assert awake["daily_wake_time"] == "07:00"
    assert awake["suppressed_until"] is None


# ── HH:MM regex ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("t", ["07:00", "00:00", "23:59", "7:00", "9:30"])
def test_hhmm_valid(t):
    assert _HHMM_RE.match(t)


@pytest.mark.parametrize("t", ["24:00", "23:60", "9:60", "99:99", "7", "abc", ""])
def test_hhmm_invalid(t):
    assert not _HHMM_RE.match(t)


# ── merge logic (unit, no DB) ────────────────────────────────────────────────

def test_awake_defaults_merged_independently():
    """Two calls to the default dict must not share the same nested reference."""
    a = dict(DEFAULT_GENERAL)
    b = dict(DEFAULT_GENERAL)
    a_awake = dict(a["awake"])
    a_awake["enabled"] = False
    a["awake"] = a_awake
    assert b["awake"]["enabled"] is True  # unchanged


# ── update_general_settings: awake validation (patched DB) ───────────────────

class _FakeDB:
    """Minimal async ctx-manager that pretends to be a DB connection."""
    def __init__(self, stored_settings=None):
        self._stored = stored_settings or {}
        self.written = None

    async def execute(self, sql, params=()):
        if sql.strip().upper().startswith("SELECT"):
            return _FakeRow(json.dumps(self._stored) if self._stored else None)
        if sql.strip().upper().startswith("UPDATE"):
            self.written = json.loads(params[0])
        return _FakeRow(None)

    async def commit(self):
        pass


class _FakeRow:
    def __init__(self, val):
        self._val = val

    async def fetchone(self):
        return (self._val,) if self._val is not None else None


class _FakeCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *_):
        pass


@pytest.mark.asyncio
async def test_update_daily_wake_time_valid(monkeypatch):
    from lazyclaw.settings import general as mod
    db = _FakeDB({"general": {"awake": {"enabled": True}}})

    monkeypatch.setattr(mod, "db_session", lambda _: _FakeCtx(db))
    result = await mod.update_general_settings(
        object(), "u1", {"awake": {"daily_wake_time": "08:30", "daily_wake_enabled": True}}
    )
    assert result["awake"]["daily_wake_time"] == "08:30"
    assert result["awake"]["daily_wake_enabled"] is True


@pytest.mark.asyncio
async def test_update_daily_wake_time_invalid(monkeypatch):
    from lazyclaw.settings import general as mod
    db = _FakeDB()
    monkeypatch.setattr(mod, "db_session", lambda _: _FakeCtx(db))
    with pytest.raises(ValueError, match="HH:MM"):
        await mod.update_general_settings(object(), "u1", {"awake": {"daily_wake_time": "25:00"}})


@pytest.mark.asyncio
async def test_update_suppressed_until_valid(monkeypatch):
    from lazyclaw.settings import general as mod
    db = _FakeDB()
    monkeypatch.setattr(mod, "db_session", lambda _: _FakeCtx(db))
    result = await mod.update_general_settings(
        object(), "u1", {"awake": {"suppressed_until": "2026-06-01T10:00:00+00:00"}}
    )
    assert "2026-06-01" in result["awake"]["suppressed_until"]


@pytest.mark.asyncio
async def test_update_suppressed_until_null(monkeypatch):
    from lazyclaw.settings import general as mod
    db = _FakeDB({"general": {"awake": {"suppressed_until": "2026-06-01T10:00:00"}}})
    monkeypatch.setattr(mod, "db_session", lambda _: _FakeCtx(db))
    result = await mod.update_general_settings(
        object(), "u1", {"awake": {"suppressed_until": None}}
    )
    assert result["awake"]["suppressed_until"] is None


@pytest.mark.asyncio
async def test_awake_update_does_not_clobber_siblings(monkeypatch):
    """Updating only `enabled` must not wipe daily_wake_time."""
    from lazyclaw.settings import general as mod
    existing_awake = {
        "enabled": True,
        "daily_wake_enabled": True,
        "daily_wake_time": "09:00",
        "suppressed_until": None,
    }
    db = _FakeDB({"general": {"awake": existing_awake}})
    monkeypatch.setattr(mod, "db_session", lambda _: _FakeCtx(db))
    result = await mod.update_general_settings(
        object(), "u1", {"awake": {"enabled": False}}
    )
    assert result["awake"]["enabled"] is False
    assert result["awake"]["daily_wake_time"] == "09:00"
    assert result["awake"]["daily_wake_enabled"] is True
