from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.llm.router import LLMRouter
from lazyclaw.llm.eco_router import EcoRouter, ROLE_BRAIN, ROLE_WORKER
from lazyclaw.llm.providers.base import LLMMessage, ToolCall
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.crypto.encryption import encrypt, decrypt
from lazyclaw.db.connection import db_session

from lazyclaw.runtime.callbacks import AgentEvent, CancellationToken, NullCallback
from lazyclaw.runtime.events import (
    FAST_DISPATCH, INSTANT_COMMAND,
    HELP_NEEDED, HELP_RESPONSE,
    COMPACTION_BOUNDARY,
    BROWSER_PLAN, BROWSER_ACTION, BROWSER_VERIFY, BROWSER_PROGRESS,
)
from lazyclaw.runtime.team_lead import TeamLead
from lazyclaw.runtime.stuck_detector import detect_stuck, detect_inline_pivot
from lazyclaw.browser.action_planner import (
    ActionPlannerState,
    PlanStatus,
    evaluate_action_result,
    make_plan_injection_prompt,
    parse_plan_from_response,
    should_inject_plan,
)
from lazyclaw.runtime.tool_executor import APPROVAL_PREFIX, ToolExecutor
from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Chat-only patterns — messages that NEVER need tools.
# Everything else gets tools and the LLM decides what to use.
# Action confirmations — short messages that continue a tool-using conversation.
# "do it", "go", "run it", "yes do it", "go ahead", "start", "proceed", "continue"
_ACTION_CONFIRM_PATTERN = re.compile(
    r"^(do it|do that|go|go ahead|run it|start|proceed|continue|execute|"
    r"yes do it|yeah do it|yep do it|ok do it|sure do it|"
    r"finish it|try it|try again|retry|send it|ship it|"
    r"first one|second one|option 1|option 2|the first|the second)[\s!?.,]*$",
    re.IGNORECASE,
)

_CHAT_ONLY_PATTERN = re.compile(
    r"^(hi|hey|hello|yo|sup|thanks|thank you|thx|ok|okay|sure|"
    r"yes|no|yep|nope|good morning|good night|good evening|"
    r"bye|goodbye|see you|how are you|what's up|whats up|"
    r"nice|cool|great|awesome|lol|haha|hehe|wow|omg|"
    r"good|bad|fine|alright|sounds good|got it|understood|"
    r"perfect|exactly|right|correct|wrong|nah|meh)[\s!?.,]*$",
    re.IGNORECASE,
)

# Heartbeat daemon prefixes task-bound reminders with [TASK_REMINDER:<task_id>]
# (see lazyclaw/tasks/store.py:609).  When a turn starts with this marker we
# bind it to that task so the finally-block safety net can auto-fail the row
# if the agent exits without calling complete_task / fail_task.
_TASK_REMINDER_RE = re.compile(r"^\[TASK_REMINDER:([^\]]+)\]")

# Telemetry-only: matches every heartbeat-shaped message so we can log
# system_prompt size + tool count per call. Used to validate that slim-path
# changes actually reduce token usage without regressing the full path.
_HEARTBEAT_TELEMETRY_RE = re.compile(
    r"^\[(REMINDER|WATCHER|MCP_WATCHER|TASK_REMINDER:[^\]]+|JOB:[^\]]+)\]"
)

# Slim-context branch — Tier A (announce-style): bypasses build_context() and
# the full SOUL.md to keep heartbeat reminders cheap. JOB:<name> deliberately
# excluded; those can be arbitrary work and stay on the full path.
# GOAL_PROGRESS rides the slim path so a user-wired daily progress cron
# costs ~5k tokens instead of ~40k — the brain just calls
# goal_progress_report(), no SOUL.md, no capabilities, no hybrid memory.
_SLIM_HEARTBEAT_PREFIX_RE = re.compile(
    r"^\[(REMINDER|WATCHER|MCP_WATCHER|TASK_REMINDER:[^\]]+|GOAL_PROGRESS)\]"
)


# Matches messages that are clearly meta/reflection questions about the
# agent's own behaviour ("why did you …?", "what tools do you have?",
# "how come the code agent ran?"). Used to skip the text-only
# AUTO-PROMOTE failsafe — those questions deserve a conversational
# reply, not a forced ``run_background`` dispatch that re-invokes the
# very tool the user is asking about. Conservative: requires both a
# leading wh-word AND a '?' so plain commands aren't mistaken for
# questions. ``can`` / ``could`` are intentionally excluded — those
# are usually polite-form commands ("can you scan upwork?").
_META_QUESTION_RE = re.compile(
    r"^\s*(why|what|how|when|where|who)\b.*\?",
    re.IGNORECASE | re.DOTALL,
)


def _is_meta_question(text: str | None) -> bool:
    """Return True when ``text`` reads like a meta/reflection question
    about the agent itself. See ``_META_QUESTION_RE`` for the pattern.
    """
    if not isinstance(text, str) or not text:
        return False
    return _META_QUESTION_RE.match(text) is not None


# Message prefixes the daemon stamps on heartbeat-lane turns. Such turns drive
# the BACKGROUND browser lane (their own tab) instead of the user's visible
# tab, so a watcher/cron browser action can't steal/block the foreground.
_BACKGROUND_TURN_PREFIXES = ("[WATCHER:", "[JOB:", "[REMINDER")

# Non-idempotent generators / actions that must NEVER be served from the
# per-turn duplicate-call cache. Each call must re-read the live page or
# re-generate; otherwise a stale prior result is silently replayed — e.g.
# ``draft_freelance_proposal`` once cached a draft of the WRONG job and the
# next call returned the same wrong draft (2026-06-03). The stuck-detector
# still guards true loops.
_NEVER_CACHE_TOOLS = frozenset({
    "draft_freelance_proposal",
    "apply_job",
})


def _infer_browser_role(message: str) -> str:
    """Infer the browser lane for a turn from its message prefix.

    Belt-and-suspenders with the explicit ``browser_role`` the daemon passes:
    keeps daemon-originated turns on the background lane even if a caller
    forgets the kwarg.
    """
    from lazyclaw.runtime.browser_turn_lock import BACKGROUND_ROLE, VISIBLE_ROLE

    if isinstance(message, str) and message.startswith(_BACKGROUND_TURN_PREFIXES):
        return BACKGROUND_ROLE
    return VISIBLE_ROLE


# ── Meta-Tool Pattern ─────────────────────────────────────────────────
# Instead of regex-guessing which tools the LLM needs (brittle, 5K tokens),
# send only 3-4 base tools. LLM discovers others via search_tools on demand.
# Schemas for discovered tools are injected dynamically.

# Base tools always sent — everything the brain needs to work.
# NOTE: `browser` is intentionally NOT here. It's injected on keyword match
# only (see _BROWSER_RE below + line where `_wants_browser` is handled).
# Training bias pushes "scrape" → `browser`; keeping it out of the default
# set forces the brain to reach for `web_search` / `dispatch_subagents` /
# `run_background` first.
#
# Same rationale for `run_command` / `read_file` / `write_file` /
# `list_directory`: cheap brains (MiniMax M2.7 in particular) misfire
# `run_command` on tasks that need external info (e.g. "find this phone
# number"). They're now keyword-gated via _SHELL_FILE_RE — brain still
# discovers them via `search_tools` when genuinely needed.
_BASE_TOOL_NAMES = frozenset({
    "search_tools", "web_search", "recall_memories", "save_memory", "delegate",
    "dispatch_subagents", "run_background",
    "connect_mcp_server", "disconnect_mcp_server",
    "watch_messages", "watch_site", "list_watchers", "stop_watcher",
})

# Minimal tools for local models (4B can't handle 4000+ tool tokens)
# Brain only needs delegate (dispatch workers) + search + memory
_LOCAL_TOOL_NAMES = frozenset({
    "delegate", "web_search", "recall_memories", "save_memory", "search_tools",
})

# MiniMax M2.7 narrates actions as text instead of emitting tool_use blocks
# once the tool count exceeds ~24. This is empirical, not documented — the
# narration rate roughly doubles between 24 and 40 tools. When the resolved
# brain is MiniMax, the loaded tool list is trimmed to this cap below.
_MINIMAX_MAX_TOOLS = 24

# Thin-router (Phase 2): the meta-tools the brain may ALWAYS call. After one
# inline non-meta (domain) tool call, the brain is narrowed to ONLY these so
# it must delegate the rest. Used only when LAZYCLAW_THIN_ROUTER is set.
_META_TOOLS = frozenset({
    "delegate", "dispatch_subagents", "run_background",
    "search_tools", "web_search", "recall_memories", "save_memory",
    "get_agent_status",
})


def _trim_tools_for_minimax(
    tools: list[dict],
    user_message: str,
    base_names: set[str],
) -> list[dict]:
    """Trim a tool list to ``_MINIMAX_MAX_TOOLS`` for MiniMax brains.

    Keeps every tool whose name is in ``base_names`` (always-needed:
    search_tools, web_search, recall_memories, save_memory, delegate,
    dispatch_subagents, run_background, …). The remaining slots are filled
    by ranking the rest by keyword overlap between the user message and
    each tool's display name + description.

    Falls back to a stable head-of-list slice when the message is empty or
    no tools score any overlap, so behaviour is deterministic regardless of
    input.
    """
    if len(tools) <= _MINIMAX_MAX_TOOLS:
        return tools

    base_keep: list[dict] = []
    candidates: list[dict] = []
    for t in tools:
        name = t.get("function", {}).get("name", "")
        if name in base_names:
            base_keep.append(t)
        else:
            candidates.append(t)

    # Truncate base set if it alone exceeds the cap (defensive — base is
    # only ~7 names so this branch is unlikely).
    if len(base_keep) >= _MINIMAX_MAX_TOOLS:
        return base_keep[:_MINIMAX_MAX_TOOLS]

    remaining_slots = _MINIMAX_MAX_TOOLS - len(base_keep)

    # Reuse the tokenizer pattern from context_builder._pick_hybrid_memories
    # (EN+ES stopwords, 2+ char alphanumeric tokens, lowercase).
    from lazyclaw.runtime.context_builder import _tokenize_for_memory

    query_tokens = _tokenize_for_memory(user_message)
    if not query_tokens:
        # No useful query — keep the first N candidates in their existing
        # order so behaviour is stable across calls.
        return base_keep + candidates[:remaining_slots]

    scored: list[tuple[int, int, dict]] = []
    for idx, t in enumerate(candidates):
        func = t.get("function", {})
        text = f"{func.get('name', '')} {func.get('description', '')}"
        tool_tokens = _tokenize_for_memory(text)
        overlap = len(query_tokens & tool_tokens)
        # Negative overlap so that higher overlap sorts first; idx as a
        # stable tiebreaker preserves the registry order.
        scored.append((-overlap, idx, t))
    scored.sort()

    # If nothing scored, fall back to insertion order — better than dropping
    # the channel-aware tools that were just injected upstream.
    if scored and scored[0][0] == 0:
        return base_keep + candidates[:remaining_slots]

    return base_keep + [t for _, _, t in scored[:remaining_slots]]

# Browser only when user explicitly asks — prevents unwanted visible browser popups.
# Word-bounded regex to avoid substring collisions ("scan" → "scanner",
# "open" → "open-source", "visit" → "visitor").
_BROWSER_RE = re.compile(
    r"\b(browser|wallapop|navigate\s+to|go\s+to|open\s+(?:the|a|url|tab|website|page)|"
    r"visit\s+the|visit\s+a|show\s+me|show\s+it|make\s+visible|qr\s+code|"
    r"log\s*in|sign\s+in|login)\b",
    re.IGNORECASE,
)

# Shell / local-file ops only when the user explicitly asks. Keeps
# `run_command` out of the default tool list so cheap brains (MiniMax M2.7)
# can't misfire it on a "find this phone number" task. Word-bounded.
# Triggers strict phrases ("run command", "shell", "edit file", "ls /…")
# AND filename-extension hits (".py", ".sh", etc.) which strongly imply
# local-file work. Anything else: brain calls `search_tools`.
_SHELL_FILE_RE = re.compile(
    r"\b(?:"
    r"run\s+(?:a\s+)?command|shell\s+command|terminal|bash|zsh|"
    r"execute\s+(?:a\s+|the\s+)?(?:command|script|file)|"
    r"read\s+(?:the\s+|a\s+|my\s+)?file|write\s+(?:to\s+|a\s+)?file|"
    r"create\s+(?:a\s+|the\s+|new\s+)?file|edit\s+(?:the\s+|a\s+)?file|"
    r"open\s+(?:the\s+|a\s+|my\s+)?file|save\s+(?:to\s+)?(?:a\s+|the\s+)?file|"
    r"list\s+(?:the\s+)?(?:directory|folder|files)|"
    r"ls\s+[/~.]|cd\s+[/~]|pwd"
    r")\b"
    r"|\.(?:py|sh|json|txt|md|yaml|yml|toml|ini|conf|log|csv|tsv)\b",
    re.IGNORECASE,
)

# Tool names gated behind _SHELL_FILE_RE. Brain can still find them via
# `search_tools(query="run command")` if the keyword detector misses.
_SHELL_FILE_TOOL_NAMES = frozenset({
    "run_command", "read_file", "write_file", "list_directory",
})

# Detects when the brain produces ASSISTANT TEXT claiming a tool was called
# while emitting zero tool_calls — i.e. the "Already on it! Background task
# is running…" hallucination. Cheap brains (MiniMax-M2.7, Gemma) sometimes
# narrate the action as text instead of emitting tool_use blocks. When this
# regex matches AND tool_calls is empty AND tools were available, we inject
# a correction system message and re-roll the iteration.
#
# Categories below are grouped by intent so future readers can extend cleanly:
#   1. State mutation already happened ("I've started…", "Kicked off…")
#   2. State mutation in flight ("Running in background", emoji status lines)
#   3. Future commitments standing in for tool calls ("I'll notify you")
#   4. Filler reassurances ("on it!", "got it, running")
#   5. Status: prefix progress lines ("Status: Finding emails for: @x, @y")
#   6. Dispatch verbs ("Dispatched 3 search agents", "Spinning up a worker")
#   7. Background promises ("Results will arrive on Telegram shortly")
#   8. Time-stamped fake starts ("Started: ~just now")
_ACTION_CLAIM_RE = re.compile(
    r"("
    # ── Emoji-prefixed status ("🚀 Background task running!") ─────────────
    # Self-contained (the emoji is part of the match) so it can't fire on
    # ordinary prose. Covers the rocket / hourglass / checkmark family the
    # user observed in failures.
    r"[🚀⏳✅🔍📡⚡]\s*(?:background\s+task|task|running|launching|dispatched|started|queued|on\s+it|done|in\s+progress|fetching|searching|finding|loading|processing|working)"
    # ── 1. State mutation already happened ────────────────────────────────
    r"|\balready\s+on\s+it"
    r"|\bi'?ve\s+(?:started|kicked\s+off|dispatched|launched|fired\s+off|sent(?:\s+off)?|created|added|scheduled|appended|queued|enqueued|registered|saved\s+to\s+(?:your|the)\s+sheet)"
    r"|\bi\s+(?:just\s+)?(?:dispatched|launched|fired\s+off|kicked\s+off|sent\s+off|spun\s+up|queued|enqueued|started\s+a\s+background)"
    r"|\bi\s+have\s+(?:queued|enqueued|scheduled|registered|added\s+to\s+the\s+queue|started|launched|dispatched)"
    r"|\bkicked\s+off\s+(?:a|the)\s+(?:background|async|search|task|job|run|agent|sub)"
    r"|\bspinning\s+up\s+(?:a|the|\d+)\s+(?:task|agent|search|background|worker|sub)"
    r"|\bfiring\s+off\s+(?:a|the)?\s*(?:search|task|background|run)"
    r"|\bqueued\s+(?:up\s+)?(?:a|the|\d+)\s+(?:task|search|job|background|run|agent)"
    r"|\b(?:just\s+)?(?:dispatched|launched|kicked\s+off|fired\s+off|sent\s+off|queued|spun\s+up)\s+(?:it|that|the\s+task|a\s+task|\d+|the\s+job|in\s+the\s+background)"
    # ── 2. State mutation in flight ───────────────────────────────────────
    r"|\bbackground\s+(?:task|job|run|agent)\s+(?:is\s+running|started|kicked\s+off|launched|in\s+progress|queued|dispatched)"
    r"|\b(?:running|searching|looking|fetching|gathering|scraping|querying|processing)\s+(?:in\s+(?:the\s+)?background|now|right\s+now|for\s+you)"
    r"|\bworking\s+on\s+(?:it|that|this)\s+(?:now|in\s+the\s+background)"
    r"|\bworking\s+in\s+(?:the\s+)?background"
    r"|\bexecuting\s+(?:in\s+(?:the\s+)?background|now|right\s+now)"
    r"|\brunning\s+(?:a|the|\d+)\s+(?:search|task|background)"
    r"|\bthis\s+(?:is|will\s+be)\s+running\s+in\s+(?:the\s+)?background"
    r"|\b(?:i'?m|i\s+am)\s+(?:running|searching|fetching|gathering|scraping|dispatching|launching|kicking\s+off|working\s+on|processing|executing)"
    r"|\b(?:i'?m|i\s+am)\s+now\s+(?:running|searching|fetching|dispatching|working\s+on|processing)"
    # ── 3. Future commitments standing in for tool calls ──────────────────
    r"|\bi'?ll\s+(?:ping|notify|message|telegram|let\s+you\s+know|update\s+you|keep\s+you\s+posted)\b"
    r"|\bi\s+will\s+(?:ping|notify|message|telegram|let\s+you\s+know|update\s+you|keep\s+you\s+posted)\b"
    r"|\bping\s+you\s+(?:on\s+telegram|when\s+(?:it'?s|done|finished)|once)"
    r"|\b(?:i'?ll|i\s+will)\s+(?:get\s+back|come\s+back|circle\s+back|follow\s+up|reach\s+out)"
    r"|\b(?:i'?ll|i\s+will)\s+(?:run|start|launch|begin|execute|kick\s+off|fire\s+off)\s+(?:that|this|the\s+search|in\s+the\s+background|now)"
    r"|\b(?:will|gonna|going\s+to)\s+(?:notify|ping|message|update|telegram)\s+you"
    # ── 4. Filler reassurances (always followed by NO tool call) ──────────
    r"|\bon\s+it!"
    r"|\broger\s+that"
    r"|\bgot\s+it,?\s+(?:running|starting|dispatching|searching|fetching|on\s+it)"
    r"|\bsit\s+tight"
    r"|\bstand\s+by"
    r"|\bhold\s+on,?\s+(?:running|searching|fetching|dispatching|i'?m\s+(?:running|searching|fetching))"
    r"|\bno\s+action\s+needed\s+from\s+you"
    r"|\bbe\s+right\s+back\s+with"
    # ── 5. Status: prefix progress lines ──────────────────────────────────
    r"|(?:^|\n)\s*status:?\s*(?:finding|searching|looking|fetching|gathering|loading|running|processing|working|analyzing|checking|preparing|building|generating|sending|posting|scraping|dispatching|querying)"
    r"|\bfinding\s+(?:emails?|contacts?|info|details?|the\s+answer|profiles?)\s+for\s*[:@]"
    r"|\bsearching\s+for\s+(?:emails?|contacts?|info|details?)\s+(?:of|for|from)"
    r"|\blooking\s+up\s+(?:emails?|contacts?|info|details?|profiles?)\s+(?:of|for|from)"
    r"|\bprocessing\s+(?:that|this|your\s+request|the\s+request)"
    # ── 6. Dispatch verbs ─────────────────────────────────────────────────
    r"|\bdispatched?\s+\d+\s+(?:search|sub)?agents?"
    r"|\bdispatching\s+(?:agents?|subagents?|search|the\s+task|workers?)"
    # ── 7. Background promises ────────────────────────────────────────────
    r"|\b(?:results?|update|output|answer)\s+(?:will\s+)?(?:come|arrive|appear|land|be\s+(?:back|ready))\s+(?:on\s+telegram|shortly|soon|when|in\s+a)"
    r"|\bonce\s+(?:it'?s\s+done|finished|complete),?\s+i'?ll"
    r"|\bas\s+soon\s+as\s+(?:it'?s\s+done|done|finished|complete)"
    r"|\bmeanwhile,?\s"
    r"|\bin\s+the\s+meantime"
    # ── 8. Time-stamped fake starts ───────────────────────────────────────
    r"|\bstarted:?\s*(?:~|just\s+now|a\s+(?:moment|minute|few)|\d+\s*(?:minute|second|hour))"
    # ── 9. Channel-read / "I'll have it shortly" refusal family ───────────
    # (2026-05-29 "Chek my whats up + chek mail" incident: the background
    #  worker shipped "two readers are scanning WhatsApp and Email in
    #  parallel. I'll have your summary in a few seconds." with tool_calls=0.
    #  None of the verb patterns above matched it, so the force-dispatch
    #  failsafe never fired and the promise was stored as the task result.)
    r"|\bi'?ll\s+have\s+(?:your|the|that|a|it|them|both)\b[^.\n]{0,40}?\b(?:in\s+a\s+(?:few|moment|sec|couple)|shortly|soon|momentarily|ready|in\s+\d+\s*(?:sec|min))"
    r"|\b(?:i'?ll|i\s+will)\s+(?:summari[sz]e|recap)\b"
    r"|\bi'?ll\s+(?:send|get)\s+(?:you\s+)?(?:a|the|your)\s+(?:summary|recap|results?|rundown)\b"
    r"|\bone\s+moment\b"
    r"|\bgive\s+me\s+(?:a\s+)?(?:moment|sec|second)\b"
    r"|\blet\s+me\s+(?:check|pull|grab|fetch|read|scan|take\s+a\s+look|go\s+(?:check|grab|read)|look\s+(?:up|into|at))\b"
    r"|\b(?:scanning|pulling|polling)\s+(?:your|the|both|through|whatsapp|email|emails?|messages?|inbox|chats?|dms?)\b"
    r"|\b(?:readers?|agents?|workers?|subagents?)\s+(?:are|is)\s+(?:scanning|reading|searching|pulling|checking|fetching|gathering)\b"
    r")",
    re.IGNORECASE,
)

# Channel keywords → prefer MCP tools over browser.
# Compiled regexes with \b word boundaries so "mail" doesn't match "airmail",
# "insta" doesn't match "install/instant", etc. (Fixed 2026-04-24 after the
# scraping task was mis-routed as "email" due to substring match.)
# Hand-tuned regexes for channels that benefit from typo/abbrev handling.
# Anything not in this map is auto-derived from BUNDLED_MCPS at lookup time
# so adding a new MCP automatically gets keyword-routing without code edits.
_HAND_TUNED_CHANNEL_KEYWORDS: dict[str, re.Pattern[str]] = {
    "whatsapp":  re.compile(r"\b(whatsapp|wa\s+(?:msg|message))\b", re.IGNORECASE),
    "instagram": re.compile(r"\b(instagram|insta|ig\s+(?:msg|message))\b", re.IGNORECASE),
    "email":     re.compile(r"\b(e-?mail|gmail|inbox)\b", re.IGNORECASE),
    # Catch "upwork", "Upwork", "upwork.com", and the user-typo variants
    # "upworker" / "upworks" (verified live 2026-05-11 — "can you vcheck
    # upworker works small projects" routed to core-only tools because
    # \bupwork\b doesn't match the "er" suffix, so the brain hallucinated
    # tool names from history instead of having them properly injected).
    # Also the transposition typos "upwokr" / "upwrok" (verified live
    # 2026-06-03 — "Find matching jobs on upwokr" matched NO channel keyword,
    # so only `browser` got injected and the brain scrolled the search grid
    # into a CDP timeout instead of calling search_jobs).
    "upwork":    re.compile(
        r"\bupw(?:ork|okr|rok)(?:er|ers|s)?\b|upwork\.com", re.IGNORECASE,
    ),
}

# Manual aliases for multi-keyword MCPs whose name doesn't match the words
# users actually say. Keys are channel labels (canonical, lowercase, no
# `mcp-` prefix); values are alternations injected into a single regex.
# Auto-derivation handles single-name MCPs (e.g. `stripe` → `\bstripe\b`),
# this map exists for the workspace/email-style aggregators.
_CHANNEL_KEYWORD_ALIASES: dict[str, list[str]] = {
    "workspace": [
        "google sheets", "google drive", "google docs", "google calendar",
        "gsheets?", "gdrive", "gdocs?", "gcal", r"\bworkspace\b",
        # Bare words last so the more-specific phrases win regex priority
        r"\bsheets?\b", r"\bdrive\b", r"\bcalendar\b", r"\bdocs?\b",
    ],
}

# Channels that should NOT be auto-routed even if their name appears (too
# generic, would over-match). e.g. "claude-code" matches "claude" which
# matches every "hi claude" greeting.
_CHANNEL_AUTOROUTE_BLOCKLIST: frozenset[str] = frozenset({
    "claude-code", "n8n-nodes",
})


def _build_channel_keywords() -> dict[str, re.Pattern[str]]:
    """Build the active channel-keyword map.

    Combines hand-tuned regexes (above) with auto-derived patterns from
    every enabled MCP in ``BUNDLED_MCPS``. Adding a new MCP requires a
    daemon restart, which matches the existing behavior for every other
    MCP-related cache.

    Auto-derivation rules:
      * Strip ``mcp-`` prefix to get the canonical channel label.
      * If the label has a hand-tuned regex, use that (skip auto).
      * Else if the label has an alias list, use those alternations.
      * Else build ``\\b<label>\\b`` (word-boundary match).
      * Skip blocklisted labels (too-generic names).
    """
    from lazyclaw.mcp.manager import BUNDLED_MCPS

    keywords: dict[str, re.Pattern[str]] = dict(_HAND_TUNED_CHANNEL_KEYWORDS)

    for mcp_name, info in BUNDLED_MCPS.items():
        if info.get("disabled"):
            continue
        # Canonicalize: strip both ``mcp-`` prefix and ``-mcp`` suffix so
        # ``mcp-upwork`` and ``workspace-mcp`` both reduce to bare names.
        canonical = mcp_name.lower()
        if canonical.startswith("mcp-"):
            canonical = canonical[4:]
        if canonical.endswith("-mcp"):
            canonical = canonical[:-4]
        if canonical in keywords:
            continue
        if canonical in _CHANNEL_AUTOROUTE_BLOCKLIST:
            continue
        if canonical in _CHANNEL_KEYWORD_ALIASES:
            patterns = _CHANNEL_KEYWORD_ALIASES[canonical]
        else:
            patterns = [rf"\b{re.escape(canonical)}\b"]
        keywords[canonical] = re.compile("|".join(patterns), re.IGNORECASE)

    return keywords


# Computed eagerly at import time so call sites continue to use it as a
# plain dict (e.g. ``for channel, pattern in _CHANNEL_KEYWORDS.items()``).
# BUNDLED_MCPS is a module-level dict in lazyclaw.mcp.manager, so it's
# stable as soon as that module finishes importing.
_CHANNEL_KEYWORDS: dict[str, re.Pattern[str]] = _build_channel_keywords()

# Channel tool suffixes to include for simple status/read queries.
# Full tool set only injected when message indicates action intent.
_CHANNEL_CORE_SUFFIXES = frozenset({
    "_status", "_setup", "_read", "_read_dms", "_read_profile",
    "_read_feed", "_list_chats", "_search", "_send", "_send_dm",
    "_reply_dm",
    # Upwork MCP uses get_* / check_* naming — without these, "find what
    # James said on upwork" / "Find James Blue's Upwork conversation"
    # silently filtered every upwork read tool out of the channel-tool
    # set (no _wants_action → core-only filter → zero matches), and the
    # background worker hallucinated 12,280 chars from prior context
    # instead of fetching. Observed 2026-05-17 19:18:03.
    "_get_messages", "_get_conversation", "_get_unread_count",
    "_check_session", "_get_my_profile", "_get_proposals",
    "_get_contracts",
})


# ─── F1 grounding helpers ─────────────────────────────────────────────────
# F1 enforcement is mechanical, not prompt-based — Opus 4.6/4.7 reads SOUL.md
# line 14 and still confabulates (2026-05-19 16:19 incident: brain called
# upwork tools 3x, got real data, then emitted 8534 chars of "Vato agreed
# $150 / Windows laptop / May 16" from training-data pattern-match). Three
# mechanical layers fix this:
#   1. Wrap channel-read tool_results with a freshness sentinel so the brain
#      has an explicit precedence marker against stale paraphrases.
#   2. Gate self_recall on no-channel-read-this-turn — injecting paraphrased
#      semantic-search snippets AFTER fresh tool results buries them.
#   3. Post-reply F1 enforcer: when channel-read tool was called this turn,
#      reject drafts containing banned 'from memory' phrases or lacking a
#      verbatim `> sender (ts): …` quote block, and re-prompt.
# Tools whose results are AUTHORITATIVE LIVE READS — when one is called,
# the brain's reply MUST quote it verbatim, not paraphrase from memory.
_CHANNEL_READ_TOOL_SUFFIXES: frozenset[str] = frozenset({
    "_get_messages",
    "_get_conversation",
    "_get_unread_count",
    "_read",
    "_read_dms",
    "_read_feed",
    "_read_profile",
    "_list_chats",
})

_CHANNEL_READ_TOOL_EXACT: frozenset[str] = frozenset({
    "upwork_last_conversation",
    "upwork_inbox_check",
    "upwork_contract_poll",
})


def _is_channel_read_tool(name: str) -> bool:
    """True if ``name`` is a tool whose result is a live channel read.

    Strips an ``mcp_<uuid>_`` prefix before matching so MCP-bridged tools
    like ``mcp_489c8963-…_upwork_get_messages`` match the same way as the
    bare NL skill names.
    """
    if not name:
        return False
    n = name.lower()
    if n.startswith("mcp_"):
        parts = n.split("_", 2)
        if len(parts) == 3:
            n = parts[2]
    if n in _CHANNEL_READ_TOOL_EXACT:
        return True
    return any(n.endswith(suf) for suf in _CHANNEL_READ_TOOL_SUFFIXES)


# Banned 'from memory' phrases — when a channel-read tool was called this
# turn but the draft contains any of these, the brain is paraphrasing from
# context (daily_log header, self_recall block, prior summary) instead of
# the just-returned tool result. Matched case-insensitively as substrings.
_F1_BANNED_PHRASES: re.Pattern[str] = re.compile(
    r"(?:"
    r"already pulled"
    r"|already (?:have|got|know)(?:\s+the)?\s+(?:thread|messages?|conversation|ask|requirements?|details?)"
    r"|from (?:our|the) (?:chat|conversation|thread|discussion)\s+(?:earlier|yesterday|before|above)"
    r"|based on what (?:he|she|they) (?:said|wrote|told)"
    r"|from memory"
    r"|i (?:recall|remember)(?:\s+(?:from|that))?"
    r"|as (?:i|we) (?:discussed|saw|noted)\s+(?:earlier|before)"
    r"|per (?:our|the) earlier"
    r")",
    re.IGNORECASE,
)

# Quote block detector — a line that begins with `> ` followed by a
# non-whitespace character. The F1 rule (SOUL.md line 14) requires the
# reply to BEGIN with a verbatim quote block of the 3 most recent
# contact-side messages. We don't enforce "begins with"; we only require
# ≥1 quote line exists, because the brain may legitimately prepend a one-
# liner like "Pulled the thread — here's what James wrote:" before the
# quotes (still grounded, still useful).
_F1_QUOTE_LINE_RE: re.Pattern[str] = re.compile(r"^>\s+\S", re.MULTILINE)

# Drafts shorter than this are exempt from the quote-block requirement —
# short acknowledgments like "Let me re-read the thread first" make no
# factual claims yet so there's nothing to ground. Bumped from 200 → 280
# so the brain has room for "I checked your DMs — James asked about X,
# pulling the full thread now" (no quotes yet, but no fabrication either).
_F1_DRAFT_QUOTE_THRESHOLD_CHARS: int = 280


def _check_f1_violation(
    draft: str, tool_results: list[str],
) -> str | None:
    """Validate ``draft`` is grounded on fresh channel-read tool results.

    Returns a short violation reason, or ``None`` if the draft passes.

    Caller is responsible for confirming a channel-read tool was actually
    called this turn — this function does NOT inspect tool history.

    Checks:
    1. Banned 'from memory' phrases — instant fail (catches "Already
       pulled the thread above").
    2. If the draft is substantive (≥ ``_F1_DRAFT_QUOTE_THRESHOLD_CHARS``)
       AND the tool returned non-empty content, the draft must contain
       at least one ``> sender (ts): …`` quote line. Short
       acknowledgments are exempt because they make no claims.
    """
    if not draft:
        return None
    m = _F1_BANNED_PHRASES.search(draft)
    if m:
        return f"banned phrase {m.group(0)!r}"
    if (
        len(draft) >= _F1_DRAFT_QUOTE_THRESHOLD_CHARS
        and not _F1_QUOTE_LINE_RE.search(draft)
        and any((tr or "").strip() for tr in tool_results)
    ):
        return "substantive reply without a `> sender (ts): …` quote block"
    return None


# Cap F1 corrections at 2 retries — beyond that, ship the draft with a log
# warning rather than burning iterations on a wedged brain.
_F1_MAX_RETRIES: int = 2

# Cap F1 confabulation raw-data-injection retries at 2 per turn. Each retry
# re-runs `detect_confabulation` on the new draft + `phase2_enforcement_
# verdict` against fresh tool data; both must pass to ship. Before 2026-05-23
# the retry was a one-shot gated on `not _confab_injected`, so the post-retry
# output was accepted blindly — that gap is what shipped today's "08:39 UTC
# since last check" fabrication. See HANDOFF_2026-05-23.md.
_F1_CONFAB_MAX_RETRIES: int = 2


def _pop_confabulated_draft_for_retry(
    messages: list[LLMMessage],
) -> LLMMessage | None:
    """Remove the trailing assistant draft so it can't poison F1 retry.

    The F1 confabulation backstop re-rolls the brain with a raw-tool-output
    injection. Originally, the bad draft was appended to ``messages`` BEFORE
    the injection, so the retried brain saw its own confabulated paraphrase
    as established context and reproduced the same hallucination
    (2026-05-22 10:09 incident: 3 unverified quotes → retry → 3 unverified
    quotes again).

    This helper pops the trailing assistant message if and only if the last
    message is in fact an assistant turn. Returns the popped message
    (caller may log/preserve it) or ``None`` if nothing was popped — which
    is the safe behavior when the last message is ``user``/``tool``/``system``,
    so we never strip non-draft turns.

    Pure: mutates the provided list in-place; no I/O.
    """
    if not messages:
        return None
    if messages[-1].role != "assistant":
        return None
    return messages.pop()

# Keywords that indicate the user wants to DO something (not just check status).
# When absent, only core channel tools are injected to reduce tool count.
# Matched with word boundaries to prevent "followers" matching "follow".
_CHANNEL_ACTION_RE = re.compile(
    r"\b(post|send|reply|follow|unfollow|like|unlike|comment|story|reel|"
    r"carousel|delete|move|mark|mute|unmute|forward|image|photo|label|"
    # Upwork verbs — without these "apply on upwork link" routed to core-only
    # tool set and the brain never saw upwork_submit_proposal / *_get_job_details.
    r"apply|submit|propose|proposal|bid|withdraw|hire)\b",
    re.IGNORECASE,
)

# Phrases that indicate the user wants to RESEARCH / FIND contact info
# rather than OPERATE an inbox or messaging account. Email/Instagram MCP
# tools handle send/read/inbox-search — they CAN'T find email addresses
# from the open web. When this regex matches, channel injection for
# email/instagram is suppressed so the agent reaches for web_search +
# mcp_scraper_extract_entities + browser instead. Fixed 2026-04-29 after
# the Valencia-salons bg task got 36 channel-MCP tools (no Google Sheets,
# no scraper search) and could only do 5 stranded web_searches before
# giving up. See plans note in MEMORY.md.
_FIND_CONTACT_RE = re.compile(
    r"\b(find|finding|missing|research|scrape|scraping|extract|extracting|"
    r"search\s+for|lookup|look\s+up|gather|collect|locate|harvest|"
    r"discover|enrich|fill\s+in|complete|add)\s+"
    r"(?:the\s+|all\s+|their\s+|its\s+|some\s+|any\s+|missing\s+|"
    r"contact\s+|business\s+|public\s+|company\s+|website\s+|owner\s+|"
    r"correct\s+)*"
    r"(?:e-?mails?|addresses?|contacts?|phone\s+numbers?|info|details?|"
    r"information)\b"
    r"|\b(?:e-?mails?|addresses?|contacts?|phone\s+numbers?)\s+"
    r"(?:of|for|from)\s+",
    re.IGNORECASE,
)

# Task manager keywords → inject task skills directly
_TASK_KEYWORDS = frozenset({
    # Core task words
    "task", "tasks", "todo", "to-do", "to do",
    # Reminder triggers (including typos)
    "remind", "reminder", "remember", "remeber", "rember", "reminde",
    "remind me", "remember me", "don't forget", "dont forget",
    # Time-based triggers (catches "after 10 minutes", "in 30 minutes")
    " minutes", " minute", " hours", " hour",
    # Task management
    "briefing", "daily briefing", "what do i have", "my tasks",
    "overdue", "upcoming", "someday", "complete task", "done with",
    "add task", "new task", "schedule", "deadline",
    # AI tasks
    "your job", "your task", "your todo", "your todos",
    "do your todos", "do the todo", "work on your", "execute your",
    "do todo list", "work todos", "ai tasks", "agent tasks",
    # Stop/cancel
    "stop tasks", "stop background", "cancel task", "cancel all",
    "stop all", "cancel background", "stop running",
})

# Task skill names to inject when task keywords detected.
# `set_reminder` intentionally NOT in this set — `add_task` is the canonical
# reminder path (auto-creates the heartbeat job, supports relative times,
# Telegram Done/Snooze buttons). Keeping `set_reminder` default-injected
# caused MiniMax to pair `set_reminder` + `add_task` for one user intent
# → duplicate reminders. `set_reminder` is still discoverable via
# search_tools for the rare cron-job-style standalone case.
_TASK_TOOL_NAMES = frozenset({
    "add_task", "list_tasks", "complete_task", "update_task",
    "delete_task", "daily_briefing", "work_todos", "stop_background",
    "schedule_job", "list_jobs", "manage_job",
})

# Budget / expense keywords → inject budget manager tools directly so NL
# control ("set nima budget to 500", "log 40 hosting on nima", "how much
# have I spent on nima") works over Telegram without a search_tools hop.
_BUDGET_KEYWORDS = frozenset({
    "budget", "budgets",
    "expense", "expenses", "spending",
    "spent", "cost", "costs", " spend",
    "how much have i spent", "how much did i spend", "how much spent",
    "recurring expense", "recurring charge", "monthly cost",
    "expense report", "spending report", "set budget",
})

_BUDGET_TOOL_NAMES = frozenset({
    "set_project_budget", "add_project_budget", "add_expense",
    "list_expenses", "expense_report", "add_recurring_expense",
    "set_default_expense_project",
})

# Budget WRITES are quick single-shot ops (one DB row + a fire-and-forget
# LazyBrain mirror), NOT long-running work — so they must stay INLINE and be
# exempt from AUTO-PROMOTE, just like the budget READS. Without this,
# "log 12 on clubbay" hit AUTO-PROMOTE at iter 1 and got shoved into a slow
# background task + verbose Telegram push, turning a <1s log into 44s + 68s
# (2026-05-28 expense-latency incident).
_QUICK_INLINE_BUDGET_WRITES = frozenset({
    "add_expense", "set_project_budget", "add_project_budget",
    "add_recurring_expense", "set_default_expense_project",
})

# Cron / heartbeat job keywords → inject schedule_job/list_jobs/manage_job.
# The brain owns "show / edit / pause / delete background jobs" intents that
# the bare survival keyword "jobs" used to swallow into the gig-economy
# bucket. `set_reminder` removed here too — `add_task` is the canonical
# reminder path (see _TASK_TOOL_NAMES note above).
_CRON_KEYWORDS = frozenset({
    "cron", "cron job", "cron jobs",
    "background job", "background jobs",
    "scheduled job", "scheduled jobs",
    "show jobs", "list jobs", "my jobs",
    "show my jobs", "show my reminders", "list reminders",
    "my reminders", "what's scheduled", "what is scheduled",
    "running jobs", "active jobs", "pause job", "delete job",
    "pause reminder", "delete reminder", "cancel reminder",
    "edit reminder", "edit job", "manage job",
})

_CRON_TOOL_NAMES = frozenset({
    "schedule_job", "list_jobs", "manage_job", "set_reminder",
    # edit_job changes a cron's schedule / instruction / name (manage_job
    # is pause/resume/delete only). Without it here, "edit my cron to run
    # every 30 min" forces a search_tools round-trip to find the tool.
    "edit_job",
})

# MCP management keywords → inject install_mcp_server + connect/list/etc.
# Without this, "connect upwork mcp" / "install instagram mcp" routed the
# brain to search_tools, which ranked `list_mcp_servers` highest (matched
# "mcp" most generically), so the brain showed an empty list and asked the
# user for a URL — even though install_mcp_server auto-installs every
# bundled MCP. Pattern: bundled name token (upwork|instagram|email|…) +
# generic verbs (install|connect|setup|add). Conservative — does NOT fire
# on bare "upwork" alone (could be a job-search query).
_MCP_MGMT_KEYWORDS = frozenset({
    "mcp", "mcp server", "mcp servers", "list mcp", "list mcps",
    "install mcp", "connect mcp", "set up mcp", "setup mcp", "add mcp",
    "remove mcp", "disconnect mcp", "favorite mcp", "unfavorite mcp",
    "reconnect mcp",
    # Bundle-name + verb pairs — matched as substrings so phrasings like
    # "connect upwork mcp" and "install upwork" both fire.
    "install upwork", "connect upwork", "set up upwork", "add upwork",
    "install instagram", "connect instagram", "set up instagram",
    "install whatsapp", "connect whatsapp", "set up whatsapp",
    "install email", "connect email", "set up email",
    "install scraper", "connect scraper",
    "install stripe", "connect stripe",
    "install canva", "connect canva",
    "install n8n", "connect n8n",
    "install workspace", "connect workspace", "google workspace mcp",
    "install claude code", "connect claude code",
    "install taskai", "install lazydoctor",
})

_MCP_MGMT_TOOL_NAMES = frozenset({
    "install_mcp_server", "connect_mcp_server", "list_mcp_servers",
    "add_mcp_server", "disconnect_mcp_server", "remove_mcp_server",
    "favorite_mcp_server", "unfavorite_mcp_server",
})

# Contact-store keywords → inject find/save/update/list/sync_macos_contacts.
# Detection covers both contact MANAGEMENT phrases ("save Buchvardi's number",
# "sync my contacts") and the messaging-with-name pattern ("tell Buchvardi",
# "DM Marcos"). Channel-keyword matches separately also force-inject
# find_contact (see _MESSAGING_CHANNELS below) so the brain always resolves
# names before whatsapp_send / instagram_send / email_send.
_CONTACT_KEYWORDS = frozenset({
    "contact", "contacts", "address book", "phonebook", "phone book",
    "save contact", "find contact", "look up contact", "lookup contact",
    "his number", "her number", "their number", "phone number of",
    "his email", "her email", "their email", "email of",
    "his instagram", "her instagram", "instagram of",
    "sync contacts", "sync my contacts", "sync macos contacts",
    "add to contacts", "save number", "save phone",
})

_CONTACT_TOOL_NAMES = frozenset({
    "find_contact", "save_contact", "update_contact",
    "list_contacts", "sync_macos_contacts",
})

# Channels where the brain almost always needs to resolve a name to a
# verified handle before sending. When any of these match, find_contact
# is force-injected on top of the channel MCP tools.
_MESSAGING_CHANNELS: frozenset[str] = frozenset({
    "whatsapp", "instagram", "email", "telegram",
})

# Survival/job keywords → inject search_jobs + survival tools directly.
# NOTE: bare "jobs" intentionally removed — it overloaded with cron-job
# intent. Specific phrases ("find job", "find work", "apply job",
# "freelance", "gig", "gigs") still cover the gig-economy case.
_SURVIVAL_KEYWORDS = frozenset({
    "freelance", "gig", "gigs",
    "find work", "find job", "search job", "apply job", "apply for",
    "survival mode", "survival status", "skills profile",
    "start gig", "submit deliverable", "invoice client",
})

# Survival skill names to inject when job keywords detected
_SURVIVAL_TOOL_NAMES = frozenset({
    "search_jobs", "apply_job", "survival_mode", "survival_status",
    "set_skills_profile", "review_deliverable",
    "start_gig", "submit_deliverable", "invoice_client",
})

# "Find matching jobs on upwork", "show me 5 upwork jobs", "search jobs on
# upwork" — bare "jobs" was pulled from _SURVIVAL_KEYWORDS (it overloaded with
# cron-job intent), so these never injected `search_jobs`. With no job-search
# tool in the set the brain fell back to the raw `browser` tool and tried to
# scroll Upwork's search grid, where Input.dispatchMouseEvent times out on the
# heavy DOM → the turn got AUTO-PROMOTE'd to a Telegram-bound background task
# (2026-06-03 incident, recurred 10+ times that day).
#
# Disambiguation from cron "jobs": require BOTH the canonical upwork token
# (typo-tolerant, shared with channel routing) AND a freelance-work token. The
# upwork token is what separates "find jobs on upwork" (freelance search) from
# "list my cron jobs" (scheduler). Route to the `search_jobs` skill — it wraps
# upwork_search_jobs (NO browser scroll), is already read-only, so the turn
# stays inline and answers in the channel it came from.
_JOB_WORK_TOKEN_RE = re.compile(
    r"\b(jobs?|work|gigs?|projects?|contracts?)\b", re.IGNORECASE,
)


def _is_upwork_job_search(text: str) -> bool:
    """True when the message is an Upwork freelance job search.

    Reuses the canonical (typo-tolerant) upwork matcher so "upwokr" etc. are
    handled in exactly one place.
    """
    if not text:
        return False
    return bool(
        _HAND_TUNED_CHANNEL_KEYWORDS["upwork"].search(text)
        and _JOB_WORK_TOKEN_RE.search(text)
    )

# n8n workflow automation — TIGHT keywords only to avoid false positives
# "watch for" removed — overlaps with watch_messages/watch_site in _BASE_TOOL_NAMES
_N8N_KEYWORDS = frozenset({
    "n8n", "automate", "automation", "every day at", "every morning",
    "when i get", "automatically", "set up a flow",
    "recurring", "auto post", "workflow",
})

_N8N_TOOL_NAMES = frozenset({
    "n8n_status", "n8n_list_workflows", "n8n_create_workflow",
    "n8n_manage_workflow", "n8n_run_workflow", "n8n_list_executions",
    "n8n_get_workflow", "n8n_update_workflow",
    "n8n_list_credentials", "n8n_get_execution",
    "n8n_create_credential", "n8n_delete_credential",
    "n8n_google_sheets_setup", "n8n_google_oauth_setup",
    "n8n_google_services_setup",
    "n8n_test_workflow", "n8n_search_templates",
    "n8n_install_template", "n8n_list_webhooks",
    # One-shot task runner — referenced from n8n_create_workflow's
    # cheat sheet. Was missing from this bundle; without it the brain
    # would narrate `<invoke name="n8n_run_task">…</invoke>` as text
    # instead of calling the tool. (logged 2026-04-22).
    "n8n_run_task",
})

# Scraper keywords → inject mcp-scraper tools. Triggered for "scrape /
# crawl / extract email / extract phone / find contact" intents. Without
# this, the agent burns iterations on web_search + browser before
# remembering scraper exists. Matched with word boundaries.
_SCRAPER_KEYWORDS_RE = re.compile(
    r"\b(scrape|scraper|scraping|crawl|crawler|crawling|extract\s+(?:email|phone|contact|entit)|"
    r"find\s+(?:emails?|phones?|contacts?)|fetch\s+page|page\s+content|markdown\s+of\s+(?:this|the))\b",
    re.IGNORECASE,
)

# Canonical scraper-pool tool names. The MCP bridge registers the pool
# under stable names (no UUID prefix) so the keyword-injection path is
# a direct registry lookup — no suffix matching required. Subset of the
# 19 tools the scraper exposes — the high-leverage ones for LazyClaw's
# "find contact / read page" needs. Search tools (search_google,
# batch_search_google) included so the brain can call them explicitly.
_SCRAPER_MCP_TOOL_NAMES: tuple[str, ...] = (
    "mcp_scraper_extract_entities",
    "mcp_scraper_crawl_url",
    "mcp_scraper_deep_crawl_site",
    "mcp_scraper_crawl_url_with_fallback",
    "mcp_scraper_intelligent_extract",
    "mcp_scraper_batch_crawl",
    "mcp_scraper_search_and_crawl",
    "mcp_scraper_search_google",
    "mcp_scraper_batch_search_google",
)

# Suffixes of the czlonkowski/n8n-mcp tools we care about when n8n
# keywords fire. The MCP bridge prefixes every tool with
# `mcp_<server_id>_`, so we match on suffix instead of full name.
# Keeping this tight (6 tools, not the full 39 exposed by n8n-mcp)
# protects the tool budget — agent already runs with ~36 tools on
# n8n turns. These six cover: discover → describe → validate → find
# example, which is the full progressive-disclosure flow.
_N8N_MCP_TOOL_SUFFIXES: tuple[str, ...] = (
    "_search_nodes",
    "_get_node",
    "_validate_node",
    "_validate_workflow",
    "_search_templates",
    "_get_template",
)
_N8N_MCP_SERVER_NAME = "n8n-nodes"

# Google Workspace — auto-inject google_run_task + relevant workspace-mcp tools
# when the user message mentions Sheets / Drive / Gmail / Calendar. Without this
# the brain can only reach Google by `search_tools("google")` first, which it
# usually skips in favor of `delegate(specialist="browser", ...)` — and the
# browser specialist has no Google tools, so it ends up driving Chrome at
# sheets.google.com. See ADR-0003.
_GOOGLE_KEYWORDS = frozenset({
    "google sheet", "google sheets", "spreadsheet", "spreadsheets",
    "google drive", "drive folder", "drive file",
    "gmail", "google calendar", "calendar event",
    "google doc", "google docs", "google workspace",
    "add row", "append row", "create sheet", "share sheet",
})

# Static skills exposed by lazyclaw/skills/builtin/google_direct.py
_GOOGLE_TOOL_NAMES = frozenset({
    "google_run_task",
    "google_project_planning_kickoff",
})

# Workspace-mcp tools are registered as `mcp_<uuid>_<name>`. Suffix-match
# the relevant subset so the brain sees the right tools without flooding
# the schema with all 49 exposed by workspace-mcp.
_GOOGLE_MCP_TOOL_SUFFIXES: tuple[str, ...] = (
    # Sheets
    "_list_spreadsheets", "_get_spreadsheet_info", "_read_sheet_values",
    "_modify_sheet_values", "_create_spreadsheet", "_create_sheet",
    "_append_table_rows",
    # Drive
    "_search_drive_files", "_get_drive_file_content", "_list_drive_items",
    "_create_drive_folder", "_create_drive_file",
    # Gmail
    "_search_gmail_messages", "_get_gmail_message_content",
    "_send_gmail_message", "_draft_gmail_message",
    # Calendar
    "_list_calendars", "_get_events", "_manage_event", "_query_freebusy",
)
_GOOGLE_MCP_SERVER_NAME = "workspace-mcp"

# Channel name → bundled MCP server name (for on-demand connect)
_CHANNEL_TO_MCP: dict[str, str] = {
    "whatsapp": "mcp-whatsapp",
    "instagram": "mcp-instagram",
    "email": "mcp-email",
}

# Max chars for a tool result before truncation (prevents MCP JSON blowup).
# 4000 chars is the right default for general-purpose tools that might
# return mega-arrays (50+ contacts, scraped page snapshots, etc.) where
# the brain only needs a summary.
_MAX_TOOL_RESULT_CHARS = 4000

# Channel-read tools are the exception: the WHOLE conversation IS the
# thing the brain needs, and a 4K cap silently drops everything past
# the first 3-5 messages. The 2026-05-24 incident: brain's
# upwork_get_conversation returned a 20-bubble JSON (~10.9 KB), the
# 4K cap chopped it to 4 KB with the last 6598 chars (15+ messages)
# replaced by a single `[truncated N chars]` marker — brain quoted
# only the first 2-3 messages it could see and the user thought the
# brain was hallucinating "no new messages from James" when really
# the tool result never reached the LLM.
#
# 50K (~12k tokens) is a safer ceiling for channel reads — covers
# ~100 messages of a typical chat without blowing up cache budget.
_MAX_TOOL_RESULT_CHARS_CHANNEL_READ = 50000

# Substring match against the tool name to pick which cap applies.
# Mirrored from f1_content_verifier._CHANNEL_READ_TOOL_PATTERNS so
# both modules agree on what counts as a channel read.
_CHANNEL_READ_TOOL_NAME_PATTERNS: tuple[str, ...] = (
    "upwork_get_conversation",
    "upwork_get_messages",
    "upwork_last_conversation",
    "upwork_inbox_check",
    "whatsapp_read",
    "whatsapp_get_messages",
    "whatsapp_get_chat",
    "whatsapp_list_chats",
    "email_read",
    "email_get_messages",
    "instagram_read_dms",
    "instagram_get_dms",
    "instagram_get_messages",
    "instagram_get_notifications",
    "telegram_get_messages",
)


def _is_channel_read_tool_name(tool_name: str | None) -> bool:
    """True iff ``tool_name`` matches a channel-read pattern.

    Substring match so MCP-wrapped names like
    ``mcp_<hash>_upwork_get_conversation`` still register.
    """
    if not tool_name:
        return False
    lowered = tool_name.lower()
    return any(pat in lowered for pat in _CHANNEL_READ_TOOL_NAME_PATTERNS)


# ── Browser fallback after repeated channel-MCP failures ────────────────
# Channel MCP tools (mcp-upwork, mcp-whatsapp, ...) drive the user's
# signed-in Brave internally, so the turn-start suppression of the native
# ``browser`` skill (MCP-first — see _suppressed_tool_names) is normally
# correct. But when the channel tool itself fails repeatedly (stale CDP
# handle, Cloudflare check, dead subprocess) the brain has NO path to the
# page at all: the late-inject path refuses to resurrect suppressed
# tools. After _BROWSER_FALLBACK_AT_FAILURES consecutive failures of the
# same channel tool we lift the suppression for ``browser`` /
# ``use_host_browser`` (NOT ``run_command`` — shell stays forbidden as a
# workaround) and hand the brain the ``browser`` schema as the sanctioned
# fallback. The native browser drives the SAME signed-in Brave profile
# the MCP uses, so it passes Cloudflare with cookies intact. Threshold 2
# + three-strikes at 3 = the brain gets one LLM iteration with the
# browser before the graceful handoff fires.
_BROWSER_FALLBACK_AT_FAILURES = 2

# Channel-prefix shapes (covers writes like ``upwork_send_message`` that
# the read-only pattern list above misses). Substring match so
# MCP-wrapped names like ``mcp_<uuid>_upwork_send_message`` register.
# Telegram is deliberately absent — the native bot-API adapter is not
# browser-backed, so a browser fallback can't help it.
_CHANNEL_MCP_TOOL_NAME_MARKERS: tuple[str, ...] = (
    "upwork_", "whatsapp_", "instagram_", "email_",
)


def _is_channel_mcp_tool_name(tool_name: str | None) -> bool:
    """True iff ``tool_name`` looks like a channel MCP tool.

    Union of the channel-read predicate and the channel-prefix markers
    so both reads (``upwork_get_conversation``) and writes
    (``upwork_send_message``) register.
    """
    if not tool_name:
        return False
    lowered = tool_name.lower()
    if "telegram_" in lowered:
        # Native bot-API adapter, not browser-backed — a browser
        # fallback can't help it (matches the marker-list intent even
        # though the read-predicate union below knows telegram reads).
        return False
    if _is_channel_read_tool_name(lowered):
        return True
    return any(m in lowered for m in _CHANNEL_MCP_TOOL_NAME_MARKERS)


def _should_inject_browser_fallback(
    tool_name: str | None,
    failure_count: int,
    suppressed_tool_names: set[str] | frozenset[str],
    already_injected: bool,
) -> bool:
    """Decide whether the browser-fallback re-injection should fire.

    Fires when a channel-MCP tool has failed
    ``_BROWSER_FALLBACK_AT_FAILURES``+ times this turn while ``browser``
    is still channel-suppressed — and at most once per turn.
    """
    if already_injected:
        return False
    if failure_count < _BROWSER_FALLBACK_AT_FAILURES:
        return False
    if "browser" not in suppressed_tool_names:
        return False
    return _is_channel_mcp_tool_name(tool_name)


def _extract_tool_names_from_search_result(result: str) -> list[str]:
    """Extract tool names from search_tools result text (bold **name**: pattern).

    Tool names may contain hyphens (UUID-prefixed MCP tools like
    mcp_c2d0f293-ccf7-4987-a4dd-7edadc97261f_instagram_read_profile).
    """
    return re.findall(r"\*\*([\w-]+)\*\*:", result)


def _compact_history(
    history: list[LLMMessage], keep_recent: int = 4,
) -> list[LLMMessage]:
    """Keep last N user/assistant exchanges in full, compact older ones.

    Old messages become 1-line summaries. Tool messages are dropped.
    Returns new list (immutable pattern).
    """
    if not history:
        return history

    # Split into user/assistant pairs and tool messages
    # Keep system messages (summaries) as-is
    recent: list[LLMMessage] = []
    old: list[LLMMessage] = []

    # Count user/assistant messages from the end
    user_assistant_count = 0
    for msg in reversed(history):
        if msg.role in ("user", "assistant") and not msg.tool_calls:
            user_assistant_count += 1

    # Walk forward, putting old messages into compact and recent into full
    seen_recent = 0
    cutoff = max(0, user_assistant_count - keep_recent)
    ua_idx = 0

    for msg in history:
        # System messages (summaries) — always keep
        if msg.role == "system":
            recent.append(msg)
            continue

        # Tool messages from old conversations — drop entirely
        if msg.role == "tool":
            if ua_idx < cutoff:
                continue  # drop old tool results
            recent.append(msg)
            continue

        # Assistant with tool_calls from old conversations — compact
        if msg.role == "assistant" and msg.tool_calls:
            if ua_idx < cutoff:
                tools_used = ", ".join(tc.name for tc in msg.tool_calls)
                summary = f"[Used {tools_used}]"
                if msg.content:
                    summary = msg.content[:100] + f" [{tools_used}]"
                old.append(LLMMessage(role="assistant", content=summary))
                continue
            recent.append(msg)
            continue

        # User/assistant messages
        if msg.role in ("user", "assistant"):
            ua_idx += 1
            if ua_idx <= cutoff:
                # Compact old messages
                content = (msg.content or "")[:100]
                if len(msg.content or "") > 100:
                    content += "..."
                old.append(LLMMessage(role=msg.role, content=content))
            else:
                recent.append(msg)
            continue

        recent.append(msg)

    # If there are old messages, combine into a single summary
    if old:
        summary_lines = []
        for msg in old[-6:]:  # Keep last 6 compacted messages max
            prefix = "User" if msg.role == "user" else "Assistant"
            summary_lines.append(f"{prefix}: {msg.content}")
        compact_msg = LLMMessage(
            role="system",
            content="--- Earlier conversation (compacted) ---\n" + "\n".join(summary_lines),
        )
        return [compact_msg] + recent

    return recent


# Stale provider-error pattern + neutral marker. Canonical definitions live in
# context_journal_filter so the recent-window filter (here) and the
# pre-summarization filter (compressor) share ONE source of truth.
from lazyclaw.runtime.context_journal_filter import (  # noqa: E402
    STALE_PROVIDER_ERROR_RE as _HISTORY_ERROR_RE,
    STALE_TOOL_ERROR_MARKER as _STALE_TOOL_ERROR_MARKER,
)


def _is_rate_limit_exception(exc: BaseException) -> bool:
    """Check if an LLM provider exception indicates a rate limit / 429.

    Catches MiniMax Token Plan 429s (`rate_limit_error`, code 2062) and
    generic provider 429s. We use this to decide whether to auto-escalate
    to the configured fallback model instead of bailing the whole turn
    with a JSON dump in the user's chat.
    """
    msg = str(exc).lower()
    return (
        "429" in msg
        or "rate_limit_error" in msg
        or "rate limit" in msg
        or "rate-limit" in msg
        or "too many requests" in msg
        or "(2062)" in msg  # MiniMax Token Plan signature
    )


def _filter_error_messages(history: list[LLMMessage]) -> list[LLMMessage]:
    """Strip/neutralize stale error blobs from PRIOR turns.

    Two shapes (the brain otherwise treats a past failure as live and either
    references it or refuses to re-attempt):
      * ``assistant`` message that is just an error dump  → DROP it.
      * ``tool`` RESULT carrying a provider/credit/auth/rate-limit error
        (matches :data:`_HISTORY_ERROR_RE`, e.g. ``Error code: 400 … credit
        balance too low``) → keep the message so tool_use↔tool_result pairing
        survives, but REPLACE its content with a neutral marker.

    Operates only on loaded HISTORY (see call site in
    ``_process_message_inner``); the current turn's live tool results are
    appended later and are never touched here. Returns a new list (immutable).
    """
    result = []
    for msg in history:
        content = msg.content or ""
        if (
            msg.role == "assistant"
            and content
            and _HISTORY_ERROR_RE.search(content)
        ):
            continue  # Drop the error assistant message
        if (
            msg.role == "tool"
            and content
            and _HISTORY_ERROR_RE.search(content)
        ):
            result.append(
                LLMMessage(
                    role="tool",
                    content=_STALE_TOOL_ERROR_MARKER,
                    tool_call_id=msg.tool_call_id,
                    tool_calls=msg.tool_calls,
                )
            )
            continue
        result.append(msg)
    return result


def _prune_old_tool_results(
    messages: list[LLMMessage], keep_last_n: int = 2,
) -> list[LLMMessage]:
    """Replace old tool results with 1-line summaries. Keep last N in full.

    Returns new list (immutable pattern — never mutate input).
    Keeps tool_call_id intact for OpenAI validation.
    """
    tool_indices = [i for i, m in enumerate(messages) if m.role == "tool"]

    if len(tool_indices) <= keep_last_n:
        return messages

    to_compress = set(tool_indices[:-keep_last_n])

    result: list[LLMMessage] = []
    for i, msg in enumerate(messages):
        if i in to_compress:
            content = msg.content or ""
            summary = content[:150].replace("\n", " ").strip()
            if len(content) > 150:
                summary += "..."
            result.append(LLMMessage(
                role=msg.role,
                content=f"[Previous result truncated] {summary}",
                tool_call_id=msg.tool_call_id,
            ))
        else:
            result.append(msg)

    return result


def _cap_tool_result(result: str, tool_name: str | None = None) -> str:
    """Truncate oversized tool results to save tokens.

    MCP tools can return huge JSON arrays (50+ contacts, full page
    snapshots). The general-purpose cap of 4K chars keeps the brain
    focused on a summary.

    Channel-read tools (upwork/whatsapp/email/instagram/telegram) are
    the exception — the whole conversation IS the load-bearing data,
    so they get a much larger cap (50K ≈ ~12k tokens, ~100 messages).
    Without this exception the 2026-05-24 incident reproduces: the
    brain's `upwork_get_conversation` result (~10.9 KB JSON for a
    20-bubble thread) was chopped to 4 KB and the user saw "no new
    messages" replies because the truncated marker was where the new
    messages should have been.

    Pure. ``tool_name`` defaults to None for legacy callers — those
    still get the 4K cap (no behavior change for non-channel paths).
    """
    if not result:
        return result
    cap = (
        _MAX_TOOL_RESULT_CHARS_CHANNEL_READ
        if _is_channel_read_tool_name(tool_name)
        else _MAX_TOOL_RESULT_CHARS
    )
    if len(result) <= cap:
        return result
    truncated = result[:cap]
    remaining = len(result) - cap
    return f"{truncated}\n... [truncated {remaining} chars]"


def _strip_tool_messages(history: list[LLMMessage]) -> list[LLMMessage]:
    """Remove tool-related messages from history for tool-free conversations.

    Converts assistant messages with tool_calls to plain text,
    and drops tool role messages entirely. This prevents GPT-5 from
    hallucinating tool calls when it sees old tool patterns in history.
    """
    result = []
    for msg in history:
        if msg.role == "tool":
            continue  # Drop tool result messages
        if msg.role == "assistant" and msg.tool_calls:
            # Convert tool-calling assistant message to plain text
            parts = []
            if msg.content:
                parts.append(msg.content)
            for tc in msg.tool_calls:
                parts.append(f"[Used {tc.name}]")
            result.append(LLMMessage(
                role="assistant",
                content=" ".join(parts) if parts else "[Used tools]",
            ))
        else:
            result.append(msg)
    return result


def _wants_any_tools(message: str) -> bool:
    """Decide if tools should be available for this message.

    Inverted approach: assume tools UNLESS the message is clearly just chat.
    The LLM is smart enough to not call tools on "what's my name?" even
    if tools are available. We only block tools for pure greetings/acks.
    """
    lower = message.lower().strip()
    # Action confirmations — these continue a prior tool-using conversation.
    # MUST check before the short-message filter, because "do it" is <8 chars.
    if _ACTION_CONFIRM_PATTERN.match(lower):
        return True
    # Very short messages (under 5 chars) — greetings like "hi", "ok", "ty"
    if len(lower) < 5:
        return False
    # Known chat-only patterns — no tools needed
    if _CHAT_ONLY_PATTERN.match(lower):
        return False
    # Everything else: give the LLM tools and let it decide
    return True


# ── Fast Dispatch ─────────────────────────────────────────────────────
# Tools that trigger automatic delegation to background specialists.

# Tools that trigger background dispatch. Browser is NOT here —
# browser tasks often need user interaction (login, captcha, 0-element pages).
# They run foreground so stuck detection can fall back to the user.
HEAVY_TOOLS: frozenset[str] = frozenset({
    "run_command", "read_file", "write_file",
})

# MCP tool base names that should trigger fast dispatch when they appear
# in multi-step chains (delete, move, organize = slow IMAP operations).
# Scraper tools land here too: crawl_url_with_fallback can iterate 7
# bypass stages and lock the lane queue for 30+ seconds otherwise. Keep
# this list tight — only tools that are GUARANTEED slow on a typical
# call. Cheap reads (crawl_url single page on a fast site) stay
# foreground so the user gets the answer inline.
_HEAVY_MCP_BASES: frozenset[str] = frozenset({
    "email_delete", "email_move", "email_mark", "email_create_label",
    # Scraper — multi-stage / multi-target work
    "crawl_url_with_fallback", "deep_crawl_site", "multi_url_crawl",
    "batch_crawl", "batch_search_google", "batch_extract_youtube_transcripts",
    "search_and_crawl", "intelligent_extract", "enhanced_process_large_content",
})

# When a user-approved plan has at least this many concrete steps, the
# plan gate auto-promotes it to a `run_background` dispatch instead of
# executing inline. Frees the lane queue so the user can keep chatting
# while the work runs; final result pings via Telegram + chat.
# Lowered 6 → 4 (Round 2): brain-as-dispatcher pattern requires that any
# plan beyond a quick lookup-and-write be handed to a worker. 1–3 step
# plans stay inline (snappy); 4+ step plans dispatch upfront.
_AUTO_BACKGROUND_STEP_THRESHOLD: int = 4
_AUTO_BG_PLAN_PREFIX: str = "AUTO_BG:"


def _handle_instant_command(
    message: str, team_lead: TeamLead | None, task_runner=None,
    user_id: str | None = None,
) -> str | None:
    """Handle status and cancel commands without LLM. Returns response or None."""
    if team_lead is None:
        return None
    stripped = message.strip()
    if TeamLead.is_status_query(stripped):
        return team_lead.format_status()
    is_cancel, target = TeamLead.is_cancel_command(stripped)
    if is_cancel:
        task_id = team_lead.find_cancel_target(target)
        if task_id:
            if task_runner and user_id:
                from lazyclaw.runtime.aio_helpers import fire_and_forget

                fire_and_forget(
                    task_runner.cancel(task_id, user_id),
                    name=f"cancel-{task_id}",
                )
            team_lead.cancel(task_id)
            return f"Cancelled task matching \"{target}\""
        return f"No running task matching \"{target}\""
    return None


_VERIFY_RE = re.compile(r"^/(confirm|reject)\b", re.IGNORECASE)


async def _handle_verification_command(
    message: str, config, user_id: str | None,
) -> str | None:
    """Parse `/confirm` / `/reject` and flip the latest skill shape.

    Returns a short user-facing reply on hit, or None when the message
    isn't a verification command. Never raises — verification flips are
    fire-and-forget and the user sees the same canned reply either way.
    """
    if not user_id or not message:
        return None
    m = _VERIFY_RE.match(message.strip())
    if not m:
        return None
    action = m.group(1).lower()
    try:
        from lazyclaw.runtime.lesson_verifier import (
            confirm_latest_shape,
            reject_latest_shape,
        )
        if action == "confirm":
            note_id = await confirm_latest_shape(config, user_id)
            if note_id:
                return "Confirmed — last skill shape marked verified."
            return "Nothing pending to confirm."
        # reject
        note_id = await reject_latest_shape(config, user_id)
        if note_id:
            return "Rejected — last skill shape flagged as known-bad."
        return "Nothing recent to reject."
    except Exception:
        logger.debug("verification command failed", exc_info=True)
        return None


async def _extract_and_store_lesson(
    eco_router, config, user_id: str, message: str, recent: list,
) -> None:
    """Fire-and-forget: extract a lesson from user correction and store it.

    Never raises — all errors caught and logged. Uses gpt-5-mini for cost.
    """
    if eco_router is None:
        return
    try:
        from lazyclaw.runtime.lesson_extractor import extract_lesson
        from lazyclaw.runtime.lesson_store import store_lesson

        lesson = await extract_lesson(
            eco_router, user_id, message, recent,
        )
        if lesson:
            await store_lesson(config, user_id, lesson)
    except Exception as e:
        logger.debug("Lesson extraction background task failed: %s", e)


class _PlanRejected(Exception):
    """Raised when the user rejects a plan at the approval gate."""

    def __init__(self, reason: str | None) -> None:
        self.reason = reason
        super().__init__(reason or "plan rejected")


# Matches internal MCP tool names: mcp_<uuid4>_<bare_tool_name>.
# See lazyclaw/mcp/bridge.py:14,67 and mcp/manager.py:485 where server_id = uuid4().
_MCP_TOOL_NAME_RE = re.compile(
    r"^mcp_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})_(.+)$"
)


def _parse_mcp_name(name: str) -> tuple[str, str] | None:
    """Return (server_id, bare_tool_name) if `name` is an MCP tool, else None."""
    m = _MCP_TOOL_NAME_RE.match(name)
    if not m:
        return None
    return m.group(1), m.group(2)


def _build_hallucination_correction(
    bad_name: str, valid_names: set[str], registry,
) -> str:
    """Craft a correction message that surfaces the RIGHT alternatives.

    Handles two shapes:
      - Plain hallucinated name (`foo_bar`) → search_tools(bar).
      - MCP-shaped name (`mcp_<uuid>_delete_spreadsheet`) → list actual
        sibling tools on the same server so the LLM sees what is real.
    """
    parsed = _parse_mcp_name(bad_name)
    if parsed is not None and registry is not None:
        server_id, bare = parsed
        prefix = f"mcp_{server_id}_"
        sibling_names = registry.list_names_by_prefix(prefix)
        # Surface the short form (strip the uuid prefix) so the LLM sees
        # familiar names.
        siblings_bare = [n[len(prefix):] for n in sibling_names][:40]
        available_display = ", ".join(siblings_bare) if siblings_bare else "(none)"
        return (
            f"[SYSTEM: The tool '{bad_name}' does NOT exist. "
            f"You called a non-existent operation on MCP server "
            f"{server_id[:8]}…. Tools ACTUALLY available on that server: "
            f"{available_display}. "
            f"If '{bare}' is what you need and it's not in that list, "
            f"the MCP server does not support it — try a native skill "
            f"(e.g. google_run_task for Drive/Sheets delete or trash ops) "
            f"or tell the user the operation is unsupported.]"
        )

    # Plain hallucination — use the last name segment as a search hint
    # instead of splitting at the first '_' (old bug: 'mcp_x'.split('_')[0] = 'mcp').
    bare_hint = bad_name.rsplit("_", 1)[-1] or bad_name
    _avail = sorted(valid_names - {"search_tools", "delegate"})[:10]
    return (
        f"[SYSTEM: The tool '{bad_name}' is not available in your current toolset. "
        f"Use search_tools('{bare_hint}') to discover available tools — "
        f"it may exist under a different name. "
        f"Your current tools: {', '.join(_avail)}. "
        f"Try search_tools FIRST, then use what you find, or respond with text if nothing fits.]"
    )


def _correction_hint_for_user(bad_name: str, registry) -> str:
    """Short user-facing hint pointing at the real alternative."""
    parsed = _parse_mcp_name(bad_name)
    if parsed is None:
        return (
            "Try rephrasing the request, or tell me explicitly which tool "
            "to use."
        )
    _server_id, bare = parsed
    # Known mappings: when the LLM wanted a delete/trash op on workspace-mcp,
    # point at the native direct-API path.
    if bare in {"delete_spreadsheet", "trash_spreadsheet", "delete_drive_item",
                "trash_drive_item", "delete_file", "trash_file"}:
        return (
            "The Google MCP server exposes create/read/list only. For "
            "delete or trash, I can use `google_run_task` with "
            "`task_type='trash_drive_item'`. Confirm and I'll proceed."
        )
    return (
        "That MCP server doesn't expose this operation. Tell me explicitly "
        "what you want done and I'll find a tool that actually fits."
    )


async def _load_auto_plan_setting(user_id: str) -> bool:
    """Read the user's auto_plan toggle. Default True (plan mode ON)."""
    try:
        from lazyclaw.config import load_config
        from lazyclaw.db.connection import db_session
        config = load_config()
        async with db_session(config) as conn:
            cur = await conn.execute(
                "SELECT auto_plan FROM users WHERE id = ?", (user_id,),
            )
            row = await cur.fetchone()
            if row is None or row[0] is None:
                return True
            # SQLite stores booleans as 0/1.
            return bool(row[0])
    except Exception:
        # Missing column on older DBs — default to ON.
        return True


class Agent:
    def __init__(
        self,
        config: Config,
        router: LLMRouter,
        registry: SkillRegistry | None = None,
        eco_router: EcoRouter | None = None,
        permission_checker=None,
        task_runner=None,
        team_lead: TeamLead | None = None,
        depth: int = 0,
    ) -> None:
        self.config = config
        self.router = router
        self.eco_router = eco_router or EcoRouter(config, router)
        self.registry = registry
        # Kept for the research-fan-out pre-plan pass (ADR-0005 Phase 4),
        # which needs a checker to run read-only research specialists.
        self._permission_checker = permission_checker
        self.executor = (
            ToolExecutor(
                registry,
                permission_checker=permission_checker,
                timeout=config.tool_timeout,
                config=config,
            )
            if registry
            else None
        )
        self._task_runner = task_runner
        self._team_lead = team_lead
        # Sub-agent nesting depth. 0 = main/foreground agent. 1 = direct
        # background sub-agent. task_runner._execute bumps this when
        # constructing a child Agent. submit() refuses spawn at depth
        # >= MAX_TASK_DEPTH so the brain sees a clean error instead of
        # the legacy "not configured" 3-strike loop.
        self._depth = depth
        # Wire TeamLead into the read-only background_status skill so the
        # brain can query live task progress on demand. Best-effort —
        # missing skill (older builds) silently no-ops.
        if self.registry is not None and team_lead is not None:
            bg_status = self.registry.get("background_status")
            if bg_status is not None and hasattr(bg_status, "attach_team_lead"):
                bg_status.attach_team_lead(team_lead)

    async def _run_plan_gate(
        self,
        *,
        user_id: str,
        message: str,
        messages_so_far: list,
        cb,
        cancel_token,
        enumeration_hint: bool = False,
    ) -> str:
        """Generate a user-facing plan, show it, block until approval.

        Returns the plan text (already approved). Raises `_PlanRejected`
        if the user rejects or times out.
        """
        from lazyclaw.runtime import plan_checkpoint
        from lazyclaw.runtime.taor import (
            make_user_facing_plan_prompt, parse_plan_steps,
        )
        # ROLE_BRAIN already imported at module top (line 12). Reuse it.

        # Build a cheap plan-only LLM call (no tools). We reuse the same
        # message history for context but append a plan-only instruction.
        tool_names: list[str] = []
        if self.registry is not None:
            try:
                tool_names = [
                    t.get("function", {}).get("name", "")
                    for t in self.registry.list_tools()
                ]
                tool_names = [n for n in tool_names if n][:40]
            except Exception:
                tool_names = []

        # Pre-plan research pass — parallel zero-brain-LLM lookups over
        # LazyBrain, past skill shapes, and (when the message references a
        # file) the codebase. Findings are injected into the plan prompt
        # so the model has context BEFORE drafting.
        research_findings: str = ""
        try:
            from lazyclaw.runtime.plan_research import gather_plan_research
            research_findings = await gather_plan_research(
                self.config, user_id, message,
            )
        except Exception:
            logger.debug("plan_research pre-pass failed (non-fatal)", exc_info=True)

        # Deep research-first fan-out (ADR-0005 Phase 4): read-only code +
        # web research specialists run in parallel BEFORE the plan is drafted
        # so the brain never plans from memory. Opt-in via
        # LAZYCLAW_RESEARCH_FANOUT — each runs a full agent loop, so it adds
        # latency; default-off keeps plan-gate timing unchanged.
        import os as _os
        if (
            _os.environ.get("LAZYCLAW_RESEARCH_FANOUT", "").strip().lower()
            in ("1", "true", "yes", "on")
            and self.registry is not None
            and self._permission_checker is not None
        ):
            try:
                from lazyclaw.runtime.research_fanout import (
                    gather_specialist_research,
                )
                deep = await gather_specialist_research(
                    self.config, user_id, message,
                    registry=self.registry,
                    eco_router=self.eco_router,
                    permission_checker=self._permission_checker,
                )
                if deep:
                    research_findings = (
                        f"{research_findings}\n\n{deep}".strip()
                    )
            except Exception:
                logger.debug(
                    "research_fanout pre-pass failed (non-fatal)", exc_info=True
                )

        plan_instruction = make_user_facing_plan_prompt(
            message, tool_names,
            enumeration_hint=enumeration_hint,
            research_findings=research_findings,
        )
        # LLMMessage is imported at the top of this module.
        plan_messages = list(messages_so_far) + [
            LLMMessage(role="system", content=plan_instruction),
        ]

        # Clarification-question loop. Capped at 4 rounds to allow
        # research_needed → question → question → plan transitions
        # without ping-pong.
        _THINK_RE = re.compile(r"<think>.*?</think>\s*", flags=re.DOTALL)
        max_rounds = 4
        plan_text: str = ""
        steps: list[str] = []
        for round_idx in range(max_rounds):
            logger.info(
                "Plan gate: requesting plan text from brain (round %d)",
                round_idx,
            )
            plan_resp = await self.eco_router.chat(
                plan_messages, user_id=user_id, role=ROLE_BRAIN,
            )
            plan_text = (plan_resp.content or "").strip()
            if "<think>" in plan_text:
                plan_text = _THINK_RE.sub("", plan_text).strip()
            if not plan_text:
                logger.warning("Plan gate: empty plan — skipping approval")
                return "(empty plan — auto-continuing)"

            if cancel_token.is_cancelled:
                raise _PlanRejected("cancelled before approval")

            # Self-research branch — brain asked for something we can look
            # up ourselves. Run another research pass on the request and
            # re-prompt; never ask the user.
            if plan_text.startswith("RESEARCH_NEEDED:"):
                rq = plan_text[len("RESEARCH_NEEDED:"):].strip()
                logger.info(
                    "Plan gate: brain requested self-research: %s",
                    rq[:120],
                )
                try:
                    from lazyclaw.runtime.plan_research import gather_plan_research
                    extra = await gather_plan_research(self.config, user_id, rq)
                except Exception:
                    extra = ""
                plan_messages.append(LLMMessage(
                    role="system",
                    content=(
                        f"## Additional research for: {rq}\n"
                        f"{extra or '(no results — proceed with the best plan you can.)'}"
                    ),
                ))
                continue

            # Clarifying-question branch.
            if plan_text.startswith("QUESTION:"):
                question = plan_text[len("QUESTION:"):].strip()
                logger.info(
                    "Plan gate: requesting clarification: %s",
                    question[:120],
                )
                await cb.on_event(AgentEvent(
                    "plan_question", question, {"question": question},
                ))
                answer = await plan_checkpoint.request_clarification(
                    user_id=user_id, question=question,
                )
                if answer is None:
                    raise _PlanRejected("question cancelled or timed out")
                logger.info(
                    "Plan gate: got answer (%d chars)", len(answer),
                )
                plan_messages.append(LLMMessage(
                    role="user",
                    content=f"(answer to your question) {answer}",
                ))
                continue

            # Normal plan branch — break out of the round loop.
            steps = parse_plan_steps(plan_text)
            break
        else:
            # Exhausted rounds without a plan.
            raise _PlanRejected(
                "too many clarification rounds — rephrase the request"
            )

        # Stream the plan to the user via the normal chat channel too,
        # so Telegram / CLI users see it even without a plan card.
        await cb.on_event(AgentEvent(
            "plan_pending", plan_text,
            {"steps": steps, "plan": plan_text},
        ))
        logger.info(
            "Plan gate: showing %d-step plan to user %s", len(steps), user_id,
        )

        decision = await plan_checkpoint.request_plan_approval(
            user_id=user_id,
            plan_text=plan_text,
            steps=steps,
        )

        if not decision.approved:
            raise _PlanRejected(decision.reason)

        await cb.on_event(AgentEvent(
            "plan_approved", "Plan approved",
            {"auto_approve_session": decision.auto_approve_session},
        ))
        logger.info(
            "Plan gate: approved (auto_approve_session=%s)",
            decision.auto_approve_session,
        )

        # Auto-promote multi-step plans to background. The brain's first
        # and ONLY tool call becomes run_background — lane queue freed
        # immediately, push notification on completion. Sentinel prefix
        # lets the plan-injection block (in process_message) emit a
        # different system wrapper that does NOT contradict the
        # "first tool call MUST be run_background" override below.
        if len(steps) >= _AUTO_BACKGROUND_STEP_THRESHOLD:
            _escaped_msg = message.replace('"', "'").replace("\n", " ")[:1500]
            plan_text = (
                f"{_AUTO_BG_PLAN_PREFIX}\n"
                f"This task has {len(steps)} steps and will run >30s. "
                f"To keep the chat responsive, it dispatches to a background "
                f"agent instead of running inline.\n\n"
                f"Original plan (for the background agent's reference):\n"
                f"{plan_text}\n\n"
                f"YOUR INSTRUCTION (NON-NEGOTIABLE):\n"
                f"  1. Your FIRST and ONLY tool call MUST be:\n"
                f'       run_background(instruction="{_escaped_msg}")\n'
                f"  2. After it returns, reply with EXACTLY ONE line:\n"
                f"       'Dispatched as background task <id> — I'll ping you "
                f"when it's done. ETA: ~Xm.'\n"
                f"  3. Do NOT execute the steps inline. Do NOT call any other "
                f"tool. The background agent has all your tools and will run "
                f"the plan."
            )
            logger.info(
                "Plan gate: %d steps ≥ threshold %d → auto-promoting to "
                "run_background",
                len(steps), _AUTO_BACKGROUND_STEP_THRESHOLD,
            )
        return plan_text

    async def process_message(
        self,
        user_id: str,
        message: str,
        chat_session_id: str | None = None,
        callback=None,
        channel_context: str | None = None,
        browser_role: str | None = None,
    ) -> str:
        """Public turn entry — serialize live-Brave access, then run the turn.

        Wrapping here covers EVERY caller uniformly (lane handler, gateway,
        web WS, Telegram, background TaskRunner) since they all funnel through
        process_message. The per-(user, role) lock is taken lazily INSIDE the
        turn (only when a browser-driving tool actually runs) and released when
        the turn ends. See runtime/browser_turn_lock.py.

        ``browser_role`` selects the browser lane: ``"background"`` for
        daemon-originated work (watcher/cron/reminder) so it drives its OWN tab
        instead of the user's visible tab, ``"visible"`` for foreground turns.
        The daemon passes it explicitly; when unset we infer it from the
        message prefix so existing callers stay correct (2026-06-02).
        """
        from lazyclaw.runtime.browser_turn_lock import browser_turn_scope

        role = browser_role or _infer_browser_role(message)
        async with browser_turn_scope(role, user_id=user_id):
            return await self._process_message_inner(
                user_id,
                message,
                chat_session_id=chat_session_id,
                callback=callback,
                channel_context=channel_context,
            )

    async def _process_message_inner(
        self,
        user_id: str,
        message: str,
        chat_session_id: str | None = None,
        callback=None,
        channel_context: str | None = None,
    ) -> str:
        cb = callback or NullCallback()
        cancel_token = CancellationToken()
        if hasattr(cb, 'cancel_token'):
            cb.cancel_token = cancel_token

        logger.info("process_message START: user=%s msg=%s", user_id[:8], message[:40])
        self._nudged_tool_use = False  # Reset per-message nudge flag
        self._nudged_research = False  # Reset research-intent nudge flag

        # Instant commands — no LLM call needed
        instant = _handle_instant_command(message, self._team_lead, self._task_runner, user_id)
        if instant is not None:
            await cb.on_event(AgentEvent(INSTANT_COMMAND, instant, {}))
            await cb.on_event(AgentEvent("done", "Response ready", {}))
            return instant

        # /confirm and /reject — manual verification overrides for the
        # most-recently-touched skill shape. Borrowed from Anki's review
        # gesture: one user-side click flips a card's outcome.
        verify_reply = await _handle_verification_command(
            message, self.config, user_id,
        )
        if verify_reply is not None:
            await cb.on_event(AgentEvent(INSTANT_COMMAND, verify_reply, {}))
            await cb.on_event(AgentEvent("done", "Response ready", {}))
            return verify_reply

        # /streaming on|off|status — Fix J toggle for whether the Web UI
        # surfaces live background-task progress frames (bg_event,
        # bg_tool_call, bg_tool_result, bg_token). background_done /
        # background_failed still always fire so the user sees results.
        try:
            from lazyclaw.runtime.streaming_setting import (
                parse_streaming_command,
                get_bg_streaming,
                set_bg_streaming,
            )
            _stream_cmd = parse_streaming_command(message)
        except Exception:
            logger.debug(
                "streaming_setting import/parse failed; continuing",
                exc_info=True,
            )
            _stream_cmd = None
        if _stream_cmd is not None:
            _action, _flag = _stream_cmd
            if _action == "status":
                _current = await get_bg_streaming(self.config, user_id)
                _reply = (
                    f"Background streaming: "
                    f"{'ON (verbose)' if _current else 'OFF (quiet)'}.\n"
                    "Toggle with `/streaming on` or `/streaming off`."
                )
            else:
                _new = await set_bg_streaming(
                    self.config, user_id, bool(_flag),
                )
                _reply = (
                    f"Background streaming → "
                    f"{'ON' if _new else 'OFF'}. "
                    + (
                        "You'll see live tool calls and progress while "
                        "background tasks run."
                        if _new
                        else "Background tasks run quietly — only the "
                             "final result appears."
                    )
                )
            await cb.on_event(AgentEvent(INSTANT_COMMAND, _reply, {}))
            await cb.on_event(AgentEvent("done", "Response ready", {}))
            return _reply

        # ── Instant dispatch — single-skill 1:1 intents ───────────────
        # Skips full context build + history compress + skill load + brain
        # LLM iteration when the message is a known direct mapping to one
        # skill (e.g. "show jobs", "check upwork inbox"). Conservative
        # table in lazyclaw/runtime/instant_dispatch.py — misses fall
        # through to the standard path silently.
        try:
            from lazyclaw.runtime.instant_dispatch import try_instant_dispatch
            _id_result = await try_instant_dispatch(message, self.registry, user_id)
        except Exception:
            logger.debug("instant_dispatch raised at import/call; continuing", exc_info=True)
            _id_result = None
        if _id_result is not None:
            await cb.on_event(AgentEvent(
                INSTANT_COMMAND,
                _id_result.output,
                {
                    "path": "instant_dispatch",
                    "skill": _id_result.skill_name,
                    "elapsed_ms": _id_result.elapsed_ms,
                },
            ))
            await cb.on_event(AgentEvent("done", "Response ready", {}))
            return _id_result.output

        # ── Plan-mode fix-task gate ──────────────────────────────────────
        # Intercepted before the compound splitter so the user gets one
        # coherent plan dialogue instead of N independent sub-task plans.
        # On approval the plan markdown is prepended to the message so the
        # brain treats it as locked-in scope. On reject we acknowledge and
        # return without invoking the brain at all.
        try:
            from lazyclaw.runtime import fix_plan as _fix_plan
            if await _fix_plan.should_enter_plan_mode(
                self.config, user_id, message,
            ):
                approved, plan, reason = await _fix_plan.gate_with_plan(
                    self.config, user_id, message,
                )
                if not approved:
                    msg = (
                        f"❌ Plan rejected — {reason or 'no reason given'}. "
                        "Tell me what to change and I'll regenerate."
                    )
                    await cb.on_event(AgentEvent(INSTANT_COMMAND, msg, {}))
                    await cb.on_event(AgentEvent("done", "Plan rejected", {}))
                    return msg
                if plan is not None:
                    # Lock the approved plan into the message. The brain
                    # sees the structured steps and is heavily biased
                    # toward following them — far more reliably than a
                    # free-form "please follow this plan" sentence.
                    plan_block = (
                        "## Approved plan (do these steps in order)\n"
                        f"{plan.to_markdown()}\n\n"
                        "## Original request\n"
                    )
                    message = f"{plan_block}{message}"
        except Exception:
            logger.debug("fix_plan gate raised; continuing without plan", exc_info=True)

        # Advance the agent's monotonic turn counter once per user message.
        # The lesson verification pump uses this as its quiet-window clock.
        try:
            from lazyclaw.runtime.turn_counter import advance_turn
            advance_turn(user_id)
        except Exception:
            logger.debug("turn_counter advance failed", exc_info=True)
        # Fire-and-forget: promote eligible pending shapes to verified.
        try:
            from lazyclaw.runtime.aio_helpers import fire_and_forget
            from lazyclaw.runtime.lesson_verifier import run_verification_pump
            fire_and_forget(
                run_verification_pump(self.config, user_id),
                name=f"verify-pump-{user_id[:8]}",
            )
        except Exception:
            logger.debug("verification pump dispatch failed", exc_info=True)

        key = await get_user_dek(self.config, user_id)
        _start_time = time.monotonic()
        _all_tools_used: list[str] = []

        # ── Compound task splitting ──────────────────────────────────
        # If message has multiple tasks AND we have a TaskRunner,
        # split and dispatch each to the right lane. TeamLead stays free.
        if (
            self._team_lead
            and self._task_runner
            and not getattr(self, "is_background", False)
            and _wants_any_tools(message)
        ):
            from lazyclaw.runtime.task_splitter import split_tasks, _looks_compound

            if _looks_compound(message):
                logger.info("COMPOUND detected for: %s", message[:60])
                sub_tasks = await split_tasks(
                    self.eco_router, user_id, message,
                )
                logger.info("COMPOUND split result: %d tasks — %s",
                            len(sub_tasks), [(s.name, s.lane) for s in sub_tasks])
                if len(sub_tasks) > 1:
                    has_bg = any(st.lane == "background" for st in sub_tasks)
                    if has_bg and self._task_runner:
                        # Mix of foreground/background — dispatch all to task_runner
                        dispatched: list[str] = []
                        for st in sub_tasks:
                            await self._task_runner.submit(
                                user_id=user_id,
                                instruction=st.instruction,
                                name=st.name,
                                callback=callback,
                                caller_depth=self._depth,
                            )
                            dispatched.append(f"{st.name} ({st.lane})")

                        status_msg = (
                            f"On it — split into {len(sub_tasks)} tasks:\n"
                            + "\n".join(f"  • {d}" for d in dispatched)
                        )

                        await cb.on_event(AgentEvent(
                            FAST_DISPATCH, status_msg,
                            {"tasks": len(sub_tasks), "names": dispatched},
                        ))
                        await cb.on_event(AgentEvent("stream_done", "", {}))
                        await cb.on_event(AgentEvent("done", "Dispatched", {}))
                        return status_msg
                    # All foreground — run sequentially inline, don't background
                    # (fall through to normal processing with the full original message)

        # Bind turn to a task if message carries the heartbeat reminder prefix.
        # Only the daemon's task-linked reminder path (tasks/store.py:609) emits
        # `[TASK_REMINDER:<task_id>]`; plain `[REMINDER]` has no id and stays
        # unbound.  Populated here, read by the safety net in the finally block.
        _bound_task_id: str | None = None
        _m = _TASK_REMINDER_RE.match(message or "")
        if _m:
            _bound_task_id = _m.group(1).strip() or None

        # Detect Tier-A slim heartbeat (REMINDER/WATCHER/MCP_WATCHER/
        # TASK_REMINDER:<id>). [JOB:...] is intentionally excluded — those
        # can be arbitrary work and stay on the full path. The slim path
        # bypasses build_context() and the full SOUL.md, dropping a typical
        # heartbeat call from ~40k tokens to ~5k.
        _is_slim_heartbeat = bool(_SLIM_HEARTBEAT_PREFIX_RE.match(message or ""))

        # Register foreground task with TeamLead (skip for background agents)
        _fg_task_id: str | None = None
        if self._team_lead and not getattr(self, "is_background", False):
            _fg_task_id = str(uuid4())
            self._team_lead.register(
                _fg_task_id, "chat", message[:80], "foreground",
                instruction_full=message,
                cancel_token=cancel_token,
                user_id=user_id,
            )
        _session_tokens = 0
        # Last ECO fallback tag seen this turn (e.g. "overloaded" → Haiku via
        # Claude CLI). Surfaces in the final "done" event so channels can show
        # a "fallback → X" chip instead of silently swapping the model.
        _last_fallback_reason: str | None = None
        _last_model_used: str | None = None

        # Initialize trace recorder
        from lazyclaw.replay.recorder import TraceRecorder
        recorder = TraceRecorder(self.config, user_id)
        logger.debug("process_message TRACE: recording user message...")
        await recorder.record_user_message(message)
        logger.debug("process_message TRACE: recorded OK")

        import asyncio as _aio

        from lazyclaw.memory.compressor import compress_history
        from lazyclaw.skills.manager import load_user_skills
        from lazyclaw.runtime.context_builder import build_context
        from lazyclaw.runtime.personality import load_personality

        # Check if using local model (needed for tool count optimization)
        _is_local_model = False
        _brain_id = ""
        try:
            from lazyclaw.llm.eco_settings import get_eco_settings as _get_eco
            from lazyclaw.llm.model_registry import get_model, get_mode_models
            logger.debug("process_message TRACE: loading eco settings...")
            _eco = await _get_eco(self.config, user_id)
            _mode = _eco.get("mode", "hybrid")
            # Check if the resolved worker model is local
            _defaults = get_mode_models(_mode)
            _worker_id = _eco.get(f"{_mode}_worker_model") or _eco.get("worker_model") or _defaults.get("worker", "")
            _brain_id = _eco.get(f"{_mode}_brain_model") or _eco.get("brain_model") or _defaults.get("brain", "")
            _worker_profile = get_model(_worker_id)
            _brain_profile = get_model(_brain_id)
            _is_local_model = bool(
                (_worker_profile and _worker_profile.is_local)
                or (_brain_profile and _brain_profile.is_local)
            )
        except Exception:
            logger.debug("Failed to load eco settings for local model check", exc_info=True)

        # MiniMax M2/M2.7 narrates actions as text instead of emitting tool_use
        # blocks under load. Append a tool-discipline suffix to the system
        # prompt that names the failure mode explicitly. The regex guard later
        # in this function catches drift after the fact; this prevents it.
        from lazyclaw.runtime.personality import (
            is_minimax_brain, minimax_tool_discipline_suffix,
        )
        _is_minimax_brain = is_minimax_brain(_brain_id)

        # Decide upfront: does this message need tools?
        needs_tools_early = self.registry is not None and _wants_any_tools(message)

        async def _load_history():
            # ORDER BY created_at, rowid: created_at is second-precision, so
            # multiple inserts in the same second tie. Without rowid as
            # tiebreaker, SQLite returns rows in arbitrary order — which can
            # split a tool_use from its tool_result and trigger Anthropic's
            # 400 "tool call result does not follow tool call" error.
            async with db_session(self.config) as db:
                if chat_session_id:
                    rows = await db.execute(
                        "SELECT id, role, content, tool_name, metadata FROM agent_messages "
                        "WHERE user_id = ? AND chat_session_id = ? "
                        "ORDER BY created_at ASC, rowid ASC",
                        (user_id, chat_session_id),
                    )
                else:
                    rows = await db.execute(
                        "SELECT id, role, content, tool_name, metadata FROM agent_messages "
                        "WHERE user_id = ? ORDER BY created_at ASC, rowid ASC",
                        (user_id,),
                    )
                return await rows.fetchall()

        logger.debug("process_message TRACE: needs_tools_early=%s slim_heartbeat=%s",
                     needs_tools_early, _is_slim_heartbeat)
        if _is_slim_heartbeat:
            # Tier-A slim path — skip build_context() and load HEARTBEAT.md
            # (~800 tokens) instead of full SOUL.md (~9.5k tokens). No
            # capabilities section, no hybrid memory pick, no LazyBrain
            # journal/pinned, no lessons recall. History still loaded so
            # task-bound reminders see prior turns.
            from lazyclaw.runtime.personality import load_heartbeat_personality
            logger.info("HEARTBEAT slim path engaged for: %r", (message or "")[:60])
            system_prompt = load_heartbeat_personality()
            history_rows = await _load_history()
        elif needs_tools_early:
            # Full parallel init — load history, skills, and rich context
            # Run sequentially with traces to find which one hangs
            logger.info("process_message TRACE: loading history...")
            history_rows = await _load_history()
            logger.info("process_message TRACE: history loaded, loading skills...")
            await load_user_skills(self.config, user_id, self.registry)
            logger.info("process_message TRACE: skills loaded, building context...")
            try:
                system_prompt = await asyncio.wait_for(
                    build_context(
                        self.config,
                        user_id,
                        registry=self.registry,
                        user_message=message,
                    ),
                    timeout=15,
                )
            except asyncio.TimeoutError:
                logger.warning("build_context timed out (>15s) — using personality only")
                system_prompt = load_personality()
            logger.info("process_message TRACE: context built")
        else:
            # Fast chat path — minimal system prompt, skip skills + MCP + memories
            _is_greeting = _CHAT_ONLY_PATTERN.match(message.strip().lower().rstrip("!?."))
            if _is_greeting and _is_local_model:
                # Tiny prompt for greetings on local models
                system_prompt = "You are LazyClaw, a helpful AI assistant. Be friendly and concise."
            else:
                system_prompt = load_personality()  # Full SOUL.md, cached
            logger.debug("process_message TRACE: loading history...")
            history_rows = await _load_history()
            logger.debug("process_message TRACE: history loaded")

        # Append MiniMax-only tool-discipline suffix. Skipping the greeting
        # fast-path-on-local branch (that branch never picks MiniMax anyway)
        # would still be safe; we apply unconditionally when brain is MiniMax.
        if _is_minimax_brain and system_prompt:
            system_prompt = (
                f"{system_prompt}\n\n---\n\n{minimax_tool_discipline_suffix()}"
            )

        logger.info("process_message TRACE: compressing history...")
        history = await compress_history(
            self.config, self.eco_router, user_id, chat_session_id,
            raw_messages=history_rows,
        )

        logger.info("process_message TRACE: history compressed (%d messages)", len(history))

        # Register delegate skill — lets the agent dispatch to specialists
        # inline (NanoClaw pattern: no separate team lead LLM call)
        _delegate_registered = False
        _dispatch_registered = False
        if self.registry is not None:
            from lazyclaw.skills.builtin.delegate import DelegateSkill

            delegate_skill = DelegateSkill(
                config=self.config,
                registry=self.registry,
                eco_router=self.eco_router,
                permission_checker=self.executor._checker if self.executor else None,
                callback=cb,
                team_lead=self._team_lead,
            )
            self.registry.register(delegate_skill)
            _delegate_registered = True

            from lazyclaw.skills.builtin.dispatch import DispatchSubagentsSkill

            dispatch_skill = DispatchSubagentsSkill(
                config=self.config,
                registry=self.registry,
                eco_router=self.eco_router,
                permission_checker=self.executor._checker if self.executor else None,
                callback=cb,
                team_lead=self._team_lead,
                # RC2 — give the skill the lane-queue-backed runner + chat
                # session so its subagent results consolidate into ONE reply
                # (foreground turns only; bg workers don't re-fan-out after
                # RC4 keeps them inline).
                task_runner=self._task_runner,
                chat_session_id=chat_session_id,
            )
            self.registry.register(dispatch_skill)
            _dispatch_registered = True

            # Re-register run_background per turn so each turn gets a
            # fresh fanout_group_id + the live channel callback. When
            # the brain calls run_background 2+ times in this turn,
            # every sibling shares the group, per-task pushes are
            # suppressed, and TaskRunner consolidates one reply once
            # the last sibling settles. See task_runner._consolidate.
            from lazyclaw.skills.builtin.background import RunBackgroundSkill

            _bg_fanout_group_id = uuid4().hex[:12]
            bg_skill = RunBackgroundSkill(
                config=self.config,
                callback=cb,
                fanout_group_id=_bg_fanout_group_id,
                chat_session_id=chat_session_id,
            )
            bg_skill._task_runner = self._task_runner
            bg_skill._caller_depth = self._depth
            self.registry.register(bg_skill)

        # Initialize channel state (used by tool nudge later, must exist for all paths)
        _matched_channels: list[str] = []

        # Meta-tool pattern: send only base tools (search_tools, memory, delegate).
        # LLM discovers other tools on demand via search_tools → schemas injected dynamically.
        # Channel detection: if message mentions whatsapp/instagram/email, prefer MCP tools over browser.
        needs_tools = self.registry is not None and _wants_any_tools(message)
        tools: list = []
        # Enumeration hint — set inside the needs_tools branch below, but we
        # initialize here so the plan-gate call path never trips on NameError
        # in the (defensive) case the branch was skipped.
        _wants_enumeration: bool = False
        # Defaults for slim-heartbeat path — referenced downstream (line ~2170
        # `if needs_tools and _wants_visible:`) outside the tool-loading block.
        _wants_browser: bool = False
        _wants_visible: bool = False

        if needs_tools and _is_slim_heartbeat:
            # Tier-A slim heartbeat. Tool budget is split by envelope kind:
            #
            #   `[REMINDER] ...`           — pure notification. Reminder text
            #     is data, NOT a command. Force ZERO tools so the brain
            #     can't interpret "delete X" as an instruction. (Seen in
            #     log 15:17:30 today: a "Time to delete n8n" reminder was
            #     processed as a command and 26 destructive n8n tool calls
            #     fired before the stuck-detector tripped.)
            #
            #   `[TASK_REMINDER:<id>] ...` — bound to a real task. Allow
            #     `complete_task` + `update_task` so user replies like
            #     "done" / "snooze 1h" still mark the task correctly.
            #
            #   `[WATCHER] / [MCP_WATCHER]` — may need a small action set
            #     (e.g. fetch follow-up). Keep the original 4-tool slim.
            _is_announce_reminder = bool(message and message.startswith("[REMINDER]"))
            if _is_announce_reminder:
                _slim_names: frozenset[str] = frozenset()
            elif _bound_task_id:
                _slim_names = frozenset({"complete_task", "update_task"})
            else:
                _slim_names = frozenset({
                    "search_tools", "web_search", "recall_memories", "delegate",
                })
            _base_names = set(_slim_names)
            tools = [
                schema for name in _slim_names
                if (schema := self.registry.get_tool_schema(name)) is not None
            ]
            logger.info(
                "HEARTBEAT slim tools: %d (slim set: %s, announce_reminder=%s, "
                "task_bound=%s)",
                len(tools), sorted(_slim_names),
                _is_announce_reminder, bool(_bound_task_id),
            )
            if not tools and _slim_names:
                logger.warning(
                    "HEARTBEAT slim path: ZERO tools resolved despite slim_names=%s "
                    "— registry may be empty",
                    sorted(_slim_names),
                )
        elif needs_tools:
            from lazyclaw.mcp.manager import _favorite_server_ids, _active_clients

            # Detect channel keywords → find matching MCP tools.
            # Skip channel injection for planning/strategy questions where
            # "email" / "instagram" are topics, not tool-use targets.
            _msg_lower = message.lower()
            _planning_words = {"how to", "strategy", "plan", "market", "reach",
                               "steps", "approach", "idea", "advice", "suggest"}
            _is_planning = any(pw in _msg_lower for pw in _planning_words)
            # Find-contact intent: "find emails for X", "missing emails",
            # "research email addresses". The user wants to DISCOVER contact
            # info from the open web — email/instagram MCP tools are useless
            # for this (they only send/read inbox). Suppress passive channels
            # so research path (web_search + scraper) gets the budget.
            _is_find_contact = bool(_FIND_CONTACT_RE.search(_msg_lower))
            _matched_channels: list[str] = []
            if not _is_planning:
                for channel, pattern in _CHANNEL_KEYWORDS.items():
                    if pattern.search(_msg_lower):
                        # Suppress passive-discovery channels for research queries.
                        # WhatsApp stays — "find whatsapp number" is rare and the
                        # MCP tool can read user's own WA contacts. Email/Instagram
                        # MCP tools cannot discover external contact info.
                        if _is_find_contact and channel in ("email", "instagram"):
                            continue
                        _matched_channels.append(channel)
                if _is_find_contact:
                    logger.info(
                        "Find-contact intent detected — email/instagram channel "
                        "injection suppressed (research path uses web_search + scraper)"
                    )

            # Re-inject channel tools if the LAST assistant response used them
            # AND the user's message looks like a continuation (reply words, short msg).
            # Without the continuation check, one-shot tool calls (e.g. mute from
            # a notification reply) make channel tools "sticky" for all future messages.
            # Skip the re-inject entirely on find-contact intent — research queries
            # should NEVER inherit messaging-channel tools from a previous turn.
            if not _matched_channels and not _is_find_contact:
                # "ok" removed — too generic, was re-injecting channel tools
                # on unrelated short replies (e.g. "ok tell me about bitcoin"
                # would re-grab whatever the last assistant call touched).
                _continuation_signals = {
                    "reply", "tell", "say", "send", "forward", "yes", "no",
                    "read", "check", "show", "next", "more",
                    # Data-correction follow-ups: when the user is replacing
                    # a value the prior turn used (phone number, email,
                    # address, name). Added 2026-05-16 after the Gerardo
                    # case — "ohh my bad number is 34 684 24 22 60" had no
                    # signal word and the brain ended up delegating to
                    # code_specialist instead of retrying whatsapp_send.
                    "wrong", "bad", "actually", "sorry", "correct", "fix",
                    "instead", "rather", "mistake", "typo",
                }
                _words = set(_msg_lower.split())
                # Phone-number-ish: a run of 8+ digits possibly broken by
                # spaces / dashes / dots (with optional leading +). Catches
                # "+34 684 24 22 60", "34-684-242-260", "684 242 260".
                _looks_like_phone = bool(
                    re.search(r"(?:\+?\d[\d \-\.]{7,}\d)", _msg_lower)
                )
                _looks_like_continuation = (
                    (bool(_words & _continuation_signals) or _looks_like_phone)
                    and len(_msg_lower) < 60
                )
                if _looks_like_continuation:
                    for msg in history[-2:]:  # Only last 2 messages (immediate context)
                        if msg.role == "assistant" and msg.tool_calls:
                            for tc in msg.tool_calls:
                                tname = tc.name.lower()
                                for channel in _CHANNEL_KEYWORDS:
                                    if channel in tname and channel not in _matched_channels:
                                        _matched_channels.append(channel)
                    if _matched_channels:
                        logger.info("Channel tools re-injected (conversation continuity): %s", _matched_channels)

            # Find MCP tools for matched channels.
            # Simple queries (status, read) get only core tools to reduce
            # tool count (51→~25). Action queries get the full set.
            _channel_tools: list = []
            _wants_action = bool(_CHANNEL_ACTION_RE.search(_msg_lower))

            # Build server_id → channel_label so tools route accurately even
            # when the channel keyword doesn't appear in the tool name (e.g.
            # workspace tools are named ``mcp_<id>_search_drive_files`` —
            # nothing says "workspace", but the server is workspace-mcp).
            # Falls back to substring matching when the registry doesn't
            # know the server for a tool (covers non-bundled MCPs).
            _server_id_to_channel: dict[str, str] = {}
            if _matched_channels:
                try:
                    from lazyclaw.mcp.manager import _active_clients

                    for sid, client in _active_clients.items():
                        cname = (getattr(client, "name", "") or "").lower()
                        if cname.startswith("mcp-"):
                            cname = cname[4:]
                        if cname.endswith("-mcp"):
                            cname = cname[:-4]
                        if cname in _matched_channels:
                            _server_id_to_channel[sid] = cname
                except Exception:
                    logger.debug(
                        "Could not build server_id → channel map; "
                        "falling back to substring tool matching",
                        exc_info=True,
                    )

            if _matched_channels:
                for tool_info in self.registry.list_mcp_tools():
                    func = tool_info.get("function", {})
                    tname = func.get("name", "").lower()
                    tdesc = func.get("description", "").lower()

                    # Authoritative path: parse server_id from tool name
                    # ``mcp_<server_id>_<tool>``. UUIDs use dashes not
                    # underscores, so split(_, 2) keeps the UUID intact.
                    parsed_channel: str | None = None
                    if tname.startswith("mcp_"):
                        parts = tname.split("_", 2)
                        if len(parts) >= 2:
                            parsed_channel = _server_id_to_channel.get(parts[1])

                    matched_via: str | None = None
                    if parsed_channel and parsed_channel in _matched_channels:
                        matched_via = parsed_channel
                    else:
                        # Fallback: substring match on name/desc — covers
                        # non-bundled MCPs and hand-tuned channels whose
                        # keyword genuinely appears in the tool string.
                        for ch in _matched_channels:
                            if ch in tname or ch in tdesc:
                                matched_via = ch
                                break

                    if not matched_via:
                        continue

                    # Filter: only core suffixes unless action intent
                    if not _wants_action:
                        _has_core = any(
                            tname.endswith(s) for s in _CHANNEL_CORE_SUFFIXES
                        )
                        if not _has_core:
                            continue

                    schema = self.registry.get_tool_schema(func.get("name", ""))
                    if schema is not None:
                        _channel_tools.append(schema)

                # On-demand connect: if channel detected but no MCP tools found,
                # the optional MCP server was never cached. Connect it now.
                if not _channel_tools:
                    logger.info(
                        "No MCP tools found for channels %s — attempting on-demand connect",
                        _matched_channels,
                    )
                    try:
                        from lazyclaw.mcp.manager import (
                            connect_server, get_server_id_by_name,
                        )
                        from lazyclaw.mcp.bridge import (
                            cache_tool_schemas, register_mcp_tools,
                        )

                        for ch in _matched_channels:
                            mcp_name = _CHANNEL_TO_MCP.get(ch)
                            if not mcp_name:
                                continue
                            try:
                                sid = await get_server_id_by_name(
                                    self.config, user_id, mcp_name,
                                )
                                if not sid:
                                    logger.info("No MCP server %s registered for user", mcp_name)
                                    continue
                                logger.info("On-demand connecting %s (id=%s)...", mcp_name, sid[:8])
                                client = await asyncio.wait_for(
                                    connect_server(self.config, user_id, sid),
                                    timeout=15,
                                )
                                tools_list = await client.list_tools()
                                await cache_tool_schemas(self.config, mcp_name, tools_list)
                                count = await register_mcp_tools(
                                    client, self.registry,
                                    config=self.config, user_id=user_id,
                                )
                                logger.info(
                                    "On-demand connected %s: %d tools registered",
                                    mcp_name, count,
                                )
                                # Re-scan for channel tools after registration
                                for tool_info in self.registry.list_mcp_tools():
                                    func = tool_info.get("function", {})
                                    tname = func.get("name", "").lower()
                                    tdesc = func.get("description", "").lower()
                                    if ch in tname or ch in tdesc:
                                        schema = self.registry.get_tool_schema(
                                            func.get("name", ""),
                                        )
                                        if schema is not None:
                                            _channel_tools.append(schema)
                            except asyncio.TimeoutError:
                                logger.warning(
                                    "Timeout connecting %s on-demand (>15s)", mcp_name,
                                )
                            except Exception:
                                # Retry once — MCP SDK has a cancel scope race
                                # that fails intermittently on first connect
                                logger.info(
                                    "First connect for %s failed, retrying...",
                                    mcp_name,
                                )
                                try:
                                    await asyncio.sleep(0.5)
                                    client = await asyncio.wait_for(
                                        connect_server(self.config, user_id, sid),
                                        timeout=15,
                                    )
                                    tools_list = await client.list_tools()
                                    await cache_tool_schemas(self.config, mcp_name, tools_list)
                                    count = await register_mcp_tools(
                                        client, self.registry,
                                        config=self.config, user_id=user_id,
                                    )
                                    logger.info(
                                        "On-demand connected %s on retry: %d tools",
                                        mcp_name, count,
                                    )
                                    for tool_info in self.registry.list_mcp_tools():
                                        func = tool_info.get("function", {})
                                        tname = func.get("name", "").lower()
                                        tdesc = func.get("description", "").lower()
                                        if ch in tname or ch in tdesc:
                                            schema = self.registry.get_tool_schema(
                                                func.get("name", ""),
                                            )
                                            if schema is not None:
                                                _channel_tools.append(schema)
                                except Exception:
                                    logger.warning(
                                        "Failed on-demand connect for %s (after retry)",
                                        mcp_name, exc_info=True,
                                    )
                    except Exception:
                        logger.warning(
                            "On-demand MCP connect failed (import or setup)",
                            exc_info=True,
                        )

            # Context carry-forward: if recent history used specific tools,
            # re-inject them so follow-up messages ("apply for it", "click that")
            # have access to the same tools without needing keywords again.
            _history_tool_names: set[str] = set()
            for msg in history[-8:]:  # Scan last 8 messages
                if msg.role == "assistant" and msg.tool_calls:
                    for tc in msg.tool_calls:
                        _history_tool_names.add(tc.name)

            # Enumeration intent detection. When the user asks for multi-target
            # work ("scrape N salons", "find all X", "for each Y"), the plan
            # prompt in taor.py uses this flag to bias toward dispatch_subagents
            # / run_background instead of a foreground browser loop. Regex is
            # the single source of truth, shared from task_splitter.
            from lazyclaw.runtime.task_splitter import _looks_enumerative
            _wants_enumeration = _looks_enumerative(message)
            if _wants_enumeration:
                logger.info(
                    "Enumeration keywords detected — plan prompt will bias "
                    "toward dispatch_subagents + run_background for: %s",
                    message[:60],
                )

            # Task manager keyword detection → inject task tools
            _task_tools_extra: list = []
            _wants_tasks = any(kw in _msg_lower for kw in _TASK_KEYWORDS)
            if not _wants_tasks and _history_tool_names & _TASK_TOOL_NAMES:
                _wants_tasks = True
                logger.info("Task tools re-injected from recent history context")
            if _wants_tasks:
                for tname in _TASK_TOOL_NAMES:
                    schema = self.registry.get_tool_schema(tname)
                    if schema is not None:
                        _task_tools_extra.append(schema)
                if _task_tools_extra:
                    logger.info(
                        "Task keywords detected — %d task tools injected",
                        len(_task_tools_extra),
                    )

            # Cron / heartbeat job keyword detection → inject cron-job tools.
            # Independent of _TASK_KEYWORDS so phrases like "show my jobs" or
            # "delete that cron" reach manage_job/list_jobs even when no
            # "task" / "reminder" word is present in the message.
            _cron_tools_extra: list = []
            _wants_cron = any(kw in _msg_lower for kw in _CRON_KEYWORDS)
            if not _wants_cron and _history_tool_names & _CRON_TOOL_NAMES:
                _wants_cron = True
                logger.info("Cron-job tools re-injected from recent history context")
            if _wants_cron:
                for cname in _CRON_TOOL_NAMES:
                    schema = self.registry.get_tool_schema(cname)
                    if schema is not None:
                        _cron_tools_extra.append(schema)
                if _cron_tools_extra:
                    logger.info(
                        "Cron-job keywords detected — %d cron tools injected",
                        len(_cron_tools_extra),
                    )

            # MCP management keyword detection → inject install/connect/list
            # so "connect upwork mcp" / "install instagram mcp" route to
            # install_mcp_server (auto-installs the bundled package) instead
            # of falling through to list_mcp_servers + asking for a URL.
            _mcp_mgmt_tools: list = []
            _wants_mcp_mgmt = any(kw in _msg_lower for kw in _MCP_MGMT_KEYWORDS)
            if not _wants_mcp_mgmt and _history_tool_names & _MCP_MGMT_TOOL_NAMES:
                _wants_mcp_mgmt = True
                logger.info("MCP-mgmt tools re-injected from recent history context")
            if _wants_mcp_mgmt:
                for mname in _MCP_MGMT_TOOL_NAMES:
                    schema = self.registry.get_tool_schema(mname)
                    if schema is not None:
                        _mcp_mgmt_tools.append(schema)
                if _mcp_mgmt_tools:
                    logger.info(
                        "MCP-mgmt keywords detected — %d tools injected",
                        len(_mcp_mgmt_tools),
                    )

            # Contact-store keyword detection → inject find/save/update/list/sync.
            # Also force-inject find_contact when a messaging channel matched
            # (whatsapp/instagram/email/telegram) so the brain resolves names
            # to verified handles before any send call.
            _contact_tools_extra: list = []
            _wants_contacts = any(kw in _msg_lower for kw in _CONTACT_KEYWORDS)
            if not _wants_contacts and _history_tool_names & _CONTACT_TOOL_NAMES:
                _wants_contacts = True
                logger.info("Contact tools re-injected from recent history context")
            _channel_messaging_match = bool(
                _matched_channels and (set(_matched_channels) & _MESSAGING_CHANNELS)
            )
            if _wants_contacts:
                _names_to_inject = _CONTACT_TOOL_NAMES
            elif _channel_messaging_match:
                # Minimal injection — only the lookup tool, not save/update/sync.
                _names_to_inject = frozenset({"find_contact"})
            else:
                _names_to_inject = frozenset()
            for cname in _names_to_inject:
                schema = self.registry.get_tool_schema(cname)
                if schema is not None:
                    _contact_tools_extra.append(schema)
            if _contact_tools_extra:
                logger.info(
                    "Contact keywords %s → %d contact tools injected",
                    "explicit" if _wants_contacts else "via messaging channel",
                    len(_contact_tools_extra),
                )

            # Budget/expense keyword detection → inject budget manager tools
            _budget_tools_extra: list = []
            _wants_budget = any(kw in _msg_lower for kw in _BUDGET_KEYWORDS)
            if not _wants_budget and _history_tool_names & _BUDGET_TOOL_NAMES:
                _wants_budget = True
                logger.info("Budget tools re-injected from recent history context")
            if _wants_budget:
                for bname in _BUDGET_TOOL_NAMES:
                    schema = self.registry.get_tool_schema(bname)
                    if schema is not None:
                        _budget_tools_extra.append(schema)
                if _budget_tools_extra:
                    logger.info(
                        "Budget keywords detected — %d budget tools injected",
                        len(_budget_tools_extra),
                    )

            # Survival/job keyword detection → inject survival tools
            _survival_tools: list = []
            _wants_survival = any(kw in _msg_lower for kw in _SURVIVAL_KEYWORDS)
            # "find/show jobs on upwork" — route to search_jobs (no browser).
            if not _wants_survival and _is_upwork_job_search(_msg_lower):
                _wants_survival = True
                logger.info(
                    "Upwork job-search intent detected — injecting search_jobs",
                )
            # Also trigger if recent history used survival tools
            if not _wants_survival and _history_tool_names & _SURVIVAL_TOOL_NAMES:
                _wants_survival = True
                logger.info("Survival tools re-injected from recent history context")
            if _wants_survival:
                for sname in _SURVIVAL_TOOL_NAMES:
                    schema = self.registry.get_tool_schema(sname)
                    if schema is not None:
                        _survival_tools.append(schema)
                if _survival_tools:
                    logger.info(
                        "Job/survival keywords detected — %d survival tools injected",
                        len(_survival_tools),
                    )

            # n8n automation keyword detection → inject n8n tools
            _n8n_tools: list = []
            _wants_n8n = any(kw in _msg_lower for kw in _N8N_KEYWORDS)
            if not _wants_n8n and _history_tool_names & _N8N_TOOL_NAMES:
                _wants_n8n = True
                logger.info("n8n tools re-injected from recent history context")
            if _wants_n8n:
                for nname in _N8N_TOOL_NAMES:
                    schema = self.registry.get_tool_schema(nname)
                    if schema is not None:
                        _n8n_tools.append(schema)
                # Also inject n8n-nodes MCP tools (search_nodes / get_node
                # / validate_workflow / ...) so the brain can discover
                # node schemas for ANY of the ~1,505 node types instead
                # of guessing from memory. Matched by tool-name suffix
                # because the MCP bridge prefixes with `mcp_<uuid>_`.
                _n8n_mcp_attached = 0
                for tool_info in self.registry.list_mcp_tools():
                    func = tool_info.get("function", {})
                    tname = func.get("name", "")
                    tdesc = func.get("description", "").lower()
                    if not any(tname.endswith(s) for s in _N8N_MCP_TOOL_SUFFIXES):
                        continue
                    # Tighten: only from the n8n-nodes MCP server (not
                    # from e.g. another MCP that happens to expose a
                    # `get_node` tool).
                    if _N8N_MCP_SERVER_NAME not in tdesc:
                        continue
                    schema = self.registry.get_tool_schema(tname)
                    if schema is not None:
                        _n8n_tools.append(schema)
                        _n8n_mcp_attached += 1
                # On-demand connect the n8n-nodes MCP server if nothing
                # was found but the user asked for n8n work.
                if _n8n_mcp_attached == 0:
                    try:
                        from lazyclaw.mcp.manager import (
                            connect_server, get_server_id_by_name,
                        )
                        from lazyclaw.mcp.bridge import (
                            cache_tool_schemas, register_mcp_tools,
                        )
                        sid = await get_server_id_by_name(
                            self.config, user_id, _N8N_MCP_SERVER_NAME,
                        )
                        if sid:
                            logger.info(
                                "On-demand connecting %s (id=%s)…",
                                _N8N_MCP_SERVER_NAME, sid[:8],
                            )
                            client = await asyncio.wait_for(
                                connect_server(self.config, user_id, sid),
                                timeout=20,
                            )
                            tools_list = await client.list_tools()
                            await cache_tool_schemas(
                                self.config, _N8N_MCP_SERVER_NAME, tools_list,
                            )
                            await register_mcp_tools(
                                client, self.registry,
                                config=self.config, user_id=user_id,
                            )
                            # Re-scan after registration.
                            for tool_info in self.registry.list_mcp_tools():
                                func = tool_info.get("function", {})
                                tname = func.get("name", "")
                                tdesc = func.get("description", "").lower()
                                if not any(
                                    tname.endswith(s)
                                    for s in _N8N_MCP_TOOL_SUFFIXES
                                ):
                                    continue
                                if _N8N_MCP_SERVER_NAME not in tdesc:
                                    continue
                                schema = self.registry.get_tool_schema(tname)
                                if schema is not None:
                                    _n8n_tools.append(schema)
                                    _n8n_mcp_attached += 1
                    except Exception:
                        # Don't fail the whole n8n turn if the catalog
                        # MCP can't start — the native n8n_* skills still
                        # work, brain just loses the describe-before-emit
                        # primitive.
                        logger.debug(
                            "n8n-nodes MCP on-demand connect failed",
                            exc_info=True,
                        )
                if _n8n_tools:
                    logger.info(
                        "n8n/automation keywords detected — %d n8n tools injected (of which %d MCP node-catalog)",
                        len(_n8n_tools), _n8n_mcp_attached,
                    )
                # n8n wins over raw channel MCP tools — suppress channel injection
                # to avoid ambiguous tool sets (e.g. "automate email notifications"
                # matching both email MCP and n8n).
                if _matched_channels:
                    logger.info(
                        "n8n detected with channel keywords %s — suppressing channel injection",
                        _matched_channels,
                    )
                    _matched_channels = []
                    _channel_tools = []

            # Scraper auto-inject — mcp-scraper is `optional: true`, so its
            # tools are only registered when explicitly connected. We do BOTH
            # sides here: (a) on-demand connect the server if scrape/crawl
            # keywords fire, (b) suffix-match the resulting tool schemas.
            _scraper_tools: list[dict] = []
            _wants_scraper = bool(_SCRAPER_KEYWORDS_RE.search(message))
            if not _wants_scraper:
                # Re-trigger if recent history called any scraper tool
                if any(
                    tname in _SCRAPER_MCP_TOOL_NAMES
                    for tname in _history_tool_names
                ):
                    _wants_scraper = True
                    logger.info("Scraper tools re-injected from recent history context")
            if _wants_scraper and self.registry is not None:
                # Canonical-name lookup — pool registers under stable names
                for tname in _SCRAPER_MCP_TOOL_NAMES:
                    schema = self.registry.get_tool_schema(tname)
                    if schema is not None:
                        _scraper_tools.append(schema)
                # If pool isn't registered yet (e.g. shard-1 boot failed),
                # try a one-shot spawn of shard-0 and re-fetch schemas.
                if not _scraper_tools:
                    try:
                        from lazyclaw.mcp.manager import _ensure_scraper_shard
                        spawned = await _ensure_scraper_shard(
                            self.config, user_id, 0,
                        )
                        if spawned:
                            for tname in _SCRAPER_MCP_TOOL_NAMES:
                                schema = self.registry.get_tool_schema(tname)
                                if schema is not None:
                                    _scraper_tools.append(schema)
                    except Exception:
                        logger.debug(
                            "scraper pool on-demand spawn failed",
                            exc_info=True,
                        )
                if _scraper_tools:
                    logger.info(
                        "Scraper keywords detected — %d scraper tools injected",
                        len(_scraper_tools),
                    )

            # Google Workspace auto-inject — mirror of the n8n block above.
            # Surfaces google_run_task + the workspace-mcp Sheets/Drive/Gmail/
            # Calendar subset on the first turn, so the brain stops falling
            # back to delegate(specialist="browser", …) for Google ops.
            _google_tools: list[dict] = []
            _wants_google = any(kw in _msg_lower for kw in _GOOGLE_KEYWORDS)
            if not _wants_google and _history_tool_names & _GOOGLE_TOOL_NAMES:
                _wants_google = True
                logger.info("Google tools re-injected from recent history context")
            if _wants_google and self.registry is not None:
                for gname in _GOOGLE_TOOL_NAMES:
                    schema = self.registry.get_tool_schema(gname)
                    if schema is not None:
                        _google_tools.append(schema)
                # workspace-mcp is a startup favorite, so the tools are
                # already registered. Suffix-match them, tightened by
                # description so we don't grab unrelated MCP tools that
                # happen to share a suffix.
                for tool_info in self.registry.list_mcp_tools():
                    func = tool_info.get("function", {})
                    tname = func.get("name", "")
                    tdesc = func.get("description", "").lower()
                    if not any(tname.endswith(s) for s in _GOOGLE_MCP_TOOL_SUFFIXES):
                        continue
                    if _GOOGLE_MCP_SERVER_NAME not in tdesc:
                        continue
                    schema = self.registry.get_tool_schema(tname)
                    if schema is not None:
                        _google_tools.append(schema)
                if _google_tools:
                    logger.info(
                        "Google keywords detected — %d Google tools injected",
                        len(_google_tools),
                    )

            # Build base tools + conditionally add browser.
            # Smart tool selection: same for all models. LLM discovers extras via search_tools().
            # When channel MCP tools dominate, trim base set to reduce noise.
            _base_names = set(_BASE_TOOL_NAMES)
            if _channel_tools and not _wants_tasks and not _wants_survival:
                # Drop tools irrelevant for messaging queries. (run_command /
                # read_file / write_file / list_directory are already keyword-
                # gated via _SHELL_FILE_RE and not in _BASE_TOOL_NAMES — kept
                # in the set here as a no-op safety net in case the gating
                # logic ever re-includes them.)
                _base_names -= {
                    "run_command", "write_file", "read_file", "list_directory",
                    "watch_site", "connect_mcp_server", "disconnect_mcp_server",
                }
                logger.info("Channel-focused: trimmed base tools to %d", len(_base_names))

            # Browser only when user explicitly asks (word-boundary match).
            # No re-injection from history — if the user has moved on, the LLM
            # should not reach for the browser on its own.
            _wants_browser = bool(_BROWSER_RE.search(_msg_lower))
            _wants_visible = any(kw in _msg_lower for kw in (
                "visible", "show me", "show it", "let me see", "make visible",
            ))
            # Browser suppressed ONLY when channel MCP tools handle the request
            # (WhatsApp/Instagram/Email have dedicated MCP tools — no browser needed).
            # Task and survival tools should NEVER suppress browser — user can
            # browse the web AND have task/job tools available simultaneously.
            # Tools we deliberately keep OUT of the active set this turn.
            # The late-inject-from-registry path further down checks this
            # set and refuses to re-add — without that check, MiniMax would
            # call ``browser`` from training/history memory, the late-inject
            # would resurrect it from the registry, and the channel-suppress
            # decision would be silently undone (this exact bug fired during
            # 2026-05-04 Upwork debugging — agent kept calling browser even
            # though Upwork MCP tools were the right path).
            _suppressed_tool_names: set[str] = set()
            # When channel MCP routing is active, suppress generic
            # browser/shell tools — the MCP handles browser automation
            # internally with the user's real cookies. This applies even
            # when the user didn't say "browser" in this turn (e.g.
            # "check upwork and send proposal" — no browser keyword, but
            # post-MCP the brain would otherwise reach for browser to
            # "verify the result"). The late-inject-from-registry path
            # respects this set and refuses to resurrect from history.
            if _channel_tools:
                _suppressed_tool_names |= {
                    "browser", "use_host_browser", "run_command",
                }
            if _wants_browser and not _channel_tools:
                _base_names.add("browser")
                logger.info("Browser keyword detected — browser tool included")
            elif _wants_browser and _channel_tools:
                logger.info("Channel detected: %s → %d MCP tools, browser suppressed (MCP-first)", _matched_channels, len(_channel_tools))
            elif _channel_tools:
                logger.info("Channel detected: %s → %d MCP tools, browser/shell suppressed for MCP-only flow", _matched_channels, len(_channel_tools))

            # Shell/file tools only when user explicitly asks. Keeps
            # `run_command` out of the default tool list so the brain can't
            # misfire it on a phone-number lookup. Re-inject from history
            # too — mid-task file work shouldn't lose the tools.
            _wants_shell_file = bool(_SHELL_FILE_RE.search(_msg_lower))
            if not _wants_shell_file and _history_tool_names & _SHELL_FILE_TOOL_NAMES:
                _wants_shell_file = True
                logger.info("Shell/file tools re-injected from recent history context")
            if _wants_shell_file:
                _base_names |= _SHELL_FILE_TOOL_NAMES
                logger.info(
                    "Shell/file keywords detected — %d tools included",
                    len(_SHELL_FILE_TOOL_NAMES),
                )
            elif _channel_tools:
                logger.info("Channel detected: %s → %d MCP tools, no browser", _matched_channels, len(_channel_tools))

            tools = [
                schema for name in _base_names
                if (schema := self.registry.get_tool_schema(name)) is not None
            ]

            tools.extend(_channel_tools)
            # Add task manager tools (deduplicated)
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            for tt in _task_tools_extra:
                if tt.get("function", {}).get("name") not in _existing_names:
                    tools.append(tt)

            # Add cron / heartbeat job tools (deduplicated)
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            for ct in _cron_tools_extra:
                if ct.get("function", {}).get("name") not in _existing_names:
                    tools.append(ct)

            # Add MCP-management tools (deduplicated)
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            for mt in _mcp_mgmt_tools:
                if mt.get("function", {}).get("name") not in _existing_names:
                    tools.append(mt)

            # Add contact-store tools (deduplicated)
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            for ct in _contact_tools_extra:
                if ct.get("function", {}).get("name") not in _existing_names:
                    tools.append(ct)

            # Add budget manager tools (deduplicated)
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            for bt in _budget_tools_extra:
                if bt.get("function", {}).get("name") not in _existing_names:
                    tools.append(bt)

            # Add survival tools (deduplicated)
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            for st in _survival_tools:
                if st.get("function", {}).get("name") not in _existing_names:
                    tools.append(st)

            # Add n8n tools (deduplicated)
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            for nt in _n8n_tools:
                if nt.get("function", {}).get("name") not in _existing_names:
                    tools.append(nt)

            # Add Google Workspace tools (deduplicated)
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            for gt in _google_tools:
                if gt.get("function", {}).get("name") not in _existing_names:
                    tools.append(gt)

            # Add mcp-scraper tools (deduplicated)
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            for sct in _scraper_tools:
                if sct.get("function", {}).get("name") not in _existing_names:
                    tools.append(sct)

            # Include favorite MCP tools ONLY when channel keywords matched
            # AND the tool name contains one of the matched channels. Without
            # the channel-name filter this loop attached every favorite tool
            # (Instagram + WhatsApp + Google on an email-matched turn), which
            # blew the context up to 123 tools on 2026-04-24 19:27.
            _MAX_TOTAL_TOOLS = 40
            _fav_prefixes = tuple(
                f"mcp_{sid}_" for sid in _favorite_server_ids
                if sid in _active_clients
            )
            _existing_names = {t.get("function", {}).get("name") for t in tools}
            _cap_hit = False
            if _fav_prefixes and _matched_channels:
                _channel_name_set = {ch.lower() for ch in _matched_channels}
                for tool_info in self.registry.list_mcp_tools():
                    if len(tools) >= _MAX_TOTAL_TOOLS:
                        _cap_hit = True
                        break
                    func = tool_info.get("function", {})
                    tname = func.get("name", "")
                    tname_lower = tname.lower()
                    if not tname.startswith(_fav_prefixes):
                        continue
                    if not any(ch in tname_lower for ch in _channel_name_set):
                        continue
                    if tname in _existing_names:
                        continue
                    schema = self.registry.get_tool_schema(tname)
                    if schema is not None:
                        tools.append(schema)
                        _existing_names.add(tname)
            # Hard trim anywhere we're over the budget (defends against
            # task/survival/n8n bundles pushing us over even when no favorites
            # were attached).
            if len(tools) > _MAX_TOTAL_TOOLS:
                _cap_hit = True
                tools = tools[:_MAX_TOTAL_TOOLS]

            # MiniMax M2.7 narration rate climbs sharply past ~24 tools.
            # When the resolved brain is MiniMax, drop to 24 by keeping the
            # base tools (search_tools, web_search, recall_memories,
            # save_memory, delegate) and ranking the remainder by keyword
            # overlap with the user message. Reuses the tokenizer pattern
            # from context_builder._pick_hybrid_memories.
            if _is_minimax_brain and len(tools) > _MINIMAX_MAX_TOOLS:
                tools = _trim_tools_for_minimax(tools, message, _base_names)
                _cap_hit = True
                logger.info(
                    "MINIMAX cap: trimmed to %d tools (limit=%d)",
                    len(tools), _MINIMAX_MAX_TOOLS,
                )
            logger.info(
                "%s mode: %d tools (cap=%d hit=%s) for: %s",
                "LOCAL" if _is_local_model else "META",
                len(tools), _MAX_TOTAL_TOOLS, _cap_hit, message[:50],
            )
            if tools:
                logger.info("Tool names sent: %s", [t.get("function", {}).get("name") for t in tools])
            else:
                logger.warning("ZERO tools resolved from base_names=%s — registry may be empty", _base_names)
        else:
            logger.info("No tools — fast chat path for: %s", message[:50])

        # Build context — keep recent messages, compact old ones.
        # Filter error messages so LLM doesn't reference past failures.
        _clean_history = _filter_error_messages(history)
        if needs_tools:
            chat_history = _compact_history(_clean_history, keep_recent=4)
        else:
            chat_history = _strip_tool_messages(_clean_history)
            if len(chat_history) > 6:
                chat_history = chat_history[-6:]

        # Message order optimized for prompt caching:
        # 1. System (STATIC — cached), 2. Channel (semi-static),
        # 3. History (dynamic), 4. User message (dynamic)
        system_messages = [LLMMessage(role="system", content=system_prompt)]
        if channel_context:
            system_messages.append(LLMMessage(role="system", content=channel_context))

        # Channel-routing hint: when MCP tools handle a domain (Upwork,
        # WhatsApp, Instagram, Email, …), the user often phrases requests
        # with words like "use my browser" — but that's a description of
        # the underlying mechanism the MCP already manages internally, not
        # an instruction to call the generic ``browser`` tool. Without this
        # hint MiniMax M2.7 reads "use my browser" literally, picks the
        # generic browser tool from training memory, ignores the MCP, and
        # falls through to a loop on Cloudflare-blocked pages. Empirically
        # observed on the 2026-05-04 Upwork apply turn — agent succeeded
        # on upwork_get_job_details, then immediately switched to browser
        # and looped 8 times before the loop-guard pulled it out.
        if _matched_channels and (_channel_tools or _suppressed_tool_names):
            _ch_label = ", ".join(_matched_channels)
            _routing_hint = (
                f"[ROUTING] This conversation is about {_ch_label}. The "
                f"{_ch_label} MCP tools handle browser automation internally — "
                "they drive the user's REAL browser with their cookies under "
                "the hood. So even when the user says 'use my browser', "
                "'apply via my browser', or similar, you MUST use the MCP "
                f"tools (mcp_*_{_matched_channels[0]}_*), NOT the generic "
                "`browser`, `use_host_browser`, or `run_command` tools. "
                "Those are deliberately excluded from your active tool list "
                "for this turn. The MCP path is end-to-end automated; the "
                "browser path is fragile and Cloudflare-blocked."
            )
            system_messages.append(LLMMessage(role="system", content=_routing_hint))
        messages: list[LLMMessage] = (
            system_messages
            + chat_history
            + [LLMMessage(role="user", content=message)]
        )

        # If user wants visible browser, prepend instruction to user message
        # so LLM calls show action before navigating
        if needs_tools and _wants_visible:
            _vis_prefix = (
                "[IMPORTANT: Browser runs HEADLESS (invisible). "
                "You MUST call browser(action='show') FIRST before any other browser action. "
                "Then navigate.]\n\n"
            )
            # Modify the last user message in-place
            _last_msg = messages[-1]
            if _last_msg.role == "user":
                messages[-1] = LLMMessage(
                    role="user", content=_vis_prefix + _last_msg.content,
                )

        logger.info("Context: %d messages (%d history + system + user), tools=%d",
                     len(messages), len(chat_history), len(tools))

        # Heartbeat telemetry — log per-call system_prompt + tool counts so we
        # can validate slim-path savings (Tier A) without regressing the full
        # path (Tier B / [JOB:...]). See _HEARTBEAT_TELEMETRY_RE comment.
        _hb_match = _HEARTBEAT_TELEMETRY_RE.match(message or "")
        if _hb_match:
            _hb_kind = _hb_match.group(1).split(":", 1)[0]
            logger.info(
                "HEARTBEAT_TELEMETRY kind=%s system_prompt_chars=%d tool_count=%d "
                "history_msgs=%d total_messages=%d msg_preview=%r",
                _hb_kind,
                len(system_prompt or ""),
                len(tools or []),
                len(chat_history or []),
                len(messages or []),
                (message or "")[:60],
            )

        # ── Learn from corrections (fire-and-forget) ─────────────
        # If the user is correcting the previous response, extract a
        # compact lesson and save it to memory for future sessions.
        if len(history) >= 2:
            from lazyclaw.runtime.lesson_extractor import is_correction

            prev_assistant = next(
                (m for m in reversed(history) if m.role == "assistant"),
                None,
            )
            if prev_assistant and is_correction(message):
                _aio.create_task(
                    _extract_and_store_lesson(
                        self.eco_router, self.config, user_id,
                        message, history[-4:],
                    )
                )

        # ── TAOR: Three-Phase loop setup (Plan → Execute → Verify) ───────
        # Pure functions imported here to keep top-level imports clean.
        from lazyclaw.runtime.taor import (
            detect_effort, make_plan_prompt, verify_response, EffortLevel,
            make_user_facing_plan_prompt, parse_plan_steps,
            has_plan_bypass_phrase,
        )
        _effort = detect_effort(message, has_tools=needs_tools)
        # Number of verify+correction passes after the execute phase.
        # LOW → 0 (skip entirely), MEDIUM → 1, HIGH → 2, MAX → 3.
        _taor_verify_passes = {
            EffortLevel.LOW: 0,
            EffortLevel.MEDIUM: 1,
            EffortLevel.HIGH: 2,
            EffortLevel.MAX: 3,
        }[_effort]
        _taor_retry_context: str | None = None  # Set on verify failure for replan
        logger.info("TAOR: effort=%s verify_passes=%d", _effort, _taor_verify_passes)

        # ── Plan-Mode approval gate (Claude-Code-style) ────────────────────
        # For MEDIUM+ tasks, show the user the plan and block on approval
        # before any tool calls. Three bypasses:
        #   (a) per-user auto_plan setting disabled → skip entirely
        #   (b) per-turn phrase ("just do it", "go ahead", etc.) → skip
        #   (c) session-level trust via plan_checkpoint → auto-approve
        _plan_mode_used: bool = False
        _plan_text_approved: str | None = None
        _auto_plan_enabled = await _load_auto_plan_setting(user_id)
        # Operating mode (ADR-0005) is now the primary control over the plan
        # gate; the legacy users.auto_plan flag is the ASK-mode fallback so
        # existing behavior is preserved. PLAN → always gate; AUTO/CHAT →
        # never; ASK → legacy auto_plan setting.
        _gate_wanted = _auto_plan_enabled
        try:
            from lazyclaw.runtime.agent_mode import AgentMode, get_agent_mode
            _mode = await get_agent_mode(self.config, user_id)
            if _mode is AgentMode.PLAN:
                _gate_wanted = True
            elif _mode in (AgentMode.AUTO, AgentMode.CHAT):
                _gate_wanted = False
            # ASK → keep legacy _auto_plan_enabled
        except Exception:
            logger.debug(
                "agent_mode plan-gate resolution failed; using auto_plan",
                exc_info=True,
            )
        _bypassed_by_phrase = has_plan_bypass_phrase(message)
        if (
            _effort != EffortLevel.LOW
            and needs_tools
            and _gate_wanted
            and not _bypassed_by_phrase
        ):
            try:
                _plan_text_approved = await self._run_plan_gate(
                    user_id=user_id,
                    message=message,
                    messages_so_far=messages,
                    cb=cb,
                    cancel_token=cancel_token,
                    enumeration_hint=_wants_enumeration,
                )
                _plan_mode_used = True
            except _PlanRejected as pr:
                logger.info("Plan rejected for user %s: %s", user_id, pr.reason)
                await cb.on_event(AgentEvent(
                    "done", "Plan rejected", {"reason": pr.reason or ""}
                ))
                if self._team_lead and _fg_task_id:
                    self._team_lead.fail(_fg_task_id, pr.reason or "plan rejected")
                    _fg_task_id = None
                return (
                    f"Plan rejected: {pr.reason or 'no reason given'}. "
                    "Tell me how you'd like to proceed."
                )

        # Agentic loop — brain decides when to stop, safety cap prevents runaway
        max_iterations = self.config.max_tool_iterations
        _nudge_at = int(max_iterations * 0.8)  # Nudge LLM at 80% of cap
        _nudged = False
        all_new_messages: list[LLMMessage] = [LLMMessage(role="user", content=message)]
        _tool_call_history: list[str] = []  # Track tool names for loop detection
        _tool_results: list[str] = []  # Track results for error detection
        # Per-turn (tool_name, sorted_arg_keys) trail. Feeds the mid-turn
        # pivot detector — when the brain runs ≥5 same-shape calls in a
        # row it's acting as a worker, and the runtime nudges it to
        # dispatch instead. Cleared at iteration end alongside the other
        # per-turn state. Memory/discovery tools (recall_memories,
        # save_memory, search_tools) are filtered out by detect_inline_pivot.
        _tool_call_signatures: list[tuple[str, tuple[str, ...]]] = []
        _pivot_nudged: bool = False
        _escalated = False  # True after auto-escalation to brain_model
        _escalation_iter = 0  # Iteration when escalation happened
        _tool_call_cache: dict[str, str] = {}  # (name, args_hash) → result
        _tool_call_hit_count: dict[str, int] = {}  # how many times cache returned the same result this turn
        _denied_approvals: set[str] = set()  # "skill_name:args_hash" — user-denied this turn
        # Graduated escalation level: 0=normal, 1=soft nudge, 2=brain escalation
        # Level 3 (give up) triggers when stuck is detected at level 2.
        _escalation_level: int = 0
        # Self-recall guard: knowledge-gap detector re-prompts the brain with
        # LazyBrain + personal_memory injection at most ONCE per turn.
        _self_recalled: bool = False
        # Counts stuck signals that fired AFTER the L2 brain escalation. The
        # 1st gets the existing HELP_NEEDED dialog (user can rescue);
        # the 2nd hard-stops the loop with a terminal message instead of
        # popping a second dialog. Without this, a stubborn brain (e.g.
        # MiniMax M2.7) can flail through 10+ extra iterations after L2 has
        # already conceded — see hirossa.com postmortem 2026-04-19.
        _post_l2_stuck_count: int = 0
        # Browser action planner — ephemeral per-conversation, not persisted
        _plan_state = ActionPlannerState(
            plan_injected=not should_inject_plan(message, []),
        )
        # Research strategy — tracks requirements + sources for research tasks
        from lazyclaw.browser.research_strategy import (
            ResearchStrategy, ResearchStatus,
            is_research_task, make_requirements_prompt,
            parse_requirements_from_response, note_source,
            make_progress_message as _make_research_progress,
        )
        _research_state: ResearchStrategy | None = (
            ResearchStrategy(
                query=message,
                info_requirements=(),
                sources_checked=(),
                gaps=(),
            ) if is_research_task(message) else None
        )
        _research_prompt_injected = False

        # Hallucination-retry guard. Each time the LLM calls a tool that
        # doesn't exist, we inject a correction and retry. Without a cap,
        # a model fixated on a non-existent tool (e.g. a Claude.ai-style
        # `mcp_<uuid>_delete_spreadsheet` that upstream doesn't expose)
        # burns the full 50 outer iterations silently. 2 attempts = one
        # genuine correction chance, then bail with a user-visible message.
        _HALLUC_MAX_RETRIES = 2
        _halluc_retries = 0

        # F1 grounding retry counter — each time the brain emits a draft
        # that violates F1 (banned 'from memory' phrase OR substantive
        # reply with no `> sender (ts): …` quote block) AFTER a channel-
        # read tool was called this turn, we inject a correction and
        # re-roll. Capped at _F1_MAX_RETRIES; further violations ship
        # with a log warning. See module-level _check_f1_violation.
        _f1_retries: int = 0

        # F1 confabulation raw-data injection guard. The confabulation
        # detector (lazyclaw/runtime/f1_confabulation_detector.py) catches
        # two escape-hatch patterns Opus 4.6/4.7 falls into under F1 retry
        # pressure: (a) confabulating a tool failure ("No conversations
        # found, make sure you're logged in") when the tool returned real
        # data, and (b) emitting `> sender (ts): …` quotes whose content
        # doesn't appear in any tool result this turn. When either fires
        # OR F1 retries exhaust, we spoon-feed the raw tool output into
        # ONE more attempt and RE-RUN the detector on the retry output.
        # `_confab_injected` records whether ANY injection has happened;
        # `_confab_retries` caps how many we'll try before degrading.
        _confab_injected: bool = False
        _confab_retries: int = 0

        # Auto-promote-to-background nudge: when a foreground turn drags
        # past N tool-using iterations and the brain hasn't yet called
        # run_background, inject a system message that strongly suggests
        # dispatching the rest async. Long foreground grinds (we've seen
        # 30+ iter on sheet enrichment) burn UI context and freeze chat.
        # Only nudges once per turn; only fires when run_background is
        # actually in the toolset; only for foreground (bg agents
        # shouldn't recursively dispatch). Fixed 2026-04-29 after a
        # Valencia-salons enrichment ran 39 iter foreground without ever
        # promoting. Track tool names called this turn so we know whether
        # the brain already dispatched.
        # Lowered 8 → 5 (Round 2): brain-as-dispatcher pattern. By iter 5
        # the user has been waiting ~30s with frozen UI; that's the line
        # where dispatching beats inline grinding.
        # Lowered 5 → 2 (Round 3): prompt-side fixes (SOUL.md TRIAGE +
        # MiniMax discipline suffix) didn't move the needle. User wants
        # brain-agnostic enforcement — doesn't matter which model is the
        # brain, big tasks must dispatch. With threshold=1 a foreground
        # turn gets ONE inline tool call to wrap up; on the second the
        # tool list is narrowed to run_background only and the brain
        # physically can't keep grinding. 1-tool quick tasks ("price of
        # X", "save this") finish before the check ever fires. Live
        # 2026-05-13 monitoring (find-jobs / show-profile / show-settings)
        # showed brain consistently burning 3 inline tool calls on
        # multi-step intents before threshold=2 fired — wasted ~15-25 s
        # per turn that the runtime ended up dispatching anyway via the
        # text-only failsafe. Lowering by one cuts that flat. The
        # _only_readonly_so_far gate below still prevents false-positive
        # promotion on pure-read intents like list_tasks / list_jobs.
        _PROMOTE_BG_AT_ITER = 1
        _called_tool_names: set[str] = set()
        _promoted_to_bg = False
        # Hard-enforce AUTO-PROMOTE: when set, the next iteration's tool list
        # is narrowed to ONLY ``run_background`` so the brain physically can't
        # drift back to inline grinding. Soft system messages alone weren't
        # enough — MiniMax M2.7 ignored them and ran 22+ extra iters with
        # `browser` / `use_host_browser` calls (see 2026-05-04 Upwork debug).
        _force_dispatch_only = False
        # Thin-router (LAZYCLAW_THIN_ROUTER): brain is a pure router — at most
        # ONE inline domain (non-meta) tool call per turn, then tools narrow to
        # meta-only so it MUST delegate. Default off → zero behavior change.
        import os as _os
        _thin_router = (
            _os.environ.get("LAZYCLAW_THIN_ROUTER", "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        _promote_iter: int | None = None
        # Thin-router cap engagement tracking — mirrors AUTO-PROMOTE's
        # (_force_dispatch_only, _promote_iter) pair. The cap only narrows
        # `tools` to meta-only; it never sets _force_dispatch_only, so the
        # AUTO-PROMOTE chat-responsiveness failsafes never fire under the
        # flag. _thin_router_capped records that the cap actually engaged and
        # _cap_iter records WHEN, so the cap-aware failsafe below can auto-
        # submit to task_runner one iter later if the brain still refuses to
        # delegate (guarantees the chat never freezes to max_iterations).
        _thin_router_capped = False
        _cap_iter: int | None = None
        # 3-strikes failure tracking — when the same MCP tool returns an
        # error 3 times this turn, break the loop with a deterministic user-
        # facing handoff (e.g. "log in at upwork.com/nx/find-work, reply
        # 'continue'") instead of grinding the safety cap or fabricating a
        # fake CLI command. Keyed by canonical tool name (mcp_<uuid>_ prefix
        # stripped) so the counter survives the per-call UUID volatility.
        _tool_failure_count: dict[str, int] = {}
        _three_strikes_break: tuple[str, str] | None = None  # (tool_name, last_error_tail)
        # Browser fallback after repeated channel-MCP failures — pending
        # is set inside the tool-result loop (failure count hits 2) and
        # flushed post-loop (suppression lift + schema inject + system
        # msg) so the system message can't slot between a tool_use and
        # its tool_result. The injected latch makes it fire at most ONCE
        # per turn. See _should_inject_browser_fallback.
        _browser_fallback_pending: tuple[str, str] | None = None  # (tool_name, error_tail)
        _browser_fallback_injected = False

        response = None
        iteration = 0
        try:
            for iteration in range(max_iterations):
                if cancel_token.is_cancelled:
                    if self._team_lead and _fg_task_id:
                        self._team_lead.cancel(_fg_task_id)
                    if _delegate_registered and self.registry:
                        self.registry.unregister("delegate")
                    await cb.on_event(AgentEvent("done", "Cancelled", {}))
                    return "Operation cancelled."

                # ── Drain side-channel notes from the user (WebSocket) ────
                # If the user typed something while we were working, inject
                # it as a system message so the agent can pivot.
                try:
                    _popper = getattr(cb, "pop_side_notes", None)
                    if callable(_popper):
                        _notes = _popper()
                        for _note in _notes:
                            messages.append(LLMMessage(
                                role="system",
                                content=(
                                    f"[User side-note mid-task]: {_note}\n"
                                    "Acknowledge briefly and incorporate into "
                                    "your current work if relevant."
                                ),
                            ))
                            await cb.on_event(AgentEvent(
                                "side_note_ack",
                                f"Got it: {_note[:60]}",
                                {"note": _note},
                            ))
                except Exception:
                    pass

                # TAOR phase: entering "think" — LLM planning / reasoning.
                try:
                    await cb.on_event(AgentEvent(
                        "phase", "think",
                        {"phase": "think", "iteration": iteration},
                    ))
                    if self._team_lead and _fg_task_id:
                        self._team_lead.update_phase(_fg_task_id, "think")
                except Exception:
                    pass

                # Prune old tool results to save tokens
                # Early iterations: keep 2 full results for context
                # Later iterations: keep only 1 (agent already has the gist)
                if iteration > 0:
                    keep_n = 1 if iteration >= 3 else 2
                    messages = _prune_old_tool_results(messages, keep_last_n=keep_n)

                # ── Token-based context compaction ────────────────────
                # Check estimated token count before every LLM call.
                # If above 80% of the model's context limit, summarise
                # older turns with a fast model to free headroom.
                if needs_tools:
                    from lazyclaw.memory.context_compactor import (
                        estimate_tokens,
                        get_context_limit,
                        compact_messages as _compact_messages,
                        COMPACTION_THRESHOLD,
                    )
                    _model_name = (
                        self.eco_router.last_routing.model
                        if self.eco_router and self.eco_router.last_routing
                        else None
                    )
                    _ctx_limit = get_context_limit(_model_name)
                    _est_tokens = estimate_tokens(messages, tools)
                    if _est_tokens > int(_ctx_limit * COMPACTION_THRESHOLD):
                        _compact_result = await _compact_messages(
                            self.eco_router, self.config, user_id,
                            messages,
                            tools=tools,
                            current_turn=iteration,
                        )
                        if _compact_result.did_compact:
                            messages = list(_compact_result.messages)
                            await cb.on_event(AgentEvent(
                                COMPACTION_BOUNDARY,
                                (
                                    f"Context compacted at iteration {iteration}: "
                                    f"{_compact_result.before_tokens:,} → "
                                    f"{_compact_result.after_tokens:,} tokens "
                                    f"({_compact_result.turns_compacted} older turns summarised)"
                                ),
                                {
                                    "iteration": iteration,
                                    "before_tokens": _compact_result.before_tokens,
                                    "after_tokens": _compact_result.after_tokens,
                                    "context_limit": _ctx_limit,
                                    "turns_compacted": _compact_result.turns_compacted,
                                },
                            ))

                # ── Browser action planner injection ──────────────────
                # At iteration 0, if this looks like a browser task, ask
                # the brain LLM to output a plan JSON before acting.
                # No extra LLM call — plan + first action in one response.
                if iteration == 0 and not _plan_state.plan_injected and tools:
                    _plan_prompt = make_plan_injection_prompt(message)
                    messages.append(LLMMessage(role="system", content=_plan_prompt))
                    from dataclasses import replace as _dc_replace
                    _plan_state = _dc_replace(_plan_state, plan_injected=True)
                    logger.info("Browser action planner: injected planning prompt")

                # ── Research strategy requirements injection ──────────────
                # At iteration 0, if this is a research task, ask the LLM to
                # list what information it needs before starting to browse.
                if (
                    iteration == 0
                    and not _research_prompt_injected
                    and _research_state is not None
                    and tools
                ):
                    _research_prompt = make_requirements_prompt(_research_state.query)
                    messages.append(LLMMessage(role="system", content=_research_prompt))
                    _research_prompt_injected = True
                    logger.info("Research strategy: injected requirements prompt")

                # ── TAOR Phase 1: PLAN ────────────────────────────────────
                # For non-LOW effort tasks, inject a Claude-optimized XML
                # planning prompt so the model decomposes the task and
                # identifies required tools before taking action.
                # Leverages Claude's strong XML structured reasoning.
                # On verify-retry, _taor_retry_context carries failure context
                # so the model adjusts its approach.
                if (
                    iteration == 0
                    and _effort != EffortLevel.LOW
                    and tools
                    and needs_tools
                ):
                    # Plan-mode override: if the user already approved a
                    # user-facing plan above, feed THAT into the loop
                    # instead of asking the model to re-plan. Keeps the
                    # approved intent intact and saves an LLM call.
                    if _plan_mode_used and _plan_text_approved:
                        # Auto-background plans (≥ N steps) carry their
                        # own non-negotiable instruction. Wrapping them
                        # with "execute step by step" contradicts the
                        # "first tool call MUST be run_background"
                        # directive — strip the wrapper for that branch.
                        if _plan_text_approved.startswith(_AUTO_BG_PLAN_PREFIX):
                            messages.append(LLMMessage(
                                role="system",
                                content=(
                                    "The user has REVIEWED AND APPROVED this "
                                    "plan. Follow the embedded instruction "
                                    "EXACTLY — it overrides any prior routing "
                                    "rules.\n\n"
                                    f"{_plan_text_approved}"
                                ),
                            ))
                            logger.info(
                                "TAOR Plan: AUTO_BG plan injected (lane will "
                                "free after run_background dispatch)",
                            )
                        else:
                            messages.append(LLMMessage(
                                role="system",
                                content=(
                                    "The user has REVIEWED AND APPROVED this plan. "
                                    "Execute it step by step — do NOT re-plan, do "
                                    "NOT ask for confirmation again.\n\n"
                                    f"{_plan_text_approved}"
                                ),
                            ))
                            logger.info(
                                "TAOR Plan: using user-approved plan (no re-plan)",
                            )
                    else:
                        _taor_plan = make_plan_prompt(
                            message, _effort, _taor_retry_context,
                        )
                        messages.append(LLMMessage(
                            role="system", content=_taor_plan,
                        ))
                        logger.info(
                            "TAOR Plan phase injected (effort=%s, retry=%s)",
                            _effort, _taor_retry_context is not None,
                        )

                # THIN-ROUTER: at most ONE inline domain (non-meta) tool call
                # per turn; once made, narrow to meta-only so a 2nd domain call
                # is impossible and the brain MUST delegate. Default off.
                if (
                    _thin_router
                    and not _force_dispatch_only
                    and tools
                    and any(n not in _META_TOOLS for n in _called_tool_names)
                ):
                    _meta_only = [
                        t for t in tools
                        if t.get("function", {}).get("name") in _META_TOOLS
                    ]
                    if _meta_only and len(_meta_only) != len(tools):
                        logger.info(
                            "THIN-ROUTER: tools narrowed %d → %d (meta-only) "
                            "after 1 inline action — brain must delegate",
                            len(tools), len(_meta_only),
                        )
                        _tr_other = {
                            t.get("function", {}).get("name")
                            for t in tools
                            if t.get("function", {}).get("name") not in _META_TOOLS
                        }
                        _tr_other.discard(None)
                        _suppressed_tool_names |= _tr_other  # type: ignore[arg-type]
                        tools = _meta_only
                        if not _thin_router_capped:  # cap engaged (first time)
                            _thin_router_capped = True
                            _cap_iter = iteration  # anchor for failsafe below

                # Hard-enforce AUTO-PROMOTE: previous iter set the flag, so
                # this iter's tool list is restricted to ONLY run_background.
                # Anything else the brain calls is dropped by the existing
                # _suppressed_tool_names path. The system message stays as
                # the "why" for the model; the tool list is the "how".
                if _force_dispatch_only and tools:
                    _bg_only = [
                        t for t in tools
                        if t.get("function", {}).get("name") == "run_background"
                    ]
                    if _bg_only:
                        if len(_bg_only) != len(tools):
                            logger.info(
                                "AUTO-PROMOTE enforced: tools narrowed %d → 1 (run_background only)",
                                len(tools),
                            )
                            # Suppress every other tool name so the late-inject
                            # path can't resurrect them from history memory.
                            _other_names = {
                                t.get("function", {}).get("name")
                                for t in tools
                                if t.get("function", {}).get("name") != "run_background"
                            }
                            _other_names.discard(None)
                            _suppressed_tool_names |= _other_names  # type: ignore[arg-type]
                        tools = _bg_only

                kwargs: dict = {}
                if tools:
                    kwargs["tools"] = tools
                logger.info("AGENTIC LOOP iter=%d: tools=%d, messages=%d",
                            iteration, len(tools), len(messages))

                # Role routing: brain for strategy + final answers,
                # worker for mid-chain tool orchestration (cheaper/local).
                #
                # iteration 0: brain picks strategy + first tools
                # iteration 1+: worker handles tool chains (just orchestrating)
                # escalated: brain takes back over for quality
                # last iteration hint: if previous response had tool calls and
                #   this iteration might be the final answer, use brain.
                iter_model = None  # Let eco_router decide based on mode + role
                if iteration == 0:
                    _iter_role = ROLE_BRAIN      # First call: brain picks strategy + tools
                elif _escalated:
                    _iter_role = ROLE_BRAIN      # After escalation: brain takes over
                else:
                    _iter_role = ROLE_WORKER     # Mid-chain: worker orchestrates tools

                model_name = "brain"
                # Show actual routing model if available
                if self.eco_router and self.eco_router.last_routing:
                    model_name = self.eco_router.last_routing.display_name
                logger.info("Iteration %d: calling %s", iteration + 1, model_name)
                await cb.on_event(AgentEvent(
                    "llm_call",
                    f"Thinking ({model_name})...",
                    {"iteration": iteration + 1, "model": model_name},
                ))
                await recorder.record_llm_call(
                    model=None, message_count=len(messages), has_tools=bool(tools),
                )

                from lazyclaw.llm.providers.base import LLMResponse as _LLMResp

                streamed_content = ""
                response = None

                # When tools are available, use non-streaming chat() to reliably
                # capture tool calls. MLX streaming drops tool_calls in many cases.
                # For pure chat (no tools), stream for real-time UX.
                if tools:
                    try:
                        response = await self.eco_router.chat(
                            messages, user_id=user_id, model=iter_model,
                            role=_iter_role, **kwargs
                        )
                        if response.content:
                            # Strip <plan>/<taor_plan> tags and <think>... thinking blocks
                            _display_content = response.content
                            if "<plan>" in _display_content:
                                _display_content = re.sub(
                                    r"<plan>.*?</plan>\s*", "", _display_content, flags=re.DOTALL
                                ).strip()
                            if "<taor_plan>" in _display_content:
                                _display_content = re.sub(
                                    r"<taor_plan>.*?</taor_plan>\s*", "", _display_content, flags=re.DOTALL
                                ).strip()
                            # Strip <think>... thinking tags (MiniMax reasoning output)
                            if "<think>" in _display_content:
                                _display_content = re.sub(
                                    r"<think>.*?</think>\s*", "", _display_content, flags=re.DOTALL
                                ).strip()
                            if _display_content:
                                await cb.on_event(AgentEvent(
                                    "token", _display_content, {"model": response.model},
                                ))
                    except Exception as exc:
                        # Rate-limit auto-escalation: MiniMax Token Plan 429s
                        # (`rate_limit_error`, code 2062) and generic provider
                        # 429s should re-route to the configured fallback
                        # model instead of dumping JSON into the user's chat.
                        if _is_rate_limit_exception(exc) and not _escalated:
                            try:
                                _fallback_name = (
                                    await self.eco_router.get_fallback_model(user_id)
                                )
                            except Exception:
                                _fallback_name = None
                            if _fallback_name:
                                logger.warning(
                                    "Brain rate-limited (%s) — escalating to fallback %s",
                                    type(exc).__name__, _fallback_name,
                                )
                                _escalated = True
                                _escalation_iter = iteration
                                iter_model = _fallback_name
                                _iter_role = "escalation"
                                streamed_content = ""
                                continue
                        logger.error("Chat failed: %s", exc, exc_info=True)
                        # Sanitize 429 payloads — never paste raw provider JSON
                        # into the user-facing message.
                        if _is_rate_limit_exception(exc):
                            _user_msg = (
                                "Brain is rate-limited right now. "
                                "Retry in a minute, or set a fallback model "
                                "with `/mode` so this re-routes automatically."
                            )
                        else:
                            _user_msg = f"Sorry, an error occurred: {exc}"
                        response = _LLMResp(
                            content=_user_msg,
                            model="unknown",
                            tool_calls=[],
                        )
                else:
                    # No tools — stream for real-time output
                    # Buffer <think>...</think> and <taor_plan>...</taor_plan> — don't show to user
                    _in_think_block = False
                    _think_buffer = ""
                    _in_taor_block = False
                    _taor_buffer = ""
                    _taor_close_tag = "</taor_plan>"
                    try:
                        async for chunk in self.eco_router.stream_chat(
                            messages, user_id=user_id, model=iter_model,
                            role=_iter_role, **kwargs
                        ):
                            if chunk.delta:
                                streamed_content += chunk.delta

                                # Buffer thinking, only emit real content.
                                # ALSO relay thinking chunks to the UI as
                                # `thinking_delta` events so the chat can
                                # show a collapsible reasoning panel.
                                text = chunk.delta
                                if "<think>" in streamed_content and not _in_think_block:
                                    _in_think_block = True
                                    _think_buffer = ""
                                if _in_think_block:
                                    _think_buffer += text
                                    if "</think>" in _think_buffer:
                                        before, after_raw = _think_buffer.split(
                                            "</think>", 1,
                                        )
                                        # Emit the closing reasoning chunk
                                        # (minus the </think> tag itself).
                                        _reasoning_chunk = before.replace(
                                            "<think>", "",
                                        )
                                        if _reasoning_chunk:
                                            await cb.on_event(AgentEvent(
                                                "thinking_delta",
                                                _reasoning_chunk, {},
                                            ))
                                        await cb.on_event(AgentEvent(
                                            "thinking_done", "", {},
                                        ))
                                        after = after_raw.strip()
                                        _in_think_block = False
                                        _think_buffer = ""
                                        if after:
                                            text = after
                                        else:
                                            continue
                                    else:
                                        # Streaming reasoning mid-block —
                                        # surface the delta, strip any
                                        # leading <think> opener.
                                        _r_chunk = text.replace("<think>", "")
                                        if _r_chunk:
                                            await cb.on_event(AgentEvent(
                                                "thinking_delta", _r_chunk, {},
                                            ))
                                        continue

                                # Buffer <taor_plan> and <plan> blocks
                                for _tag in ("<taor_plan>", "<plan>"):
                                    _close = _tag.replace("<", "</")
                                    if _tag in streamed_content and not _in_taor_block:
                                        _in_taor_block = True
                                        _taor_buffer = ""
                                        _taor_close_tag = _close
                                        break
                                if _in_taor_block:
                                    _taor_buffer += text
                                    if _taor_close_tag in _taor_buffer:
                                        after = _taor_buffer.split(_taor_close_tag, 1)[1].strip()
                                        _in_taor_block = False
                                        _taor_buffer = ""
                                        if after:
                                            text = after
                                        else:
                                            continue
                                    else:
                                        continue

                                await cb.on_event(AgentEvent(
                                    "token", text, {"model": chunk.model},
                                ))

                            if chunk.done:
                                response = _LLMResp(
                                    content=streamed_content,
                                    model=chunk.model,
                                    usage=chunk.usage,
                                    tool_calls=chunk.tool_calls,
                                )
                    except Exception as exc:
                        # Mirror the chat-path rate-limit escalation: switch
                        # to the configured fallback model on first 429.
                        if _is_rate_limit_exception(exc) and not _escalated:
                            try:
                                _fallback_name = (
                                    await self.eco_router.get_fallback_model(user_id)
                                )
                            except Exception:
                                _fallback_name = None
                            if _fallback_name:
                                logger.warning(
                                    "Stream rate-limited (%s) — escalating to fallback %s",
                                    type(exc).__name__, _fallback_name,
                                )
                                _escalated = True
                                _escalation_iter = iteration
                                iter_model = _fallback_name
                                _iter_role = "escalation"
                                streamed_content = ""
                                await cb.on_event(AgentEvent("stream_done", "", {}))
                                continue
                        logger.error("Streaming failed: %s", exc, exc_info=True)
                        await cb.on_event(AgentEvent("stream_done", "", {}))
                        if _is_rate_limit_exception(exc):
                            _user_msg = (
                                "Brain is rate-limited right now. "
                                "Retry in a minute, or set a fallback model "
                                "with `/mode` so this re-routes automatically."
                            )
                        else:
                            _user_msg = f"Sorry, an error occurred: {exc}"
                        response = _LLMResp(
                            content=_user_msg,
                            model="unknown",
                            tool_calls=[],
                        )

                if response is None:
                    response = _LLMResp(content=streamed_content or "No response received.", model="unknown")

                logger.info(
                    "LLM response: model=%s, content_len=%d, tool_calls=%d",
                    response.model,
                    len(response.content or ""),
                    len(response.tool_calls or []),
                )

                # Track router fallback + final model so the "done" event
                # can tell channels when Sonnet silently became Haiku.
                _resp_fallback = getattr(response, "fallback_reason", None)
                if _resp_fallback:
                    _last_fallback_reason = _resp_fallback
                if response.model and response.model != "unknown":
                    _last_model_used = response.model

                await recorder.record_llm_response(
                    content=response.content or "",
                    model=response.model,
                    has_tool_calls=bool(response.tool_calls),
                )

                # Report token usage
                usage = response.usage or {}
                total_tokens = usage.get("total_tokens", 0)
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                eco_mode = usage.get("eco_mode")  # "eco", "hybrid_free", or None (paid)
                _session_tokens += total_tokens
                await cb.on_event(AgentEvent(
                    "tokens",
                    f"{total_tokens} tokens ({prompt_tokens} in, {completion_tokens} out)",
                    {"total": total_tokens, "prompt": prompt_tokens,
                     "completion": completion_tokens, "model": response.model,
                     "eco_mode": eco_mode,
                     "fallback_reason": _resp_fallback},
                ))

                # ── Parse browsing plan from LLM response ─────────────
                # If the planner injected a prompt, try to extract the JSON
                # plan block from the response content (no extra LLM call).
                if (
                    response.content
                    and _plan_state.plan is None
                    and response.tool_calls
                    and any(tc.name == "browser" for tc in response.tool_calls)
                ):
                    _parsed_plan = parse_plan_from_response(response.content)
                    if _parsed_plan is not None:
                        from dataclasses import replace as _dc_replace
                        _plan_state = _dc_replace(_plan_state, plan=_parsed_plan)
                        await cb.on_event(AgentEvent(
                            BROWSER_PLAN,
                            f"Plan: {_parsed_plan.goal}",
                            {
                                "goal": _parsed_plan.goal,
                                "steps": [s.description for s in _parsed_plan.steps],
                                "current_step": _parsed_plan.current_step,
                            },
                        ))

                # ── Parse research requirements from first response ────────
                # If research mode is active and requirements not yet set,
                # extract the JSON array the LLM should have output.
                if (
                    _research_state is not None
                    and not _research_state.info_requirements
                    and response.content
                    and iteration == 0
                ):
                    _reqs = parse_requirements_from_response(response.content)
                    if _reqs:
                        from dataclasses import replace as _dc_replace
                        _research_state = _dc_replace(
                            _research_state,
                            info_requirements=tuple(_reqs),
                            gaps=tuple(_reqs),
                        )

                # Debug: log tool state
                logger.info(
                    "TOOL STATE: tools_sent=%d, tool_calls_received=%d, names=%s",
                    len(tools),
                    len(response.tool_calls or []),
                    [tc.name for tc in (response.tool_calls or [])],
                )

                # Track tool names this turn for the auto-promote heuristic.
                for _tc in response.tool_calls or []:
                    _called_tool_names.add(_tc.name)

                # If no tools were provided but LLM returned tool_calls
                # (hallucination from history patterns), ignore them
                if not tools and response.tool_calls:
                    logger.warning(
                        "LLM returned %d tool_calls but no tools were provided — ignoring",
                        len(response.tool_calls),
                    )
                    response = _LLMResp(
                        content=response.content or "Hello! How can I help you?",
                        model=response.model,
                        usage=response.usage,
                        tool_calls=[],
                    )

                # Filter out hallucinated tool calls for tools not in the current list.
                # If a tool is not in the sent set but exists in the full registry,
                # dynamically inject it (the LLM remembered it from history).
                if tools and response.tool_calls:
                    _valid_names = {
                        t.get("function", {}).get("name") for t in tools
                    }
                    # Before dropping, try to inject missing tools from registry.
                    # BUT — never re-inject a tool we DELIBERATELY suppressed for
                    # this turn (e.g. ``browser`` when Upwork/WhatsApp/etc. MCP
                    # routing took over). Without this check the LLM's history-
                    # memory of ``browser`` would silently undo the channel
                    # routing decision and the agent would loop on browser
                    # actions instead of using the proper MCP tool.
                    _injected_count = 0
                    _resurrect_blocked: list[str] = []
                    for tc in response.tool_calls:
                        if tc.name not in _valid_names and self.registry:
                            if tc.name in _suppressed_tool_names:
                                _resurrect_blocked.append(tc.name)
                                continue
                            if (
                                _thin_router_capped
                                and tc.name not in _META_TOOLS
                            ):
                                # THIN-ROUTER cap: after the one inline
                                # action only meta tools are callable.
                                # The narrowing only suppresses tools that
                                # were IN the sent list — domain tools the
                                # LLM remembers from history (e.g.
                                # upwork_inbox_check on 2026-06-10 16:20)
                                # would otherwise re-enter through this
                                # late-inject door and bypass the cap.
                                _resurrect_blocked.append(tc.name)
                                continue
                            _schema = self.registry.get_tool_schema(tc.name)
                            if _schema is not None:
                                tools.append(_schema)
                                _valid_names.add(tc.name)
                                _injected_count += 1
                    if _injected_count:
                        logger.info(
                            "Late-injected %d tools from registry (LLM remembered from history)",
                            _injected_count,
                        )
                    if _resurrect_blocked:
                        logger.info(
                            "Refused to resurrect channel-suppressed tools from history: %s",
                            _resurrect_blocked,
                        )

                    _valid_calls = [
                        tc for tc in response.tool_calls if tc.name in _valid_names
                    ]
                    _dropped = len(response.tool_calls) - len(_valid_calls)
                    if _dropped > 0:
                        _dropped_names = [tc.name for tc in response.tool_calls if tc.name not in _valid_names]
                        logger.warning(
                            "Dropped %d hallucinated tool calls (not in current tools): %s",
                            _dropped, _dropped_names,
                        )
                        if _valid_calls:
                            # Mixed success — reset the hallucination counter.
                            _halluc_retries = 0
                            response = _LLMResp(
                                content=response.content,
                                model=response.model,
                                usage=response.usage,
                                tool_calls=_valid_calls,
                            )
                        else:
                            # ALL tool calls were hallucinated.
                            _halluc_retries += 1
                            _first_bad = _dropped_names[0]
                            _correction = _build_hallucination_correction(
                                _first_bad, _valid_names, self.registry,
                            )

                            if _halluc_retries > _HALLUC_MAX_RETRIES:
                                # AUTO-PROMOTE failsafe (hallucination-cap path):
                                # if the runtime had narrowed the tool list to
                                # ONLY run_background and the brain kept calling
                                # suppressed tools (e.g. MiniMax M2.7 invoking
                                # the now-dead `browser` 3× until the cap), we
                                # already have everything we need to do the
                                # dispatch ourselves: original user message,
                                # user_id, working task_runner. Synthesize the
                                # run_background call here instead of bailing
                                # with an "tool doesn't exist" error. Mirrors
                                # the text-only failsafe a few lines below.
                                if (
                                    _force_dispatch_only
                                    and self._task_runner is not None
                                    and user_id is not None
                                    and not getattr(self, "is_background", False)
                                ):
                                    logger.warning(
                                        "AUTO-PROMOTE failsafe (hallucination "
                                        "cap): brain looped on suppressed tool "
                                        "%r after AUTO-PROMOTE narrowed tools "
                                        "to run_background only — runtime "
                                        "auto-submitting original message to "
                                        "task_runner",
                                        _first_bad,
                                    )
                                    try:
                                        from lazyclaw.runtime.agent_settings import (
                                            get_agent_settings,
                                        )
                                        _agent_settings = await get_agent_settings(
                                            self.config, user_id,
                                        )
                                        _bg_task_id = await self._task_runner.submit(
                                            user_id=user_id,
                                            instruction=message,
                                            name=None,
                                            timeout=_agent_settings.get(
                                                "specialist_timeout_s", 600,
                                            ),
                                            callback=callback,
                                            # Fix H: source="brain" without a
                                            # fanout_group_id makes chat_ws's
                                            # event-bus pump filter drop the
                                            # task_event_bus delivery (line
                                            # ~519) while the original
                                            # callback path still surfaces
                                            # bg_done to chat — kills the
                                            # duplicate "Background task
                                            # completed" cards in web UI.
                                            # task_runner.submit only routes
                                            # through the brain-fanout
                                            # consolidator when BOTH source
                                            # and fanout_group_id are set
                                            # (see line 340), so source
                                            # alone is inert there.
                                            source="brain",
                                            caller_depth=self._depth,
                                        )
                                        if self._team_lead and _fg_task_id:
                                            self._team_lead.cancel(_fg_task_id)
                                            _fg_task_id = None
                                        await cb.on_event(AgentEvent(
                                            FAST_DISPATCH,
                                            "Auto-promoted to background after "
                                            "hallucination cap",
                                            {
                                                "task_id": _bg_task_id,
                                                "trigger": "hallucination_cap",
                                                "suppressed_tool": _first_bad,
                                            },
                                        ))
                                        await cb.on_event(AgentEvent(
                                            "done", "Dispatched", {},
                                        ))
                                        if _delegate_registered and self.registry:
                                            self.registry.unregister("delegate")
                                        return (
                                            "Continuing in background — will "
                                            "report back when done."
                                        )
                                    except Exception:
                                        logger.exception(
                                            "AUTO-PROMOTE failsafe (hallucination "
                                            "cap) failed; falling back to "
                                            "tool-doesn't-exist bail",
                                        )

                                # Bail — LLM is stuck on non-existent tool(s).
                                # Surface to user instead of burning more iterations.
                                _bail_msg = (
                                    f"I tried to use **{_first_bad}** but that tool "
                                    f"doesn't exist in my current toolset. "
                                    f"{_correction_hint_for_user(_first_bad, self.registry)}"
                                )
                                logger.warning(
                                    "Hallucination cap reached (%d retries on %r) — bailing",
                                    _halluc_retries, _first_bad,
                                )
                                await cb.on_event(AgentEvent(
                                    "tool_result",
                                    f"Bailed: tool {_first_bad!r} does not exist",
                                    {"reason": "hallucination_cap",
                                     "requested_tool": _first_bad,
                                     "retries": _halluc_retries},
                                ))
                                await cb.on_event(AgentEvent("done", _bail_msg, {}))
                                if self._team_lead and _fg_task_id:
                                    self._team_lead.fail(
                                        _fg_task_id,
                                        f"hallucination cap on {_first_bad!r}",
                                    )
                                    _fg_task_id = None
                                return _bail_msg

                            messages.append(LLMMessage(role="assistant", content=response.content or ""))
                            messages.append(LLMMessage(role="user", content=_correction))
                            logger.warning(
                                "Injecting correction for hallucinated tools (retry %d/%d)",
                                _halluc_retries, _HALLUC_MAX_RETRIES,
                            )
                            continue  # Retry the agentic loop iteration
                    else:
                        # No hallucinations this round — reset the counter so
                        # a mid-session slip doesn't carry over to new tasks.
                        _halluc_retries = 0

                if not response.tool_calls:
                    _final_content = response.content or streamed_content or ""

                    # AUTO-PROMOTE text-only failsafe: if the runtime already
                    # narrowed the tool list to ONLY run_background but the
                    # brain dodged it by returning a text summary instead of
                    # dispatching, force the dispatch via task_runner here.
                    # The downstream failsafe at the bottom of the iter loop
                    # (line ~4491) only catches the "next iter, still calling
                    # tools" path — a text-only response exits the loop right
                    # below this block, so without this check the user gets
                    # the brain's summary instead of the background dispatch.
                    # Fixes the 16:04:24 turn-1 case (MiniMax wrote a job
                    # listing markdown table instead of run_background).
                    #
                    # Meta-question guard: SKIP the failsafe when the user's
                    # original message reads like a reflection question
                    # ("why did you …?", "what tools do you have?"). Those
                    # deserve a conversational reply, not a forced bg
                    # dispatch that re-invokes the very tool the user is
                    # asking about. 2026-05-16 loop incident: "why u usde
                    # code ahent ???" got auto-promoted → bg agent re-fired
                    # claude-code MCP on the meta question. See
                    # ``_is_meta_question`` + tests/test_auto_promote_meta_question_guard.py.
                    if (
                        _force_dispatch_only
                        and _promote_iter is not None
                        and "run_background" not in _called_tool_names
                        and self._task_runner is not None
                        and user_id is not None
                        and not _is_meta_question(message)
                    ):
                        logger.warning(
                            "AUTO-PROMOTE failsafe (text-only path): brain "
                            "returned text instead of dispatching at iter=%d "
                            "(promote_iter=%d) — runtime auto-submitting "
                            "original message to task_runner",
                            iteration, _promote_iter,
                        )
                        try:
                            from lazyclaw.runtime.agent_settings import (
                                get_agent_settings,
                            )
                            _agent_settings = await get_agent_settings(
                                self.config, user_id,
                            )
                            _bg_task_id = await self._task_runner.submit(
                                user_id=user_id,
                                instruction=message,
                                name=None,
                                timeout=_agent_settings.get(
                                    "specialist_timeout_s", 600,
                                ),
                                callback=callback,
                                # Fix H — see hallucination-cap failsafe
                                # above for the rationale.
                                source="brain",
                                caller_depth=self._depth,
                            )
                            if self._team_lead and _fg_task_id:
                                self._team_lead.cancel(_fg_task_id)
                                _fg_task_id = None
                            await cb.on_event(AgentEvent(
                                FAST_DISPATCH,
                                "Auto-promoted to background after text-only "
                                "refusal",
                                {"task_id": _bg_task_id},
                            ))
                            await cb.on_event(AgentEvent(
                                "done", "Dispatched", {},
                            ))
                            if _delegate_registered and self.registry:
                                self.registry.unregister("delegate")
                            return (
                                "Continuing in background — will report "
                                "back when done."
                            )
                        except Exception:
                            logger.exception(
                                "AUTO-PROMOTE failsafe (text-only) failed; "
                                "continuing inline",
                            )

                    # Action-claim hallucination: the brain returned text
                    # like "Already on it! Background task is running…" or
                    # "I'll ping you on Telegram when done" but emitted ZERO
                    # tool_use blocks. Catches cheap brains (MiniMax-M2.7,
                    # Gemma) narrating actions instead of dispatching them.
                    # Inject a correction and re-roll up to _HALLUC_MAX_RETRIES
                    # times; after that, surface a corrected message so the
                    # user sees honesty rather than the lie.
                    _claim_match = (
                        _ACTION_CLAIM_RE.search(_final_content)
                        if _final_content else None
                    )
                    if (
                        _claim_match
                        and tools
                        and _halluc_retries < _HALLUC_MAX_RETRIES
                        # If the brain ALREADY dispatched async work this
                        # turn, a "I'll send the consolidated result shortly"
                        # status is TRUTHFUL — dispatch_subagents auto-
                        # consolidates (RC2) and run_background pushes when
                        # done. Don't treat it as a hallucinated action-claim
                        # or we'd force a confused re-roll / re-dispatch.
                        # (2026-05-29 — pairs with the broadened
                        # _ACTION_CLAIM_RE so legit dispatch statuses survive.)
                        and "dispatch_subagents" not in _called_tool_names
                        and "run_background" not in _called_tool_names
                        # delegate is a real fire-and-forget dispatch too —
                        # a post-delegate "I've dispatched" is truthful.
                        and "delegate" not in _called_tool_names
                    ):
                        _halluc_retries += 1
                        _matched_phrase = _claim_match.group(0)[:60]
                        _correction = (
                            "[SYSTEM: You wrote text claiming a task was "
                            f"started or dispatched (\"{_matched_phrase}\"…) "
                            "but you did NOT emit any tool_use block. The "
                            "user has not had any work done yet. Either:\n"
                            "  1. Call the right tool NOW (run_background, "
                            "google_run_task, dispatch_subagents, "
                            "send_gmail, append_sheet_rows, etc.), OR\n"
                            "  2. Rewrite your reply to honestly tell the "
                            "user nothing has been dispatched and ask for "
                            "what's missing.\n"
                            "Never claim work that wasn't tool-dispatched.]"
                        )
                        messages.append(LLMMessage(
                            role="assistant", content=_final_content,
                        ))
                        messages.append(LLMMessage(
                            role="user", content=_correction,
                        ))
                        logger.warning(
                            "Action-claim hallucination from %s (matched %r) "
                            "— injecting correction (retry %d/%d)",
                            response.model, _matched_phrase,
                            _halluc_retries, _HALLUC_MAX_RETRIES,
                        )
                        # Reset streamed_content so the next iteration's
                        # fresh stream isn't double-prefixed by old text.
                        streamed_content = ""
                        continue

                    # Action-claim retries EXHAUSTED: the brain kept
                    # narrating a dispatch it never made (the SDK-path
                    # failure mode — Opus writes "✅ running in the
                    # background" instead of emitting a run_background
                    # tool_use block). Rather than ship that false "done",
                    # force the dispatch to task_runner — same failsafe as
                    # the AUTO-PROMOTE text-only path, but reachable when the
                    # brain claimed the action at iter 0 (never auto-promoted).
                    # Guards mirror that path: foreground only, real task
                    # runner, not a meta-question, not already dispatched.
                    if (
                        _claim_match
                        and _halluc_retries >= _HALLUC_MAX_RETRIES
                        and self._task_runner is not None
                        and user_id is not None
                        and not getattr(self, "is_background", False)
                        and "run_background" not in _called_tool_names
                        # Already dispatched subagents → the status is true,
                        # do NOT force a redundant background task (RC2).
                        and "dispatch_subagents" not in _called_tool_names
                        # Same for delegate — it already dispatched a worker.
                        and "delegate" not in _called_tool_names
                        and not _is_meta_question(message)
                    ):
                        logger.warning(
                            "Action-claim failsafe: brain claimed dispatch "
                            "%d× without a tool_use block — runtime "
                            "force-submitting original message to task_runner",
                            _halluc_retries,
                        )
                        try:
                            from lazyclaw.runtime.agent_settings import (
                                get_agent_settings,
                            )
                            _agent_settings = await get_agent_settings(
                                self.config, user_id,
                            )
                            _bg_task_id = await self._task_runner.submit(
                                user_id=user_id,
                                instruction=message,
                                name=None,
                                timeout=_agent_settings.get(
                                    "specialist_timeout_s", 600,
                                ),
                                callback=callback,
                                source="brain",
                                caller_depth=self._depth,
                            )
                            if self._team_lead and _fg_task_id:
                                self._team_lead.cancel(_fg_task_id)
                                _fg_task_id = None
                            await cb.on_event(AgentEvent(
                                FAST_DISPATCH,
                                "Force-dispatched after action-claim with no "
                                "tool call",
                                {"task_id": _bg_task_id},
                            ))
                            await cb.on_event(AgentEvent(
                                "done", "Dispatched", {},
                            ))
                            if _delegate_registered and self.registry:
                                self.registry.unregister("delegate")
                            return (
                                "Continuing in background — will report "
                                "back when done."
                            )
                        except Exception:
                            logger.exception(
                                "Action-claim force-dispatch failed; "
                                "continuing inline",
                            )

                    # Nudge: if iteration 0 returned text-only but channel
                    # tools were available, the LLM likely repeated old data
                    # from history instead of calling the tool. Force retry once.
                    if (
                        iteration == 0
                        and _matched_channels
                        and tools
                        and not getattr(self, "_nudged_tool_use", False)
                    ):
                        self._nudged_tool_use = True
                        _channel_tool_names = [
                            t.get("function", {}).get("name", "")
                            for t in tools
                            if any(ch in t.get("function", {}).get("name", "").lower()
                                   for ch in _matched_channels)
                        ][:3]
                        _nudge = (
                            "[SYSTEM: You answered from memory without calling any tool. "
                            "Your data may be stale. Use one of these tools to get LIVE data: "
                            f"{', '.join(_channel_tool_names)}. "
                            "Call the tool NOW — do not repeat old numbers.]"
                        )
                        messages.append(LLMMessage(role="assistant", content=_final_content))
                        messages.append(LLMMessage(role="user", content=_nudge))
                        logger.warning("Nudging tool use: LLM skipped tools for channel query")
                        continue

                    # Research-intent hallucination: user asked the brain to
                    # FIND/RESEARCH/SCRAPE external data (emails, contacts,
                    # public info) but it returned text-only without calling
                    # any web/scraper tool. By definition the answer is
                    # fabricated — the brain has no other source. Catches
                    # MiniMax-M2.7's "Here's the table:" + fake values pattern
                    # that the verb-based _ACTION_CLAIM_RE misses.
                    if (
                        iteration == 0
                        and tools
                        and _final_content
                        and _is_find_contact
                        and not getattr(self, "_nudged_research", False)
                    ):
                        self._nudged_research = True
                        _research_nudge = (
                            "[SYSTEM: The user asked you to FIND external "
                            "data (emails / contacts / public info), but you "
                            "answered without calling any tool. Anything you "
                            "wrote is fabricated. Call one of these now: "
                            "web_search, mcp_scraper_search_google, "
                            "mcp_scraper_extract_entities, "
                            "mcp_scraper_crawl_url, or run_background for a "
                            "multi-target enrichment. Do NOT invent emails or "
                            "contact info — only return values returned by a "
                            "tool. If a tool returns nothing, say so honestly.]"
                        )
                        messages.append(LLMMessage(
                            role="assistant", content=_final_content,
                        ))
                        messages.append(LLMMessage(
                            role="user", content=_research_nudge,
                        ))
                        logger.warning(
                            "Nudging tool use: %s skipped tools for "
                            "research-intent query",
                            response.model,
                        )
                        streamed_content = ""
                        continue

                    # `_history_content` keeps <think>...</think> intact for
                    # multi-turn quality on interleaved-thinking models (MiniMax
                    # M2/M2.7 — see HF model card: "important to retain the
                    # thinking content from the assistant's turns within the
                    # historical messages"). `_final_content` is the user-facing
                    # version with internal tags stripped.
                    _history_content = _final_content
                    if "<taor_plan>" in _history_content:
                        _history_content = re.sub(
                            r"<taor_plan>.*?</taor_plan>\s*",
                            "", _history_content, flags=re.DOTALL,
                        ).strip()
                    if "<plan>" in _history_content:
                        _history_content = re.sub(
                            r"<plan>.*?</plan>\s*",
                            "", _history_content, flags=re.DOTALL,
                        ).strip()
                    _final_content = _history_content
                    if "<think>" in _final_content:
                        _final_content = re.sub(
                            r"<think>.*?</think>\s*", "", _final_content, flags=re.DOTALL
                        ).strip()

                    # Empty response from worker/brain model — escalate to fallback.
                    # Covers: MiniMax error 2013 (empty), Haiku empty response bug,
                    # worker can't produce final answer, etc.
                    # Use the fallback model (different provider) so we don't
                    # loop back to the same broken brain.
                    if (
                        not _final_content.strip()
                        and tools
                        and not _escalated
                    ):
                        try:
                            _fallback_name = await self.eco_router.get_fallback_model(user_id)
                        except Exception:
                            _fallback_name = None
                        logger.warning(
                            "Empty LLM response from %s (usage=%s, tools=%d) — escalating to fallback %s",
                            response.model, response.usage, len(tools), _fallback_name,
                        )
                        _escalated = True
                        _escalation_iter = iteration
                        # Pin to fallback model AND use a non-brain/worker role
                        # so eco_router.chat() takes the explicit-model bypass
                        # (_route_paid) instead of re-picking the broken brain.
                        iter_model = _fallback_name
                        _iter_role = "escalation" if _fallback_name else ROLE_BRAIN
                        streamed_content = ""
                        continue  # Retry with a different provider

                    # F1 grounding enforcement — when a channel-read tool was
                    # called this turn, the reply MUST quote the tool result,
                    # not paraphrase from memory. SOUL.md line 14 is prompt-
                    # only; this is mechanical. Catches the 2026-05-19 16:19
                    # pattern: brain calls upwork_get_messages × 2 +
                    # upwork_last_conversation × 1, then emits 8534 chars of
                    # "Already pulled the thread — Vato agreed $150" with NO
                    # quote block and FAKE numbers from training-data bias.
                    # See module-level _check_f1_violation for the rules.
                    if (
                        _final_content
                        and _f1_retries < _F1_MAX_RETRIES
                        and any(
                            _is_channel_read_tool(n)
                            for n in _tool_call_history
                        )
                    ):
                        # F1 phase-2 lite (observation-only) — verify each
                        # `> sender (ts): content` line in the draft actually
                        # appears in the fresh tool_results from this turn.
                        # Catches "quote-from-garbage" (e.g. today's 13:34
                        # incident: bad room_id arg made the MCP scrape
                        # unrelated DOM, brain quoted it faithfully).
                        # F1_PHASE2_ENFORCE is False — logs WARN only, no
                        # block / no re-prompt. Telemetry first.
                        try:
                            from lazyclaw.runtime.f1_content_verifier import (
                                observe_or_enforce as _f1p2_observe,
                            )
                            _f1p2_observe(_final_content, _tool_results)
                        except Exception:
                            logger.debug(
                                "F1 phase-2 observer raised (non-fatal)",
                                exc_info=True,
                            )

                        _violation = _check_f1_violation(
                            _final_content, _tool_results,
                        )
                        if _violation:
                            _f1_retries += 1
                            _f1_correction = (
                                f"[SYSTEM: F1 grounding violation — {_violation}. "
                                "You called a channel-read tool this turn — its "
                                "result is the most recent tool message above and "
                                "is wrapped in [LIVE READ @ … — AUTHORITATIVE] / "
                                "[END LIVE READ] markers. Your reply MUST: "
                                "(1) begin with a verbatim quote block of the 3 "
                                "most recent contact-side messages, formatted "
                                "`> {sender} ({timestamp}): {exact content}` "
                                "copied character-for-character from the tool "
                                "result, (2) contain no 'from memory' / "
                                "'already pulled' phrases, (3) trace every "
                                "concrete claim (dollar amount, date, scope "
                                "item, deliverable, device, platform) to a "
                                "quoted line. Rewrite using ONLY the LIVE READ "
                                "content. If a fact is not in the LIVE READ, "
                                "write 'not specified — needs to be asked' "
                                "instead of guessing from prior context.]"
                            )
                            messages.append(LLMMessage(
                                role="assistant", content=_final_content,
                            ))
                            messages.append(LLMMessage(
                                role="user", content=_f1_correction,
                            ))
                            logger.warning(
                                "F1 grounding violation (retry %d/%d): %s | "
                                "tools_called=%s | draft_head=%r",
                                _f1_retries, _F1_MAX_RETRIES, _violation,
                                _tool_call_history[-5:],
                                (_final_content or "")[:160],
                            )
                            streamed_content = ""
                            continue
                        # F1 passed — capped retries are also a pass: we ship
                        # the draft with a log warning rather than burn more
                        # iterations on a wedged brain.

                    # F1 confabulation backstop — runs on EVERY draft of a
                    # channel-read turn (not just the first one). Catches:
                    #   (a) The 2026-05-20 14:23 incident: confabulated tool
                    #       failure ("No conversations found, make sure
                    #       you're logged in") when tool returned real data.
                    #   (b) Made-up `> sender (ts): …` quotes whose content
                    #       doesn't appear in any tool result this turn.
                    #   (c) Phase-2 enforcement: any unverified quote on a
                    #       channel-read turn (gated check; non-channel
                    #       turns stay observation-only).
                    #   (d) F1 phase-1 retries exhausted with a residual
                    #       violation.
                    # 2026-05-23 fix: detector now re-runs on the RETRY
                    # output too (the `not _confab_injected` gate that used
                    # to skip the post-retry check is gone). Retries are
                    # capped at `_F1_CONFAB_MAX_RETRIES`; further drafts
                    # ship with `[F1-accepted-degraded]` to break the loop.
                    if (
                        _final_content
                        and any(
                            _is_channel_read_tool(n)
                            for n in _tool_call_history
                        )
                    ):
                        try:
                            from lazyclaw.runtime.f1_confabulation_detector import (
                                detect_confabulation as _detect_confab,
                                build_raw_data_injection as _build_raw_inj,
                            )
                            try:
                                from lazyclaw.runtime.f1_content_verifier import (
                                    phase2_enforcement_verdict,
                                )
                            except ImportError:
                                phase2_enforcement_verdict = None  # type: ignore
                            _confab_verdict = _detect_confab(
                                _final_content,
                                _tool_call_history,
                                _tool_results,
                            )
                            _phase2_block = False
                            _phase2_reason = ""
                            if phase2_enforcement_verdict is not None:
                                _phase2_block, _phase2_reason = (
                                    phase2_enforcement_verdict(
                                        _final_content,
                                        _tool_call_history,
                                        _tool_results,
                                    )
                                )
                            _f1_exhausted_with_violation = (
                                _f1_retries >= _F1_MAX_RETRIES
                                and _check_f1_violation(
                                    _final_content, _tool_results,
                                ) is not None
                            )
                            _needs_retry = (
                                _confab_verdict.is_confabulation
                                or _phase2_block
                                or _f1_exhausted_with_violation
                            )
                            if (
                                _needs_retry
                                and _confab_retries < _F1_CONFAB_MAX_RETRIES
                            ):
                                if _confab_verdict.is_confabulation:
                                    logger.warning(
                                        "[F1-confabulation] tool=%s kind=%s "
                                        "phrase=%r payload=%d bytes. "
                                        "Triggering raw-data injection retry "
                                        "(%d of %d).",
                                        _confab_verdict.tool_name,
                                        _confab_verdict.kind,
                                        _confab_verdict.offending_phrase,
                                        _confab_verdict.payload_bytes,
                                        _confab_retries + 1,
                                        _F1_CONFAB_MAX_RETRIES,
                                    )
                                    _inj_reason = "confabulation"
                                elif _phase2_block:
                                    logger.warning(
                                        "[F1-phase2-block] retry (%d of %d) "
                                        "forced — %s",
                                        _confab_retries + 1,
                                        _F1_CONFAB_MAX_RETRIES,
                                        _phase2_reason,
                                    )
                                    _inj_reason = "phase2_unverified_quote"
                                else:
                                    logger.warning(
                                        "[F1-retry-exhausted] %d phase-1 "
                                        "attempts exhausted; raw-data "
                                        "injection (%d of %d). last_violation"
                                        "=%s",
                                        _F1_MAX_RETRIES,
                                        _confab_retries + 1,
                                        _F1_CONFAB_MAX_RETRIES,
                                        _check_f1_violation(
                                            _final_content, _tool_results,
                                        ),
                                    )
                                    _inj_reason = "retry_exhausted"
                                # On the SECOND forced retry, the prior
                                # injection didn't fix the brain — log a
                                # distinct marker so we can grep production
                                # for cases where the second retry was
                                # actually load-bearing.
                                if _confab_retries >= 1:
                                    logger.warning(
                                        "[F1-post-retry-rewrite-forced] "
                                        "first injection didn't clear; "
                                        "forcing second rewrite. reason=%s "
                                        "kind=%s",
                                        _inj_reason,
                                        _confab_verdict.kind,
                                    )
                                _raw_inj = _build_raw_inj(
                                    _confab_verdict,
                                    _tool_call_history,
                                    _tool_results,
                                    reason=_inj_reason,
                                )
                                # Pop the brain's confabulated draft BEFORE
                                # appending the injection. Otherwise the
                                # retry sees its own poisoned paraphrase as
                                # prior context and reproduces the same
                                # hallucination (2026-05-22 10:09 incident:
                                # 3 unverified quotes pre-retry → same 3
                                # unverified quotes post-retry).
                                _popped = _pop_confabulated_draft_for_retry(
                                    messages,
                                )
                                if _popped is not None:
                                    logger.info(
                                        "[F1-confabulation] popped draft "
                                        "(%d chars) before retry — reason=%s "
                                        "kind=%s",
                                        len(_popped.content or ""),
                                        _inj_reason,
                                        _confab_verdict.kind,
                                    )
                                messages.append(LLMMessage(
                                    role="user", content=_raw_inj,
                                ))
                                _confab_injected = True
                                _confab_retries += 1
                                streamed_content = ""
                                continue
                            if _needs_retry:
                                # Retries exhausted — ship the draft anyway
                                # with a loud log. Better one slightly bad
                                # reply than an infinite retry loop.
                                logger.warning(
                                    "[F1-accepted-degraded] confab retries "
                                    "exhausted (%d/%d); shipping draft. "
                                    "verdict_kind=%s phase2_block=%s "
                                    "draft_head=%r",
                                    _confab_retries,
                                    _F1_CONFAB_MAX_RETRIES,
                                    _confab_verdict.kind,
                                    _phase2_block,
                                    (_final_content or "")[:160],
                                )
                        except Exception:
                            logger.debug(
                                "F1 confabulation detector raised "
                                "(non-fatal)", exc_info=True,
                            )

                    # Knowledge-gap self-recall — re-prompt brain with LazyBrain
                    # + personal_memory injection BEFORE this answer ships, when
                    # the brain just emitted a confusion phrase or the user is
                    # repeating themselves. One shot per turn (_self_recalled).
                    #
                    # Gated on no-channel-read-this-turn: if a channel-read
                    # tool already ran, the brain just got authoritative live
                    # data — injecting stale semantic-search snippets AFTER
                    # the tool_result buries it and primes cross-contamination
                    # (the 2026-05-19 16:19 incident: self_recall fired AFTER
                    # 3 upwork reads, bumped prompt 67K → 77K chars, then the
                    # re-prompted brain regurgitated the stale paraphrases).
                    if (
                        not _self_recalled
                        and _final_content
                        and not any(
                            _is_channel_read_tool(n)
                            for n in _tool_call_history
                        )
                    ):
                        try:
                            from lazyclaw.runtime.self_recall import (
                                detect_knowledge_gap, build_recall_block,
                            )
                            _prior_user = [
                                (m.content or "")
                                for m in messages[-12:]
                                if m.role == "user"
                            ]
                            _is_gap, _gap_query = detect_knowledge_gap(
                                _final_content, message, _prior_user,
                            )
                            if _is_gap:
                                _block = await build_recall_block(
                                    self.config, user_id, _gap_query,
                                )
                                if _block:
                                    logger.info(
                                        "Knowledge gap detected — injecting "
                                        "self-recall and re-prompting brain",
                                    )
                                    messages.append(LLMMessage(
                                        role="assistant", content=_history_content,
                                    ))
                                    messages.append(LLMMessage(
                                        role="system", content=_block,
                                    ))
                                    _self_recalled = True
                                    streamed_content = ""
                                    continue
                        except Exception:
                            logger.debug(
                                "self_recall hook failed (non-fatal)",
                                exc_info=True,
                            )

                    # Final text response (already streamed to user). History
                    # carries the unstripped version so M2 sees its prior <think>
                    # trace next turn.
                    await cb.on_event(AgentEvent("stream_done", "", {}))
                    all_new_messages.append(
                        LLMMessage(role="assistant", content=_history_content)
                    )
                    break

                # ── Fast dispatch ─────────────────────────────────────
                # On first heavy tool call (any iteration), push to TaskRunner
                # and return immediately so the lane queue stays free.
                # Only dispatch tools that are actually in the current tools list.
                _current_tool_names = {
                    t.get("function", {}).get("name") for t in tools
                } if tools else set()
                def _is_heavy(tc_name: str) -> bool:
                    """Check if a tool call is heavy (should fast-dispatch)."""
                    if tc_name in HEAVY_TOOLS:
                        return True
                    # MCP tools: mcp_{uuid-with-hyphens}_{base_name}
                    # e.g. mcp_aa828e97-7923-4189-b6e4-1f2ace89b115_email_delete
                    if tc_name.startswith("mcp_"):
                        for base in _HEAVY_MCP_BASES:
                            if tc_name.endswith("_" + base):
                                return True
                    return False

                # Don't fast-dispatch if task tools were injected — task
                # operations (add_task, daily_briefing) should stay foreground.
                _has_task_tools = bool(_current_tool_names & _TASK_TOOL_NAMES)
                if (
                    iteration <= 2  # Allow dispatch on first few iterations
                    and self._task_runner is not None
                    and not getattr(self, "is_background", False)  # Don't re-dispatch background tasks
                    and not _has_task_tools  # Task messages stay foreground
                    and any(
                        _is_heavy(tc.name) and tc.name in _current_tool_names
                        for tc in response.tool_calls
                    )
                ):
                    from lazyclaw.runtime.agent_settings import get_agent_settings

                    _agent_settings = await get_agent_settings(self.config, user_id)
                    _active_count = self._team_lead.active_count if self._team_lead else 0
                    if (
                        _agent_settings.get("auto_delegate", True)
                        and _active_count
                            < _agent_settings.get("max_concurrent_specialists", 3)
                    ):
                        _specialist_name = response.tool_calls[0].name

                        _task_id = await self._task_runner.submit(
                            user_id=user_id,
                            instruction=message,
                            name=None,  # auto-generates readable name from instruction
                            timeout=_agent_settings.get("specialist_timeout_s", 120),
                            callback=callback,
                            caller_depth=self._depth,
                        )

                        # Foreground task was re-routed to background
                        if self._team_lead and _fg_task_id:
                            self._team_lead.cancel(_fg_task_id)
                            _fg_task_id = None

                        await cb.on_event(AgentEvent(
                            FAST_DISPATCH,
                            f"Dispatched to background ({_specialist_name})",
                            {"task_id": _task_id, "specialist": _specialist_name},
                        ))
                        await cb.on_event(AgentEvent("stream_done", "", {}))
                        await cb.on_event(AgentEvent("done", "Dispatched", {}))

                        if _delegate_registered:
                            self.registry.unregister("delegate")
                        return "On it — working in background."

                # End streaming before tool lines appear (prevents text/tool garbling)
                if streamed_content:
                    await cb.on_event(AgentEvent("stream_done", "", {}))
                    streamed_content = ""

                # TAOR phase: entering "act" — tool execution.
                try:
                    await cb.on_event(AgentEvent(
                        "phase", "act",
                        {"phase": "act", "iteration": iteration,
                         "tools": [tc.name for tc in (response.tool_calls or [])]},
                    ))
                    if self._team_lead and _fg_task_id:
                        self._team_lead.update_phase(_fg_task_id, "act")
                except Exception:
                    pass

                # Assistant message with tool calls (may have partial text content)
                assistant_msg = LLMMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                )
                messages.append(assistant_msg)
                all_new_messages.append(assistant_msg)

                # If delegate is among tool calls, execute ONLY delegate.
                # Delegate runs a full specialist loop — other parallel tool
                # calls are redundant and waste tokens (double-agent problem).
                _tool_calls_to_run = response.tool_calls
                _skipped_calls: list[ToolCall] = []
                if len(response.tool_calls) > 1:
                    _delegate_calls = [
                        tc for tc in response.tool_calls if tc.name == "delegate"
                    ]
                    if _delegate_calls:
                        _skipped_calls = [
                            tc for tc in response.tool_calls if tc.name != "delegate"
                        ]
                        _tool_calls_to_run = _delegate_calls
                        logger.info(
                            "Delegate detected — skipping %d parallel tool calls",
                            len(_skipped_calls),
                        )

                # Inject stub results for skipped calls (LLM expects tool_call_id responses)
                for sc in _skipped_calls:
                    messages.append(LLMMessage(
                        role="tool",
                        content="Skipped — delegate is handling this task.",
                        tool_call_id=sc.id,
                    ))

                # ── Parallel pre-execution for read-only tools ─────────────
                # Identify read-only tools that are not already in the cache.
                # Run them concurrently via execute_batch(), then use the
                # pre-computed results in the sequential loop below.
                # State-modifying tools always run sequentially in the loop.
                _pre_executed: dict[str, tuple[str, int]] = {}  # tc.id → (result, duration_ms)
                _ro_to_batch: list[ToolCall] = []
                for _btc in _tool_calls_to_run:
                    _bkey = f"{_btc.name}:{json.dumps(_btc.arguments, sort_keys=True)}"
                    if _bkey not in _tool_call_cache:
                        _skill = self.registry.get(_btc.name) if self.registry else None
                        if _skill and getattr(_skill, "read_only", False):
                            _ro_to_batch.append(_btc)
                if len(_ro_to_batch) > 1:
                    _batch_outcomes = await self.executor.execute_batch(
                        _ro_to_batch, user_id, callback=cb,
                    )
                    for _btc, _bres, _bdur, _bgroup in _batch_outcomes:
                        _pre_executed[_btc.id] = (_bres, _bdur)

                # MCP UserInputError recovery is COLLECTED inside the loop
                # and emitted ONCE after all tool_results land. Inserting a
                # system message between two tool_results breaks the
                # tool_call ↔ tool_result pairing required by MiniMax /
                # Anthropic / OpenAI (MiniMax 400 code 2013: "tool call
                # result does not follow tool call").
                _pending_recoveries: list[tuple[str, dict]] = []

                # Execute each tool call
                for tc in _tool_calls_to_run:
                    _display = self.registry.get_display_name(tc.name) if self.registry else tc.name
                    _all_tools_used.append(_display)
                    await cb.on_event(AgentEvent(
                        "tool_call", _display,
                        {
                            "tool": tc.name,
                            "display_name": _display,
                            "args": tc.arguments,
                            # Stable id so the chat UI can match the
                            # later tool_result back to THIS specific
                            # call (otherwise back-to-back same-name
                            # calls collide on `name+running`).
                            "tool_call_id": tc.id,
                        },
                    ))
                    # Emit browser-specific action event for transparency
                    if tc.name == "browser":
                        _br_action = tc.arguments.get("action", "")
                        _br_target = (
                            tc.arguments.get("url", "")
                            or tc.arguments.get("ref", "")
                            or tc.arguments.get("query", "")
                            or tc.arguments.get("text", "")
                        )
                        _br_step = (
                            (_plan_state.plan.current_step + 1)
                            if _plan_state.plan else 0
                        )
                        _br_total = (
                            len(_plan_state.plan.steps)
                            if _plan_state.plan else 0
                        )
                        await cb.on_event(AgentEvent(
                            BROWSER_ACTION,
                            f"Browser: {_br_action}",
                            {
                                "action": _br_action,
                                "target": _br_target[:100],
                                "step_number": _br_step,
                                "total_steps": _br_total,
                            },
                        ))
                    await recorder.record_tool_call(tc.name, tc.arguments)
                    # Inject background flag so browser uses headless
                    if getattr(self, "is_background", False) and tc.name == "browser":
                        tc = ToolCall(
                            id=tc.id, name=tc.name,
                            arguments={**tc.arguments, "_background": True},
                        )

                    # Fix I — snapshot registry MCP tools BEFORE executing
                    # connect_mcp_server / install_mcp_server, so the post-hook
                    # below can inject ONLY the just-connected MCP's tools
                    # (the true delta) instead of every previously-registered
                    # MCP whose tools weren't in the per-turn curated `tools`.
                    _mcp_pre_snapshot: set[str] | None = None
                    if tc.name in ("connect_mcp_server", "install_mcp_server"):
                        from lazyclaw.runtime.mcp_tool_reinject import (
                            snapshot_mcp_tool_names,
                        )
                        _mcp_pre_snapshot = snapshot_mcp_tool_names(self.registry)

                    # Duplicate call cache — skip re-executing identical tool calls.
                    # Non-idempotent generators/actions are never cached (see
                    # _NEVER_CACHE_TOOLS) so a stale prior result can't replay.
                    _cache_key = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
                    if tc.name not in _NEVER_CACHE_TOOLS and _cache_key in _tool_call_cache:
                        _tool_call_hit_count[_cache_key] = _tool_call_hit_count.get(_cache_key, 0) + 1
                        _hits = _tool_call_hit_count[_cache_key]
                        if _hits >= 2:
                            # Repeated cache hits = brain is looping. Don't return
                            # the silent cached result that masks the loop — return
                            # a steering message that tells the brain it's repeating
                            # itself with the same args, and to pick a different
                            # action or finalize. The "Just a moment" hallucination
                            # bug came from this exact pattern: 7 cache hits in a
                            # row, brain interpreted "verifier failure" as "page is
                            # blocked" and fabricated a CAPTCHA story.
                            result = (
                                f"[loop guard] You called `{tc.name}` with these "
                                f"exact args {_hits + 1} times this turn — the "
                                "result hasn't changed. Stop replaying the same "
                                "action. Pick a DIFFERENT tool, change the args, "
                                "or send a final reply describing what you have "
                                "so far. Re-calling this tool with the same args "
                                "again will not produce different output."
                            )
                            logger.warning(
                                "Tool %s loop-guard fired (%d cache hits) — steering brain",
                                tc.name, _hits + 1,
                            )
                        else:
                            result = _tool_call_cache[_cache_key]
                            logger.info("Tool %s cache hit (skipped re-execution)", tc.name)
                    elif tc.id in _pre_executed:
                        # Already executed in parallel batch above
                        result, _dur_ms = _pre_executed[tc.id]
                        logger.debug("Tool %s used parallel pre-execution result (%dms)", tc.name, _dur_ms)
                    else:
                        result = await self.executor.execute(tc, user_id, callback=cb)

                    # Cap oversized results before injecting into context
                    # Skip capping + caching for approval responses (JSON args must stay intact)
                    if isinstance(result, str) and not result.startswith(APPROVAL_PREFIX):
                        # Pass tool name so channel-read calls get the
                        # 50K cap instead of the 4K default — see
                        # `_cap_tool_result` for the 2026-05-24
                        # incident this fixes.
                        result = _cap_tool_result(result, tc.name)
                        # Don't memoize failures — caching errors made retries
                        # return the same stale error in 0 ms, masking the fact
                        # that the brain wasn't varying its args. With this
                        # skip, identical broken retries re-execute (still
                        # caught by stuck_detector) and varied args naturally
                        # miss the cache and call the underlying tool fresh.
                        _is_err_result = result.startswith((
                            "[MCP ERROR]", "[NO DATA]", "Error:", "error:",
                            "Tool error:",
                        ))
                        # MCP tools that return Python dicts (e.g. mcp-upwork's
                        # ``submit_proposal`` returning {"status": "error",
                        # "message": "..."}) get JSON-encoded into the result
                        # string. The string starts with ``{"`` not any of the
                        # prefixes above, so the prior check missed them — and
                        # the 3-strikes counter never incremented for the most
                        # common failure shape in the codebase. Detect via
                        # cheap substring scan; tolerant to single/double quote
                        # styles produced by both ``json.dumps`` and ``repr``.
                        if not _is_err_result and result.startswith(("{", "[")):
                            _err_status_markers = (
                                '"status": "error"', '"status":"error"',
                                "'status': 'error'",
                                '"status": "unknown"', '"status":"unknown"',
                                "'status': 'unknown'",
                                '"status": "failed"', '"status":"failed"',
                                "'status': 'failed'",
                                '"isError": true', '"isError":true',
                                "'isError': True",
                                # mcp-upwork empty_or_blocked reads: only
                                # the wall diagnoses are failures — a
                                # selector_drift_or_truly_empty result may
                                # be a genuinely empty list and must NOT
                                # feed the failure counter.
                                '"diagnosis": "cloudflare_challenge"',
                                '"diagnosis":"cloudflare_challenge"',
                                "'diagnosis': 'cloudflare_challenge'",
                                '"diagnosis": "login_required"',
                                '"diagnosis":"login_required"',
                                "'diagnosis': 'login_required'",
                            )
                            head = result[:512]  # only check the head; results can be huge
                            _is_err_result = any(m in head for m in _err_status_markers)
                        if not _is_err_result and tc.name not in _NEVER_CACHE_TOOLS:
                            _tool_call_cache[_cache_key] = result
                        elif _is_err_result:
                            logger.debug(
                                "Skipping cache for error result on %s (len=%d)",
                                tc.name, len(result),
                            )

                        # Per-tool consecutive-failure counter — feeds the
                        # 3-strikes graceful-handoff break further below.
                        # Strip ``mcp_<uuid>_`` so tool name is stable across
                        # MCP subprocess restarts (UUID changes per restart).
                        _short = tc.name
                        if _short.startswith("mcp_"):
                            _parts = _short.split("_", 2)
                            if len(_parts) == 3:
                                _short = _parts[2]
                        if _is_err_result:
                            _tool_failure_count[_short] = (
                                _tool_failure_count.get(_short, 0) + 1
                            )
                            # Browser-fallback trigger — checked BEFORE the
                            # three-strikes assignment so a same-batch
                            # 2nd+3rd failure defers the handoff one
                            # iteration and the brain actually SEES the
                            # fallback. Flushed post-loop (see the
                            # "Browser fallback re-injection" block).
                            if (
                                _browser_fallback_pending is None
                                and _should_inject_browser_fallback(
                                    _short,
                                    _tool_failure_count[_short],
                                    _suppressed_tool_names,
                                    _browser_fallback_injected,
                                )
                            ):
                                _browser_fallback_pending = (_short, result[-300:])
                                logger.info(
                                    "Browser fallback queued after %d "
                                    "failures of %s",
                                    _tool_failure_count[_short], _short,
                                )
                            if (
                                _tool_failure_count[_short] >= 3
                                and _three_strikes_break is None
                                and _browser_fallback_pending is None
                            ):
                                _three_strikes_break = (_short, result[-300:])
                                logger.warning(
                                    "Three-strikes break: %s failed 3x — "
                                    "graceful handoff queued",
                                    _short,
                                )
                        else:
                            _tool_failure_count.pop(_short, None)

                    await recorder.record_tool_result(tc.name, result if isinstance(result, str) else str(result))

                    # Handle approval-required responses BEFORE emitting tool_result
                    # to avoid duplicate result events (one for placeholder, one for real)
                    if isinstance(result, str) and result.startswith(APPROVAL_PREFIX):
                        try:
                            parts = result[len(APPROVAL_PREFIX):]
                            colon_idx = parts.index(":")
                            skill_name = parts[:colon_idx]
                            args_json = parts[colon_idx + 1:]
                            parsed_args = json.loads(args_json) if args_json != "{}" else {}
                        except (ValueError, json.JSONDecodeError) as parse_err:
                            logger.error("Malformed approval response: %s (%s)", result, parse_err)
                            result = f"Tool error: malformed approval response"
                            tool_msg = LLMMessage(
                                role="tool",
                                content=result,
                                tool_call_id=tc.id,
                            )
                            messages.append(tool_msg)
                            all_new_messages.append(tool_msg)
                            continue

                        # Check if user already denied this exact tool+args this turn
                        _denial_key = f"{skill_name}:{json.dumps(parsed_args, sort_keys=True)}"
                        if _denial_key in _denied_approvals:
                            logger.info("Tool %s already denied this turn, skipping re-prompt", skill_name)
                            result = (
                                f"The user already denied '{skill_name}' with these arguments. "
                                f"Do not retry this action. Try a different approach or ask the user."
                            )
                            tool_msg = LLMMessage(
                                role="tool",
                                content=result,
                                tool_call_id=tc.id,
                            )
                            messages.append(tool_msg)
                            all_new_messages.append(tool_msg)
                            continue

                        # Try inline approval via callback (CLI y/n prompt)
                        # Pass display name so the UI shows friendly names
                        if hasattr(cb, '_pending_display_name'):
                            cb._pending_display_name = _display
                        approved = await cb.on_approval_request(skill_name, parsed_args)

                        if approved:
                            await cb.on_event(AgentEvent(
                                "approval", f"{_display} approved",
                                {"skill": skill_name, "display_name": _display, "approved": True},
                            ))
                            result = await self.executor.execute_allowed(tc, user_id, callback=cb)
                            await recorder.record_tool_result(tc.name, result if isinstance(result, str) else str(result))
                            await cb.on_event(AgentEvent(
                                "tool_result", _display,
                                {
                                    "tool": tc.name,
                                    "display_name": _display,
                                    "tool_call_id": tc.id,
                                },
                            ))
                        else:
                            _denied_approvals.add(_denial_key)
                            # Audit the user's denial (2026-06-10, Phase 3)
                            # — approvals are logged by execute_allowed;
                            # denials never reach the executor.
                            try:
                                from lazyclaw.permissions.audit import log_action

                                await log_action(
                                    self.config, user_id, "tool_denied",
                                    skill_name=skill_name, arguments=parsed_args,
                                )
                            except Exception:
                                logger.debug("denial audit swallowed", exc_info=True)
                            await cb.on_event(AgentEvent(
                                "approval", f"{_display} denied",
                                {"skill": skill_name, "display_name": _display, "approved": False},
                            ))
                            result = (
                                f"The user denied the action '{skill_name}'. "
                                f"Do not retry this action. Explain what you wanted to do "
                                f"and ask if the user wants to try a different approach."
                            )
                    else:
                        # Normal tool (no approval needed) — emit result event
                        await cb.on_event(AgentEvent(
                            "tool_result", _display,
                            {
                                "tool": tc.name,
                                "display_name": _display,
                                "tool_call_id": tc.id,
                            },
                        ))

                    # Track step with TeamLead
                    if self._team_lead and _fg_task_id:
                        self._team_lead.update_step(_fg_task_id, _display)

                    # ── Non-browser result verification ──────────────────
                    # Stamp tool results with `→ FAILED: <reason>` when the
                    # output looks like an error (HTTP 4xx/5xx, "Error:" /
                    # "Exception:" / traceback, empty result from tools that
                    # shouldn't be empty). Browser already self-stamps via
                    # action_verifier — classify() is a no-op if the marker
                    # is already present. The marker flows both into the
                    # LLM context (so it stops paraphrasing errors as
                    # success) and into stuck_detector.detect_no_progress.
                    _result_str = result if isinstance(result, str) else str(result)
                    # Skip classify() for channel reads: result_verifier's
                    # failure-marker regexes ("timed out", "not authorized",
                    # "Error", ...) match the *quoted human message* inside a
                    # conversation JSON and falsely stamp it `→ FAILED:`. The
                    # 50K-cap helper (_is_channel_read_tool_name) is the same
                    # gate used for the channel-read result cap, so reuse it.
                    if _is_channel_read_tool_name(tc.name):
                        _rv_status, _rv_reason = "uncertain", None
                    else:
                        from lazyclaw.runtime import result_verifier as _rv
                        _rv_status, _rv_reason = _rv.classify(tc.name, _result_str)
                        if _rv_status == "failed" and _rv_reason:
                            _result_str = _rv.stamp_failed(_result_str, _rv_reason)
                            result = _result_str  # keep downstream paths in sync

                    # ── Fix A: MCP-tool reinjection ───────────────────
                    # When the brain calls connect_mcp_server /
                    # install_mcp_server the MCP's tools get registered
                    # globally but the per-turn `tools` list doesn't
                    # know. Reinject them now so the next iter can use
                    # the freshly-connected MCP. Fix I narrows this to
                    # the DELTA (tools NEW since the pre-execute
                    # snapshot) so connecting one MCP doesn't drag in
                    # every other already-connected MCP's tools (seen
                    # live 2026-05-14: a sheets connect blew tool count
                    # from 35 → 162). See mcp_tool_reinject.
                    if (
                        tc.name in ("connect_mcp_server", "install_mcp_server")
                        and _rv_status != "failed"
                    ):
                        from lazyclaw.runtime.mcp_tool_reinject import (
                            reinject_mcp_tools,
                        )
                        _injected_post_hook = reinject_mcp_tools(
                            self.registry, tools, _suppressed_tool_names,
                            pre_connect_tool_names=_mcp_pre_snapshot,
                        )
                        if _injected_post_hook:
                            logger.info(
                                "MCP-connect post-hook: injected %d "
                                "tools after %s (first 3: %s)",
                                len(_injected_post_hook), tc.name,
                                _injected_post_hook[:3],
                            )

                    # F1 grounding aid — wrap channel-read tool results with
                    # a freshness sentinel so the brain has an explicit
                    # precedence marker against any stale paraphrases sitting
                    # in the system prompt (daily_logs, lazybrain journal,
                    # semantic-search recall). Without this, Opus 4.6 cannot
                    # distinguish "fact I just read from MCP" from "fact
                    # summarized in last week's daily_log header" — see
                    # 2026-05-19 16:19 incident in MEMORY.
                    if _is_channel_read_tool(tc.name) and isinstance(result, str):
                        from datetime import datetime, timezone
                        _live_ts = datetime.now(timezone.utc).strftime(
                            "%Y-%m-%d %H:%M:%S UTC",
                        )
                        result = (
                            f"[LIVE READ @ {_live_ts} — AUTHORITATIVE; "
                            "supersedes any daily_log, journal, or memory "
                            "paraphrase of the same conversation. Quote the "
                            "messages below verbatim in your reply — do NOT "
                            "merge with prior context. If a fact is not "
                            "below, say 'not specified — needs to be asked'.]"
                            f"\n{result}\n"
                            "[END LIVE READ]"
                        )

                    tool_msg = LLMMessage(
                        role="tool",
                        content=result,
                        tool_call_id=tc.id,
                    )
                    messages.append(tool_msg)
                    all_new_messages.append(tool_msg)
                    _tool_call_history.append(tc.name)
                    _tool_results.append(_result_str)
                    # Mid-turn pivot tracking: fingerprint = (tool_name,
                    # sorted_arg_keys). 5 of the same in a row trips the
                    # detector below and injects a "dispatch don't
                    # iterate" system message.
                    _tool_call_signatures.append(
                        (tc.name, tuple(sorted((tc.arguments or {}).keys())))
                    )

                    # ── MCP parameter-validation recovery (deferred) ──
                    # If the tool returned a UserInputError-style error AND
                    # we have its parameter schema, queue it for ONE
                    # consolidated system message AFTER the loop. We must
                    # NOT append a system message here — interleaving any
                    # non-tool message between tool_results breaks the
                    # tool_call ↔ tool_result pairing the brain expects
                    # (MiniMax 400 code 2013, Anthropic invalid_request).
                    if (
                        isinstance(result, str)
                        and result.startswith("[MCP ERROR]")
                        and any(
                            phrase in result
                            for phrase in (
                                "UserInputError",
                                "must be provided",
                                "Invalid",
                                "invalid",
                                "required parameter",
                                "missing parameter",
                                "missing required",
                            )
                        )
                        and self.registry is not None
                    ):
                        _recovery_schema = self.registry.get_tool_schema(tc.name)
                        if _recovery_schema is not None:
                            _pending_recoveries.append(
                                (tc.name, _recovery_schema),
                            )
                            logger.info(
                                "MCP UserInputError on %s — queued "
                                "parameter schema for post-loop recovery",
                                tc.name,
                            )

                    # ── Hard stop: AUTO-PROMOTE done — run_background fired ──
                    # When the runtime narrowed tools to run_background only
                    # (`_force_dispatch_only`) and the brain just called it,
                    # the foreground turn is DONE. Continuing the loop lets
                    # the action-claim hallucination retry re-widen the tool
                    # list, after which the brain dispatches run_background
                    # AGAIN (observed 2026-05-16: tasks 84b4f487 + ae358d06
                    # 'fix_gerardo_number_memory' + '_v2' fired 27s apart from
                    # one foreground turn). Mirrors the FAST_DISPATCH return
                    # used by the delegate fast-path (line ~4006). Only the
                    # initial foreground turn exits — background sub-turns
                    # keep iterating normally.
                    if (
                        tc.name == "run_background"
                        and _force_dispatch_only
                        and not getattr(self, "is_background", False)
                    ):
                        logger.info(
                            "AUTO-PROMOTE: run_background dispatched — "
                            "exiting foreground turn (was iter=%d)",
                            iteration,
                        )
                        await cb.on_event(AgentEvent(
                            FAST_DISPATCH,
                            "Auto-promoted — running in background",
                            {"trigger": "auto_promote_dispatch"},
                        ))
                        await cb.on_event(AgentEvent("done", "Dispatched", {}))
                        if _delegate_registered and self.registry:
                            self.registry.unregister("delegate")
                        # Foreground turn handed off cleanly — release TeamLead
                        # slot so the UI's "in flight" indicator clears. Without
                        # this, the chat card lingers in _active forever (lane
                        # queue empties via `Job completed`, but observability
                        # state leaks).
                        if self._team_lead and _fg_task_id:
                            self._team_lead.complete(
                                _fg_task_id,
                                "Continuing in background — will report back when done.",
                            )
                            _fg_task_id = None
                        return (
                            "Continuing in background — will report back "
                            "when done."
                        )

                    # ── Hard stop: delegate dispatched — exit foreground ──
                    # delegate is fire-and-forget (delegate.py:268): it
                    # schedules run_specialist detached and returns a
                    # "started" string. Continuing the loop is what let the
                    # brain trip the action-claim retry, duplicate the work,
                    # and get AUTO-PROMOTED (2026-06-08 14:18 triple-exec).
                    # Hand off and return the delegate's own message, exactly
                    # like the run_background hard-stop above. Only the
                    # initial foreground turn exits — background sub-turns
                    # keep iterating. Skip on error results so the brain can
                    # correct (e.g. 'Unknown specialist').
                    if (
                        tc.name == "delegate"
                        and not getattr(self, "is_background", False)
                        and isinstance(result, str)
                        and not result.startswith(("Error", "Unknown"))
                    ):
                        logger.info(
                            "delegate dispatched — exiting foreground turn "
                            "(was iter=%d)",
                            iteration,
                        )
                        await cb.on_event(AgentEvent("done", "Delegated", {}))
                        if _delegate_registered and self.registry:
                            self.registry.unregister("delegate")
                        if self._team_lead and _fg_task_id:
                            self._team_lead.complete(
                                _fg_task_id,
                                "Delegated to a specialist — will report "
                                "back when done.",
                            )
                            _fg_task_id = None
                        return result

                    # ── Hard stop: OAuth credential not authorized ────
                    # n8n_management surfaces this with a STOP_OAUTH_CREDENTIAL
                    # marker when a workflow call fails because the user
                    # hasn't finished OAuth consent. Break the loop
                    # immediately and hand the consent URL back.
                    if "STOP_OAUTH_CREDENTIAL" in _result_str:
                        logger.warning(
                            "OAuth credential not authorized — "
                            "hard-stopping agent loop for user %s", user_id,
                        )
                        await cb.on_event(AgentEvent(
                            "done", "OAuth credential not authorized",
                            {"reason": "oauth_pending"},
                        ))
                        # Strip the internal marker before returning to user.
                        _user_msg = _result_str.replace(
                            "Error: STOP_OAUTH_CREDENTIAL: ", "",
                        )
                        if self._team_lead and _fg_task_id:
                            self._team_lead.fail(
                                _fg_task_id, "OAuth credential not authorized",
                            )
                            _fg_task_id = None
                        return _user_msg

                    # ── Browser action planner: evaluate result ────────
                    # Track plan progress after every browser call.
                    # REPLAN → inject fallback hint.
                    # ESCALATE → let the stuck detector handle it (don't inject).
                    if tc.name == "browser" and _plan_state.plan is not None:
                        _result_str = result if isinstance(result, str) else str(result)
                        _plan_decision = evaluate_action_result(
                            _plan_state,
                            tc.arguments.get("action", ""),
                            _result_str,
                        )
                        from dataclasses import replace as _dc_replace
                        _plan_state = _dc_replace(
                            _plan_state,
                            plan=_plan_decision.new_state.plan,
                            browser_call_count=_plan_decision.new_state.browser_call_count,
                            consecutive_failures=_plan_decision.new_state.consecutive_failures,
                        )
                        if (
                            _plan_decision.status in (PlanStatus.CONTINUE, PlanStatus.REPLAN)
                            and _plan_decision.system_message
                        ):
                            messages.append(LLMMessage(
                                role="system",
                                content=_plan_decision.system_message,
                            ))

                    # ── BROWSER_VERIFY: parse and emit verification result ─
                    if tc.name == "browser":
                        _res_for_verify = result if isinstance(result, str) else str(result)
                        _verify_succeeded: bool | None = None
                        _verify_evidence = ""
                        if "→ SUCCESS:" in _res_for_verify:
                            _verify_succeeded = True
                            _idx = _res_for_verify.find("→ SUCCESS:")
                            _verify_evidence = _res_for_verify[_idx + 10:].split("\n")[0].strip()[:120]
                        elif "→ FAILED:" in _res_for_verify:
                            _verify_succeeded = False
                            _idx = _res_for_verify.find("→ FAILED:")
                            _verify_evidence = _res_for_verify[_idx + 9:].split("\n")[0].strip()[:120]
                        if _verify_succeeded is not None:
                            await cb.on_event(AgentEvent(
                                BROWSER_VERIFY,
                                _verify_evidence,
                                {
                                    "action": tc.arguments.get("action", ""),
                                    "succeeded": _verify_succeeded,
                                    "evidence": _verify_evidence,
                                },
                            ))

                    # ── Research strategy: update per-source ──────────────
                    # After each browser or web_search result, record the source
                    # and inject a progress hint so the LLM knows when to stop.
                    if tc.name in ("browser", "web_search") and _research_state is not None:
                        _res_str = result if isinstance(result, str) else str(result)
                        _src_url = (
                            tc.arguments.get("url", "")
                            or tc.arguments.get("query", "")
                        )
                        from dataclasses import replace as _dc_replace
                        _research_state = note_source(
                            _research_state,
                            url=_src_url,
                            title="",
                            tool_result=_res_str,
                        )
                        _progress_msg = _make_research_progress(_research_state)
                        if _progress_msg:
                            messages.append(LLMMessage(
                                role="system",
                                content=_progress_msg,
                            ))
                        logger.debug(
                            "Research state: sources=%d status=%s gaps=%d",
                            _research_state.sources_count,
                            _research_state.status.value,
                            len(_research_state.gaps),
                        )
                        await cb.on_event(AgentEvent(
                            BROWSER_PROGRESS,
                            f"Research: {_research_state.requirements_met_count}/{len(_research_state.info_requirements)} found",
                            {
                                "sources_checked": _research_state.sources_count,
                                "requirements_met": _research_state.requirements_met_count,
                                "total_requirements": len(_research_state.info_requirements),
                                "gaps": list(_research_state.gaps[:3]),
                                "status": _research_state.status.value,
                            },
                        ))

                    # Dynamic schema injection: after search_tools returns,
                    # inject discovered tool schemas so LLM can call them next iteration
                    if tc.name == "search_tools" and self.registry:
                        discovered = _extract_tool_names_from_search_result(
                            result if isinstance(result, str) else str(result)
                        )
                        for dname in discovered:
                            schema = self.registry.get_tool_schema(dname)
                            if schema and schema not in tools:
                                tools.append(schema)
                        if discovered:
                            logger.info(
                                "Injected %d tool schemas: %s",
                                len(discovered), ", ".join(discovered),
                            )

                # ── Emit MCP UserInputError recovery (post-loop) ─────
                # All tool_results are now appended in correct order. Safe
                # to inject a single system message containing every failing
                # tool's parameter schema for the next iteration's re-read.
                if _pending_recoveries:
                    _recovery_lines = [
                        "[RECOVERY] One or more tool calls in this turn "
                        "failed with a parameter validation error. "
                        "Re-read each tool's parameter schema below — pay "
                        "attention to required fields and conditional "
                        "requirements (e.g. \"either A or B must be "
                        "provided\"). Then call the tool(s) again with "
                        "corrected args, OR if you can't fix it, tell the "
                        "user what's missing. Do NOT retry with the same "
                        "args.\n",
                    ]
                    for _r_name, _r_schema in _pending_recoveries:
                        _r_params = _r_schema.get(
                            "function", {},
                        ).get("parameters", {})
                        try:
                            _r_json = json.dumps(_r_params, indent=2)[:1500]
                        except Exception:
                            _r_json = str(_r_params)[:1500]
                        _recovery_lines.append(
                            f"\nSchema for `{_r_name}`:\n"
                            f"```json\n{_r_json}\n```"
                        )
                    _recovery_msg = LLMMessage(
                        role="system",
                        content="".join(_recovery_lines),
                    )
                    messages.append(_recovery_msg)
                    # Do NOT persist to DB. Transient per-call guidance, not
                    # conversation. Persisting would slot a system row between
                    # tool_use and tool_result on second-precision created_at,
                    # corrupting the tool_use→tool_result pairing on reload.
                    logger.info(
                        "Emitted consolidated recovery for %d failing tool(s): %s",
                        len(_pending_recoveries),
                        ", ".join(n for n, _ in _pending_recoveries),
                    )

                # ── Terminal tools: force text response next iteration ──
                # After these tools, the job is done. Inject a stop signal so
                # the LLM responds with a brief confirmation, not more tools.
                _TERMINAL_TOOLS = frozenset({
                    # Task management
                    "add_task", "complete_task", "delete_task", "update_task",
                    "daily_briefing", "list_tasks", "work_todos",
                    # Messaging platforms (one-shot actions)
                    "whatsapp_send", "whatsapp_mute", "whatsapp_list_muted",
                    "whatsapp_send_image",
                    "instagram_send", "instagram_like", "instagram_comment",
                    "instagram_follow", "instagram_unfollow",
                    "email_send",
                })
                if response and response.tool_calls:
                    _terminal_used = any(
                        tc.name in _TERMINAL_TOOLS for tc in response.tool_calls
                    )
                    if _terminal_used:
                        messages.append(LLMMessage(
                            role="system",
                            content=(
                                "RESPOND NOW with a SHORT message (1-3 sentences max). "
                                "Do NOT call any more tools. Do NOT explain how things work. "
                                "Do NOT write code or technical details. Just show the result."
                            ),
                        ))

                # ── Browser fallback re-injection ──
                # A channel MCP tool (upwork_* / whatsapp_* / ...) failed
                # twice this turn while the native ``browser`` skill was
                # channel-suppressed at turn start (MCP-first). Lift the
                # suppression for ``browser`` / ``use_host_browser``
                # (``run_command`` STAYS suppressed — shell is never the
                # sanctioned workaround), inject the registry schema into
                # the live tool list, and tell the brain the fallback path
                # exists. Post-loop so the system message can't slot
                # between a tool_use and its tool_result. NOT persisted to
                # DB — transient per-turn guidance, same as the
                # [RECOVERY] message above.
                if (
                    _browser_fallback_pending is not None
                    and not _browser_fallback_injected
                ):
                    _fb_tool, _fb_err = _browser_fallback_pending
                    _browser_fallback_pending = None
                    _browser_fallback_injected = True
                    _suppressed_tool_names -= {"browser", "use_host_browser"}
                    try:
                        _fb_existing = {
                            t.get("function", {}).get("name") for t in tools
                        }
                        if "browser" not in _fb_existing and self.registry:
                            _fb_schema = self.registry.get_tool_schema("browser")
                            if _fb_schema is not None:
                                tools.append(_fb_schema)
                    except Exception:
                        logger.debug(
                            "Browser fallback schema injection failed "
                            "(non-fatal)", exc_info=True,
                        )
                    # Collapse whitespace + neuter backticks so a crafted
                    # MCP error can't escape the code fence below and read
                    # as instructions (fence-escape prompt injection).
                    _fb_err_tail = " ".join((_fb_err or "").split())
                    _fb_err_tail = _fb_err_tail.replace("```", "'''")
                    if len(_fb_err_tail) > 200:
                        _fb_err_tail = _fb_err_tail[-200:]
                    messages.append(LLMMessage(
                        role="system",
                        content=(
                            f"[FALLBACK] `{_fb_tool}` failed twice — last "
                            f"error:\n```\n{_fb_err_tail}\n```\n"
                            "The `browser` tool is NOW AVAILABLE as the "
                            "sanctioned fallback. It drives the user's "
                            "signed-in Brave (cookies intact, passes "
                            "Cloudflare). Start with "
                            'browser(action="open", url=...) on the '
                            "relevant page and continue from there. Do "
                            f"NOT retry `{_fb_tool}` with the same args."
                        ),
                    ))
                    logger.info(
                        "Browser fallback injected after 2 failures of %s",
                        _fb_tool,
                    )

                # ── 3-strikes graceful handoff ──
                # Same MCP tool returned an error 3 times in a row this turn.
                # Don't keep grinding and don't let the brain fabricate a fake
                # CLI command (e.g. the Upwork "Run uvx upwork-mcp --login" lie
                # we hit in 2026-05-04 debug). Emit a deterministic, actionable
                # handoff message and exit the loop. Pick a re-auth URL based
                # on the failed tool's domain when we can guess one.
                if _three_strikes_break is not None:
                    _failed_tool, _last_err = _three_strikes_break
                    _reauth_url = ""
                    _service_label = "the relevant service"
                    _tool_lower = _failed_tool.lower()
                    if "upwork" in _tool_lower:
                        _reauth_url = "https://www.upwork.com/nx/find-work"
                        _service_label = "Upwork"
                    elif "instagram" in _tool_lower:
                        _reauth_url = "https://www.instagram.com/"
                        _service_label = "Instagram"
                    elif (
                        "whatsapp" in _tool_lower
                        or "wa_" in _tool_lower
                    ):
                        _reauth_url = "https://web.whatsapp.com/"
                        _service_label = "WhatsApp Web"
                    elif "gmail" in _tool_lower or "google" in _tool_lower:
                        _reauth_url = "https://mail.google.com/"
                        _service_label = "Gmail / Google"

                    # Best-effort: navigate the (host-bridge) Brave to the
                    # re-auth URL so the user lands on the right page with
                    # one click. The host bridge is the user's REAL Brave
                    # with their cookies — one navigation is enough.
                    if _reauth_url and self.registry:
                        try:
                            _br = self.registry.get_skill("browser")
                            if _br is not None:
                                async def _open_for_reauth(skill, url, uid):
                                    try:
                                        await skill.execute(
                                            uid,
                                            {"action": "open", "target": url},
                                        )
                                    except Exception:
                                        logger.debug(
                                            "Re-auth browser open failed (non-fatal)",
                                            exc_info=True,
                                        )
                                asyncio.create_task(
                                    _open_for_reauth(_br, _reauth_url, user_id),
                                )
                        except Exception:
                            logger.debug(
                                "Re-auth browser dispatch failed (non-fatal)",
                                exc_info=True,
                            )

                    _err_tail = (_last_err or "").strip()
                    if len(_err_tail) > 200:
                        _err_tail = _err_tail[-200:]

                    if _reauth_url:
                        _handoff = (
                            f"I tried `{_failed_tool}` 3 times — each one failed:\n\n"
                            f"```\n{_err_tail}\n```\n\n"
                            f"This usually means the {_service_label} session needs a "
                            f"refresh or there's a 'Verify you are human' check.\n\n"
                            f"Please:\n"
                            f"1. Open Brave at {_reauth_url} (I'll try to open it for you)\n"
                            f"2. Sign in / solve the challenge if needed\n"
                            f"3. Reply `continue` and I'll retry the request."
                        )
                    else:
                        _handoff = (
                            f"I tried `{_failed_tool}` 3 times — each one failed:\n\n"
                            f"```\n{_err_tail}\n```\n\n"
                            f"This usually means the underlying service needs auth or "
                            f"is rate-limiting. Please verify your login on the host "
                            f"browser, then reply `continue` and I'll retry."
                        )

                    if _delegate_registered and self.registry:
                        try:
                            self.registry.unregister("delegate")
                        except Exception:
                            pass
                    await cb.on_event(AgentEvent(
                        "done",
                        f"Three-strikes break on {_failed_tool}",
                        {"failed_tool": _failed_tool, "reauth_url": _reauth_url},
                    ))
                    logger.info(
                        "Three-strikes graceful handoff returned to user (tool=%s, reauth=%s)",
                        _failed_tool, _reauth_url or "(none)",
                    )
                    if self._team_lead and _fg_task_id:
                        self._team_lead.fail(
                            _fg_task_id, f"3 strikes on {_failed_tool}",
                        )
                        _fg_task_id = None
                    return _handoff

                # ── Auto-promote-to-background nudge ──
                # Foreground turns that hit _PROMOTE_BG_AT_ITER without
                # having dispatched run_background should be moved off the
                # blocking lane. Inject a strong system instruction telling
                # the brain to package up the rest as a background task.
                # Only fires for foreground turns that have run_background
                # available and haven't already used it.
                #
                # Exemption (added 2026-05-13 after Upwork-conversation bug):
                # if EVERY tool call this turn is a quick read-only inspection
                # (recall_memories, search_tools, *_get_*, *_search_*,
                # vault_get, list_*, etc.) — or a quick single-shot budget
                # write (see _QUICK_INLINE_BUDGET_WRITES) — skip auto-promote
                # even past the
                # iter threshold. The brain is just paging through info to
                # reply inline; forcing run_background here strands the
                # follow-up tool calls and the user gets the misleading
                # "that tool doesn't exist" hallucination loop. Long-running
                # work — submit_proposal, send_message, browser type/click,
                # apply_*, deliver_*, schedule_job — still gates on the
                # iteration cap as before.
                _is_foreground = not getattr(self, "is_background", False)
                _has_run_bg = any(
                    (t.get("function", {}) or {}).get("name") == "run_background"
                    for t in (tools or [])
                )

                def _is_readonly_inspection(name: str) -> bool:
                    if not name:
                        return False
                    n = name.lower()
                    if n.startswith("mcp_"):
                        parts = n.split("_", 2)
                        if len(parts) == 3:
                            n = parts[2]
                    if n in {
                        "recall_memories", "search_tools", "save_memory",
                        "list_tasks", "list_jobs", "list_watchers",
                        "work_todos", "daily_briefing", "vault_get",
                        # Deterministic read-only NL skills whose names
                        # don't fit the get_*/list_*/search_* shape but
                        # which are pure fetches that should NOT trigger
                        # AUTO-PROMOTE. Without this, asking
                        # "find out what James wants on Upwork" called
                        # `upwork_last_conversation` (correct!) and the
                        # brain was IMMEDIATELY force-promoted to
                        # run_background before it could synthesize a
                        # reply from the fetched data. Observed
                        # 2026-05-17 19:17:41.
                        "upwork_last_conversation",
                        "upwork_inbox_check",
                        "upwork_contract_poll",
                        # Job/proposal READS — "find me 5 upwork jobs" is a
                        # pure fetch. Without these, a web job search that
                        # reached for the raw MCP tool got AUTO-PROMOTE'd to a
                        # Telegram-bound background task instead of answering
                        # inline (2026-06-03). The `search_jobs` NL skill is
                        # already covered by the startswith("search_") rule.
                        "upwork_search_jobs",
                        "upwork_get_job_details",
                        "upwork_get_offers",
                        "find_contact",
                        "list_contacts",
                        "list_memories",
                        "lookup_project_asset",
                        # Budget reads — a Web UI "show me nima's expenses" /
                        # "expense report" must answer INLINE, not get
                        # AUTO-PROMOTE'd to a Telegram-bound background task.
                        "list_expenses",
                        "expense_report",
                        "list_projects",
                        "get_project",
                    }:
                        return True
                    # Quick single-shot budget writes stay inline too — see
                    # _QUICK_INLINE_BUDGET_WRITES (2026-05-28 latency incident).
                    if n in _QUICK_INLINE_BUDGET_WRITES:
                        return True
                    # Channel reads (whatsapp_read/list_chats, email_read,
                    # instagram_read_dms, etc.) are pure fetches — a Web UI
                    # "check my whatsapp" must answer INLINE, not get
                    # AUTO-PROMOTE'd to a background task whose result lands
                    # on Telegram (2026-05-26 incident). `_is_channel_read_
                    # tool_name` already curates the channel-read surface.
                    if _is_channel_read_tool_name(name):
                        return True
                    return (
                        "_get_" in n
                        # `search_jobs` matches startswith; `upwork_search_jobs`
                        # and other `<service>_search_*` reads need the
                        # mid-string form (the name starts with the service,
                        # not "search_").
                        or "_search_" in n
                        # Document/sheet READS — `read_sheet`, `read_doc`,
                        # `read_pdf` (native skills) and `<mcp>_read_sheet_
                        # values` are pure fetches. Without these a Web-UI
                        # "check sheet what we have there" that paged through
                        # read_sheet_values got AUTO-PROMOTE'd to a background
                        # task whose consolidated reply then vanished on web
                        # (2026-06-04). Mirrors the get_/list_/search_ rules.
                        or "_read_" in n
                        or n.startswith("get_")
                        or n.startswith("list_")
                        or n.startswith("search_")
                        or n.startswith("read_")
                        or n.startswith("recall_")
                        or n.endswith("_get_messages")
                        or n.endswith("_get_conversation")
                    )

                _only_readonly_so_far = bool(_called_tool_names) and all(
                    _is_readonly_inspection(nm) for nm in _called_tool_names
                )

                if (
                    _is_foreground
                    and _has_run_bg
                    and not _promoted_to_bg
                    and "run_background" not in _called_tool_names
                    # `dispatch_subagents` is ALREADY a non-blocking async
                    # dispatch (returns immediately with task IDs). Promoting
                    # a turn that called it to a background worker is
                    # redundant AND triggers a nested fan-out whose subagent
                    # results strand in pending_subagent_notes — the
                    # 2026-05-29 "Chek my whats up" future-tense-promise bug.
                    # Let the dispatch_subagents consolidation turn deliver
                    # the results instead (task_runner brain-fanout).
                    and "dispatch_subagents" not in _called_tool_names
                    # `delegate` is ALSO a fire-and-forget async dispatch
                    # (delegate.py:268 schedules run_specialist detached and
                    # returns immediately). Promoting a turn that delegated
                    # to a background worker spawns a THIRD redundant
                    # executor — the 2026-06-08 14:18 triple-execution bug.
                    and "delegate" not in _called_tool_names
                    and iteration >= _PROMOTE_BG_AT_ITER
                    and not _only_readonly_so_far
                    # THIN-ROUTER replaces AUTO-PROMOTE with the earlier
                    # 1-inline-action cap; don't double-handle under the flag.
                    and not _thin_router
                ):
                    _promoted_to_bg = True
                    _force_dispatch_only = True
                    _promote_iter = iteration
                    _promote_msg = LLMMessage(
                        role="system",
                        content=(
                            f"[AUTO-PROMOTE] You've used {iteration} iterations "
                            "in a foreground chat turn and haven't finished. The "
                            "user is waiting in chat with their UI frozen. STOP "
                            "doing more work in this turn. Your NEXT response "
                            "MUST: (1) call `run_background(instruction=..., "
                            "name=...)` with a self-contained instruction that "
                            "captures everything still needed (current state, "
                            "what's done, what remains, target sheet/file, "
                            "success criteria), then (2) write a short reply to "
                            "the user like 'Continuing in background, will "
                            "report back when done.' DO NOT call any other tool "
                            "this turn — the runtime has restricted your tool "
                            "list to ONLY `run_background` for this iteration. "
                            "Background tasks have full tool access and will "
                            "surface results back to chat when complete."
                        ),
                    )
                    messages.append(_promote_msg)
                    logger.info(
                        "Iteration %d: AUTO-PROMOTE injected — pushing brain "
                        "to dispatch run_background and exit foreground turn",
                        iteration + 1,
                    )

                # Failsafe: if AUTO-PROMOTE fired AND brain went one more
                # iteration without calling run_background (despite the
                # tool list being narrowed to that single tool), the
                # runtime auto-submits the original user message via
                # task_runner. Guarantees a chat-responsive turn within 1
                # extra iter regardless of brain compliance.
                if (
                    _force_dispatch_only
                    and _promote_iter is not None
                    and iteration > _promote_iter
                    and "run_background" not in _called_tool_names
                    and self._task_runner is not None
                    and user_id is not None
                ):
                    logger.warning(
                        "AUTO-PROMOTE failsafe: brain refused run_background "
                        "even with narrowed tool list at iter=%d (promote_iter=%d) — "
                        "runtime auto-submitting original message to task_runner",
                        iteration, _promote_iter,
                    )
                    try:
                        from lazyclaw.runtime.agent_settings import get_agent_settings
                        _agent_settings = await get_agent_settings(self.config, user_id)
                        _bg_task_id = await self._task_runner.submit(
                            user_id=user_id,
                            instruction=message,
                            name=None,
                            timeout=_agent_settings.get("specialist_timeout_s", 600),
                            callback=callback,
                            # Fix H — see hallucination-cap failsafe for rationale.
                            source="brain",
                            caller_depth=self._depth,
                        )
                        if self._team_lead and _fg_task_id:
                            self._team_lead.cancel(_fg_task_id)
                            _fg_task_id = None
                        await cb.on_event(AgentEvent(
                            FAST_DISPATCH,
                            "Auto-promoted to background after AUTO-PROMOTE refusal",
                            {"task_id": _bg_task_id},
                        ))
                        await cb.on_event(AgentEvent("done", "Dispatched", {}))
                        if _delegate_registered and self.registry:
                            self.registry.unregister("delegate")
                        return "Continuing in background — will report back when done."
                    except Exception:
                        logger.exception(
                            "AUTO-PROMOTE failsafe failed; continuing inline",
                        )

                # Cap-aware responsiveness failsafe (THIN-ROUTER path): the
                # cap only narrows `tools` to meta-only — it never sets
                # _force_dispatch_only, so the AUTO-PROMOTE failsafes above
                # never fire under the flag. Without this, a brain that
                # ignores the narrowed list and keeps iterating without
                # delegating runs to max_iterations (frozen chat). Mirrors the
                # AUTO-PROMOTE failsafe exactly, but keys on the cap flags:
                # one iter AFTER the cap engaged, if the brain still hasn't
                # called any dispatch tool, auto-submit the original message
                # to task_runner. The brain gets one capped iteration first to
                # actually delegate (iteration > _cap_iter).
                if (
                    _thin_router
                    and _thin_router_capped
                    and _cap_iter is not None
                    and iteration > _cap_iter
                    and not getattr(self, "is_background", False)
                    and self._task_runner is not None
                    and user_id is not None
                    and not any(
                        d in _called_tool_names
                        for d in ("delegate", "dispatch_subagents", "run_background")
                    )
                ):
                    logger.warning(
                        "THIN-ROUTER failsafe: brain refused to delegate even "
                        "with meta-only tool list at iter=%d (cap_iter=%d) — "
                        "runtime auto-submitting original message to task_runner",
                        iteration, _cap_iter,
                    )
                    try:
                        from lazyclaw.runtime.agent_settings import get_agent_settings
                        _agent_settings = await get_agent_settings(self.config, user_id)
                        _bg_task_id = await self._task_runner.submit(
                            user_id=user_id,
                            instruction=message,
                            name=None,
                            timeout=_agent_settings.get("specialist_timeout_s", 600),
                            callback=callback,
                            # Fix H — see hallucination-cap failsafe for rationale.
                            source="brain",
                            caller_depth=self._depth,
                        )
                        if self._team_lead and _fg_task_id:
                            self._team_lead.cancel(_fg_task_id)
                            _fg_task_id = None
                        await cb.on_event(AgentEvent(
                            FAST_DISPATCH,
                            "Auto-promoted to background after THIN-ROUTER cap refusal",
                            {"task_id": _bg_task_id},
                        ))
                        await cb.on_event(AgentEvent("done", "Dispatched", {}))
                        if _delegate_registered and self.registry:
                            self.registry.unregister("delegate")
                        return "Continuing in background — will report back when done."
                    except Exception:
                        logger.exception(
                            "THIN-ROUTER failsafe failed; continuing inline",
                        )

                # ── Running-long nudge ──
                # At 80% of safety cap, tell the LLM to wrap up or ask user
                if iteration >= _nudge_at and not _nudged:
                    _nudged = True
                    _nudge_msg = LLMMessage(
                        role="system",
                        content=(
                            "You've been working for a while. You have a few steps left. "
                            "Either: (1) finish what you're doing and summarize results, "
                            "or (2) if you need more work, explain what's left and ask "
                            "the user if they want you to continue."
                        ),
                    )
                    messages.append(_nudge_msg)
                    logger.info(
                        "Iteration %d/%d: injected running-long nudge",
                        iteration + 1, max_iterations,
                    )

                # ── Stuck detection (replaces old inline loop detection) ──
                # Only run if tools were actually called this iteration
                if response and response.tool_calls:
                    # TAOR phase: entering "observe" — interpreting tool results.
                    try:
                        await cb.on_event(AgentEvent(
                            "phase", "observe",
                            {"phase": "observe", "iteration": iteration,
                             "results_count": len(response.tool_calls)},
                        ))
                        if self._team_lead and _fg_task_id:
                            self._team_lead.update_phase(_fg_task_id, "observe")
                    except Exception:
                        pass

                    _last_result = _tool_results[-1] if _tool_results else None

                    # ── Mid-turn pivot ─────────────────────────────
                    # Brain dispatches, workers execute. If the brain has
                    # run ≥5 same-shape tool calls in this turn, it has
                    # slipped into worker role for what should be a
                    # fan-out. Fire ONE system nudge per turn redirecting
                    # it to dispatch_subagents / run_background.
                    if not _pivot_nudged:
                        _pivot_signal = detect_inline_pivot(
                            _tool_call_signatures, threshold=5,
                        )
                        if _pivot_signal is not None:
                            logger.info(
                                "Mid-turn pivot fired: %d× %s — "
                                "nudging brain to dispatch",
                                _pivot_signal.count, _pivot_signal.tool_name,
                            )
                            messages.append(LLMMessage(
                                role="system",
                                content=(
                                    f"⚠ BRAIN DISPATCHES, WORKERS EXECUTE.\n\n"
                                    f"You've executed {_pivot_signal.count} "
                                    f"same-shape `{_pivot_signal.tool_name}` "
                                    f"calls in this turn. Stop iterating "
                                    f"inline.\n\n"
                                    f"Pivot now:\n"
                                    f"- Split the remaining items across "
                                    f"2–3 `dispatch_subagents` workers "
                                    f"(each handling a chunk via batch "
                                    f"scraper tools), OR\n"
                                    f"- Fire ONE `run_background` if it's "
                                    f"a single batch job.\n\n"
                                    f"Reply with a short status — the "
                                    f"brain consolidates when workers "
                                    f"return."
                                ),
                            ))
                            _pivot_nudged = True

                    _stuck_signal = detect_stuck(
                        _tool_call_history, _tool_results, _last_result,
                    )
                    if _stuck_signal is not None:
                        logger.warning(
                            "Stuck detected: %s (%s)", _stuck_signal.reason, _stuck_signal.context,
                        )

                        # ── Graduated recovery (3 levels) ─────────────────────
                        # Level 1 (soft): read + try different approach, no model switch
                        # Level 2 (medium): switch to brain, demand strategy change
                        # Level 3 (hard): give up / ask user for help
                        if _escalation_level == 0:
                            _escalation_level = 1
                            logger.info(
                                "Stuck level 1 (soft nudge): %s", _stuck_signal.reason
                            )
                            messages.append(LLMMessage(
                                role="system",
                                content=(
                                    f"⚠ Not making progress: {_stuck_signal.context}\n\n"
                                    f"Before your next action:\n"
                                    f"1. Use action='read' to understand what's currently on the page.\n"
                                    f"2. Use action='snapshot' to see available elements.\n"
                                    f"Then try a DIFFERENT approach — not the same action again."
                                ),
                            ))
                            _tool_call_history.clear()
                            _tool_results.clear()
                            _tool_call_signatures.clear()
                            continue

                        elif _escalation_level == 1:
                            _escalation_level = 2
                            _escalated = True  # triggers brain model in routing
                            _escalation_iter = iteration
                            _post_l2_stuck_count = 0  # reset window counter
                            logger.info(
                                "Stuck level 2 (strategy change, brain escalation): %s",
                                _stuck_signal.reason,
                            )
                            await cb.on_event(AgentEvent(
                                "llm_call",
                                "Escalating to brain model...",
                                {"escalation": True},
                            ))
                            messages.append(LLMMessage(
                                role="system",
                                content=(
                                    f"⚠ STRATEGY CHANGE REQUIRED: {_stuck_signal.context}\n\n"
                                    f"The previous approach failed twice. You MUST choose ONE:\n"
                                    f"1. Use web_search to RESEARCH how to accomplish this — find the right URL, steps, or workaround\n"
                                    f"2. Try a COMPLETELY DIFFERENT approach (different URL, different button, different strategy)\n"
                                    f"3. If the page requires login or human interaction, say so explicitly\n"
                                    f"4. If the task is truly impossible right now, STOP and explain what went wrong (text only)\n\n"
                                    f"Describe what you've tried, why it failed, and what you'll try differently."
                                ),
                            ))
                            _tool_call_history.clear()
                            _tool_results.clear()
                            _tool_call_signatures.clear()
                            iter_model = None  # Let eco_router decide
                            _iter_role = ROLE_BRAIN
                            continue

                        # Level 3: brain also stuck — give up.
                        # Background agents: break immediately (can't ask user).
                        # Foreground agents: 1st post-L2 stuck → ask user for
                        # help; 2nd → hard-stop with a terminal message
                        # (don't keep popping help dialogs at the user).
                        _post_l2_stuck_count += 1
                        _is_bg = getattr(self, "is_background", False)
                        _hard_stop = _is_bg or _post_l2_stuck_count >= 2
                        if _hard_stop:
                            if _is_bg:
                                logger.warning("Background agent stuck after escalation — giving up")
                            else:
                                logger.warning(
                                    "Stuck level 3 hard-stop: %d post-L2 stuck signals "
                                    "(reason=%s, tool=%s) — terminating loop without "
                                    "another HELP_NEEDED dialog",
                                    _post_l2_stuck_count,
                                    _stuck_signal.reason,
                                    _stuck_signal.tool_name,
                                )
                            _failed_tool = _stuck_signal.tool_name or "the same step"
                            all_new_messages.append(LLMMessage(
                                role="assistant",
                                content=(
                                    f"I tried '{_failed_tool}' repeatedly and it kept "
                                    f"failing — {_stuck_signal.context}. I'm stopping "
                                    f"so I don't burn more time on a broken path. "
                                    f"Tell me what you'd like me to do differently."
                                ),
                            ))
                            break

                        await cb.on_event(AgentEvent(
                            HELP_NEEDED, _stuck_signal.context,
                            {"reason": _stuck_signal.reason, "tool": _stuck_signal.tool_name,
                             "needs_browser": _stuck_signal.needs_browser},
                        ))

                        # Ask user for help — timeout after 5 minutes
                        try:
                            _help_response = await asyncio.wait_for(
                                cb.on_help_request(
                                    _stuck_signal.context, _stuck_signal.needs_browser,
                                ),
                                timeout=300.0,
                            )
                        except asyncio.TimeoutError:
                            logger.info("Help request timed out after 5 min, auto-skipping")
                            _help_response = "skip"

                        if _help_response == "skip":
                            all_new_messages.append(LLMMessage(
                                role="assistant",
                                content=f"I got stuck: {_stuck_signal.context}. Let me try a different approach.",
                            ))
                            break

                        # Browser handoff: user said "ready" → ensure visible → wait for "done"
                        if _stuck_signal.needs_browser and _help_response in (
                            "ready", "show me", "show", "ok", "yes",
                        ):
                            if not getattr(self, "is_background", False):
                                from lazyclaw.skills.builtin.browser_actions.backends import (
                                    get_visible_cdp_backend as _get_visible_cdp_backend,
                                    raise_browser_window as _raise_browser_window,
                                    stop_remote_session as _stop_remote_session,
                                )
                                await _get_visible_cdp_backend(user_id)

                                # Server mode: send noVNC URL via Telegram
                                from lazyclaw.browser.remote_takeover import (
                                    get_active_session, is_server_mode,
                                )
                                _remote = get_active_session(user_id) if is_server_mode() else None

                                if _remote:
                                    await cb.on_event(AgentEvent(
                                        HELP_RESPONSE,
                                        f"Browser ready for remote control: {_remote.url}",
                                        {"novnc_url": _remote.url,
                                         "stuck_context": _stuck_signal.context},
                                    ))
                                    try:
                                        _done_resp = await asyncio.wait_for(
                                            cb.on_help_request(
                                                "Say 'done' when you're finished.",
                                                False,
                                            ),
                                            timeout=600.0,  # 10 min for browser handoff
                                        )
                                    except asyncio.TimeoutError:
                                        logger.info("Browser handoff timed out after 10 min")
                                        _done_resp = "skip"
                                    # Cleanup: stop noVNC, relaunch headless
                                    await _stop_remote_session(user_id)
                                else:
                                    await _raise_browser_window()
                                    # Give macOS time to bring window to foreground
                                    await asyncio.sleep(1.0)
                                    await cb.on_event(AgentEvent(
                                        HELP_RESPONSE,
                                        "Browser is visible. Take over and say 'done' when finished.",
                                        {},
                                    ))
                                    try:
                                        _done_resp = await asyncio.wait_for(
                                            cb.on_help_request(
                                                "Browser is open on your screen. Say 'done' when you're finished.",
                                                False,
                                            ),
                                            timeout=600.0,  # 10 min for browser handoff
                                        )
                                    except asyncio.TimeoutError:
                                        logger.info("Browser handoff timed out after 10 min")
                                        _done_resp = "skip"

                                if _done_resp == "skip":
                                    all_new_messages.append(LLMMessage(
                                        role="assistant",
                                        content=f"I got stuck: {_stuck_signal.context}. User chose to skip.",
                                    ))
                                    break

                        # Notify observability that help was received
                        await cb.on_event(AgentEvent(
                            HELP_RESPONSE,
                            f"User help received: {_help_response}",
                            {"response": _help_response},
                        ))

                        # Take snapshot after user intervention
                        try:
                            from lazyclaw.skills.builtin.browser_actions.backends import get_cdp_backend as _get_cdp_backend
                            _snap_backend = await _get_cdp_backend(user_id)
                            _snap_url = await _snap_backend.current_url()
                            _snap_title = await _snap_backend.title()
                            _snapshot = f"After user help: now on {_snap_title} ({_snap_url})"
                        except Exception:
                            logger.debug("Failed to get browser snapshot after user help", exc_info=True)
                            _snapshot = f"User intervention complete. Response: {_help_response}"

                        # Inject snapshot as assistant message (NOT tool —
                        # tool results were already appended, so a second tool
                        # msg with the same id would orphan and crash OpenAI).
                        _help_msg = LLMMessage(
                            role="assistant", content=_snapshot,
                        )
                        messages.append(_help_msg)
                        all_new_messages.append(_help_msg)
                        _tool_call_history.clear()
                        _tool_results.clear()
                        _tool_call_signatures.clear()
                        continue

            else:
                # Safety cap reached — shouldn't happen often with nudge
                if self._team_lead and _fg_task_id:
                    self._team_lead.fail(_fg_task_id, "Safety cap reached")
                    _fg_task_id = None
                logger.warning(
                    "Safety cap reached (%d iterations). Last tool: %s",
                    max_iterations,
                    _tool_call_history[-1] if _tool_call_history else "none",
                )
                all_new_messages.append(
                    LLMMessage(
                        role="assistant",
                        content=(response.content if response and response.content else "")
                        or (
                            "I hit the safety limit. Here's what I've done so far. "
                            "Say 'continue' if you want me to keep going."
                        ),
                    )
                )
        except asyncio.CancelledError:
            if self._team_lead and _fg_task_id:
                self._team_lead.cancel(_fg_task_id)
            await cb.on_event(AgentEvent("done", "Cancelled", {}))
            if _delegate_registered and self.registry:
                self.registry.unregister("delegate")
            if _dispatch_registered and self.registry:
                self.registry.unregister("dispatch_subagents")
            return "Operation cancelled."

        # ── TAOR Phase 3: VERIFY ──────────────────────────────────────
        # After the execute phase, verify the final response with lightweight
        # heuristic checks (no LLM call). On failure, inject failure context
        # and make one correction LLM call, then re-verify. Repeats up to
        # _taor_verify_passes times. LOW effort skips this entirely.
        #
        # The correction call uses ROLE_BRAIN (Claude) and explicitly instructs
        # text-only response — no tool calls. This keeps it cheap and fast.
        if _taor_verify_passes > 0 and needs_tools and all_new_messages:
            from lazyclaw.llm.providers.base import LLMResponse as _LLMRespV
            # TAOR phase: entering "reflect" — verification pass.
            try:
                await cb.on_event(AgentEvent(
                    "phase", "reflect",
                    {"phase": "reflect", "passes": _taor_verify_passes},
                ))
                if self._team_lead and _fg_task_id:
                    self._team_lead.update_phase(_fg_task_id, "reflect")
            except Exception:
                pass
            for _taor_v in range(_taor_verify_passes):
                _taor_last = all_new_messages[-1]
                _taor_final = _taor_last.content or ""
                _taor_ok, _taor_reason = verify_response(
                    message, _taor_final, _tool_results, _effort,
                )
                if _taor_ok:
                    logger.info("TAOR verify passed on attempt %d", _taor_v + 1)
                    break
                # Last attempt — log and proceed with best-effort response.
                if _taor_v == _taor_verify_passes - 1:
                    logger.info(
                        "TAOR verify: %d passes exhausted, using last response",
                        _taor_verify_passes,
                    )
                    break
                logger.info(
                    "TAOR verify failed (pass %d/%d): %s",
                    _taor_v + 1, _taor_verify_passes, _taor_reason,
                )
                _taor_retry_context = _taor_reason
                await cb.on_event(AgentEvent(
                    "taor_verify_retry",
                    f"Self-checking response (attempt {_taor_v + 2})",
                    {"reason": _taor_reason, "attempt": _taor_v + 1},
                ))
                # Inject correction directive — text-only, no tools.
                _correction_directive = (
                    "[SELF-VERIFICATION FAILED: "
                    f"{_taor_reason} "
                    "Provide a corrected, complete response that addresses "
                    "this issue. Respond with text only — do NOT call any tools.]"
                )
                messages.append(LLMMessage(role="assistant", content=_taor_final))
                messages.append(LLMMessage(role="user", content=_correction_directive))
                try:
                    _corr_resp = await self.eco_router.chat(
                        messages, user_id=user_id, role=ROLE_BRAIN,
                    )
                    if _corr_resp.content and _corr_resp.content.strip():
                        _corrected = _corr_resp.content
                        all_new_messages.append(
                            LLMMessage(role="assistant", content=_corrected)
                        )
                        await cb.on_event(AgentEvent(
                            "token", _corrected,
                            {"model": _corr_resp.model, "taor_correction": True},
                        ))
                        await cb.on_event(AgentEvent("stream_done", "", {}))
                except Exception as _ve:
                    logger.warning("TAOR verify correction call failed: %s", _ve)
                    break

        # ── TAOR Phase 4: CRITIC (real LLM audit) ─────────────────────
        # When the user has critic_mode ON and the turn is HIGH/MAX
        # effort, run a real LLM auditor over the assistant's last
        # reply. Loops up to 3 attempts: pass → ship as-is; fail → ask
        # brain to rewrite text-only → re-check. Fails open on any
        # exception (network, parse, rate limit) so a flaky critic can
        # never block a reply that might already be fine. Top-level
        # only — subagents skip this.
        if (
            _effort in (EffortLevel.HIGH, EffortLevel.MAX)
            and needs_tools
            and all_new_messages
            and all_new_messages[-1].role == "assistant"
        ):
            try:
                from lazyclaw.runtime.dispatcher import _IS_SUBAGENT
                if not _IS_SUBAGENT.get():
                    from lazyclaw.teams.settings import get_team_settings
                    _team_cfg = await get_team_settings(self.config, user_id)
                    if _team_cfg.get("critic_mode"):
                        from lazyclaw.runtime.critic import (
                            run_critic, build_tool_trace,
                        )
                        _critic_trace = build_tool_trace(
                            _tool_call_history, _tool_results,
                        )
                        _critic_reply_in = all_new_messages[-1].content or ""
                        _critic_result = await run_critic(
                            user_message=message,
                            reply=_critic_reply_in,
                            tool_trace=_critic_trace,
                            base_messages=list(messages),
                            eco_router=self.eco_router,
                            user_id=user_id,
                            model_id=_team_cfg.get("critic_model") or None,
                            max_loops=3,
                        )
                        logger.info(
                            "TAOR critic: passed=%s loops=%d rewrites=%d",
                            _critic_result.passed,
                            _critic_result.loops_used,
                            _critic_result.rewrite_count,
                        )
                        if _critic_result.final_reply != _critic_reply_in:
                            all_new_messages[-1] = LLMMessage(
                                role="assistant",
                                content=_critic_result.final_reply,
                            )
                            try:
                                await cb.on_event(AgentEvent(
                                    "critic",
                                    f"Critic {'passed' if _critic_result.passed else 'flagged'} "
                                    f"after {_critic_result.loops_used} pass(es)",
                                    {
                                        "passed": _critic_result.passed,
                                        "loops_used": _critic_result.loops_used,
                                        "rewrite_count": _critic_result.rewrite_count,
                                        "issues": list(_critic_result.last_issues),
                                    },
                                ))
                            except Exception:
                                pass
            except Exception as _ce:
                logger.warning("TAOR critic call failed (failing open): %s", _ce)

        # ── Post-loop: persist + cleanup (guarded by finally) ─────────
        content = ""
        try:
            # Resolve chat session
            if not chat_session_id:
                async with db_session(self.config) as db:
                    row = await db.execute(
                        "SELECT id FROM agent_chat_sessions "
                        "WHERE user_id = ? AND archived_at IS NULL "
                        "ORDER BY created_at DESC LIMIT 1",
                        (user_id,),
                    )
                    existing = await row.fetchone()
                    if existing:
                        chat_session_id = existing[0]
                    else:
                        chat_session_id = str(uuid4())
                        await db.execute(
                            "INSERT INTO agent_chat_sessions (id, user_id) VALUES (?, ?)",
                            (chat_session_id, user_id),
                        )
                        await db.commit()

            # Store ALL messages (user, assistant, tool calls, tool results) encrypted
            rows = []
            for msg in all_new_messages:
                msg_id = str(uuid4())
                encrypted_content = encrypt(msg.content, key)
                tool_name = None
                metadata = None

                if msg.tool_calls:
                    # Encrypt tool-call ARGUMENTS — they carry user content
                    # (vault values, message bodies) just like `content`
                    # (2026-06-10 audit, Phase 2). Read side is tolerant:
                    # metadata_codec passes legacy plaintext rows through.
                    from lazyclaw.memory.metadata_codec import encode_tool_metadata

                    metadata = encode_tool_metadata(
                        json.dumps(
                            [
                                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                                for tc in msg.tool_calls
                            ]
                        ),
                        key,
                    )
                if msg.tool_call_id:
                    tool_name = msg.tool_call_id

                rows.append((msg_id, user_id, chat_session_id, msg.role, encrypted_content, tool_name, metadata))

            async with db_session(self.config) as db:
                # Ensure chat session row exists (upsert)
                if chat_session_id:
                    await db.execute(
                        "INSERT OR IGNORE INTO agent_chat_sessions (id, user_id) VALUES (?, ?)",
                        (chat_session_id, user_id),
                    )
                await db.executemany(
                    "INSERT INTO agent_messages (id, user_id, chat_session_id, role, content, tool_name, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                # Update message count + auto-set title from first user message
                if chat_session_id:
                    await db.execute(
                        "UPDATE agent_chat_sessions SET message_count = "
                        "(SELECT COUNT(*) FROM agent_messages WHERE chat_session_id = ?) "
                        "WHERE id = ?",
                        (chat_session_id, chat_session_id),
                    )
                    # Set title from first user message if still untitled
                    user_msgs = [m for m in all_new_messages if m.role == "user"]
                    if user_msgs:
                        first_text = user_msgs[0].content[:50]
                        await db.execute(
                            "UPDATE agent_chat_sessions SET title = ? "
                            "WHERE id = ? AND (title IS NULL OR title = '')",
                            (first_text, chat_session_id),
                        )
                await db.commit()

            # Record and return the final assistant message content
            final = all_new_messages[-1]
            content = final.content or ""
            if not content.strip():
                content = "I wasn't able to generate a response. Please try again."
            await recorder.record_final_response(content)

            # Fire work summary for direct mode (with model attribution)
            from lazyclaw.runtime.summary import build_work_summary
            from lazyclaw.llm.pricing import calculate_cost

            _models_used = []
            _total_cost = 0.0
            _routing = self.eco_router.last_routing if self.eco_router else None
            if _routing:
                _models_used.append((_routing.display_name, _routing.icon, _routing.is_local))
                _total_cost = calculate_cost(
                    _routing.model,
                    _session_tokens // 2,  # approximate split
                    _session_tokens - _session_tokens // 2,
                )

            _direct_summary = build_work_summary(
                start_time=_start_time,
                llm_calls=iteration + 1,
                tools_used=_all_tools_used,
                specialists=[],
                total_tokens=_session_tokens,
                user_message=message,
                response=content,
                models_used=_models_used,
                total_cost=_total_cost,
            )
            await cb.on_event(AgentEvent(
                "work_summary", "Task complete",
                {"summary": _direct_summary},
            ))

        finally:
            # Safety net: if this turn was bound to a task (via the
            # [TASK_REMINDER:<id>] prefix) and the agent ended without calling
            # complete_task or fail_task, auto-fail the row so LazyBrain and
            # the tasks table stop lying about outcome.  Only auto-fails —
            # never auto-completes (preserves graph honesty).
            if _bound_task_id and not (
                {"complete_task", "fail_task"} & set(_all_tools_used)
            ):
                try:
                    from lazyclaw.tasks.store import fail_task as _fail_task
                    await _fail_task(
                        self.config,
                        user_id,
                        _bound_task_id,
                        error="agent exited without marking",
                    )
                    logger.info(
                        "TAOR safety net: auto-failed bound task %s "
                        "(user=%s) — agent turn ended without "
                        "complete_task/fail_task",
                        _bound_task_id, user_id,
                    )
                except Exception:
                    logger.debug(
                        "auto-fail of bound task %s failed",
                        _bound_task_id, exc_info=True,
                    )

            # ALWAYS mark foreground task done/failed in TeamLead
            if self._team_lead and _fg_task_id:
                if content:
                    self._team_lead.complete(
                        _fg_task_id, content[:100], result_full=content,
                    )
                else:
                    self._team_lead.fail(_fg_task_id, "Post-loop error")

            await cb.on_event(AgentEvent(
                "done", "Response ready",
                {
                    "model_used": _last_model_used,
                    "fallback_reason": _last_fallback_reason,
                },
            ))

            # Clean up per-message skills to avoid stale callback references
            if _delegate_registered and self.registry:
                self.registry.unregister("delegate")
            if _dispatch_registered and self.registry:
                self.registry.unregister("dispatch_subagents")

        # LazyBrain post-processing (fire-and-forget).  Both calls swallow
        # their own errors — we never block the reply on PKM bookkeeping.
        if content:
            try:
                from lazyclaw.runtime.wikilink_injector import inject as _lb_inject
                # Hard cap so a cold cache (post-save invalidate ⇒ DB
                # roundtrip) never holds up the reply. Warm-cache path
                # is microseconds; first call after a write does the
                # fetch and the next reply benefits from the warmed cache.
                content = await asyncio.wait_for(
                    _lb_inject(self.config, user_id, content),
                    timeout=0.5,
                )
            except asyncio.TimeoutError:
                logger.debug("wikilink_injector skipped (slow cache miss)")
            except Exception:
                logger.debug("wikilink_injector failed", exc_info=True)

            # Truly fire-and-forget: the worker-LLM call inside
            # capture_text_with_llm can take 1-3s on Gemma/Ollama, and the
            # lane queue is FIFO per user — if we await here, the next
            # message blocks behind PKM bookkeeping. Detach via create_task.
            async def _bg_auto_capture() -> None:
                try:
                    from lazyclaw.lazybrain.auto_capture import (
                        capture_text_with_llm as _lb_capture_llm,
                    )
                    from lazyclaw.llm.eco_router import EcoRouter as _EcoRouter

                    _eco = _EcoRouter(self.config, self.router)
                    await _lb_capture_llm(
                        self.config,
                        user_id,
                        message,
                        _eco,
                        source="chat",
                    )
                except Exception:
                    logger.warning(
                        "lazybrain auto_capture failed for user %s",
                        user_id, exc_info=True,
                    )
            try:
                asyncio.create_task(_bg_auto_capture())
            except Exception:
                logger.warning("could not schedule auto_capture", exc_info=True)

        return content
