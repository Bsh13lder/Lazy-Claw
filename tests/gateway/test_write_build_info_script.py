"""scripts/write-build-info.sh — the source of the deploy stamp.

Everything downstream (image layer, /api/health) is only as trustworthy as
this script, so it is exercised against real throwaway git repos: clean tree,
dirty tree, untracked-only tree, and no-git-at-all.

Two invariants matter more than the field values:
  - it ALWAYS emits parseable JSON (a build must never die on its stamp), and
  - it ALWAYS exits 0, even with no git available.

Every subprocess here runs against a repo under tmp_path with an explicit
output path; the script never touches the real checkout.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from lazyclaw.gateway.build_info import load_build_info

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "write-build-info.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not installed"
)

# Isolate from the developer's global git config (signing, hooks, templates).
_COMMIT_FLAGS = [
    "-c", "user.name=Test",
    "-c", "user.email=test@example.invalid",
    "-c", "commit.gpgsign=false",
]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo with exactly one commit."""
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "--quiet")
    (path / "app.py").write_text("print('v1')\n")
    _git(path, "add", "app.py")
    _git(path, *_COMMIT_FLAGS, "commit", "--quiet", "-m", "init")
    return path


def _run(repo_dir: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), str(out)],
        cwd=str(repo_dir),
        env={**os.environ, "LAZYCLAW_BUILD_INFO_REPO": str(repo_dir)},
        capture_output=True, text=True,
    )


def _stamp(repo_dir: Path, out: Path) -> tuple[dict, subprocess.CompletedProcess]:
    result = _run(repo_dir, out)
    assert result.returncode == 0, (
        f"stamping must never fail a build (stderr: {result.stderr})"
    )
    return json.loads(out.read_text()), result


def test_clean_tree_produces_valid_json(repo, tmp_path):
    out = tmp_path / "BUILD_INFO.json"
    info, result = _stamp(repo, out)

    assert info["sha"] == _git(repo, "rev-parse", "HEAD")
    assert info["short_sha"] == _git(repo, "rev-parse", "--short=12", "HEAD")
    assert info["branch"] == _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    assert info["dirty"] is False
    assert info["dirty_files"] == []
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", info["built_at"])
    assert "clean tree" in result.stderr
    assert f"Deploying {info['short_sha']}" in result.stderr


def test_dirty_tree_is_flagged_and_listed(repo, tmp_path):
    (repo / "app.py").write_text("print('uncommitted work')\n")
    out = tmp_path / "BUILD_INFO.json"
    info, result = _stamp(repo, out)

    assert info["dirty"] is True
    assert info["dirty_files"] == ["app.py"]
    assert info["sha"] == _git(repo, "rev-parse", "HEAD")


def test_dirty_tree_warns_loudly_but_does_not_block(repo, tmp_path):
    (repo / "app.py").write_text("print('wip')\n")
    (repo / "second.py").write_text("x = 1\n")
    _git(repo, "add", "second.py")  # staged counts too
    out = tmp_path / "BUILD_INFO.json"
    info, result = _stamp(repo, out)

    assert result.returncode == 0, "a dirty tree must warn, never block"
    assert "WARNING" in result.stderr
    assert "UNCOMMITTED" in result.stderr
    assert "app.py" in result.stderr and "second.py" in result.stderr
    assert "-dirty" in result.stderr
    assert sorted(info["dirty_files"]) == ["app.py", "second.py"]


def test_untracked_files_do_not_mark_the_tree_dirty(repo, tmp_path):
    """BUILD_INFO.json itself is untracked — it must not self-trigger."""
    (repo / "BUILD_INFO.json").write_text("{}")
    (repo / "scratch.log").write_text("noise\n")
    out = tmp_path / "BUILD_INFO.json"
    info, _ = _stamp(repo, out)

    assert info["dirty"] is False
    assert info["dirty_files"] == []


def test_non_git_directory_yields_unknown_stub(tmp_path):
    """`docker compose build` in a tarball/CI checkout without .git."""
    plain = tmp_path / "no-git"
    plain.mkdir()
    out = tmp_path / "BUILD_INFO.json"
    info, result = _stamp(plain, out)

    assert info["sha"] == "unknown"
    assert info["dirty"] is None
    assert info["dirty_files"] == []
    assert "not a git repository" in result.stderr


def test_stamp_round_trips_through_the_gateway_loader(repo, tmp_path):
    """script → file → loader: the contract the health endpoint depends on."""
    (repo / "app.py").write_text("print('wip')\n")
    out = tmp_path / "BUILD_INFO.json"
    info, _ = _stamp(repo, out)

    loaded = load_build_info(out)
    assert loaded["sha"] == info["sha"]
    assert loaded["short_sha"] == info["short_sha"]
    assert loaded["branch"] == info["branch"]
    assert loaded["describe"] == info["describe"]
    assert loaded["dirty"] is True
    assert loaded["built_at"] == info["built_at"]


def test_paths_with_spaces_stay_valid_json(repo, tmp_path):
    tricky = repo / "a file with spaces.py"
    tricky.write_text("x = 1\n")
    _git(repo, "add", str(tricky))
    _git(repo, *_COMMIT_FLAGS, "commit", "--quiet", "-m", "add tricky")
    tricky.write_text("x = 2\n")

    out = tmp_path / "BUILD_INFO.json"
    info, _ = _stamp(repo, out)

    assert info["dirty"] is True
    assert "a file with spaces.py" in info["dirty_files"]


def test_renames_record_both_paths(repo, tmp_path):
    """A rename emits the original path as a second NUL record — don't split
    it into a bogus second entry with its first 3 characters chopped off."""
    _git(repo, "mv", "app.py", "renamed.py")
    out = tmp_path / "BUILD_INFO.json"
    info, _ = _stamp(repo, out)

    assert info["dirty"] is True
    assert info["dirty_files"] == ["app.py -> renamed.py"]


def test_default_output_path_is_the_repo_root(repo):
    """No argument → <repo>/BUILD_INFO.json, next to the Dockerfile context."""
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=str(repo),
        env={**os.environ, "LAZYCLAW_BUILD_INFO_REPO": str(repo)},
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert json.loads((repo / "BUILD_INFO.json").read_text())["dirty"] is False


def test_script_is_executable():
    assert os.access(SCRIPT, os.X_OK), "make rebuild invokes it via bash, but "\
        "the file should still be directly runnable"
