"""Tests for lazyclaw.browser.profile_resolver — multi-account browser profile paths.

Covers:
- Default (back-compat) path layout when account_slug is None.
- Per-account path layout under accounts/<slug>/.
- Slug regex enforcement (empty, uppercase, special chars, length).
- ensure_profile_dir mkdir idempotency.
- list_account_slugs ignores stray files / invalid names.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from lazyclaw.browser.profile_resolver import (
    ACCOUNTS_DIRNAME,
    PROFILES_DIRNAME,
    InvalidAccountSlugError,
    ensure_profile_dir,
    list_account_slugs,
    resolve_profile_dir,
    validate_slug,
)


@dataclass
class FakeConfig:
    database_dir: Path


# ── resolve_profile_dir ──────────────────────────────────────────────


def test_resolve_default_path_is_legacy_layout(tmp_path):
    cfg = FakeConfig(database_dir=tmp_path)
    p = resolve_profile_dir(cfg, "alice")
    assert p == tmp_path / PROFILES_DIRNAME / "alice"


def test_resolve_per_account_path(tmp_path):
    cfg = FakeConfig(database_dir=tmp_path)
    p = resolve_profile_dir(cfg, "alice", "reddit_main")
    expected = tmp_path / PROFILES_DIRNAME / "alice" / ACCOUNTS_DIRNAME / "reddit_main"
    assert p == expected


def test_resolve_does_not_create_directory(tmp_path):
    cfg = FakeConfig(database_dir=tmp_path)
    p = resolve_profile_dir(cfg, "alice", "marketing")
    assert not p.exists()


@pytest.mark.parametrize("slug", [
    "",
    "BAD",
    "with space",
    "with/slash",
    "ab.cd",
    "x" * 33,        # too long (>32)
    "-leading-dash",
    "_leading_under",
    "ünicode",
])
def test_resolve_rejects_invalid_slug(tmp_path, slug):
    cfg = FakeConfig(database_dir=tmp_path)
    with pytest.raises(InvalidAccountSlugError):
        resolve_profile_dir(cfg, "alice", slug)


@pytest.mark.parametrize("slug", [
    "a",
    "ab",
    "reddit_main",
    "reddit-main",
    "x123",
    "1abc",
    "x" * 32,        # exactly 32 ok
])
def test_validate_slug_accepts_canonical_shapes(slug):
    # Should not raise.
    validate_slug(slug)


def test_validate_slug_rejects_non_string():
    with pytest.raises(InvalidAccountSlugError):
        validate_slug(123)  # type: ignore[arg-type]


# ── ensure_profile_dir ───────────────────────────────────────────────


def test_ensure_profile_dir_creates_default(tmp_path):
    cfg = FakeConfig(database_dir=tmp_path)
    p = ensure_profile_dir(cfg, "alice")
    assert p.exists() and p.is_dir()


def test_ensure_profile_dir_creates_account_path_with_parents(tmp_path):
    cfg = FakeConfig(database_dir=tmp_path)
    p = ensure_profile_dir(cfg, "alice", "marketing")
    assert p.exists() and p.is_dir()
    # Parents must exist too
    assert (tmp_path / PROFILES_DIRNAME / "alice" / ACCOUNTS_DIRNAME).is_dir()


def test_ensure_profile_dir_is_idempotent(tmp_path):
    cfg = FakeConfig(database_dir=tmp_path)
    p1 = ensure_profile_dir(cfg, "alice", "marketing")
    p2 = ensure_profile_dir(cfg, "alice", "marketing")
    assert p1 == p2
    assert p1.exists()


# ── list_account_slugs ───────────────────────────────────────────────


def test_list_account_slugs_empty_when_no_accounts(tmp_path):
    cfg = FakeConfig(database_dir=tmp_path)
    assert list_account_slugs(cfg, "alice") == []


def test_list_account_slugs_returns_sorted(tmp_path):
    cfg = FakeConfig(database_dir=tmp_path)
    ensure_profile_dir(cfg, "alice", "zeta")
    ensure_profile_dir(cfg, "alice", "alpha")
    ensure_profile_dir(cfg, "alice", "mike")
    assert list_account_slugs(cfg, "alice") == ["alpha", "mike", "zeta"]


def test_list_account_slugs_ignores_invalid_entries(tmp_path):
    cfg = FakeConfig(database_dir=tmp_path)
    ensure_profile_dir(cfg, "alice", "main")
    # Stray hand-created entries that fail slug validation.
    bad = tmp_path / PROFILES_DIRNAME / "alice" / ACCOUNTS_DIRNAME
    (bad / "BAD-CASE").mkdir()
    (bad / "stray_file.txt").write_text("hi")
    assert list_account_slugs(cfg, "alice") == ["main"]
