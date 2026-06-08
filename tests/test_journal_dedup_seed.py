"""Cross-user journal bug — FIX A + FIX B regression tests (2026-06-01).

FIX A: ``_seed_today_journals`` must only seed users that own real content
(at least one non-journal note). Dead/test accounts that own nothing but
journal stubs were being seeded every tick, minting duplicate journal pages
and stranding the real user's ``[[Journal — DATE]]`` backlinks.

FIX B: refreshing a journal note's title (``Journal — DATE`` -> a descriptive
phrase) must NOT strand its inbound ``[[Journal — DATE]]`` links. The canonical
title is preserved as an alias and inbound/outbound links resolve by alias.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.heartbeat.daemon import (
    _is_journal_only_note,
    _tags_from_raw,
    _user_owns_non_journal_note,
    select_active_user_ids,
)
from lazyclaw.lazybrain import store


# ----------------------- FIX A: pure predicate -----------------------


def _journal_tags(date: str = "2026-05-16") -> str:
    return json.dumps([f"journal/{date}", "owner/user"])


def _content_tags() -> str:
    return json.dumps(["project", "owner/user"])


def test_tags_from_raw_handles_null_and_malformed():
    assert _tags_from_raw(None) == []
    assert _tags_from_raw("") == []
    assert _tags_from_raw("not-json") == []
    assert _tags_from_raw(json.dumps({"k": "v"})) == []
    assert _tags_from_raw(json.dumps(["a", "b"])) == ["a", "b"]
    assert _tags_from_raw(["a", "b"]) == ["a", "b"]


def test_is_journal_only_note_true_for_journal_tag():
    assert _is_journal_only_note(_journal_tags()) is True


def test_is_journal_only_note_false_for_content_tag():
    assert _is_journal_only_note(_content_tags()) is False


def test_is_journal_only_note_false_for_null_or_malformed():
    assert _is_journal_only_note(None) is False
    assert _is_journal_only_note("") is False
    assert _is_journal_only_note("not-json") is False


def test_user_owns_non_journal_note_true_when_real_content_exists():
    rows = [("real", _journal_tags()), ("real", _content_tags())]
    assert _user_owns_non_journal_note("real", rows) is True


def test_user_owns_non_journal_note_false_when_only_journals():
    rows = [
        ("dead", _journal_tags("2026-05-16")),
        ("dead", _journal_tags("2026-05-17")),
    ]
    assert _user_owns_non_journal_note("dead", rows) is False


def test_user_owns_non_journal_note_false_when_no_notes():
    rows = [("real", _content_tags())]
    assert _user_owns_non_journal_note("ghost", rows) is False


def test_select_active_user_ids_excludes_journal_only_and_empty():
    rows = [
        ("real", _journal_tags()),
        ("real", _content_tags()),
        ("dead", _journal_tags()),
        ("u-test", _journal_tags("2026-05-15")),
    ]
    all_ids = ["real", "dead", "u-test", "ghost"]
    assert select_active_user_ids(all_ids, rows) == ["real"]


def test_select_active_user_ids_preserves_order():
    rows = [
        ("b", _content_tags()),
        ("a", _content_tags()),
        ("c", _content_tags()),
    ]
    assert select_active_user_ids(["a", "b", "c"], rows) == ["a", "b", "c"]


def test_select_active_user_ids_empty_when_no_real_content():
    rows = [("dead", _journal_tags()), ("u-test", _journal_tags())]
    assert select_active_user_ids(["dead", "u-test"], rows) == []


# ----------------------- FIX B: title refresh preserves backlinks --------------


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        for uid in ("u1", "u2", "u3", "u4"):
            await db.execute(
                "INSERT INTO users (id, username, password_hash, encryption_salt) "
                "VALUES (?, ?, ?, ?)",
                (uid, uid, "x", f"salt-{uid}"),
            )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


async def _links_to(c: Config, from_note_id: str) -> list:
    async with db_session(c) as db:
        cur = await db.execute(
            "SELECT to_note_id FROM note_links WHERE from_note_id = ?",
            (from_note_id,),
        )
        return [r[0] for r in await cur.fetchall()]


@pytest.mark.asyncio
async def test_title_refresh_preserves_inbound_link(cfg):
    uid = "u1"
    j = await store.save_note(
        cfg, uid, content="# Journal -- 2026-05-16\n",
        title="Journal — 2026-05-16", tags=["journal/2026-05-16", "owner/user"],
    )
    n = await store.save_note(
        cfg, uid, content="did work [[Journal — 2026-05-16]]", title="task x",
    )
    assert await _links_to(cfg, n["id"]) == [j["id"]]

    # Title refresh, NO explicit aliases -> exercises update_note auto-preserve.
    await store.update_note(
        cfg, uid, j["id"], title="2026-05-16 — shipped the fix",
    )

    assert await _links_to(cfg, n["id"]) == [j["id"]]


@pytest.mark.asyncio
async def test_title_refresh_records_canonical_alias(cfg):
    uid = "u2"
    j = await store.save_note(
        cfg, uid, content="# Journal -- 2026-05-16\n",
        title="Journal — 2026-05-16", tags=["journal/2026-05-16", "owner/user"],
    )
    await store.update_note(cfg, uid, j["id"], title="2026-05-16 — busy day")
    refreshed = await store.get_note(cfg, uid, j["id"])
    aliases = [a.lower() for a in (refreshed.get("aliases") or [])]
    assert "journal — 2026-05-16" in aliases


@pytest.mark.asyncio
async def test_new_link_resolves_via_alias_after_rename(cfg):
    uid = "u3"
    j = await store.save_note(
        cfg, uid, content="# Journal -- 2026-05-16\n",
        title="Journal — 2026-05-16", tags=["journal/2026-05-16", "owner/user"],
    )
    await store.update_note(cfg, uid, j["id"], title="2026-05-16 — late note")

    n = await store.save_note(
        cfg, uid, content="see [[Journal — 2026-05-16]]", title="task z",
    )
    assert await _links_to(cfg, n["id"]) == [j["id"]]


@pytest.mark.asyncio
async def test_explicit_alias_path_preserves_link(cfg):
    uid = "u4"
    j = await store.save_note(
        cfg, uid, content="# Journal -- 2026-05-16\n",
        title="Journal — 2026-05-16", tags=["journal/2026-05-16", "owner/user"],
    )
    n = await store.save_note(
        cfg, uid, content="ref [[Journal — 2026-05-16]]", title="task q",
    )
    await store.update_note(
        cfg, uid, j["id"], title="2026-05-16 — recap",
        aliases=["Journal — 2026-05-16"],
    )
    refreshed = await store.get_note(cfg, uid, j["id"])
    aliases = [a.lower() for a in (refreshed.get("aliases") or [])]
    assert "journal — 2026-05-16" in aliases
    assert await _links_to(cfg, n["id"]) == [j["id"]]
