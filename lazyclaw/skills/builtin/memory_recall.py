from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class MemoryRecallSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def read_only(self) -> bool:
        return True

    @property
    def category(self) -> str:
        return "memory"

    @property
    def name(self) -> str:
        return "recall_memories"

    @property
    def description(self) -> str:
        return (
            "Search the user's saved memory across BOTH the legacy personal "
            "facts table AND LazyBrain (the user's second brain — TILs, "
            "decisions, deadlines, ideas, prices, addresses, plans). "
            "Substring match on personal facts; semantic + substring search "
            "on LazyBrain. Use this whenever you suspect the user already "
            "told you something — phrasing in memory may differ from your "
            "query."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to search for (e.g., 'name', 'timezone', 'google'). "
                        "Substring + semantic match, case-insensitive."
                    ),
                },
            },
            "required": ["query"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Memory system not configured"
        from lazyclaw.memory.personal import search_memories, get_memories

        query = ((params or {}).get("query") or "").strip()
        if not query:
            return "Error: `query` is required."

        # ── 1. Legacy personal_memory substring search ─────────────────
        personal_hits = await search_memories(self._config, user_id, query)

        # ── 2. LazyBrain semantic + substring search ───────────────────
        lb_hits = await _safe_lazybrain_search(self._config, user_id, query)

        if personal_hits or lb_hits:
            return _format_combined_hits(query, personal_hits, lb_hits)

        # ── 3. Both empty — return previews from both stores so the brain
        #      sees what's stored before it gives up. Prevents the "no
        #      match → I have no memory of X" loop when the answer was
        #      phrased differently.
        vault_keys = await _vault_keys_safe(self._config, user_id)
        all_personal = await get_memories(self._config, user_id, limit=8)
        all_lb = await _safe_recent_lb_notes(self._config, user_id, limit=8)

        if not all_personal and not all_lb:
            base = f"No direct match for '{query}' and no memories stored yet."
            if vault_keys:
                return (
                    f"{base} Vault contains {len(vault_keys)} credentials "
                    f"(names only): {', '.join(vault_keys)}. "
                    f"If the user is asking about a credential, call "
                    f"`vault_get(key=...)` instead of retrying recall_memories."
                )
            return f"{base} Nothing to search."

        return _format_no_match_preview(
            query, all_personal, all_lb, vault_keys,
        )


# ── Helpers ────────────────────────────────────────────────────────────


async def _safe_lazybrain_search(config, user_id: str, query: str) -> list[dict]:
    """Run semantic_search; return [] on any failure (Ollama down, etc.)."""
    try:
        from lazyclaw.lazybrain import embeddings as lb_embeddings
        from lazyclaw.lazybrain.store import is_user_facing_memory_note
    except Exception:
        logger.debug("LazyBrain unavailable — skill recall personal-only", exc_info=True)
        return []
    try:
        result = await lb_embeddings.semantic_search(
            config, user_id, query, k=8,
        )
    except Exception:
        logger.debug("semantic_search raised — falling back to empty", exc_info=True)
        return []
    raw = result.get("results") if isinstance(result, dict) else None
    if not raw:
        return []
    return [n for n in raw if is_user_facing_memory_note(n)]


async def _safe_recent_lb_notes(config, user_id: str, limit: int) -> list[dict]:
    """Recent user-facing LazyBrain notes for the no-match preview."""
    try:
        from lazyclaw.lazybrain import store as lb_store
        from lazyclaw.lazybrain.store import is_user_facing_memory_note
    except Exception:
        return []
    try:
        notes = await lb_store.list_notes(config, user_id, limit=limit * 3)
    except Exception:
        return []
    filtered = [n for n in notes if is_user_facing_memory_note(n)]
    return filtered[:limit]


def _format_combined_hits(
    query: str,
    personal: list[dict],
    lb: list[dict],
) -> str:
    """Render personal + LazyBrain hits as a single answer body."""
    lines: list[str] = []
    total = len(personal) + len(lb)
    lines.append(f"Matches for '{query}' ({total}):")
    if personal:
        lines.append("")
        lines.append("**Personal facts:**")
        for m in personal:
            mtype = m.get("type") or m.get("memory_type") or "?"
            lines.append(
                f"- [{mtype}] {m['content']} "
                f"(importance: {m['importance']}, id: {m['id']})"
            )
    if lb:
        lines.append("")
        lines.append("**LazyBrain notes:**")
        for n in lb:
            title = (n.get("title") or "(untitled)").strip()
            content = (n.get("content") or "").strip().replace("\n", " ")
            preview = content[:200]
            tags = n.get("tags") or []
            tag_chip = (
                f" [{', '.join(t for t in tags if not t.startswith('owner/'))[:60]}]"
                if tags else ""
            )
            score = n.get("_score")
            score_chip = f" (score: {score})" if score is not None else ""
            lines.append(
                f"- **{title}**{tag_chip}{score_chip} — {preview} (id: lb:{n['id']})"
            )
    return "\n".join(lines)


def _format_no_match_preview(
    query: str,
    personal: list[dict],
    lb: list[dict],
    vault_keys: list[str],
) -> str:
    lines: list[str] = []
    lines.append(
        f"No direct match for '{query}'. Here's what IS stored — "
        f"the answer might be phrased differently:"
    )
    if personal:
        lines.append("")
        lines.append("**Personal facts (most recent):**")
        for m in personal:
            mtype = m.get("type") or m.get("memory_type") or "?"
            lines.append(f"- [{mtype}] {m['content']} (id: {m['id']})")
    if lb:
        lines.append("")
        lines.append("**LazyBrain notes (most recent):**")
        for n in lb:
            title = (n.get("title") or "(untitled)").strip()
            content = (n.get("content") or "").strip().replace("\n", " ")
            lines.append(f"- **{title}** — {content[:160]} (id: lb:{n['id']})")
    if vault_keys:
        lines.append("")
        lines.append(
            f"Vault keys (names only, values encrypted): "
            f"{', '.join(vault_keys)}"
        )
        lines.append(
            "If the user is asking about a credential/API key/OAuth secret, "
            "call `vault_get(key=...)` — credentials are NEVER in personal memory."
        )
    lines.append("")
    lines.append(
        "STOP. Do not retry recall_memories with other keywords — both "
        "stores are searched on every call. If the fact isn't above, ask "
        "the user instead of guessing."
    )
    return "\n".join(lines)


async def _vault_keys_safe(config, user_id: str) -> list[str]:
    try:
        from lazyclaw.crypto.vault import list_credentials
        return await list_credentials(config, user_id)
    except Exception:
        return []
