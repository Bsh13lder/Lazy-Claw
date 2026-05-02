"""Auto-save browser templates — upsert by host + correction capture.

Guarantees:
  1. ``upsert_by_host`` on an empty store creates one row with run_count=1
     and ``auto_saved=1``.
  2. A second call for the same host updates the existing row, bumps
     run_count, and merges new setup URLs / checkpoints.
  3. Existing user-edited playbook is NOT clobbered by an auto-save (we
     only fill empty fields on update).
  4. ``get_template_by_host`` is case-insensitive on the netloc.
  5. ``append_correction`` appends a ``## Correction`` block, bumps
     ``corrections_pending``, and dedups same text within 60s.
  6. Compaction threshold = 5 → after the 5th correction, a background
     task is scheduled (we don't run the worker LLM here, just confirm
     the trigger fires without raising).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lazyclaw.browser import template_corrections
from lazyclaw.browser import templates as tpl_store
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db


@pytest.fixture
async def tmp_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("user-1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_upsert_creates_then_updates(tmp_config: Config) -> None:
    tpl, was_created = await tpl_store.upsert_by_host(
        tmp_config, "user-1",
        host="upwork.com",
        name="Upwork",
        icon="💼",
        setup_urls=["https://upwork.com/jobs"],
        checkpoints=["Submit"],
        playbook="Initial playbook.",
    )
    assert was_created is True
    assert tpl["run_count"] == 1
    assert tpl["success_count"] == 1
    assert int(tpl["auto_saved"]) == 1
    assert tpl["version"] == 1

    tpl2, was_created2 = await tpl_store.upsert_by_host(
        tmp_config, "user-1",
        host="upwork.com",
        name="Upwork",
        icon="💼",
        setup_urls=["https://upwork.com/messages"],  # new URL
        checkpoints=["Submit", "Confirm send"],       # one new checkpoint
        playbook="Should NOT replace the existing playbook.",
    )
    assert was_created2 is False
    assert tpl2["id"] == tpl["id"]
    assert tpl2["run_count"] == 2
    assert tpl2["success_count"] == 2
    # Existing playbook preserved — auto-save never clobbers user-edited content.
    assert tpl2["playbook"] == "Initial playbook."
    # Setup URLs and checkpoints merged uniquely.
    assert "https://upwork.com/jobs" in tpl2["setup_urls"]
    assert "https://upwork.com/messages" in tpl2["setup_urls"]
    assert tpl2["checkpoints"] == ["Submit", "Confirm send"]


@pytest.mark.asyncio
async def test_get_template_by_host_case_insensitive(tmp_config: Config) -> None:
    await tpl_store.upsert_by_host(
        tmp_config, "user-1",
        host="upwork.com",
        name="Upwork",
        icon=None,
        setup_urls=["https://Upwork.com/JOBS"],
        checkpoints=[],
        playbook=None,
    )
    found = await tpl_store.get_template_by_host(tmp_config, "user-1", "UPWORK.com")
    assert found is not None
    assert found["name"] == "Upwork"


@pytest.mark.asyncio
async def test_append_correction_dedup_and_bump(tmp_config: Config) -> None:
    tpl, _ = await tpl_store.upsert_by_host(
        tmp_config, "user-1",
        host="upwork.com",
        name="Upwork",
        icon=None,
        setup_urls=["https://upwork.com"],
        checkpoints=[],
        playbook="Steps:\n1. Open Upwork",
    )
    tpl_id = tpl["id"]

    # First correction lands.
    updated = await template_corrections.append_correction(
        tmp_config, "user-1", tpl_id, "side_note", "click Refresh first",
    )
    assert updated is not None
    assert updated["corrections_pending"] == 1
    assert "## Correction" in (updated["playbook"] or "")
    assert "click Refresh first" in (updated["playbook"] or "")

    # Same text within dedup window → no-op.
    dup = await template_corrections.append_correction(
        tmp_config, "user-1", tpl_id, "side_note", "click Refresh first",
    )
    assert dup is None

    # Different text bumps pending counter.
    diff = await template_corrections.append_correction(
        tmp_config, "user-1", tpl_id, "reject", "wait for Cloudflare to clear",
    )
    assert diff is not None
    assert diff["corrections_pending"] == 2


@pytest.mark.asyncio
async def test_compaction_trigger_fires_without_raising(
    tmp_config: Config, monkeypatch
) -> None:
    """Crossing the threshold schedules background compaction. We stub the
    worker LLM so the test doesn't need Ollama / network."""
    tpl, _ = await tpl_store.upsert_by_host(
        tmp_config, "user-1",
        host="example.com",
        name="Example",
        icon=None,
        setup_urls=["https://example.com"],
        checkpoints=[],
        playbook="Steps:\n1. Open page",
    )
    tpl_id = tpl["id"]

    # Stub compact_playbook to avoid an LLM call but still exercise the
    # update path that bumps version + clears corrections_pending.
    async def fake_compact(_router, _config, _user, _playbook):
        return "Compacted playbook v2.\nSteps:\n1. Open page\n2. Wait for Cloudflare"

    from lazyclaw.browser import template_synth
    monkeypatch.setattr(template_synth, "compact_playbook", fake_compact)

    for i in range(5):
        await template_corrections.append_correction(
            tmp_config, "user-1", tpl_id, "side_note", f"correction #{i}",
        )

    # Compaction is scheduled via asyncio.create_task — give it a tick.
    for _ in range(20):
        await asyncio.sleep(0.05)
        post = await tpl_store.get_template(tmp_config, "user-1", tpl_id)
        if post and post.get("version", 1) > 1:
            break
    post = await tpl_store.get_template(tmp_config, "user-1", tpl_id)
    assert post is not None
    assert post["version"] == 2
    assert post["corrections_pending"] == 0
    assert "Compacted playbook v2" in (post["playbook"] or "")
