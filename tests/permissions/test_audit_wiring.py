"""Audit log wiring (2026-06-10 audit, Phase 3).

The audit_log schema + query API existed since the permissions module
shipped, but NOTHING in the execution path ever called ``log_action`` —
TODO.md said "complete" while the table stayed empty. These tests pin the
three wiring points: tool execution, tool denial, and permission changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.llm.providers.base import ToolCall
from lazyclaw.permissions.models import ALLOW, DENY, ResolvedPermission
from lazyclaw.runtime.tool_executor import ToolExecutor

pytestmark = pytest.mark.asyncio


class _FakeSkill:
    name = "echo"
    category = "utility"
    read_only = True
    timeout = 5

    async def execute(self, user_id: str, params: dict) -> str:
        return "ok"


class _FakeRegistry:
    def get(self, name: str):
        return _FakeSkill() if name == "echo" else None


class _FakeChecker:
    def __init__(self, level: str) -> None:
        self._level = level

    async def check_effective(self, user_id: str, skill_name: str) -> ResolvedPermission:
        return ResolvedPermission(skill_name=skill_name, level=self._level, source="test")

    check = check_effective


@pytest.fixture
async def config(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo!!")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    await create_user_dek(c, "u1", "salt-a")
    try:
        yield c
    finally:
        await close_pool()


async def _audit_rows(config, action: str) -> list[tuple]:
    async with db_session(config) as db:
        rows = await (
            await db.execute(
                "SELECT user_id, action, skill_name FROM audit_log WHERE action = ?",
                (action,),
            )
        ).fetchall()
    return [tuple(r) for r in rows]


async def test_allowed_execution_writes_tool_executed(config):
    executor = ToolExecutor(_FakeRegistry(), _FakeChecker(ALLOW), config=config)
    result = await executor.execute(ToolCall(id="t1", name="echo", arguments={}), "u1")
    assert result == "ok"
    rows = await _audit_rows(config, "tool_executed")
    assert rows == [("u1", "tool_executed", "echo")]


async def test_denied_execution_writes_tool_denied(config):
    executor = ToolExecutor(_FakeRegistry(), _FakeChecker(DENY), config=config)
    result = await executor.execute(ToolCall(id="t1", name="echo", arguments={}), "u1")
    assert "not permitted" in result
    rows = await _audit_rows(config, "tool_denied")
    assert rows == [("u1", "tool_denied", "echo")]


async def test_execute_allowed_after_approval_writes_tool_approved(config):
    executor = ToolExecutor(_FakeRegistry(), _FakeChecker(ALLOW), config=config)
    result = await executor.execute_allowed(
        ToolCall(id="t1", name="echo", arguments={}), "u1"
    )
    assert result == "ok"
    rows = await _audit_rows(config, "tool_approved")
    assert rows == [("u1", "tool_approved", "echo")]


async def test_permission_change_writes_audit_row(config):
    from lazyclaw.permissions.settings import apply_permission_change

    await apply_permission_change(config, "u1", "payment", "deny")
    rows = await _audit_rows(config, "permission_changed")
    assert rows == [("u1", "permission_changed", "payment")]


async def test_audit_failure_never_breaks_execution(config, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("audit db gone")

    monkeypatch.setattr("lazyclaw.runtime.tool_executor.log_action", _boom)
    executor = ToolExecutor(_FakeRegistry(), _FakeChecker(ALLOW), config=config)
    result = await executor.execute(ToolCall(id="t1", name="echo", arguments={}), "u1")
    assert result == "ok"
