"""Test that ``run_specialist`` populates Code Specialist visibility fields.

These five fields ride into the CodeSpecialist.tsx page so the user can see:
 - which prompt was sent to claude-code MCP (``prompt_sent``)
 - what claude-code did, step by step (``transcript``)
 - where generated files live (``workspace_dir``, ``files_touched``)
 - a one-line project summary (``short_description``)

The point of this test isn't to exercise the full agent loop — that's
covered by integration tests. It's to lock down the capture contract:
the Code Specialist run *must* populate these fields, and other
specialists *must* leave them empty so the UI hides the panels.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lazyclaw.llm.providers.base import LLMResponse, ToolCall
from lazyclaw.teams.runner import (
    SpecialistResult,
    TranscriptStep,
    _scan_workspace_files,
    _summarize,
    run_specialist,
)
from lazyclaw.teams.specialist import (
    BROWSER_SPECIALIST,
    CODE_SPECIALIST,
    SpecialistConfig,
)


# ── Pure-function unit tests (no agent loop) ─────────────────────────


class TestSummarize:
    def test_string_passthrough(self):
        assert _summarize("hello world", 100) == "hello world"

    def test_string_truncated(self):
        assert _summarize("x" * 200, 50) == "x" * 50

    def test_dict_serialized(self):
        out = _summarize({"a": 1, "b": "two"}, 100)
        assert "a" in out and "1" in out and "b" in out

    def test_newlines_collapsed(self):
        # Multi-line tool args must render as single-line previews so
        # the timeline doesn't wrap.
        out = _summarize("line1\nline2\nline3", 100)
        assert "\n" not in out
        assert "line1 line2 line3" == out

    def test_bad_input_falls_back(self):
        # Non-serializable input must NOT raise. `json.dumps(default=str)`
        # serializes Weird via str(), which yields the quoted form
        # "<weird>" — exact representation is unimportant; what matters
        # is that we get a non-empty string back without an exception.
        class Weird:
            def __repr__(self):
                return "<weird>"
        out = _summarize(Weird(), 100)
        assert "weird" in out


class TestScanWorkspaceFiles:
    def test_empty_path_returns_empty(self):
        assert _scan_workspace_files("") == ()

    def test_missing_dir_returns_empty(self):
        assert _scan_workspace_files("/definitely/does/not/exist") == ()

    def test_lists_files_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("zoo.py", "apple.py", "mango.md"):
                with open(os.path.join(tmp, name), "w") as f:
                    f.write("# test")
            files = _scan_workspace_files(tmp)
            assert files == ("apple.py", "mango.md", "zoo.py")

    def test_skips_noise_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Real file at root
            with open(os.path.join(tmp, "main.py"), "w") as f:
                f.write("# test")
            # Noise: must be skipped
            for noise in (".git", "__pycache__", "node_modules", ".venv"):
                os.makedirs(os.path.join(tmp, noise))
                with open(os.path.join(tmp, noise, "junk.txt"), "w") as f:
                    f.write("junk")
            # Hidden file: must be skipped
            with open(os.path.join(tmp, ".hidden"), "w") as f:
                f.write("hidden")

            files = _scan_workspace_files(tmp)
            assert files == ("main.py",)


# ── Integration: run_specialist actually captures the fields ────────


@pytest.fixture
def fake_registry():
    """Skill registry returning no tools — minimal scaffolding."""
    r = MagicMock()
    r.list_tools.return_value = []
    r.list_mcp_tools.return_value = []
    return r


@pytest.fixture
def fake_router():
    """EcoRouter stub. First chat() returns a tool call, second returns final text."""
    router = MagicMock()
    # Sequence: first chat returns a tool_use call, second returns the final reply.
    router.chat = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[ToolCall(
                    id="tc_1",
                    name="calculate",
                    arguments={"expression": "2+2"},
                )],
                model="test-model",
            ),
            LLMResponse(
                content="The answer is 4. Files written to workspace.",
                tool_calls=None,
                model="test-model",
            ),
        ]
    )
    return router


@pytest.mark.asyncio
async def test_code_specialist_populates_visibility_fields(
    tmp_path, fake_registry, fake_router,
):
    """The Code Specialist run must populate ALL five visibility fields.

    This is the contract the CodeSpecialist.tsx page depends on. Use a
    tmp_path as the workspace root so we don't litter /workspace.
    """
    # Pre-create a fake "generated file" the post-run scan should find.
    workspace_root = str(tmp_path)
    # Patch the WORKSPACE_ROOT so resolver lands in tmp_path.
    with patch("lazyclaw.teams.runner.code_workspace_dir") as mock_resolver:
        per_task_dir = os.path.join(workspace_root, "proj", "g1", "task-1")
        os.makedirs(per_task_dir, exist_ok=True)
        with open(os.path.join(per_task_dir, "hello.py"), "w") as f:
            f.write("print('hi')")
        mock_resolver.return_value = per_task_dir

        # Patch the tool executor to return a canned result without
        # actually running the calculate skill.
        with patch("lazyclaw.teams.runner.ToolExecutor") as MockExec:
            inst = MockExec.return_value
            inst.execute = AsyncMock(return_value="4")

            result = await run_specialist(
                user_id="u1",
                specialist=CODE_SPECIALIST,
                task="Compute 2+2 and write it to hello.py",
                registry=fake_registry,
                eco_router=fake_router,
                permission_checker=None,
                project_tag="proj",
                goal_id="g1",
                task_id="task-1",
            )

    # ── Visibility-field contract ─────────────────────────────────
    assert result.success is True
    assert result.workspace_dir == per_task_dir
    assert "hello.py" in result.files_touched
    # short_description = first line of task, ≤120 chars.
    assert result.short_description.startswith("Compute 2+2")
    # prompt_sent must contain the WORKSPACE: hint so the user can see
    # exactly what claude-code received.
    assert "WORKSPACE:" in result.prompt_sent
    assert per_task_dir in result.prompt_sent
    # Transcript captured the one tool call.
    assert len(result.transcript) == 1
    step = result.transcript[0]
    assert isinstance(step, TranscriptStep)
    assert step.name == "calculate"
    assert "2+2" in step.args_summary
    assert step.success is True


@pytest.mark.asyncio
async def test_non_code_specialist_leaves_fields_empty(
    tmp_path, fake_registry, fake_router,
):
    """Browser/research specialists MUST leave the new fields empty so
    the CodeSpecialist.tsx hide-empty contract holds. If we accidentally
    started populating them everywhere, the page would show empty
    'Workspace' / 'Prompt' panels on every task.
    """
    with patch("lazyclaw.teams.runner.ToolExecutor") as MockExec:
        inst = MockExec.return_value
        inst.execute = AsyncMock(return_value="ok")

        result = await run_specialist(
            user_id="u1",
            specialist=BROWSER_SPECIALIST,
            task="open google.com",
            registry=fake_registry,
            eco_router=fake_router,
            permission_checker=None,
            project_tag="proj",
            goal_id="g1",
            task_id="task-2",
        )

    assert result.workspace_dir == ""
    assert result.files_touched == ()
    assert result.transcript == ()
    assert result.prompt_sent == ""
    assert result.short_description == ""
