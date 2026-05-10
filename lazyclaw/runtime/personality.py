"""Personality loader with filesystem caching.

SOUL.md is read once and cached in memory. Re-reads only when
the file's mtime changes (e.g., user edits the file).
"""

from __future__ import annotations

from pathlib import Path

_FALLBACK_PERSONALITY = (
    "You are Claw, a helpful AI assistant. Be direct, friendly, and efficient."
)

_FALLBACK_HEARTBEAT_PERSONALITY = (
    "You are LazyClaw. This turn was triggered by a scheduled heartbeat "
    "(reminder, watcher, or task escalation), not by a user message. Keep "
    "your reply short (1–4 sentences) and Telegram-shaped. Do not open a "
    "browser, dispatch background work, or run >2 tools. Never fabricate "
    "numbers. If a [TASK_REMINDER:<id>] turn is bound, only call complete_task "
    "or fail_task when the user explicitly says done or skip."
)

# Appended to SOUL.md only when the resolved brain is MiniMax M2/M2.7. The
# Anthropic-compat endpoint is correct (MiniMax themselves recommend it for
# full system/tool/thinking support) but M2.7 still narrates actions as text
# instead of emitting tool_use blocks under load. This suffix names the failure
# mode explicitly. The regex guard in agent.py catches drift after the fact;
# this suffix prevents it before the fact.
_MINIMAX_TOOL_DISCIPLINE_SUFFIX = (
    "[TOOL DISCIPLINE — REQUIRED]\n"
    "You are an interleaved-thinking model. Your reasoning happens in <think>\n"
    "blocks; your ACTIONS must happen in tool_use blocks.\n"
    "\n"
    "Forbidden in user-facing text:\n"
    "- \"I'll notify/ping/message/Telegram you\"\n"
    "- \"Background task is running / started / kicked off\"\n"
    "- \"Dispatched N agents\", \"Status: Running\", emoji-prefixed status lines\n"
    "- \"On it\", \"Got it, running\", \"Stand by\", \"Sit tight\"\n"
    "- ANY claim that a state change has happened or is in progress\n"
    "\n"
    "Rule: if your reply implies work was started, queued, dispatched, sent, or\n"
    "saved — you MUST emit a tool_use block in this same turn. If you cannot,\n"
    "say so plainly and ask for what is missing. Never claim work that wasn't\n"
    "tool-dispatched.\n"
    "\n"
    "[TOOL FIT — REQUIRED]\n"
    "External public info (phone numbers, addresses, business hours, company\n"
    "details, opening times, public emails, customer-service lines, Wikipedia-\n"
    "style facts) → use `web_search` ONLY.\n"
    "- NEVER call `vault_list` or `vault_get` to find external info. The\n"
    "  vault holds the USER's own credentials/API keys/passwords — it does\n"
    "  not contain third-party phone numbers or company details.\n"
    "- NEVER call `run_command` to find external info. The shell is local\n"
    "  and cannot reach the public internet from your environment.\n"
    "- NEVER call `recall_memories` repeatedly for the same query. One call\n"
    "  per topic per turn. If it misses, move to `web_search` or ask the\n"
    "  user.\n"
    "\n"
    "[STOP AFTER SUCCESS — REQUIRED]\n"
    "After a successful state-changing tool returns (add_task, save_memory,\n"
    "schedule_job, set_reminder, update_task, complete_task, delete_task)\n"
    "you MUST stop calling tools and reply to the user with a 1–3 sentence\n"
    "confirmation. Do NOT chain more tools unless the user's original\n"
    "request has multiple explicit parts you have NOT yet completed.\n"
    "\n"
    "[LOOKUP BEFORE WRITE — REQUIRED]\n"
    "When the user asks you to remember/save/schedule something that needs\n"
    "external lookup (phone, address, URL, hours, public info), do the\n"
    "`web_search` FIRST and read its result, THEN call the write tool with\n"
    "the looked-up value embedded in `notes` / `content` / `description`.\n"
    "Never write a placeholder task and plan to 'fill it in later' — the\n"
    "next turn belongs to the user, not you.\n"
    "\n"
    "[DISPATCH FIRST — REQUIRED]\n"
    "Before your FIRST tool call on a NEW user task, answer 3 questions:\n"
    "  Q1. Will this need ≥4 tool calls?       (yes → dispatch)\n"
    "  Q2. Will it run ≥30 s wall time?         (yes → dispatch)\n"
    "  Q3. Multi-step browser flow, form\n"
    "      submission, batch lookup, or\n"
    "      \"for each of N items\"?              (yes → dispatch)\n"
    "If ANY answer is yes, your FIRST and ONLY tool call this turn MUST be\n"
    "`run_background(instruction=\"<self-contained restatement: current\n"
    "state, what's done, what remains, success criteria>\", name=\"<short>\")`.\n"
    "Then reply exactly: \"Continuing in background — will report back when\n"
    "done.\"\n"
    "Do NOT \"just take a quick look first\" before dispatching. The\n"
    "background worker has the same tools you do; let it look. Reading the\n"
    "job details / opening the form / fetching the page YOURSELF before\n"
    "dispatching is the failure mode this gate exists to prevent.\n"
    "Dispatch examples: \"apply for me on <job>\" (multi-step form + draft +\n"
    "submit), \"find emails for these N businesses\" (batch lookup), \"monitor\n"
    "X and ping me when Y\" (long-running), \"scrape this catalog\" (many\n"
    "pages), \"book me a slot at …\" (multi-step form).\n"
    "Stay inline: \"what's the price of X\" (1–2 tools), \"send a quick reply\"\n"
    "(1 tool), \"remind me to call Mom at 5pm\" (1 tool), greetings, status\n"
    "questions, factual lookups."
)


def is_minimax_brain(model: str | None) -> bool:
    """Return True when the resolved brain model is a MiniMax M2/M2.7 variant.

    Both capitalizations appear in the model registry (`MiniMax-M2.7`,
    `minimax-m2.5`, `MiniMax-Text-01`, `minimax-m2.7-highspeed`, etc.) so we
    match case-insensitively against the prefix.
    """
    if not model:
        return False
    return model.lower().startswith("minimax-")


def minimax_tool_discipline_suffix() -> str:
    """The tool-discipline block to append for MiniMax brains."""
    return _MINIMAX_TOOL_DISCIPLINE_SUFFIX

# In-memory cache: content + mtime
_cache: str | None = None
_cache_mtime: float = 0.0

# Separate cache for the slim HEARTBEAT.md personality so it doesn't evict
# the SOUL.md cache (and vice-versa).
_hb_cache: str | None = None
_hb_cache_mtime: float = 0.0


def _find_project_root() -> Path:
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


def load_personality(personality_path: str | None = None) -> str:
    """Load SOUL.md with mtime-based caching (~0ms on cache hit)."""
    global _cache, _cache_mtime

    root = _find_project_root()
    path = root / (personality_path or "personality/SOUL.md")
    try:
        mtime = path.stat().st_mtime
        if _cache is not None and mtime == _cache_mtime:
            return _cache
        _cache = path.read_text(encoding="utf-8")
        _cache_mtime = mtime
        return _cache
    except FileNotFoundError:
        return _FALLBACK_PERSONALITY


def load_heartbeat_personality(personality_path: str | None = None) -> str:
    """Load HEARTBEAT.md (slim ~1k-token personality) with mtime caching.

    Used by the agent's slim-context branch for announce-style heartbeat
    triggers ([REMINDER], [WATCHER], [MCP_WATCHER], [TASK_REMINDER:<id>]).
    Falls back to a tiny inline string if the file is missing.
    """
    global _hb_cache, _hb_cache_mtime

    root = _find_project_root()
    path = root / (personality_path or "personality/HEARTBEAT.md")
    try:
        mtime = path.stat().st_mtime
        if _hb_cache is not None and mtime == _hb_cache_mtime:
            return _hb_cache
        _hb_cache = path.read_text(encoding="utf-8")
        _hb_cache_mtime = mtime
        return _hb_cache
    except FileNotFoundError:
        return _FALLBACK_HEARTBEAT_PERSONALITY


def build_system_prompt(
    personality: str, extra_context: str | None = None
) -> str:
    if extra_context:
        return f"{personality}\n\n---\n\n{extra_context}"
    return personality
