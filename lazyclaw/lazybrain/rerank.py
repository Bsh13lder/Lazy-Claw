"""Optional cross-encoder reranker for the /ask retrieval path.

Cuts hallucinated citations by re-scoring the top-N hybrid results with
a query-document cross-encoder. Default model is ``BAAI/bge-reranker-v2-m3``
(~278 M params, March 2024) which runs on CPU in ≤ 200 ms per batch of 20
query-doc pairs — fast enough to stay inline in /ask without breaking the
~1 s budget.

Disabled by default. Enable per-user via the ``users.settings`` JSON key
``lazybrain.reranker_enabled = true``. The model is downloaded once into
``~/.cache/huggingface`` on first call; if the download or import fails,
``rerank_results`` returns the input unchanged so /ask never breaks.
"""
from __future__ import annotations

import json
import logging
from threading import Lock
from typing import Any

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"

# Lazy singleton — first /ask call instantiates, subsequent calls reuse.
_reranker: Any | None = None
_reranker_failed: bool = False
_lock = Lock()


def _load_reranker(model: str = DEFAULT_MODEL) -> Any | None:
    """Return a singleton Reranker instance, or None when load fails.

    Failure is sticky for the process lifetime — we don't retry every
    call, since a failed import / download usually means the dep just
    isn't installed.
    """
    global _reranker, _reranker_failed
    if _reranker is not None:
        return _reranker
    if _reranker_failed:
        return None
    with _lock:
        if _reranker is not None:
            return _reranker
        if _reranker_failed:
            return None
        try:
            from rerankers import Reranker  # type: ignore
            _reranker = Reranker(model, model_type="cross-encoder")
        except Exception as exc:
            logger.info(
                "Reranker unavailable (%s) — /ask will skip rerank pass",
                exc,
            )
            _reranker_failed = True
            return None
    return _reranker


async def is_enabled(config: Config, user_id: str) -> bool:
    """Read ``users.settings.lazybrain.reranker_enabled``. Defaults False.

    Cheap one-row lookup; we don't cache because the user can flip the
    flag in settings without restart.
    """
    try:
        async with db_session(config) as db:
            row = await db.execute(
                "SELECT settings FROM users WHERE id = ?", (user_id,),
            )
            r = await row.fetchone()
        if not r or not r[0]:
            return False
        try:
            settings = json.loads(r[0])
        except (json.JSONDecodeError, TypeError):
            return False
        lb = settings.get("lazybrain") or {}
        return bool(lb.get("reranker_enabled"))
    except Exception:
        return False


def rerank_results(
    query: str,
    results: list[dict],
    *,
    top_k: int | None = None,
) -> list[dict]:
    """Re-rank a list of note dicts (each with ``content`` / ``title``).

    Returns a new list ordered by reranker score DESC. ``top_k`` truncates
    to that many docs; ``None`` keeps all reranked items. Falls through to
    the input list unchanged when:

    - The ``rerankers`` package isn't installed.
    - Model download fails.
    - Any exception is raised mid-rerank.
    """
    if not results:
        return results
    r = _load_reranker()
    if r is None:
        return results[:top_k] if top_k else results

    docs: list[str] = []
    for note in results:
        body = (note.get("content") or "").strip()
        title = (note.get("title") or "").strip()
        # Title prepended so the reranker has the strongest signal up
        # front — many BGE-style models are length-sensitive.
        docs.append(f"{title}\n\n{body[:1500]}")

    try:
        ranking = r.rank(query=query, docs=docs, doc_ids=list(range(len(docs))))
        # rerankers >=0.3 returns a Results object with .results sorted DESC.
        ordered_idx = [item.doc_id for item in ranking.results]
    except Exception as exc:
        logger.debug("rerank pass failed: %s", exc)
        return results[:top_k] if top_k else results

    out = [results[i] for i in ordered_idx if 0 <= i < len(results)]
    return out[:top_k] if top_k else out


__all__ = ["DEFAULT_MODEL", "is_enabled", "rerank_results"]
