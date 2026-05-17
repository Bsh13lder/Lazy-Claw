"""Tests for ``code_workspace_dir`` — the per-task workspace path resolver.

The path is built from user-derived strings (``project_tag`` can come from
anywhere — Upwork job ID, user prompt parsing, etc.) so we need confidence
that:

1. Slugification strips path-traversal characters (``..``, ``/``, leading
   ``.``) — a malicious ``project_tag`` must NOT escape the workspace mount.
2. Empty / None values fall back to safe defaults (``untagged``, ``adhoc``,
   ``task``) rather than producing a path like ``//foo//`` that would
   silently land outside ``/workspace``.
3. The slug is capped so a 10k-char tag can't blow path-length limits.
4. The resolver is pure — same inputs produce same path, no side effects.
"""

from __future__ import annotations

import pytest

from lazyclaw.teams.specialist import (
    WORKSPACE_ROOT,
    _slugify_for_path,
    code_workspace_dir,
)


class TestSlugifyForPath:
    """Sanitization is the security boundary — exhaustive."""

    @pytest.mark.parametrize(
        "value,fallback,expected",
        [
            ("upwork:job-123", "untagged", "upwork_job-123"),
            ("Reddit DM", "untagged", "reddit_dm"),
            ("user_request", "untagged", "user_request"),
            ("", "untagged", "untagged"),
            (None, "untagged", "untagged"),
            ("   ", "untagged", "untagged"),
        ],
    )
    def test_happy_path(self, value, fallback, expected):
        assert _slugify_for_path(value, fallback) == expected

    @pytest.mark.parametrize(
        "evil",
        [
            "../etc/passwd",
            "/etc/passwd",
            "...//../",
            ".hidden",
            "..",
            "../../..",
            "/foo/bar/../baz",
        ],
    )
    def test_path_traversal_blocked(self, evil):
        """Path-traversal characters must never survive into the slug."""
        out = _slugify_for_path(evil, "fallback")
        # No traversal sequences. No path separator. No leading dot.
        assert ".." not in out
        assert "/" not in out
        assert not out.startswith(".")
        # Must produce a non-empty, safe segment.
        assert out
        # Must consist only of [a-z0-9_-].
        assert all(c.isalnum() or c in "_-" for c in out)

    def test_long_input_truncated(self):
        very_long = "x" * 500
        out = _slugify_for_path(very_long, "fallback")
        assert len(out) <= 64

    def test_lowercase_normalization(self):
        assert _slugify_for_path("MixedCASE", "fb") == "mixedcase"


class TestCodeWorkspaceDir:
    """End-to-end path resolution. Pure function — no FS side effects."""

    def test_all_fields_present(self):
        path = code_workspace_dir(
            task_id="task-abc",
            project_tag="upwork:job-42",
            goal_id="goal-xyz",
            root="/workspace",
        )
        assert path == "/workspace/upwork_job-42/goal-xyz/task-abc"

    def test_only_task_id(self):
        path = code_workspace_dir(task_id="t1", root="/workspace")
        assert path == "/workspace/untagged/adhoc/t1"

    def test_none_falls_back_safely(self):
        path = code_workspace_dir(
            task_id="t1",
            project_tag=None,
            goal_id=None,
            root="/workspace",
        )
        assert path == "/workspace/untagged/adhoc/t1"

    def test_evil_project_tag_cannot_escape(self):
        """Adversarial input: project_tag tries to escape /workspace.

        Any traversal attempt must land under /workspace — even after
        os.path.normpath collapses redundant separators. This is the
        single most important contract of this function.
        """
        import os

        path = code_workspace_dir(
            task_id="t1",
            project_tag="../../etc/passwd",
            goal_id="goal",
            root="/workspace",
        )
        # Normalized path must still be rooted under /workspace.
        norm = os.path.normpath(path)
        assert norm.startswith("/workspace/"), (
            f"path escape: {path!r} normalized to {norm!r}"
        )

    def test_default_root_is_module_constant(self):
        path = code_workspace_dir(task_id="t1", project_tag="upwork:job-1")
        assert path.startswith(WORKSPACE_ROOT)

    def test_purity_same_inputs_same_output(self):
        kwargs = dict(task_id="t1", project_tag="upwork:job-1", goal_id="g")
        a = code_workspace_dir(**kwargs)
        b = code_workspace_dir(**kwargs)
        assert a == b

    def test_no_filesystem_side_effect(self, tmp_path):
        """The resolver must NOT create the directory. Callers do that.

        Verifies isolation — unit tests should be able to exercise path
        resolution without touching the filesystem.
        """
        target = code_workspace_dir(
            task_id="never-created",
            project_tag="test",
            goal_id="g",
            root=str(tmp_path),
        )
        import os
        assert not os.path.exists(target)
