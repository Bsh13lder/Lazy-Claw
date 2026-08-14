"""Deploy stamp — makes "what code is prod actually running?" answerable.

LazyClaw images are baked from the working tree rather than a git ref, so a
build silently captures whatever happened to be on disk. That produced the
deploy-drift incident class: fixes committed-but-not-deployed and
deployed-but-not-committed, with nothing on the running server to tell them
apart after the fact.

``scripts/write-build-info.sh`` records the tree's git identity into
``BUILD_INFO.json`` before ``docker compose build``; the Dockerfile bakes that
file into the image at ``/app/BUILD_INFO.json``; this module reads it once at
startup and ``GET /api/health`` returns it as the ``build`` object.

Everything here is fail-soft on purpose: a missing, unreadable, truncated or
malformed stamp degrades to ``"unknown"``. A health endpoint that 500s because
its provenance metadata is unparseable would be strictly worse than not having
the metadata at all.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"

#: Only these keys are exposed at ``/api/health``. ``dirty_files`` stays in the
#: on-disk stamp (``docker exec lazyclaw cat /app/BUILD_INFO.json``) rather than
#: on an unauthenticated endpoint — the flag is what callers need, the file list
#: is forensic detail.
_PUBLIC_FIELDS = ("sha", "short_sha", "branch", "describe", "dirty", "built_at")

_ENV_PATH_VAR = "LAZYCLAW_BUILD_INFO_PATH"

# Baked location inside the image, then the repo root for host/dev runs
# (this file is lazyclaw/gateway/build_info.py → parents[2] is the repo root).
_IMAGE_PATH = Path("/app/BUILD_INFO.json")
_REPO_PATH = Path(__file__).resolve().parents[2] / "BUILD_INFO.json"

_cache: dict[str, Any] | None = None


def unknown_build_info() -> dict[str, Any]:
    """The fail-soft stamp: every field ``"unknown"``, ``dirty`` indeterminate.

    ``dirty`` is ``None`` rather than ``False`` because "we don't know" and
    "the tree was clean" are different claims, and quietly asserting the
    stronger one is how provenance metadata starts lying.
    """
    return {
        "sha": UNKNOWN,
        "short_sha": UNKNOWN,
        "branch": UNKNOWN,
        "describe": UNKNOWN,
        "dirty": None,
        "built_at": UNKNOWN,
    }


def candidate_paths() -> tuple[Path, ...]:
    """Where the stamp may live, most specific first."""
    override = os.environ.get(_ENV_PATH_VAR)
    if override:
        return (Path(override),)
    return (_IMAGE_PATH, _REPO_PATH)


def _coerce_dirty(value: Any) -> bool | None:
    """Only a real JSON boolean counts. Anything else means "unknown"."""
    if isinstance(value, bool):
        return value
    return None


def _coerce_str(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return UNKNOWN


def load_build_info(path: Path | None = None) -> dict[str, Any]:
    """Read the stamp from disk. Never raises — degrades to ``unknown``.

    Args:
        path: explicit stamp location; when omitted, :func:`candidate_paths`
            is tried in order and the first existing file wins.
    """
    info = unknown_build_info()

    paths = (path,) if path is not None else candidate_paths()
    for candidate in paths:
        try:
            if not candidate.is_file():
                continue
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            logger.warning(
                "[build-info] unreadable stamp at %s (%s) — reporting 'unknown'",
                candidate, exc,
            )
            continue

        if not isinstance(raw, dict):
            logger.warning(
                "[build-info] stamp at %s is %s, expected an object — "
                "reporting 'unknown'",
                candidate, type(raw).__name__,
            )
            continue

        # A partial stamp (e.g. the Dockerfile's {"sha":"unknown"} fallback) is
        # valid: fill what is present, leave the rest unknown.
        for field in _PUBLIC_FIELDS:
            if field not in raw:
                continue
            info[field] = (
                _coerce_dirty(raw[field]) if field == "dirty"
                else _coerce_str(raw[field])
            )
        return info

    logger.info(
        "[build-info] no stamp found (looked in: %s) — /api/health will report "
        "sha=unknown. Run `make rebuild` to bake one.",
        ", ".join(str(p) for p in paths),
    )
    return info


def get_build_info() -> dict[str, Any]:
    """Cached stamp. Read once at startup; the image can't change under us."""
    global _cache
    if _cache is None:
        _cache = load_build_info()
    # Copy so a caller mutating the health payload can't poison the cache.
    return dict(_cache)


def reset_build_info_cache() -> None:
    """Drop the cached stamp (tests; and re-read after an in-place swap)."""
    global _cache
    _cache = None
