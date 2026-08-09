"""Regression tests for agent_messages.metadata shape tolerance.

2026-06-10 incident: the metadata-encryption pass changed the stored
tool-calls shape from ``{"tool_calls": [...]}`` to a bare list.
``chat_history.get_session_messages`` still assumed a dict and raised
``AttributeError`` mid-loop — one new-shape row 500'd the whole history
endpoint, making the chat look like the DB was wiped.

2026-08 tool-observability pass: history now enriches each stored
tool-call entry at read time with ``display`` (MCP prefix stripped),
``result`` (joined from the role="tool" sibling row, capped at 500
chars) and ``status`` ("done"/"unknown"), and tags heartbeat-stamped
user rows ([JOB:/[WATCHER:/[REMINDER) with ``kind: "cron"``. All
retroactive — no rows are rewritten.
"""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes import chat_history
from lazyclaw.gateway.routes.chat_history import (
    _enrich_tool_calls,
    _extract_tool_calls,
)
from lazyclaw.runtime.session_resolver import (
    get_primary_session_id,
    invalidate_primary_session,
)


class TestExtractToolCalls:
    def test_new_bare_list_shape(self):
        """Post-codec rows store the tool-call list directly."""
        raw = json.dumps([{"id": "tc1", "name": "web_search", "arguments": "{}"}])
        result = _extract_tool_calls(raw)
        assert result == [{"id": "tc1", "name": "web_search", "arguments": "{}"}]

    def test_legacy_dict_shape(self):
        """Pre-codec rows wrap the list under a tool_calls key."""
        raw = json.dumps({"tool_calls": [{"id": "tc1", "name": "browser"}]})
        result = _extract_tool_calls(raw)
        assert result == [{"id": "tc1", "name": "browser"}]

    def test_legacy_dict_without_tool_calls_key(self):
        assert _extract_tool_calls(json.dumps({"other": 1})) is None

    def test_invalid_json_returns_none(self):
        assert _extract_tool_calls("not json at all") is None

    def test_decrypt_fallback_sentinel_returns_none(self):
        """decrypt_field falls back to '[encrypted]' on key mismatch."""
        assert _extract_tool_calls("[encrypted]") is None

    def test_none_returns_none(self):
        assert _extract_tool_calls(None) is None

    def test_scalar_json_returns_none(self):
        """A bare string/number is valid JSON but not a tool-call shape."""
        assert _extract_tool_calls(json.dumps("hello")) is None
        assert _extract_tool_calls(json.dumps(42)) is None

    def test_empty_list_passes_through(self):
        assert _extract_tool_calls(json.dumps([])) == []


class TestEnrichToolCalls:
    """Read-time enrichment: display + result join + status, fail-soft."""

    def test_matching_tool_row_attaches_result_and_done(self):
        entries = [{"id": "tc1", "name": "web_search", "arguments": "{}"}]
        out = _enrich_tool_calls(entries, {"tc1": "10 results found"})
        assert out[0]["result"] == "10 results found"
        assert out[0]["status"] == "done"
        # Original keys survive untouched.
        assert out[0]["id"] == "tc1"
        assert out[0]["name"] == "web_search"
        assert out[0]["arguments"] == "{}"

    def test_missing_tool_row_is_unknown_without_result_key(self):
        entries = [{"id": "tc-lost", "name": "web_search", "arguments": "{}"}]
        out = _enrich_tool_calls(entries, {})
        assert out[0]["status"] == "unknown"
        assert "result" not in out[0]

    def test_result_truncated_at_500(self):
        entries = [{"id": "tc1", "name": "browser", "arguments": "{}"}]
        out = _enrich_tool_calls(entries, {"tc1": "R" * 600})
        assert out[0]["result"] == "R" * 500

    def test_mcp_display_name_stripped(self):
        entries = [{
            "id": "tc1",
            "name": "mcp_8f2a1c3d-77e0-4b52-9c1e-abc123def456_send_message",
            "arguments": "{}",
        }]
        out = _enrich_tool_calls(entries, {})
        assert out[0]["display"] == "send_message"
        # `name` itself is untouched — clients match on it.
        assert out[0]["name"].startswith("mcp_")

    def test_plain_name_display_equals_name(self):
        entries = [{"id": "tc1", "name": "web_search", "arguments": "{}"}]
        out = _enrich_tool_calls(entries, {})
        assert out[0]["display"] == "web_search"

    def test_input_entries_not_mutated(self):
        entry = {"id": "tc1", "name": "web_search", "arguments": "{}"}
        _enrich_tool_calls([entry], {"tc1": "result"})
        assert entry == {"id": "tc1", "name": "web_search", "arguments": "{}"}

    def test_malformed_entries_pass_through(self):
        # Non-dict entries and entries with junk ids must never raise.
        entries = ["not-a-dict", {"id": ["unhashable"], "name": 42}, {}]
        out = _enrich_tool_calls(entries, {"tc1": "x"})
        assert out[0] == "not-a-dict"
        assert out[1]["status"] == "unknown"
        assert out[2]["status"] == "unknown"


# ── Endpoint-level enrichment (result join + cron tagging) ───────────────


async def _setup_db(tmp_path: Path) -> Config:
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    return cfg


def _fake_user() -> User:
    return User(id="u1", username="alice", display_name=None,
                encryption_salt="salt-a", role="user")


def _make_app(cfg: Config, monkeypatch) -> FastAPI:
    monkeypatch.setattr(chat_history, "_config", cfg)
    app = FastAPI()
    app.include_router(chat_history.router)
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    return app


_MCP_NAME = "mcp_8f2a1c3d-77e0-4b52-9c1e-abc123def456_send_message"


@pytest.fixture
async def env(tmp_path: Path, monkeypatch):
    invalidate_primary_session("u1")
    cfg = await _setup_db(tmp_path)

    session_id = await get_primary_session_id(cfg, "u1")
    key = await get_user_dek(cfg, "u1")

    tool_calls_meta = json.dumps([
        {"id": "tc-1", "name": _MCP_NAME, "arguments": '{"to": "James"}'},
        {"id": "tc-2", "name": "web_search", "arguments": '{"q": "x"}'},
    ])
    rows = [
        # Normal user row — must stay byte-identical (no kind key).
        ("m-user", "user", encrypt("hello there", key), None, None),
        # Assistant row with two tool calls.
        ("m-a", "assistant", encrypt("on it", key), None,
         encrypt(tool_calls_meta, key)),
        # Result row for tc-1 only — tc-2's result row is "lost".
        ("m-t1", "tool", encrypt("R" * 600, key), "tc-1", None),
        # Heartbeat-stamped internal turns.
        ("m-cron-job", "user", encrypt("[JOB:morning_brief] run it", key),
         None, None),
        ("m-cron-watch", "user", encrypt("[WATCHER:upwork] check inbox", key),
         None, None),
        ("m-cron-rem", "user", encrypt("[REMINDER] take meds", key),
         None, None),
    ]
    async with db_session(cfg) as db:
        for msg_id, role, content, tool_name, metadata in rows:
            await db.execute(
                "INSERT INTO agent_messages "
                "(id, user_id, chat_session_id, role, content, tool_name, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (msg_id, "u1", session_id, role, content, tool_name, metadata),
            )
        await db.commit()

    app = _make_app(cfg, monkeypatch)
    try:
        yield cfg, app, session_id
    finally:
        invalidate_primary_session("u1")
        await close_pool()


@pytest.mark.asyncio
async def test_history_joins_results_and_display(env):
    cfg, app, session_id = env
    with TestClient(app) as client:
        resp = client.get(f"/api/chat/sessions/{session_id}/messages")
    assert resp.status_code == 200
    by_id = {m["id"]: m for m in resp.json()["messages"]}

    tc1, tc2 = by_id["m-a"]["tool_calls"]
    # tc-1: matching tool row → result joined (capped 500) + done.
    assert tc1["id"] == "tc-1"
    assert tc1["name"] == _MCP_NAME
    assert tc1["display"] == "send_message"
    assert tc1["arguments"] == '{"to": "James"}'
    assert tc1["result"] == "R" * 500
    assert tc1["status"] == "done"
    # tc-2: no tool row on this page → unknown, no result key.
    assert tc2["display"] == "web_search"
    assert tc2["status"] == "unknown"
    assert "result" not in tc2

    # The role="tool" row itself keeps its exact payload shape.
    tool_row = by_id["m-t1"]
    assert tool_row["role"] == "tool"
    assert "kind" not in tool_row
    assert tool_row["content"] == "R" * 600


@pytest.mark.asyncio
async def test_history_tags_cron_rows(env):
    cfg, app, session_id = env
    with TestClient(app) as client:
        resp = client.get(f"/api/chat/sessions/{session_id}/messages")
    assert resp.status_code == 200
    by_id = {m["id"]: m for m in resp.json()["messages"]}

    for msg_id, content in (
        ("m-cron-job", "[JOB:morning_brief] run it"),
        ("m-cron-watch", "[WATCHER:upwork] check inbox"),
        ("m-cron-rem", "[REMINDER] take meds"),
    ):
        assert by_id[msg_id]["kind"] == "cron", msg_id
        assert by_id[msg_id]["content"] == content, "content must be unchanged"

    # Normal user row: no kind key at all.
    assert "kind" not in by_id["m-user"]
    assert by_id["m-user"]["content"] == "hello there"
