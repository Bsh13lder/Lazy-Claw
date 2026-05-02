"""Append user corrections to a template's encrypted playbook.

A correction is anything the user types/rejects mid-flight that contradicts
what the agent just did. We keep the signal cheap by only listening to two
strong sources:

  • side-notes during a templated run (the user is actively tutoring)
  • checkpoint rejections (the rejection reason IS the correction)

Each correction is appended as a versioned ``## Correction (TS — source)``
block to the existing playbook. ``corrections_pending`` is bumped so a
worker-LLM compaction pass rewrites the playbook once it crosses
``_COMPACTION_THRESHOLD``.

Dedup: same-text corrections within 60s are coalesced. Real users rarely
re-type the same correction within a minute; agents that retry on a tight
loop would flood the playbook without this guard.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Literal

from lazyclaw.browser import templates as tpl_store
from lazyclaw.config import Config

logger = logging.getLogger(__name__)

CorrectionSource = Literal["side_note", "reject", "approval_reason"]

_DEDUP_WINDOW_S = 60.0
_COMPACTION_THRESHOLD = 5  # pending corrections that trigger worker LLM rewrite
_lock = Lock()
_recent: dict[tuple[str, str], tuple[float, str]] = {}  # (user_id, tpl_id) -> (ts, text)


def _is_dup(user_id: str, tpl_id: str, text: str) -> bool:
    now = time.time()
    key = (user_id, tpl_id)
    with _lock:
        last = _recent.get(key)
        if last and last[1] == text and now - last[0] < _DEDUP_WINDOW_S:
            return True
        _recent[key] = (now, text)
        # cheap GC — drop entries older than 5 min
        cutoff = now - 300
        for k, (ts, _) in list(_recent.items()):
            if ts < cutoff:
                _recent.pop(k, None)
    return False


def _now_human() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


async def append_correction(
    config: Config,
    user_id: str,
    tpl_id: str,
    source: CorrectionSource,
    correction_text: str,
) -> dict | None:
    """Append a correction block to the template's playbook.

    Returns the updated template dict, or None if the template no longer
    exists / dedup hit / empty text.
    """
    text = (correction_text or "").strip()
    if not text:
        return None
    if _is_dup(user_id, tpl_id, text):
        return None

    tpl = await tpl_store.get_template(config, user_id, tpl_id)
    if tpl is None:
        logger.debug("append_correction: template %s gone", tpl_id)
        return None

    existing_playbook = (tpl.get("playbook") or "").rstrip()
    block = f"\n\n## Correction ({_now_human()} — {source})\n{text}"
    new_playbook = (existing_playbook + block) if existing_playbook else block.lstrip()

    pending = (tpl.get("corrections_pending") or 0) + 1
    updated = await tpl_store.update_template(
        config, user_id, tpl_id,
        playbook=new_playbook,
        corrections_pending=pending,
    )
    logger.info(
        "Appended correction to template %s ('%s') from %s — pending=%d",
        tpl_id, tpl.get("name"), source, pending,
    )

    # Fire-and-forget compaction once enough corrections have piled up.
    # Worker LLM rewrites the playbook merging the correction blocks. If
    # the worker is unreachable (Ollama down), we keep the original blocks
    # and try again on the next correction.
    if pending >= _COMPACTION_THRESHOLD:
        try:
            asyncio.create_task(
                _compact_in_background(config, user_id, tpl_id),
            )
        except RuntimeError:
            # No running event loop (sync context) — skip; next correction
            # in an async context will retry.
            pass
    return updated


async def _compact_in_background(
    config: Config, user_id: str, tpl_id: str,
) -> None:
    """Worker-LLM playbook rewrite. Bumps version, resets corrections_pending."""
    from lazyclaw.browser.template_synth import compact_playbook
    from lazyclaw.llm.router import LLMRouter

    tpl = await tpl_store.get_template(config, user_id, tpl_id)
    if tpl is None:
        return
    if (tpl.get("corrections_pending") or 0) < _COMPACTION_THRESHOLD:
        return  # another worker already drained it

    try:
        new_playbook = await compact_playbook(
            LLMRouter(config), config, user_id, tpl.get("playbook") or "",
        )
    except Exception:
        logger.warning("compaction worker call raised", exc_info=True)
        return
    if not new_playbook:
        return  # worker silent / unreachable — keep originals

    new_version = (tpl.get("version") or 1) + 1
    await tpl_store.update_template(
        config, user_id, tpl_id,
        playbook=new_playbook,
        corrections_pending=0,
        version=new_version,
    )
    logger.info(
        "Compacted template %s ('%s') — version %d → %d",
        tpl_id, tpl.get("name"), tpl.get("version") or 1, new_version,
    )
