"""Tests for the contract-poll cron pipeline (PR 2).

Covers:
  - ``lazyclaw/survival/contracts.py`` tracking helpers (add / get /
    filter_unseen / FIFO eviction at SEEN_CAP / dup-no-op / empty reject)
  - ``UpworkContractPollSkill`` happy path (new contracts → formatted
    Telegram message + marked seen)
  - dedup across calls (no double-report)
  - ``dry_run`` path doesn't poison the seen set
  - missing MCP tools surfaced gracefully
  - empty MCP result → ``[SILENT]`` prefix
  - JSON-string response normalization
  - instant_dispatch route matches the cron's canonical instruction
  - mode_skill registers the cron with the canonical instruction
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.runtime.instant_dispatch import INSTANT_ROUTES, _CRON_PREFIX_RE
from lazyclaw.skills.builtin.survival.mode_skill import (
    SURVIVAL_CANONICAL_INSTRUCTIONS,
)
from lazyclaw.skills.builtin.survival.upwork_contract_poll import (
    UpworkContractPollSkill,
    _normalize_contracts,
)
from lazyclaw.survival.contracts import (
    SEEN_CAP,
    add_seen_contract_url,
    filter_unseen,
    get_seen_contract_urls,
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


# ── contracts.py tracking helpers ────────────────────────────────────


@pytest.mark.asyncio
async def test_get_seen_empty_default(tmp_config):
    assert await get_seen_contract_urls(tmp_config, "u1") == []


@pytest.mark.asyncio
async def test_add_seen_basic(tmp_config):
    seen = await add_seen_contract_url(
        tmp_config, "u1", "https://www.upwork.com/c/A",
    )
    assert seen == ["https://www.upwork.com/c/A"]
    assert await get_seen_contract_urls(tmp_config, "u1") == [
        "https://www.upwork.com/c/A",
    ]


@pytest.mark.asyncio
async def test_add_seen_idempotent(tmp_config):
    await add_seen_contract_url(tmp_config, "u1", "https://x/A")
    await add_seen_contract_url(tmp_config, "u1", "https://x/A")
    await add_seen_contract_url(tmp_config, "u1", "https://x/A")
    assert await get_seen_contract_urls(tmp_config, "u1") == ["https://x/A"]


@pytest.mark.asyncio
async def test_add_seen_rejects_empty(tmp_config):
    with pytest.raises(ValueError):
        await add_seen_contract_url(tmp_config, "u1", "")
    with pytest.raises(ValueError):
        await add_seen_contract_url(tmp_config, "u1", "   ")


@pytest.mark.asyncio
async def test_add_seen_fifo_eviction(tmp_config):
    """When the list passes SEEN_CAP, oldest entries are evicted."""
    for i in range(SEEN_CAP + 5):
        await add_seen_contract_url(tmp_config, "u1", f"https://x/c{i:03d}")
    seen = await get_seen_contract_urls(tmp_config, "u1")
    assert len(seen) == SEEN_CAP
    # First 5 (c000..c004) should be gone; last entry should be the most recent
    assert "https://x/c000" not in seen
    assert "https://x/c004" not in seen
    assert seen[-1] == f"https://x/c{SEEN_CAP + 4:03d}"


@pytest.mark.asyncio
async def test_filter_unseen(tmp_config):
    await add_seen_contract_url(tmp_config, "u1", "https://x/A")
    await add_seen_contract_url(tmp_config, "u1", "https://x/B")
    urls = ["https://x/A", "https://x/B", "https://x/C", "https://x/D"]
    new = await filter_unseen(tmp_config, "u1", urls)
    assert new == ["https://x/C", "https://x/D"]


@pytest.mark.asyncio
async def test_filter_unseen_does_not_mark(tmp_config):
    """filter_unseen is read-only; caller must call add_seen explicitly."""
    urls = ["https://x/A", "https://x/B"]
    await filter_unseen(tmp_config, "u1", urls)
    # Nothing was marked seen — second call should still report both as new
    assert await filter_unseen(tmp_config, "u1", urls) == urls


@pytest.mark.asyncio
async def test_filter_unseen_empty_input(tmp_config):
    assert await filter_unseen(tmp_config, "u1", []) == []


# ── _normalize_contracts shape coercion ──────────────────────────────


def test_normalize_handles_list():
    raw = [{"url": "https://x/A", "title": "T"}]
    assert _normalize_contracts(raw) == raw


def test_normalize_handles_string():
    raw = json.dumps([{"url": "https://x/A"}])
    assert _normalize_contracts(raw) == [{"url": "https://x/A"}]


def test_normalize_handles_result_wrapper():
    raw = {"result": [{"url": "https://x/A"}]}
    assert _normalize_contracts(raw) == [{"url": "https://x/A"}]


def test_normalize_handles_garbage():
    assert _normalize_contracts(None) == []
    assert _normalize_contracts("not-json") == []
    assert _normalize_contracts({"weird_key": "value"}) == []
    assert _normalize_contracts([1, 2, "string"]) == []  # non-dict items dropped


# ── UpworkContractPollSkill ──────────────────────────────────────────


class _FakeTool:
    def __init__(self, response):
        self._response = response

    async def execute(self, user_id: str, params: dict):
        return self._response


class _FakeRegistry:
    def __init__(self, response):
        self._tool = _FakeTool(response)

    def list_mcp_tools(self):
        return [{"function": {"name": "mcp_abc_upwork_get_contracts"}}]

    def get(self, name: str):
        if "upwork_get_contracts" in name:
            return self._tool
        return None


class _EmptyRegistry:
    def list_mcp_tools(self):
        return []

    def get(self, name: str):
        return None


@pytest.mark.asyncio
async def test_poll_no_mcp_tool_graceful(tmp_config):
    skill = UpworkContractPollSkill(config=tmp_config, registry=_EmptyRegistry())
    result = await skill.execute("u1", {})
    assert "mcp-upwork tools not registered" in result


@pytest.mark.asyncio
async def test_poll_empty_response_silent(tmp_config):
    registry = _FakeRegistry({"result": []})
    skill = UpworkContractPollSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {})
    assert result.startswith("[SILENT]")
    assert "No active contracts" in result


@pytest.mark.asyncio
async def test_poll_new_contract_reports_and_marks(tmp_config):
    contracts = [
        {
            "url": "https://www.upwork.com/c/CONTRACT_A",
            "title": "Build a monitor",
            "client_name": "James Blue",
            "status": "active",
            "rate": "$120",
        },
    ]
    registry = _FakeRegistry({"result": contracts})
    skill = UpworkContractPollSkill(config=tmp_config, registry=registry)

    result = await skill.execute("u1", {})
    assert "1 new contract" in result
    assert "Build a monitor" in result
    assert "James Blue" in result
    assert "$120" in result
    assert "CONTRACT_A" in result
    # NOT silent — there's news to report
    assert not result.startswith("[SILENT]")

    # Was marked seen
    seen = await get_seen_contract_urls(tmp_config, "u1")
    assert seen == ["https://www.upwork.com/c/CONTRACT_A"]


@pytest.mark.asyncio
async def test_poll_dedup_across_calls(tmp_config):
    """Second call with the same contracts produces [SILENT] no-new."""
    contracts = [
        {"url": "https://x/A", "title": "T1", "client_name": "C1"},
        {"url": "https://x/B", "title": "T2", "client_name": "C2"},
    ]
    registry = _FakeRegistry({"result": contracts})
    skill = UpworkContractPollSkill(config=tmp_config, registry=registry)

    # First call — both new
    first = await skill.execute("u1", {})
    assert "2 new contract" in first

    # Second call — same response, nothing new
    second = await skill.execute("u1", {})
    assert second.startswith("[SILENT]")
    assert "0 new" in second


@pytest.mark.asyncio
async def test_poll_dry_run_does_not_mark(tmp_config):
    contracts = [{"url": "https://x/A", "title": "T", "client_name": "C"}]
    registry = _FakeRegistry({"result": contracts})
    skill = UpworkContractPollSkill(config=tmp_config, registry=registry)

    result = await skill.execute("u1", {"dry_run": True})
    assert "1 new contract" in result

    # Seen set unchanged — dry_run flag honored
    assert await get_seen_contract_urls(tmp_config, "u1") == []


@pytest.mark.asyncio
async def test_poll_partial_new_only_unseen_reported(tmp_config):
    """If A was already seen and B is new, only B reports."""
    await add_seen_contract_url(tmp_config, "u1", "https://x/A")
    contracts = [
        {"url": "https://x/A", "title": "Old", "client_name": "C1"},
        {"url": "https://x/B", "title": "New", "client_name": "C2"},
    ]
    registry = _FakeRegistry({"result": contracts})
    skill = UpworkContractPollSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {})
    assert "1 new contract" in result
    assert "New" in result
    assert "Old" not in result


@pytest.mark.asyncio
async def test_poll_string_response_parsed(tmp_config):
    """mcp-upwork sometimes returns a JSON-encoded string; must parse."""
    contracts = [{"url": "https://x/A", "title": "T", "client_name": "C"}]
    registry = _FakeRegistry(json.dumps({"result": contracts}))
    skill = UpworkContractPollSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {})
    assert "1 new contract" in result


# ── instant_dispatch routing ─────────────────────────────────────────


def test_instant_dispatch_matches_canonical_instruction():
    """The canonical cron instruction must route to upwork_contract_poll."""
    canonical = SURVIVAL_CANONICAL_INSTRUCTIONS["survival_contract_intake"]
    cron_form = f"[JOB:survival_contract_intake] {canonical}"
    stripped = _CRON_PREFIX_RE.sub("", cron_form.strip(), count=1).strip().lower()

    matched = None
    for route in INSTANT_ROUTES:
        if route.skill_name == "upwork_contract_poll":
            if route.pattern.match(stripped):
                matched = route
                break
    assert matched is not None, (
        f"canonical instruction {canonical!r} (stripped: {stripped!r}) did "
        "not match the upwork_contract_poll instant-dispatch route — the "
        "cron would ping-pong through search_tools every fire"
    )


@pytest.mark.parametrize("phrase", [
    "check my upwork contracts",
    "check upwork contracts",
    "list upwork contracts",
    "show me my upwork contracts",
    "poll upwork contracts",
    "poll my upwork contracts and intake any new ones",
    "scan upwork contracts now",
])
def test_instant_dispatch_matches_user_phrases(phrase):
    """User-typed variants must also hit the same route."""
    for route in INSTANT_ROUTES:
        if route.skill_name == "upwork_contract_poll":
            assert route.pattern.match(phrase.lower()), (
                f"phrase {phrase!r} should hit upwork_contract_poll"
            )
            return
    pytest.fail("upwork_contract_poll route not found in INSTANT_ROUTES")


@pytest.mark.parametrize("phrase", [
    "check my upwork inbox",
    "send a message to john",
    "what time is it",
])
def test_instant_dispatch_does_not_overmatch(phrase):
    """Unrelated phrases must NOT match the contract poll route."""
    for route in INSTANT_ROUTES:
        if route.skill_name == "upwork_contract_poll":
            assert not route.pattern.match(phrase.lower()), (
                f"phrase {phrase!r} wrongly matched contract poll"
            )
            return


def test_canonical_instruction_registered():
    """Drift-restore key present so heartbeat protects this cron too."""
    assert "survival_contract_intake" in SURVIVAL_CANONICAL_INSTRUCTIONS
    assert (
        SURVIVAL_CANONICAL_INSTRUCTIONS["survival_contract_intake"]
        == "poll my upwork contracts and intake any new ones"
    )
