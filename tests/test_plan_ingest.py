"""Coverage for ``lazybrain.plan_ingest`` — Claude Code plan-mode files
mirroring into LazyBrain.

Idempotency is the headline guarantee: re-running the heartbeat tick
must not duplicate notes when nothing on disk changed. Tests use a
synthetic plans directory so they never touch ~/.claude/plans.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import plan_ingest, store


@pytest.fixture
async def cfg_with_plans(tmp_path: Path, monkeypatch):
    """Fresh DB + a registered user + a fake plans directory pointed at
    via the LAZYCLAW_CLAUDE_PLANS_DIR env override."""
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir()
    monkeypatch.setenv("LAZYCLAW_CLAUDE_PLANS_DIR", str(plans_dir))

    c = Config(database_dir=tmp_path / "db")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u-pi", "alice", "x", "salt-pi"),
        )
        await db.commit()
    try:
        yield c, plans_dir
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_first_pass_ingests_plan(cfg_with_plans) -> None:
    cfg, plans_dir = cfg_with_plans
    plan = plans_dir / "synthetic-test-plan.md"
    plan.write_text("# Title\nThis is a synthetic plan body.\n")

    summary = await plan_ingest.ingest_claude_plans(cfg, "u-pi")
    assert summary["ingested"] == 1
    assert summary["checked"] == 1
    assert summary["opted_out"] is False

    # The plan landed as a kind/plan note keyed off the slug.
    note = await store.find_by_title(
        cfg, "u-pi", "Plan · synthetic test plan",
    )
    assert note is not None, "plan must be findable by its derived title"
    assert "kind/plan" in note["tags"]
    assert "owner/user" in note["tags"]
    assert "source/claude-plan" in note["tags"]
    assert "synthetic plan body" in note["content"]


@pytest.mark.asyncio
async def test_second_pass_is_noop_when_unchanged(cfg_with_plans) -> None:
    """Same file content + same mtime → ingest_claude_plans skips."""
    cfg, plans_dir = cfg_with_plans
    plan = plans_dir / "stable-plan.md"
    plan.write_text("Untouched body.\n")

    first = await plan_ingest.ingest_claude_plans(cfg, "u-pi")
    assert first["ingested"] == 1

    second = await plan_ingest.ingest_claude_plans(cfg, "u-pi")
    assert second["ingested"] == 0
    assert second["skipped"] == 1, "unchanged plan must be skipped"


@pytest.mark.asyncio
async def test_mtime_change_triggers_reingest(cfg_with_plans) -> None:
    """When the plan file is rewritten, the mirror updates."""
    cfg, plans_dir = cfg_with_plans
    plan = plans_dir / "evolving-plan.md"
    plan.write_text("v1 body.\n")
    await plan_ingest.ingest_claude_plans(cfg, "u-pi")

    # Bump mtime to ensure the comparison fires (file system timer
    # resolution can fold quick rewrites into the same second).
    plan.write_text("v2 body — totally rewritten.\n")
    new_mtime = time.time() + 5
    os.utime(plan, (new_mtime, new_mtime))

    second = await plan_ingest.ingest_claude_plans(cfg, "u-pi")
    assert second["ingested"] == 1, "newer mtime must re-ingest"

    note = await store.find_by_title(
        cfg, "u-pi", "Plan · evolving plan",
    )
    assert note is not None
    assert "v2 body" in note["content"]


@pytest.mark.asyncio
async def test_user_opt_out_skips_everything(cfg_with_plans) -> None:
    cfg, plans_dir = cfg_with_plans
    (plans_dir / "irrelevant.md").write_text("body\n")

    # Flip the user's settings to opt out.
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            ('{"ingest_claude_plans": false}', "u-pi"),
        )
        await db.commit()

    summary = await plan_ingest.ingest_claude_plans(cfg, "u-pi")
    assert summary["opted_out"] is True
    assert summary["checked"] == 0
    assert summary["ingested"] == 0


@pytest.mark.asyncio
async def test_long_body_is_capped(cfg_with_plans) -> None:
    """Plans longer than _BODY_CAP_CHARS are truncated with a sentinel
    so the original file stays the source of truth on disk."""
    cfg, plans_dir = cfg_with_plans
    plan = plans_dir / "epic-plan.md"
    long_body = "X" * (plan_ingest._BODY_CAP_CHARS + 5000)
    plan.write_text(long_body)

    await plan_ingest.ingest_claude_plans(cfg, "u-pi")
    note = await store.find_by_title(cfg, "u-pi", "Plan · epic plan")
    assert note is not None

    # The stored content carries a frontmatter block plus the truncated
    # body. Parse the frontmatter out so we're asserting against the
    # post-truncation body, not "body + ~250 chars of YAML".
    from lazyclaw.lazybrain.frontmatter import parse_frontmatter
    _props, body, _has = parse_frontmatter(note["content"])
    assert len(body) <= plan_ingest._BODY_CAP_CHARS + 4, (
        f"truncation must clamp the brain-resident excerpt; got {len(body)}"
    )
    assert body.endswith("…"), "truncation marker must be visible"


@pytest.mark.asyncio
async def test_missing_plans_dir_is_safe(tmp_path: Path, monkeypatch) -> None:
    """Pointing the override at a non-existent directory must never raise."""
    nope = tmp_path / "does-not-exist"
    monkeypatch.setenv("LAZYCLAW_CLAUDE_PLANS_DIR", str(nope))

    c = Config(database_dir=tmp_path / "db")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u-empty", "bob", "x", "salt-empty"),
        )
        await db.commit()

    try:
        summary = await plan_ingest.ingest_claude_plans(c, "u-empty")
        assert summary == {
            "checked": 0, "ingested": 0, "skipped": 0, "opted_out": False,
        }
    finally:
        await close_pool()
