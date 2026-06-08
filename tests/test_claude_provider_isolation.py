"""Claude-process session isolation — both transports.

Regression guard for the Claude-Code <-> lazyclaw session leak: the spawned
``claude`` process must (a) load NO host-user filesystem settings
(``setting_sources=[]`` / ``--setting-sources=``) and (b) run in a
lazyclaw-owned cwd so its transcripts never co-mingle with the user's
personal Claude Code sessions in the inherited process cwd.

The SDK side is covered in ``test_claude_sdk_provider.py::TestSessionIsolation``;
this file covers the shared helper and the legacy CLI transport.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lazyclaw.llm.providers._claude_home import isolated_claude_cwd
from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.llm.providers.claude_cli_provider import ClaudeCLIProvider


class TestIsolatedClaudeCwd:
    def test_lives_under_database_dir(self, tmp_path) -> None:
        with patch.dict(os.environ, {"DATABASE_DIR": str(tmp_path)}, clear=False):
            cwd = isolated_claude_cwd()
        assert os.path.realpath(str(tmp_path)) in cwd
        # no user_id → stable _shared leaf under claude_agent_home
        assert cwd.endswith(os.path.join("claude_agent_home", "_shared"))

    def test_creates_directory(self, tmp_path) -> None:
        target = tmp_path / "fresh"
        with patch.dict(os.environ, {"DATABASE_DIR": str(target)}, clear=False):
            cwd = isolated_claude_cwd()
        assert os.path.isdir(cwd)

    def test_default_when_unset(self, tmp_path) -> None:
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_DIR"}
        with patch.dict(os.environ, env, clear=True):
            cwd = isolated_claude_cwd()
        # falls back to ./data, resolved to an absolute path
        assert cwd.endswith(os.path.join("data", "claude_agent_home", "_shared"))
        assert os.path.isabs(cwd)

    def test_scoped_per_user(self, tmp_path) -> None:
        """Different users get DIFFERENT buckets so their agent transcripts
        never co-mingle (multi-tenant isolation guarantee)."""
        with patch.dict(os.environ, {"DATABASE_DIR": str(tmp_path)}, clear=False):
            a = isolated_claude_cwd("alice")
            b = isolated_claude_cwd("bob")
            shared = isolated_claude_cwd(None)
        assert a != b
        assert a.endswith(os.path.join("claude_agent_home", "u-alice"))
        assert b.endswith(os.path.join("claude_agent_home", "u-bob"))
        assert shared.endswith(os.path.join("claude_agent_home", "_shared"))

    def test_sanitizes_unsafe_user_id(self, tmp_path) -> None:
        """A user_id with path separators / unsafe chars cannot escape the
        claude_agent_home dir or inject traversal."""
        with patch.dict(os.environ, {"DATABASE_DIR": str(tmp_path)}, clear=False):
            cwd = isolated_claude_cwd("../../etc/passwd")
        base = os.path.realpath(os.path.join(str(tmp_path), "claude_agent_home"))
        assert cwd.startswith(base + os.sep)
        assert ".." not in os.path.relpath(cwd, base)


class TestCLIProviderSpawnIsolation:
    """Drive ClaudeCLIProvider.chat() with a mocked subprocess and assert
    the spawn carries the isolation flag + cwd."""

    @pytest.mark.asyncio
    async def test_spawn_passes_setting_sources_and_cwd(self, tmp_path) -> None:
        captured: dict = {}

        def _fake_exec(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(
                return_value=(
                    b'{"result":"ok","session_id":"s",'
                    b'"usage":{"input_tokens":1,"output_tokens":1}}',
                    b"",
                )
            )
            return proc

        p = ClaudeCLIProvider(claude_bin="claude", model="sonnet")
        with patch.dict(os.environ, {"DATABASE_DIR": str(tmp_path)}, clear=False), \
             patch.object(p, "_grab_warm_proc", return_value=None), \
             patch.object(p, "_pre_warm", new=AsyncMock()), \
             patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            await p.chat([LLMMessage(role="user", content="hi")])

        # inbound: empty setting-sources = SDK isolation mode
        assert "--setting-sources=" in captured["args"], (
            "CLI spawn must pass --setting-sources= so it does not load the "
            "user's personal ~/.claude settings/CLAUDE.md/hooks"
        )
        # outbound: pinned to the lazyclaw-owned isolated cwd under the
        # configured DATABASE_DIR (tmp_path here), NOT the process cwd.
        cwd = captured["kwargs"].get("cwd")
        assert cwd is not None, "CLI spawn must pin cwd (was unset → inherits process cwd)"
        assert os.path.realpath(str(tmp_path)) in cwd
        # no user_id passed → _shared leaf
        assert cwd.endswith(os.path.join("claude_agent_home", "_shared"))
        assert cwd != os.getcwd()

    @pytest.mark.asyncio
    async def test_spawn_cwd_scoped_per_user(self, tmp_path) -> None:
        """A user_id passed to chat() routes the spawn into that user's
        bucket — never the shared one."""
        captured: dict = {}

        def _fake_exec(*args, **kwargs):
            captured["kwargs"] = kwargs
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(
                return_value=(b'{"result":"ok","usage":{"input_tokens":1,"output_tokens":1}}', b"")
            )
            return proc

        p = ClaudeCLIProvider(claude_bin="claude", model="sonnet")
        with patch.dict(os.environ, {"DATABASE_DIR": str(tmp_path)}, clear=False), \
             patch.object(p, "_grab_warm_proc", return_value=None), \
             patch.object(p, "_pre_warm", new=AsyncMock()), \
             patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            await p.chat([LLMMessage(role="user", content="hi")], user_id="carol")

        assert captured["kwargs"]["cwd"].endswith(os.path.join("claude_agent_home", "u-carol"))

    @pytest.mark.asyncio
    async def test_health_check_uses_isolated_cwd(self, tmp_path) -> None:
        """Even the `claude --version` probe runs in the isolated cwd, not
        the inherited process cwd."""
        captured: dict = {}

        def _fake_exec(*args, **kwargs):
            captured["kwargs"] = kwargs
            proc = MagicMock()
            proc.returncode = 0
            proc.wait = AsyncMock(return_value=0)
            return proc

        p = ClaudeCLIProvider(claude_bin="claude", model="sonnet")
        with patch.dict(os.environ, {"DATABASE_DIR": str(tmp_path)}, clear=False), \
             patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            ok = await p.health_check()

        assert ok is True
        assert captured["kwargs"].get("cwd") is not None
        assert os.path.realpath(str(tmp_path)) in captured["kwargs"]["cwd"]


class TestCLIWarmPoolPerUser:
    """A warm proc spawned for one user must NOT be grabbed for another —
    else the second user's transcript would write into the first's bucket."""

    def test_grab_requires_matching_cwd(self) -> None:
        import time
        p = ClaudeCLIProvider(claude_bin="claude", model="sonnet")
        proc = MagicMock()
        proc.returncode = None  # alive
        args = ["claude", "-p", "--model", "sonnet"]
        # Pre-warmed under user A's cwd
        p._warm_procs = [(proc, time.monotonic(), tuple(args), "/data/claude_agent_home/u-alice")]
        # Bob asks for the same args but a different cwd → no reuse
        assert p._grab_warm_proc(args, "/data/claude_agent_home/u-bob") is None
        # Alice (same cwd) → reuse
        assert p._grab_warm_proc(args, "/data/claude_agent_home/u-alice") is proc
