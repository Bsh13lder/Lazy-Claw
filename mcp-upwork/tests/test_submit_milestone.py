"""Unit tests for upwork_submit_milestone.

The submit-milestone tool is one of the most sensitive write actions in
the MCP — it kicks off Upwork's 14-day client-review timer and notifies
the client. These tests cover the pre-browser refusal layer so a bad
draft never reaches the modal.
"""

from __future__ import annotations

import pytest

from upwork_mcp.tools.contracts import SubmitMilestoneParams, submit_milestone


# ── Param validation ────────────────────────────────────────────────


def test_submit_milestone_params_defaults():
    p = SubmitMilestoneParams(
        contract_url="https://www.upwork.com/nx/wm/contracts/123",
    )
    assert p.message == ""
    assert p.milestone_id is None
    assert p.attachments is None
    assert p.draft_only is False


# ── URL validation guards ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_milestone_refuses_non_http_contract_url():
    p = SubmitMilestoneParams(contract_url="not-a-url")
    out = await submit_milestone(p)
    assert out["status"] == "error"
    assert "full URL" in out["message"]


@pytest.mark.asyncio
async def test_submit_milestone_refuses_non_upwork_contract_url():
    p = SubmitMilestoneParams(contract_url="https://malicious.example.com/c/1")
    out = await submit_milestone(p)
    assert out["status"] == "error"
    assert "upwork.com" in out["message"]


# ── Message URL guard ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_milestone_refuses_url_in_message():
    p = SubmitMilestoneParams(
        contract_url="https://www.upwork.com/nx/wm/contracts/123",
        message="see deliverable at https://my-cdn.example.com/file.zip",
    )
    out = await submit_milestone(p)
    assert out["status"] == "blocked"
    assert out["offending_token"]


@pytest.mark.asyncio
async def test_submit_milestone_refuses_lazyclaw_in_message():
    p = SubmitMilestoneParams(
        contract_url="https://www.upwork.com/nx/wm/contracts/123",
        message="Built with LazyClaw — see attached",
    )
    out = await submit_milestone(p)
    assert out["status"] == "blocked"
    assert "lazyclaw" in out["offending_token"].lower()


# ── Attachment pre-flight ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_milestone_rejects_missing_attachment():
    p = SubmitMilestoneParams(
        contract_url="https://www.upwork.com/nx/wm/contracts/123",
        message="files attached",
        attachments=["/tmp/does-not-exist-9f2a3b.zip"],
    )
    out = await submit_milestone(p)
    assert out["status"] == "missing_attachment"
    assert "does-not-exist" in out["missing_path"]
