"""Track which Upwork contracts we've already processed.

Stored in ``users.settings.survival.seen_contract_urls`` as a JSON
array of contract URLs. Bounded length 100 — oldest evicted FIFO when
the cap is hit. URLs only (plaintext) — no contract content, no client
names, no monetary values. Anything sensitive about the contract lives
in the encrypted Goal row created by the intake skill.

Used by the ``survival_contract_intake`` cron (added in PR 2) to skip
contracts the intake flow has already opened. PR 3 wires the actual
``new_contract_intake`` skill behind this — for PR 2 we just log new
contracts so the cron is provable end-to-end without taking action.
"""

from __future__ import annotations

import json
import logging

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


# Hard cap to bound the row size. 100 is plenty for a freelancer —
# anyone hitting 100 active+ended contracts has bigger problems than
# de-dup, and we want a finite worst-case size in users.settings.
SEEN_CAP: int = 100


async def get_seen_contract_urls(config: Config, user_id: str) -> list[str]:
    """Return the recorded contract URLs for this user (oldest first).

    Missing setting / corrupt JSON / wrong type all return ``[]`` —
    same defensive shape as the surrounding survival helpers.
    """
    async with db_session(config) as db:
        cursor = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
    if not row or not row[0]:
        return []
    try:
        settings = json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        logger.debug("settings JSON unparseable for user %s", user_id, exc_info=True)
        return []
    survival = settings.get("survival") or {}
    raw = survival.get("seen_contract_urls") or []
    if not isinstance(raw, list):
        return []
    return [str(u) for u in raw if u]


async def add_seen_contract_url(
    config: Config, user_id: str, contract_url: str,
) -> list[str]:
    """Append ``contract_url`` (no-op if already present). Returns the new list.

    FIFO-evicts the oldest entry when the list would exceed
    :data:`SEEN_CAP` after the append. Empty / falsy URLs are
    rejected so a typo doesn't poison the dedup set.
    """
    url = (contract_url or "").strip()
    if not url:
        raise ValueError("contract_url is required and must be non-empty")

    async with db_session(config) as db:
        cursor = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        try:
            settings = json.loads(row[0]) if row and row[0] else {}
        except (json.JSONDecodeError, TypeError):
            settings = {}

        survival = settings.get("survival")
        if not isinstance(survival, dict):
            survival = {}
        seen = survival.get("seen_contract_urls")
        if not isinstance(seen, list):
            seen = []

        # Already present — return current list unchanged. Don't bump
        # to the end; FIFO is about insertion order, not access order.
        if url in seen:
            return [str(u) for u in seen if u]

        seen.append(url)
        if len(seen) > SEEN_CAP:
            # Drop the oldest so the row stays bounded.
            seen = seen[-SEEN_CAP:]

        survival["seen_contract_urls"] = seen
        new_settings = dict(settings)
        new_settings["survival"] = survival
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (json.dumps(new_settings), user_id),
        )
        await db.commit()

    logger.info(
        "survival.contracts marked seen user=%s url=%s total=%d",
        user_id, url[:80], len(seen),
    )
    return [str(u) for u in seen if u]


async def filter_unseen(
    config: Config, user_id: str, contract_urls: list[str],
) -> list[str]:
    """Return the subset of ``contract_urls`` we haven't seen yet.

    Convenience for the poller skill — one DB read instead of N. Does
    NOT mark anything as seen; caller must call
    :func:`add_seen_contract_url` per-URL once it's been processed
    (so a crash mid-intake means we retry, not silently skip).
    """
    if not contract_urls:
        return []
    seen = set(await get_seen_contract_urls(config, user_id))
    return [u for u in contract_urls if u and u not in seen]
