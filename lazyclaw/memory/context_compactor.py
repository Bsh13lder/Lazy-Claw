"""Token-based automatic context compaction for the agentic loop.

Monitors token count before each LLM call. When above 80% of the model's
context limit, summarises older turns with a fast model (ROLE_WORKER /
Haiku-class) to free space while preserving critical information.

Key behaviours
--------------
- Keeps last N turns verbatim (configurable, default 5)
- Tool results from those N turns are kept verbatim automatically
- Summarises everything older into a structured ## Session Summary block
- Loads custom compaction rules from personal memory (type='compaction_rule')
- Falls back to a fast no-LLM summary when the older content is tiny
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
from lazyclaw.llm.providers.base import LLMMessage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# Compact when estimated token usage exceeds this fraction of the context limit
COMPACTION_THRESHOLD = 0.80

# Number of most-recent turns to keep verbatim (one turn = user msg + agent
# response + any tool call/result pairs belonging to that exchange)
DEFAULT_KEEP_TURNS = 5

# Minimum estimated tokens in the *older* section before paying for an LLM
# summarisation call.  Below this, use the fast no-LLM fallback.
MIN_LLM_SUMMARIZE_TOKENS = 500

# Default context limit (tokens) used when the model is unknown
_DEFAULT_CONTEXT_LIMIT = 128_000


# ---------------------------------------------------------------------------
# Context-limit lookup
# ---------------------------------------------------------------------------

def get_context_limit(model_name: str | None) -> int:
    """Return the context window size (tokens) for *model_name*.

    Checks the model catalog first, then falls back to a conservative default.
    """
    if not model_name:
        return _DEFAULT_CONTEXT_LIMIT
    try:
        from lazyclaw.llm.model_registry import get_model
        profile = get_model(model_name)
        if profile and profile.max_context:
            return profile.max_context
    except Exception as exc:
        logger.debug("Failed to look up model context limit for %r, using default: %s", model_name, exc)
    return _DEFAULT_CONTEXT_LIMIT


# ---------------------------------------------------------------------------
# Token estimation (no external tokeniser required)
# ---------------------------------------------------------------------------

def estimate_tokens(messages: list[LLMMessage], tools: list | None = None) -> int:
    """Rough token count using the ~4-chars-per-token heuristic.

    Includes per-message JSON framing overhead and tool-schema sizes.
    Intentionally errs on the high side for safety.
    """
    total_chars = 0
    for msg in messages:
        total_chars += 10  # role + JSON framing overhead
        if msg.content:
            total_chars += len(msg.content)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                total_chars += len(tc.name) + len(str(tc.arguments)) + 16
    if tools:
        for t in tools:
            total_chars += len(str(t))
    # Use 3.5 chars/token (slightly aggressive) so we trigger early enough
    return int(total_chars / 3.5)


# ---------------------------------------------------------------------------
# Custom compaction rules
# ---------------------------------------------------------------------------

async def load_compaction_rules(config, user_id: str) -> list[str]:
    """Load per-user compaction rules from personal memory.

    Rules are personal memories with ``memory_type == 'compaction_rule'``.
    Example content: "Always preserve: active task list"
    """
    try:
        from lazyclaw.memory.personal import get_memories
        memories = await get_memories(config, user_id, limit=50)
        return [
            m["content"] for m in memories
            if m.get("type") == "compaction_rule"
        ]
    except Exception as exc:
        logger.debug("Failed to load compaction rules: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Turn splitting
# ---------------------------------------------------------------------------

def _split_into_turns(messages: list[LLMMessage]) -> list[list[LLMMessage]]:
    """Group messages into turns at user-message boundaries.

    Each turn starts with a user message and includes all following assistant
    / tool messages until the next user message.  Returns oldest-first list.
    """
    turns: list[list[LLMMessage]] = []
    current: list[LLMMessage] = []
    for msg in messages:
        if msg.role == "user" and current:
            turns.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        turns.append(current)
    return turns


# ---------------------------------------------------------------------------
# Summary formatters
# ---------------------------------------------------------------------------

_COMPACT_SYSTEM_PROMPT = """\
Summarise the older part of this conversation for context continuity.
Output EXACTLY the following format (no preamble, no extra markdown):

## Session Summary (compacted at turn {turn_n})
### Key Decisions:
<1-3 bullets of important decisions or outcomes>
### Active Tasks:
<tasks started but not yet completed, or "None">
### User Preferences:
<user preferences or instructions discovered, or "None">
### Important Context:
<technical details, file paths, error context, code snippets worth keeping>
### Tool Results (preserved):
<key tool results — search findings, file contents, API responses>

Rules:
- Preserve ALL factual data (file paths, URLs, error messages, code)
- Drop: greetings, filler, superseded plans, failed tool attempts
- Keep: decisions, tool outcomes, what user asked, what was delivered{custom_rules_section}
"""


def _quick_compact_summary(older_msgs: list[LLMMessage], turn_n: int) -> str:
    """Fast no-LLM summary — extracts key lines from older messages."""
    lines = [
        f"## Session Summary (compacted at turn {turn_n})",
        "### Important Context:",
    ]
    for msg in older_msgs:
        content = (msg.content or "")[:150].replace("\n", " ").strip()
        if not content:
            continue
        if msg.role == "user":
            lines.append(f"  - User: {content}")
        elif msg.role == "assistant":
            lines.append(f"  - Assistant: {content}")
        # Tool results omitted in quick mode (content is small anyway)
    return "\n".join(lines)


async def _llm_compact_summary(
    eco_router: EcoRouter,
    user_id: str,
    older_msgs: list[LLMMessage],
    custom_rules: list[str],
    turn_n: int,
) -> str:
    """Ask the LLM (ROLE_WORKER / Haiku-class) to produce a structured summary."""
    custom_section = ""
    if custom_rules:
        rules_text = "\n".join(f"- {r}" for r in custom_rules)
        custom_section = f"\n\nCustom rules from memory config:\n{rules_text}"

    system_prompt = _COMPACT_SYSTEM_PROMPT.format(
        turn_n=turn_n,
        custom_rules_section=custom_section,
    )

    transcript_lines: list[str] = []
    for msg in older_msgs:
        content = (msg.content or "")[:600]
        if msg.role == "user":
            transcript_lines.append(f"User: {content}")
        elif msg.role == "assistant":
            prefix = ""
            if msg.tool_calls:
                names = ", ".join(tc.name for tc in msg.tool_calls)
                prefix = f"[called {names}] "
            transcript_lines.append(f"Assistant: {prefix}{content}")
        elif msg.role == "tool":
            transcript_lines.append(f"Tool result: {content[:400]}")

    transcript = "\n\n".join(transcript_lines)
    llm_messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(
            role="user",
            content=f"Conversation to summarise:\n\n{transcript}",
        ),
    ]

    try:
        response = await eco_router.chat(
            llm_messages, user_id=user_id, role=ROLE_WORKER,
        )
        return response.content or _quick_compact_summary(older_msgs, turn_n)
    except Exception as exc:
        logger.warning(
            "LLM compaction failed (%s) — falling back to quick summary", exc,
        )
        return _quick_compact_summary(older_msgs, turn_n)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompactionResult:
    """Immutable result from compact_messages."""

    messages: tuple  # tuple[LLMMessage, ...] — new message list
    did_compact: bool
    before_tokens: int
    after_tokens: int
    turns_compacted: int


async def compact_messages(
    eco_router: EcoRouter,
    config,
    user_id: str,
    messages: list[LLMMessage],
    tools: list | None = None,
    keep_turns: int = DEFAULT_KEEP_TURNS,
    current_turn: int = 0,
) -> CompactionResult:
    """Summarise older turns to bring token count under the compaction threshold.

    Args:
        eco_router:    LLM router — uses ROLE_WORKER for cost efficiency.
        config:        App config (for personal memory access).
        user_id:       User ID (for custom rules + routing).
        messages:      Current messages list (system + history + accumulated).
        tools:         Tool schemas included in the LLM call (for token budget).
        keep_turns:    Number of most-recent turns to keep verbatim.
        current_turn:  Current agentic-loop iteration (for logging / summary).

    Returns:
        CompactionResult with the new message list and bookkeeping fields.
    """
    before_tokens = estimate_tokens(messages, tools)

    # Separate system messages (always kept) from conversational messages
    system_msgs = [m for m in messages if m.role == "system"]
    conv_msgs = [m for m in messages if m.role != "system"]

    turns = _split_into_turns(conv_msgs)

    if len(turns) <= keep_turns:
        return CompactionResult(
            messages=tuple(messages),
            did_compact=False,
            before_tokens=before_tokens,
            after_tokens=before_tokens,
            turns_compacted=0,
        )

    older_turns = turns[:-keep_turns]
    recent_turns = turns[-keep_turns:]

    older_msgs = [msg for turn in older_turns for msg in turn]
    recent_msgs = [msg for turn in recent_turns for msg in turn]

    older_tokens = estimate_tokens(older_msgs)

    if older_tokens < MIN_LLM_SUMMARIZE_TOKENS:
        summary_text = _quick_compact_summary(older_msgs, current_turn)
        logger.info(
            "Context compaction (quick): %d older turns at iteration %d",
            len(older_turns), current_turn,
        )
    else:
        custom_rules = await load_compaction_rules(config, user_id)
        summary_text = await _llm_compact_summary(
            eco_router, user_id, older_msgs, custom_rules, current_turn,
        )
        logger.info(
            "Context compaction (LLM): %d older turns → summary at iteration %d "
            "(%d tokens in older section)",
            len(older_turns), current_turn, older_tokens,
        )

    summary_msg = LLMMessage(role="system", content=summary_text)
    compacted: list[LLMMessage] = system_msgs + [summary_msg] + recent_msgs

    after_tokens = estimate_tokens(compacted, tools)
    logger.info(
        "Compaction complete: %d → %d estimated tokens (saved ~%d)",
        before_tokens, after_tokens, before_tokens - after_tokens,
    )

    return CompactionResult(
        messages=tuple(compacted),
        did_compact=True,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        turns_compacted=len(older_turns),
    )
