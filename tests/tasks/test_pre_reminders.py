"""Unit tests for advance-reminder resolution (``tasks.pre_reminders``).

Proton-Calendar-style behaviour: the user's configured ``reminder_offsets``
drive advance reminders for EVERY timed task — not only "important" ones.
These tests exercise the pure resolver without touching the DB (settings
reads are monkeypatched) so the offset math + gate-removal are pinned.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lazyclaw.tasks.pre_reminders import (
    parse_iso_datetime,
    parse_offset_against,
    resolve_pre_reminders,
)


# ── pure helpers ─────────────────────────────────────────────────────────

def test_parse_offset_against_hours() -> None:
    base = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    assert parse_offset_against("-2h", base) == base - timedelta(hours=2)


def test_parse_offset_against_combined() -> None:
    base = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    assert parse_offset_against("-1d2h30m", base) == base - timedelta(
        days=1, hours=2, minutes=30
    )


def test_parse_offset_against_naive_base_coerced_to_utc() -> None:
    naive = datetime(2026, 7, 1, 12, 0)
    out = parse_offset_against("-1h", naive)
    assert out is not None and out.tzinfo is not None


@pytest.mark.parametrize("bad", ["", "2h", "+2h", "banana", "-0h", "-"])
def test_parse_offset_against_rejects_bad(bad) -> None:
    base = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    assert parse_offset_against(bad, base) is None


def test_parse_iso_datetime_naive_gets_utc() -> None:
    out = parse_iso_datetime("2026-07-01T12:00:00")
    assert out is not None and out.tzinfo is not None


def test_parse_iso_datetime_rejects_garbage() -> None:
    assert parse_iso_datetime("not-a-date") is None


# ── resolver: explicit input ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_when_no_reminder_at() -> None:
    out = await resolve_pre_reminders(object(), "u1", reminder_at=None, explicit=["-2h"])
    assert out == []


@pytest.mark.asyncio
async def test_explicit_empty_list_opts_out() -> None:
    """Passing [] explicitly disables auto-derivation (return [])."""
    base = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    out = await resolve_pre_reminders(object(), "u1", reminder_at=base, explicit=[])
    assert out == []


@pytest.mark.asyncio
async def test_explicit_two_offsets_yield_two_timestamps() -> None:
    base_dt = datetime.now(timezone.utc) + timedelta(days=3)
    base = base_dt.isoformat()
    out = await resolve_pre_reminders(
        object(), "u1", reminder_at=base, explicit=["-2h", "-1h"]
    )
    assert len(out) == 2
    got = {parse_iso_datetime(x) for x in out}
    assert (base_dt - timedelta(hours=2)) in got
    assert (base_dt - timedelta(hours=1)) in got
    # All strictly between now and the reminder time, and sorted.
    now = datetime.now(timezone.utc)
    assert all(now < parse_iso_datetime(x) < base_dt for x in out)
    assert out == sorted(out)


@pytest.mark.asyncio
async def test_explicit_past_offsets_filtered() -> None:
    """Offsets landing before 'now' are dropped."""
    base = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    # -2h against a base 30m away is already in the past → filtered.
    out = await resolve_pre_reminders(
        object(), "u1", reminder_at=base, explicit=["-2h", "-10m"]
    )
    # Only the -10m offset survives (base-10m is still in the future).
    assert len(out) == 1


@pytest.mark.asyncio
async def test_explicit_absolute_iso_entries_accepted() -> None:
    base_dt = datetime.now(timezone.utc) + timedelta(days=2)
    mid = (base_dt - timedelta(hours=5)).isoformat()
    out = await resolve_pre_reminders(
        object(), "u1", reminder_at=base_dt.isoformat(), explicit=[mid]
    )
    assert len(out) == 1
    assert parse_iso_datetime(out[0]) == parse_iso_datetime(mid)


# ── resolver: settings-driven (gate removed) ─────────────────────────────

@pytest.mark.asyncio
async def test_non_important_task_still_gets_offsets_from_settings(monkeypatch) -> None:
    """The old 'important-only' gate is gone: a low-priority, plain-title
    task with a reminder now derives offsets from the user's settings."""
    from lazyclaw.settings import general as gen_mod

    async def _fake_get(_config, _user_id):
        return {"reminder_offsets": ["-3h", "-1h"]}

    monkeypatch.setattr(gen_mod, "get_general_settings", _fake_get)

    base_dt = datetime.now(timezone.utc) + timedelta(days=1)
    out = await resolve_pre_reminders(
        object(), "u1", reminder_at=base_dt.isoformat(), explicit=None
    )
    got = {parse_iso_datetime(x) for x in out}
    assert (base_dt - timedelta(hours=3)) in got
    assert (base_dt - timedelta(hours=1)) in got
    assert len(out) == 2


@pytest.mark.asyncio
async def test_no_offsets_configured_returns_empty(monkeypatch) -> None:
    from lazyclaw.settings import general as gen_mod

    async def _fake_get(_config, _user_id):
        return {"reminder_offsets": []}

    monkeypatch.setattr(gen_mod, "get_general_settings", _fake_get)
    base = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    out = await resolve_pre_reminders(object(), "u1", reminder_at=base, explicit=None)
    assert out == []


@pytest.mark.asyncio
async def test_settings_read_failure_uses_fallback(monkeypatch) -> None:
    from lazyclaw.settings import general as gen_mod

    async def _boom(_config, _user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(gen_mod, "get_general_settings", _boom)
    base_dt = datetime.now(timezone.utc) + timedelta(days=1)
    out = await resolve_pre_reminders(
        object(), "u1", reminder_at=base_dt.isoformat(), explicit=None
    )
    # Fallback offsets are ["-30m"] (quiet default, 2026-07-30 noise pass).
    got = {parse_iso_datetime(x) for x in out}
    assert got == {base_dt - timedelta(minutes=30)}
