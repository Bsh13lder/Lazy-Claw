"""Browser-profile path resolution — single source of truth.

Default layout (back-compat):
    <database_dir>/browser_profiles/<user_id>/

Per-account layout (multi-account, e.g. two Reddit accounts for two
businesses):
    <database_dir>/browser_profiles/<user_id>/accounts/<slug>/

When ``account_slug`` is ``None`` the legacy path is returned unchanged so
existing logged-in profiles keep working.

The previous compute (``config.database_dir / "browser_profiles" / user_id``)
was duplicated across ~15 callsites. They all now route through
:func:`resolve_profile_dir` so adding/changing the layout is a one-file
change.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Slug must be DNS-label-friendly: lowercase alnum, dashes, underscores.
# Length 1-32 keeps directory names readable on every filesystem.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,31}$")

PROFILES_DIRNAME = "browser_profiles"
ACCOUNTS_DIRNAME = "accounts"


class InvalidAccountSlugError(ValueError):
    """Raised when an account slug fails validation."""


def validate_slug(slug: str) -> None:
    """Raise InvalidAccountSlugError if slug is not allowed.

    Allowed shape: starts with ``[a-z0-9]``, then ``[a-z0-9_-]`` up to 32
    chars total. Empty strings, uppercase, and special chars all fail —
    this is the canonical key on disk and in settings, so the rules are
    deliberately strict.
    """
    if not isinstance(slug, str):
        raise InvalidAccountSlugError(
            f"account_slug must be str, got {type(slug).__name__}"
        )
    if not _SLUG_RE.match(slug):
        raise InvalidAccountSlugError(
            f"invalid account_slug: {slug!r} "
            "(must match [a-z0-9][a-z0-9_-]{0,31})"
        )


def _user_root(config, user_id: str) -> Path:
    """Return ``<database_dir>/browser_profiles/<user_id>/`` (no mkdir)."""
    return Path(config.database_dir) / PROFILES_DIRNAME / user_id


def resolve_profile_dir(
    config,
    user_id: str,
    account_slug: str | None = None,
) -> Path:
    """Resolve the on-disk Chromium profile directory for this user.

    With ``account_slug=None`` (default), returns the legacy single-profile
    path. With a slug, returns an isolated per-account profile directory
    so cookies / local-storage / extensions never collide between accounts
    on the same domain.

    Does NOT create the directory — see :func:`ensure_profile_dir` for that.
    """
    base = _user_root(config, user_id)
    if account_slug is None:
        return base
    validate_slug(account_slug)
    return base / ACCOUNTS_DIRNAME / account_slug


def ensure_profile_dir(
    config,
    user_id: str,
    account_slug: str | None = None,
) -> Path:
    """Resolve and create the profile directory if missing. Returns the path.

    Idempotent: ``parents=True, exist_ok=True``.
    """
    path = resolve_profile_dir(config, user_id, account_slug)
    path.mkdir(parents=True, exist_ok=True)
    logger.debug(
        "profile_resolver.resolve user=%s slug=%s path=%s",
        user_id, account_slug, path,
    )
    return path


def list_account_slugs(config, user_id: str) -> list[str]:
    """List all account slugs that have a profile directory on disk.

    Returns an empty list when the user has no per-account profiles. The
    slugs are filesystem children of ``<user_root>/accounts/`` whose name
    passes :func:`validate_slug` (defensive — ignores stray files / dirs
    written by hand).
    """
    accounts_dir = _user_root(config, user_id) / ACCOUNTS_DIRNAME
    if not accounts_dir.exists():
        return []

    slugs: list[str] = []
    for child in sorted(accounts_dir.iterdir()):
        if not child.is_dir():
            continue
        try:
            validate_slug(child.name)
        except InvalidAccountSlugError:
            logger.debug(
                "profile_resolver.list_account_slugs skipping %s (invalid slug)",
                child,
            )
            continue
        slugs.append(child.name)
    return slugs
