"""System prompt builder with agent self-awareness.

Assembles: personality (SOUL.md) + capabilities (skills, MCP, config) + memories.
Capabilities are cached (60s TTL) to avoid per-message MCP RPC overhead.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from lazyclaw.config import Config
from lazyclaw.runtime.personality import load_personality

logger = logging.getLogger(__name__)

# Patterns that indicate error/debug details — strip from activity summaries
# to prevent the LLM from hallucinating about past technical issues.
_ERROR_NOISE_RE = re.compile(
    r"((?:HTTP\s*)?\b[45]\d{2}\b"        # HTTP 401, 500, etc.
    r"|error|exception|traceback"
    r"|unauthorized|forbidden"
    r"|rate.?limit|timeout"
    r"|failed|failure|crashed"
    r"|stack.?trace|ECONNREFUSED"
    r"|api.?key|credentials?.?issue)",
    re.IGNORECASE,
)

# F1 grounding — markers that flag a daily-log entry as a paraphrased dump
# of a channel conversation (e.g. "## Last Upwork Conversation\n**Contact:**
# James Blue\n**Vato (you):** $120, 1 week" — the 2026-05-16 daily_log).
# Re-injecting these as "Recent Activity" plants competing facts that win
# the attention war against fresh tool_result data for the same thread.
# Authoritative content for any channel question belongs to the live MCP
# read tool, not the diary. Drop them from the prompt entirely.
_CHANNEL_THREAD_PARAPHRASE_MARKERS: tuple[str, ...] = (
    "## last upwork conversation",
    "## last whatsapp conversation",
    "## last instagram conversation",
    "## last email thread",
    "## last telegram conversation",
    "**contact:**",
    "**thread time:**",
    "### messages:",
)


def _is_channel_thread_paraphrase(summary: str) -> bool:
    """True if summary is a paraphrased dump of channel messages.

    Checks the first 400 chars (where conversation-dump headers live) so a
    daily_log that merely mentions a contact name in passing is NOT
    suppressed — only logs that lead with a channel-thread structure.
    """
    if not summary:
        return False
    head = summary[:400].lower()
    return any(marker in head for marker in _CHANNEL_THREAD_PARAPHRASE_MARKERS)

# Technical implementation details that confuse the LLM when injected
# into context. The agent parrots "PBKDF2 cache" etc. when user asks
# "how is going" because these look like relevant recent events.
_TECH_NOISE_RE = re.compile(
    r"(PBKDF2|AES.?256|GCM|HMAC|SHA.?256|bcrypt"
    r"|LRU\s*cache|connection\s*pool|WAL\s*mode"
    r"|asyncio|aiosqlite|uvicorn|FastAPI"
    r"|CDP|Playwright|langchain"
    r"|refactor|dead\s*code|cleanup"
    r"|token\s*sav|token\s*reduc|token\s*optim"
    r"|parallel\s*startup|startup\s*time"
    r"|ms\s*→|s\s*→|0ms|0\.2ms|14ms|420ms"
    r"|context\s*window|prompt\s*cach"
    r"|system\s*prompt|tool\s*schema"
    r"|commit|merge|branch|git\b"
    r"|MCP\s*(bridge|server|client|parallel))",
    re.IGNORECASE,
)


def _sanitize_activity_summary(summary: str) -> str:
    """Remove error/debug and technical implementation noise from activity summaries.

    Sentences mentioning HTTP errors, stack traces, auth failures, or
    internal implementation details (caching, crypto, refactoring) are
    stripped so the LLM doesn't hallucinate about past technical issues.
    """
    if not summary:
        return ""
    # Split into sentences, keep only clean ones
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    clean = [
        s for s in sentences
        if not _ERROR_NOISE_RE.search(s) and not _TECH_NOISE_RE.search(s)
    ]
    return " ".join(clean).strip()

# Cache for capabilities section (60s TTL)
_capabilities_cache: str = ""
_capabilities_time: float = 0.0
_CAPABILITIES_TTL = 60.0

# Cache for MCP status (60s TTL)
_mcp_cache: list[str] = []
_mcp_cache_time: float = 0.0

# Lock protecting both caches against concurrent async rebuilds
_cache_lock = asyncio.Lock()


# Stopwords excluded from keyword-overlap scoring. Short list, English + Spanish
# (user is bilingual — see memory: Madrid, Spain). Kept deliberately small:
# we only need to filter words that appear in ~every message.
_MEMORY_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "to", "of", "in", "on", "at", "for", "with", "by", "from", "as",
    "this", "that", "these", "those", "it", "its", "my", "me", "you",
    "your", "we", "us", "our", "can", "could", "would", "should",
    "will", "just", "only", "not", "no", "yes", "so", "if", "then",
    "about", "what", "how", "why", "when", "where", "who",
    "el", "la", "los", "las", "un", "una", "de", "del", "y", "o",
    "que", "es", "son", "para", "por", "con", "sin", "como", "pero",
    "si", "no", "tu", "yo", "mi", "me", "te", "se", "le", "lo",
})

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}", re.IGNORECASE)


def _tokenize_for_memory(text: str) -> set[str]:
    """Lowercase, strip stopwords, keep tokens of length ≥ 2."""
    if not text:
        return set()
    return {
        tok for tok in _TOKEN_RE.findall(text.lower())
        if tok not in _MEMORY_STOPWORDS
    }


# Per-item char cap on injected memory content. Without this a single
# 2KB note dominates the system prompt and crowds out other facts.
# 240 chars ≈ 60 tokens — enough for one fact / decision / preference,
# not enough to drag a whole essay into the prompt.
_MEMORY_PER_ITEM_CAP = 240

# Total char budget for the merged "What I know about you" section.
# ~1500 chars ≈ 400 tokens. Picks stop adding once exceeded — we'd
# rather under-inject than blow the prompt budget. Tunable via the
# LAZYCLAW_MEMORY_BUDGET_CHARS env var.
_MEMORY_TOTAL_BUDGET = 1500


def _budget_chars() -> int:
    """Resolve the runtime memory-injection budget (env override safe)."""
    import os

    raw = os.environ.get("LAZYCLAW_MEMORY_BUDGET_CHARS")
    if raw:
        try:
            n = int(raw)
            if 200 <= n <= 8000:
                return n
        except ValueError:
            pass
    return _MEMORY_TOTAL_BUDGET


def _truncate_memory(content: str) -> str:
    """Cap a single memory's content; preserve word boundary when possible."""
    s = (content or "").strip()
    if len(s) <= _MEMORY_PER_ITEM_CAP:
        return s
    cut = s[:_MEMORY_PER_ITEM_CAP]
    # Walk back to the last whitespace so we don't slice mid-word.
    sp = cut.rfind(" ")
    if sp > _MEMORY_PER_ITEM_CAP - 60:
        cut = cut[:sp]
    return cut.rstrip(" ,.;:") + "…"


# Negation tokens that strongly signal "this fact contradicts another."
# Used by the conflict detector below — ANY of these in one of two
# memories that share a subject = surface a ⚠️ CONFLICT warning to the
# brain so it asks the user instead of guessing.
_NEGATION_TOKENS: frozenset[str] = frozenset({
    "no", "not", "never", "instead", "actually", "no longer",
    "n't", "doesn't", "don't", "didn't", "isn't", "aren't",
    "ya no", "no es", "tampoco",  # ES
})


def _memory_subject_key(text: str) -> str:
    """First 4 non-stopword tokens, joined — the dedup/conflict bucket key.

    Two memories with the same subject key are candidates for conflict
    detection. E.g. ``"I prefer oat milk"`` and ``"I never drink oat milk"``
    both reduce to ``prefer/oat/milk`` (modulo negation), so they bucket
    together and the negation tokens trigger the warning.
    """
    toks = [
        t for t in _TOKEN_RE.findall((text or "").lower())
        if t not in _MEMORY_STOPWORDS and t not in _NEGATION_TOKENS
    ]
    return "/".join(toks[:4])


# Minimum shared content tokens for two memories to be considered a
# conflict candidate. 2 catches "prefer oat milk" + "never drink oat
# milk" (shared: oat, milk) — verbs differ so the threshold has to be
# lower than first instinct. False positives are cheap (one warning
# line that nudges the brain to ask the user) so we err generous.
_CONFLICT_MIN_SHARED_TOKENS = 2


def _detect_conflicts(memories: list[dict]) -> list[tuple[dict, dict]]:
    """Heuristic contradiction detection over already-picked memories.

    Two memories are flagged when:
      1. Their content-token sets (stopwords + negation tokens stripped)
         share at least ``_CONFLICT_MIN_SHARED_TOKENS`` tokens — set
         intersection so "I prefer oat milk" and "I never drink oat milk
         anymore" still bucket together despite different verbs.
      2. EXACTLY ONE side carries a negation token (no/never/instead/…).
         Same-polarity disagreements ("prefer oat" vs "prefer almond")
         are *preference changes*, not contradictions — we leave those
         to the LLM.

    Heuristic-only by design — false positives are cheap (one warning
    line that nudges the brain to ask), false negatives preserve
    current behaviour. Returns up to 3 pairs to bound the warning block.
    """
    if len(memories) < 2:
        return []

    # Cache content-token sets once — avoid re-tokenising in the
    # quadratic pair scan below.
    enriched: list[tuple[dict, set[str], bool]] = []
    for m in memories:
        text = (m.get("content") or "").lower()
        toks = {
            t for t in _TOKEN_RE.findall(text)
            if t not in _MEMORY_STOPWORDS and t not in _NEGATION_TOKENS
        }
        if not toks:
            continue
        has_neg = any(tok in text for tok in _NEGATION_TOKENS)
        enriched.append((m, toks, has_neg))

    conflicts: list[tuple[dict, dict]] = []
    for i in range(len(enriched)):
        for j in range(i + 1, len(enriched)):
            a, a_toks, a_neg = enriched[i]
            b, b_toks, b_neg = enriched[j]
            if a_neg == b_neg:
                continue  # same polarity → preference change, not conflict
            if len(a_toks & b_toks) < _CONFLICT_MIN_SHARED_TOKENS:
                continue
            conflicts.append((a, b))
            if len(conflicts) >= 3:
                return conflicts
    return conflicts


def _format_memory_marker(mem: dict) -> str:
    """``[source:conf ✓ DATE]`` tag prepended to every injected memory.

    Tells the brain at a glance: where did this come from, how strongly
    do we trust it, and when was it last verified. Without this the
    LLM treats every fact as equally fresh — stale 2024 facts crowd out
    new 2026 ones with no signal to disambiguate.
    """
    mtype = (mem.get("memory_type") or "").lower()
    conf = int(mem.get("importance") or 5)
    last = (mem.get("created_at") or "")[:10]

    # LazyBrain mirrors carry their full updated_at as the verification
    # date; legacy personal_memory has no verification log.
    if mtype == "lazybrain":
        if last:
            return f"[note:{conf} ✓ {last}]"
        return f"[note:{conf}]"
    # Personal-memory sub-types (fact / preference / context / etc.)
    label = mtype or "fact"
    if last:
        return f"[{label}:{conf} ✓ {last}]"
    return f"[{label}:{conf}]"


def _pick_hybrid_memories(
    pool: list[dict],
    user_message: str | None,
    n_importance: int,
    n_relevant: int,
) -> list[dict]:
    """Pick top-N by importance + top-N by keyword overlap with the message.

    Pool is already sorted by importance DESC (see get_memories). We slice
    the first `n_importance` entries, then rank the remainder by token
    overlap against the user message and append up to `n_relevant` more.

    Each picked memory's ``content`` is truncated to
    ``_MEMORY_PER_ITEM_CAP`` and the merged result is capped at
    ``_budget_chars()`` total — picks stop the moment the budget is
    exhausted so a single long note can't crowd out everything else.
    Returned dicts are SHALLOW COPIES with truncated ``content`` so we
    don't mutate the caller's pool.

    Falls back to pure importance when there's no message or no overlap.
    """
    if not pool:
        return []

    by_importance = pool[:n_importance]

    relevant: list[dict] = []
    if user_message and len(pool) > n_importance:
        query_tokens = _tokenize_for_memory(user_message)
        if query_tokens:
            already_chosen_ids = {m["id"] for m in by_importance}
            remainder = [m for m in pool if m["id"] not in already_chosen_ids]

            scored: list[tuple[int, int, dict]] = []
            for idx, mem in enumerate(remainder):
                mem_tokens = _tokenize_for_memory(mem.get("content") or "")
                overlap = len(query_tokens & mem_tokens)
                if overlap > 0:
                    scored.append((-overlap, idx, mem))
            scored.sort()
            relevant = [m for _, _, m in scored[:n_relevant]]

    # Token-budget loop — interleave importance and relevance so neither
    # category gets starved when the budget is tight.
    budget = _budget_chars()
    chosen: list[dict] = []
    seen_ids: set = set()
    spent = 0
    imp_iter = iter(by_importance)
    rel_iter = iter(relevant)
    while spent < budget:
        # Importance first on each round so stable facts always win
        # over query-relevance when the budget is small.
        picked_this_round = False
        for source in (imp_iter, rel_iter):
            try:
                m = next(source)
            except StopIteration:
                continue
            if m["id"] in seen_ids:
                continue
            # Sanitize paraphrase-class content (session-log / fact /
            # other / NULL) BEFORE truncation so embedded `**Sender
            # (HH:MM):**` patterns are mangled before they enter the
            # cached system prompt. Without this, a reference- or
            # project-typed note that happens to QUOTE a session-log
            # excerpt would surface the mimic surface intact. The
            # memory pool here mixes legacy personal_memory (no type)
            # with lazybrain notes (typed) — pass each through the
            # sanitizer which is a no-op for authoritative types.
            from lazyclaw.lazybrain.paraphrase_sanitizer import (
                sanitize_recall_content,
            )
            raw_content = sanitize_recall_content(
                m.get("content"),
                m.get("memory_type"),
                title=m.get("title"),
            )
            content = _truncate_memory(raw_content)
            if not content:
                continue
            cost = len(content) + 4  # bullet + newline overhead
            if spent + cost > budget and chosen:
                # Stop adding new picks once budget is gone.
                return chosen
            seen_ids.add(m["id"])
            picked_this_round = True
            chosen.append({**m, "content": content})
            spent += cost
        if not picked_this_round:
            break

    return chosen


async def build_context(
    config: Config,
    user_id: str,
    registry=None,
    channel_id: str | None = None,
    project_id: str | None = None,
    user_message: str | None = None,
) -> str:
    """Build system prompt with personality + capabilities + memories.

    Args:
        config: App config.
        user_id: Current user identifier.
        registry: Skill registry (for capabilities section).
        channel_id: Active channel (loads CHANNEL memory layer when provided).
        project_id: Active project (loads PROJECT memory layer when provided).
        user_message: Current user input — used to re-rank personal memories by
            keyword overlap so context-relevant facts surface even when they
            sit below the importance cutoff.
    """
    personality = load_personality()

    # 1. Capabilities (cached 60s)
    capabilities = await _build_capabilities_cached(config, user_id, registry)

    # 2. Multi-layer memory context (Global → Project → Channel → User)
    #    Injected before activity logs so layered context informs interpretation
    #    of recent events. Synchronous file I/O — fast for small markdown files.
    layer_context = ""
    try:
        from lazyclaw.memory.layers import load_session_context
        layer_context = load_session_context(
            config, user_id,
            channel_id=channel_id,
            project_id=project_id,
        )
    except Exception:
        logger.debug("Failed to load session context layers", exc_info=True)

    # 2b. LazyBrain pinned notes + today's journal (Phase 18 — shared PKM)
    #     Runs in parallel isolation: failures never block prompt build.
    #
    #     Cache-boundary discipline (see MEMORY/feedback-claude-cache-
    #     amplifies-stale-context): this section lives ON THE SAME SIDE
    #     of the Anthropic prompt cache as the personal-memory pool —
    #     it gets cached for the turn. Anything injected here competes
    #     with fresh tool results for the model's attention until the
    #     cache invalidates. Two gates close that contamination class:
    #       (1) Pinned notes route through ``filter_pinned_for_cache``,
    #           which fails closed on session-log + NULL memory_type
    #           rows. Mirrors the policy applied to the merged memory
    #           pool below (lines ~451–490) so the two layers can't
    #           disagree on what's safe to pre-cache.
    #       (2) Today's journal (``Journal — YYYY-MM-DD``) is a rolling
    #           append-only log full of paraphrased prior-turn content;
    #           pre-caching it is the documented 2026-05-19 16:19
    #           hallucination path. The journal stays queryable on
    #           demand via ``lazybrain_recall_typed_memory`` (memory_
    #           type='session-log') but is excluded from the cached
    #           layer by ``should_inject_journal``.
    lazybrain_section = ""
    try:
        from lazyclaw.lazybrain import journal as lb_journal
        from lazyclaw.lazybrain import store as lb_store
        from lazyclaw.runtime.context_journal_filter import (
            filter_pinned_for_cache, should_inject_journal,
        )

        pinned_raw = await lb_store.list_notes(
            config, user_id, pinned_only=True, limit=5,
        )
        pinned, excluded_pinned = filter_pinned_for_cache(pinned_raw)
        if excluded_pinned:
            logger.debug(
                "lazybrain pinned cache filter excluded %d notes: %s",
                len(excluded_pinned), excluded_pinned,
            )
        today = await lb_journal.get_journal(config, user_id)
        parts: list[str] = []
        if pinned:
            parts.append("### 📌 Pinned notes (from user's second brain)")
            for n in pinned:
                title = n.get("title") or "(untitled)"
                snippet = (n.get("content") or "").strip().splitlines()[0][:160]
                parts.append(f"- **{title}** — {snippet}")
        if should_inject_journal(today):
            parts.append("### 📓 Today's journal")
            parts.append(today["content"][:600])
        elif today:
            logger.debug(
                "lazybrain journal cache filter excluded today's "
                "journal title=%r — fetch on-demand via recall skill",
                today.get("title"),
            )
        if parts:
            lazybrain_section = "## Second Brain (LazyBrain)\n" + "\n".join(parts)
    except Exception:
        logger.debug("Failed to load lazybrain context section", exc_info=True)

    # 3. Personal memories — hybrid pick: always-on facts (importance) +
    #    context-relevant facts (keyword overlap with the current message).
    #    Fetch a wider pool once, then pick 5+5 with dedup.
    #    Pool merges:
    #      - legacy personal_memory table (importance-ranked facts)
    #      - LazyBrain user-saved + auto-captured notes (TILs, decisions,
    #        prices, deadlines, ideas), excluding mirrors of stores already
    #        represented in the pool (#memory / #daily-log / #session-end)
    #        so the same content doesn't appear twice.
    #    Without this merge, anything saved into LazyBrain (manual or
    #    auto-captured) is invisible to the agent unless explicitly
    #    pinned — write-only brain bug.
    from lazyclaw.memory.personal import get_memories

    pool = await get_memories(config, user_id, limit=40)
    try:
        from lazyclaw.lazybrain import store as _lb_store
        from lazyclaw.lazybrain.memory_types import is_auto_inject_type
        from lazyclaw.lazybrain.store import is_user_facing_memory_note

        lb_notes = await _lb_store.list_notes(config, user_id, limit=40)
        # Typed-memory auto-inject filter: only `user | feedback | project |
        # reference` notes are safe to pre-inject into the cached system
        # prompt. ``session-log`` paraphrases (and unclassified rows where
        # memory_type IS NULL) are queryable on-demand via the recall
        # skill but never pre-injected, closing the contamination class
        # that produced the 2026-05-19 16:19 hallucination. See
        # MEMORY/feedback-claude-cache-amplifies-stale-context.
        _lb_total = len(lb_notes)
        _lb_kept = 0
        for n in lb_notes:
            if not is_user_facing_memory_note(n):
                continue
            if not is_auto_inject_type(n.get("memory_type")):
                continue
            content = (n.get("content") or "").strip()
            if not content:
                continue
            # Cap LazyBrain importance at 6 so auto-capture deadlines/decisions
            # (importance 7-8) don't drown out user-saved memories (default 5).
            imp = min(int(n.get("importance") or 5), 6)
            pool.append({
                "id": f"lb:{n['id']}",
                "memory_type": "lazybrain",
                # Per-item truncation happens in _pick_hybrid_memories so
                # the budget loop sees the cap; storing 500 chars here
                # keeps the merge cheap and lets the picker decide.
                "content": content[:500],
                "importance": imp,
            })
            _lb_kept += 1
        if _lb_total:
            logger.info(
                "LazyBrain auto-inject filter: %d / %d notes passed "
                "memory_type gate (excluded: session-log + NULL + fact + other)",
                _lb_kept, _lb_total,
            )
        # Re-sort merged pool by importance DESC so _pick_hybrid_memories
        # still sees a properly-ordered list.
        pool.sort(key=lambda m: int(m.get("importance") or 0), reverse=True)
    except Exception:
        logger.debug("Failed to merge lazybrain notes into memory pool", exc_info=True)

    memories = _pick_hybrid_memories(pool, user_message, n_importance=5, n_relevant=5)

    # 4. Recent activity (daily/weekly logs — agent's "diary")
    # Summaries are sanitized to remove error details that could cause
    # the LLM to hallucinate about past issues (e.g. "401 errors").
    activity_section = ""
    try:
        from lazyclaw.memory.daily_log import list_daily_logs

        recent_logs = await list_daily_logs(config, user_id, limit=10)
        if recent_logs:
            log_lines = []
            for log in reversed(recent_logs):  # oldest first
                summary = _sanitize_activity_summary(log.get("summary", ""))
                if not summary:
                    continue
                # F1 grounding: drop channel-thread paraphrase dumps. They
                # contaminated the 2026-05-19 16:19 reply — the daily_log
                # header "## Last Upwork Conversation … **Vato (you):**
                # $120, 1 week" outweighed fresh upwork_get_messages data.
                if _is_channel_thread_paraphrase(summary):
                    continue
                if log["date"].endswith("_week"):
                    log_lines.append(f"**Week of {log['date'][:10]}:** {summary[:250]}")
                elif log["date"].endswith("_month"):
                    log_lines.append(f"**Month {log['date'][:7]}:** {summary[:200]}")
                else:
                    log_lines.append(f"**{log['date']}:** {summary[:150]}")
            if log_lines:
                activity_section = "## Recent Activity\n" + "\n".join(log_lines)
    except Exception:
        logger.debug("Failed to load daily activity logs", exc_info=True)

    # Inject current date so the LLM knows what year/day it is
    from datetime import datetime, timezone

    now_utc = datetime.now(timezone.utc)
    date_section = f"## Current date\nToday is {now_utc.strftime('%A, %B %d, %Y')} (UTC)."

    # Ownership rules — without this the LLM tends to stamp user-initiated
    # tasks/notes with owner='agent', which wipes them from the "You" tab.
    ownership_rules = (
        "## Ownership rules\n"
        "- When the human asks you to do something (add task, save a note, "
        "remember a fact), stamp it with owner='user'. It is THEIR data.\n"
        "- Only use owner='agent' for work you self-initiate without being "
        "asked (background research, self-scheduled checks, autonomous monitoring).\n"
        "- Phrases like 'remind me', 'add a task for me', 'save this for me', "
        "'I need to…' → always owner='user'."
    )

    # 4b. Skill-outcome lessons — if the current message mentions a
    #     learning topic (n8n / instagram / email / whatsapp), inject
    #     the top-2 past working shapes so the model doesn't have to
    #     rediscover them. Keyword match keeps this zero-cost for
    #     unrelated turns.
    topic_lesson_section = await _build_topic_lessons_section(
        config, user_id, user_message,
    )

    # Combine sections — layered context between capabilities and activity
    sections = [personality, date_section, ownership_rules]
    if capabilities:
        sections.append(capabilities)
    if layer_context:
        sections.append(layer_context)
    if lazybrain_section:
        sections.append(lazybrain_section)
    if activity_section:
        sections.append(activity_section)
    if topic_lesson_section:
        sections.append(topic_lesson_section)
    if memories:
        # Provenance + confidence on every line so the brain can reason
        # about staleness instead of treating all facts as equally fresh.
        lines = [
            f"- {_format_memory_marker(m)} {m['content']}" for m in memories
        ]
        # Conflict scan — if two injected memories contradict, surface a
        # warning so the brain ASKS the user rather than guesses a merge.
        conflicts = _detect_conflicts(memories)
        warning_lines: list[str] = []
        for a, b in conflicts:
            a_snip = (a.get("content") or "")[:80]
            b_snip = (b.get("content") or "")[:80]
            warning_lines.append(
                f"- ⚠️ CONFLICT: \"{a_snip}\" vs \"{b_snip}\" — ASK the user "
                f"to confirm before relying on either."
            )
        body = "\n".join(lines)
        if warning_lines:
            body = "\n".join(warning_lines) + "\n" + body
        sections.append("## What I know about you\n" + body)

    return "\n\n---\n\n".join(sections)


_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "n8n":       ("n8n", "workflow", "webhook"),
    "instagram": ("instagram", "insta", "reel", " ig "),
    "email":     ("email", "gmail", "inbox", "imap"),
    "whatsapp":  ("whatsapp", "wa msg", "whats app"),
    "browser":   ("browser", "open ", "click ", "navigate ", "screenshot",
                  "scroll", " url ", "tab "),
    "web":       ("search ", "google ", "duckduckgo", "lookup ", "find info",
                  "what is ", "who is ", "latest news"),
    "telegram":  ("telegram", " tg "),
    "lazybrain": ("note", "wikilink", "graph", "lazybrain"),
    "tasks":     ("task", "remind", "todo", "background job"),
    "vault":     ("api key", "credential", "secret ", "vault"),
}


async def _build_topic_lessons_section(
    config: Config,
    user_id: str,
    user_message: str | None,
) -> str:
    """Topic-keyword triggered injection of past-success skill shapes.

    Each matching topic contributes up to 2 exemplars. Total injected
    text is bounded by the lesson formatter itself (≤2 KB per topic by
    construction). Never raises — any recall failure just skips injection.
    """
    if not user_message:
        return ""
    hay = f" {user_message.lower()} "
    # Score each topic by # keyword hits — a chatty message that mentions
    # both "browser" and "search google" should prefer the topics with the
    # strongest signal, not fan out to every loosely-matching one.
    scored: list[tuple[int, str]] = []
    for topic, kws in _TOPIC_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in hay)
        if hits:
            scored.append((hits, topic))
    if not scored:
        return ""
    scored.sort(reverse=True)
    # Cap at top-2 topics to bound total context injection.
    topics_hit: list[str] = [t for _, t in scored[:2]]
    try:
        from lazyclaw.runtime.skill_lesson import (
            recall_skill_lessons, format_lessons_as_exemplars,
        )
    except Exception:
        logger.debug("skill_lesson import failed", exc_info=True)
        return ""

    blocks: list[str] = []
    for topic in topics_hit:
        try:
            hits = await recall_skill_lessons(
                config, user_id, topic=topic, intent=user_message, k=2,
            )
        except Exception:
            logger.debug("recall failed for topic %s", topic, exc_info=True)
            continue
        if not hits:
            continue
        block = format_lessons_as_exemplars(hits)
        if block:
            blocks.append(f"### Past working shapes for {topic}\n{block}")
    if not blocks:
        return ""
    return "## Learned skill shapes\n" + "\n\n".join(blocks)


async def _build_capabilities_cached(
    config: Config,
    user_id: str,
    registry=None,
) -> str:
    """Build capabilities section with 60s TTL cache."""
    global _capabilities_cache, _capabilities_time

    # Fast path (no lock) — return cached if still valid
    now = time.monotonic()
    if _capabilities_cache and (now - _capabilities_time) < _CAPABILITIES_TTL:
        return _capabilities_cache

    # Slow path — acquire lock with timeout to avoid deadlock from stuck MCP
    try:
        acquired = await asyncio.wait_for(_cache_lock.acquire(), timeout=3)
    except asyncio.TimeoutError:
        logger.warning("Capabilities cache lock held >3s — returning stale or empty")
        return _capabilities_cache or ""
    try:
        now = time.monotonic()
        if _capabilities_cache and (now - _capabilities_time) < _CAPABILITIES_TTL:
            return _capabilities_cache
        result = await asyncio.wait_for(
            _build_capabilities_section(config, user_id, registry),
            timeout=10,
        )
        _capabilities_cache = result
        _capabilities_time = now
        return result
    except asyncio.TimeoutError:
        logger.warning("_build_capabilities_section timed out (>10s)")
        return _capabilities_cache or ""
    finally:
        _cache_lock.release()


def invalidate_capabilities_cache() -> None:
    """Call when skills or MCP servers change to force rebuild."""
    global _capabilities_cache, _capabilities_time, _mcp_cache, _mcp_cache_time
    _capabilities_cache = ""
    _capabilities_time = 0.0
    _mcp_cache = []
    _mcp_cache_time = 0.0


async def _build_capabilities_section(
    config: Config,
    user_id: str,
    registry=None,
) -> str:
    """Build the capabilities section showing available tools and services."""
    lines = [
        "## Your Capabilities",
        "",
        "You are LazyClaw, an E2E encrypted AI agent platform. "
        "Here is what you have available right now:",
        "",
    ]

    # Skills by category
    if registry is not None:
        categories = registry.list_by_category()
        for cat, skill_names in sorted(categories.items()):
            if cat == "mcp":
                continue
            display_names = [
                registry.get_display_name(name) for name in skill_names
            ]
            lines.append(f"**{_category_label(cat)}:** {', '.join(display_names)}")
        lines.append("")

    # Connected MCP servers (cached separately)
    mcp_lines = await _get_mcp_status_cached(config, user_id)
    if mcp_lines:
        lines.append(f"**MCP Servers Connected ({len(mcp_lines)}):**")
        for mcp_line in mcp_lines:
            lines.append(f"  - {mcp_line}")
        lines.append("")

    # Current config — show ECO-resolved models
    eco_mode = "hybrid"
    _brain_display = config.brain_model
    try:
        from lazyclaw.llm.eco_settings import get_eco_settings
        from lazyclaw.llm.model_registry import get_mode_models
        eco = await get_eco_settings(config, user_id)
        eco_mode = eco.get("mode", "hybrid")
        _m = get_mode_models(eco_mode)
        _brain_display = eco.get("brain_model") or _m["brain"]
    except Exception:
        logger.debug("Failed to load ECO settings for capabilities section", exc_info=True)
    config_parts = [f"Model: {_brain_display}"]

    try:
        config_parts.append(f"ECO: {eco_mode}")
    except Exception:
        logger.debug("Failed to append ECO mode to config parts", exc_info=True)

    try:
        from lazyclaw.teams.settings import get_team_settings
        team = await get_team_settings(config, user_id)
        config_parts.append(f"Team: {team.get('mode', 'never')}")
    except Exception:
        logger.debug("Failed to load team settings for capabilities section", exc_info=True)

    # Ollama status — only check in hybrid mode (uses local models).
    # Skip in claude/full modes to avoid connection spam when Ollama isn't running.
    ollama_status = ""
    if eco_mode == "hybrid":
        try:
            from lazyclaw.llm.providers.ollama_provider import OllamaProvider
            provider = OllamaProvider()
            if await provider.health_check():
                running = await provider.list_running()
                if running:
                    model_names = [m["name"] for m in running]
                    ollama_status = f"running ({', '.join(model_names)})"
                else:
                    ollama_status = "running (no models loaded)"
            else:
                ollama_status = "not running"
            await provider.close()
        except Exception:
            logger.debug("Ollama health check failed", exc_info=True)
            ollama_status = "unavailable"

    lines.append(f"**Config:** {' | '.join(config_parts)} | Ollama: {ollama_status}")
    lines.append(f"  ECO modes: hybrid/full (current: {eco_mode}). Change: eco_set_mode")
    lines.append("  Direct channels (no browser): Instagram, WhatsApp, Email — prefer over browser")

    return "\n".join(lines)


async def _get_mcp_status_cached(config: Config, user_id: str) -> list[str]:
    """Get MCP status with 60s TTL cache (avoids ListToolsRequest spam)."""
    global _mcp_cache, _mcp_cache_time

    # Fast path (no lock)
    now = time.monotonic()
    if _mcp_cache and (now - _mcp_cache_time) < _CAPABILITIES_TTL:
        return _mcp_cache

    # Slow path — lock with timeout, rebuild
    try:
        await asyncio.wait_for(_cache_lock.acquire(), timeout=3)
    except asyncio.TimeoutError:
        logger.warning("MCP cache lock held >3s — returning stale")
        return _mcp_cache
    try:
        now = time.monotonic()
        if _mcp_cache and (now - _mcp_cache_time) < _CAPABILITIES_TTL:
            return _mcp_cache
        result = await asyncio.wait_for(
            _get_mcp_status(config, user_id),
            timeout=8,
        )
        _mcp_cache = result
        _mcp_cache_time = now
        return result
    except asyncio.TimeoutError:
        logger.warning("_get_mcp_status timed out (>8s)")
        return _mcp_cache
    finally:
        _cache_lock.release()


async def _get_mcp_status(config: Config, user_id: str) -> list[str]:
    """Query connected MCP server names and tool counts (uncached)."""
    try:
        from lazyclaw.mcp.bridge import load_cached_schemas
        from lazyclaw.mcp.manager import _active_clients, BUNDLED_MCPS
        from lazyclaw.db.connection import db_session
        import json as _json

        async with db_session(config) as db:
            rows = await db.execute(
                "SELECT id, name, favorite FROM mcp_connections WHERE user_id = ?",
                (user_id,),
            )
            all_servers = [(row[0], row[1], bool(row[2])) for row in await rows.fetchall()]

        if not all_servers:
            return []

        result = []
        for server_id, name, is_fav in all_servers:
            desc = BUNDLED_MCPS.get(name, {}).get("description", "")

            # Get tool count: from live client or cache
            tool_count = 0
            if server_id in _active_clients:
                try:
                    tools = await asyncio.wait_for(
                        _active_clients[server_id].list_tools(),
                        timeout=5,
                    )
                    tool_count = len(tools)
                except (asyncio.TimeoutError, Exception):
                    logger.debug("Failed to list tools for MCP server %s", name, exc_info=True)
                    tool_count = 0
                status = "connected"
            else:
                cached = await load_cached_schemas(config, name)
                if cached:
                    try:
                        tool_count = len(_json.loads(cached))
                    except Exception:
                        logger.debug("Failed to parse cached MCP schema for %s", name, exc_info=True)
                status = "idle (lazy)"

            entry = f"{name}: {desc}" if desc else name
            entry += f" ({tool_count} tools, {status})"
            if is_fav:
                entry += " [favorite]"
            result.append(entry)

        return result
    except Exception as exc:
        logger.debug("Failed to get MCP status: %s", exc)
        return []


def _category_label(cat: str) -> str:
    """Human-readable category label."""
    return {
        "general": "Core Skills",
        "utility": "Utilities",
        "search": "Search",
        "research": "Research",
        "memory": "Memory",
        "vault": "Vault",
        "browser": "Browser",
        "computer": "Computer",
        "skills": "Skills Management",
        "custom": "Custom Skills",
        "security": "Security",
    }.get(cat, cat.title())
