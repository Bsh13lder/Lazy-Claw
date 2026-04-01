"""Three-phase TAOR loop utilities: Plan → Execute → Verify.

Pure functions — no async, no lazyclaw imports (avoids circular deps).
Optimized for Claude models: planning prompts use structured XML tags
that Claude handles particularly well for decomposition and reasoning.

Effort levels:
    LOW    — simple lookups, greetings. Skip plan and verify entirely.
    MEDIUM — standard tool tasks. Plan injection + 1 verify pass.
    HIGH   — complex multi-step. Plan injection + 2 verify passes.
    MAX    — deeply complex. Exhaustive plan + 3 verify passes.
"""
from __future__ import annotations

import re
from enum import Enum


class EffortLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


# Patterns that suggest a task needs deep reasoning / multi-step execution.
_HIGH_EFFORT = re.compile(
    r"\b(refactor|implement|build|create|migrate|analyze|investigate|"
    r"debug|review|compare|design|architect|optimize|integrate|"
    r"configure|deploy|automate|generate|rewrite|restructure|"
    r"set\s+up|set\s+me\s+up|write\s+a\s+\w+)\b",
    re.IGNORECASE,
)

# Patterns that indicate a simple lookup / informational request.
_LOW_EFFORT = re.compile(
    r"^(what|who|when|where|why|how\s+does|how\s+do\s+i|how\s+is|how\s+are|"
    r"how'?s|tell\s+me|explain|show\s+me|list|get|find|search|look\s+up|"
    r"check|status|is\s+there|are\s+there)\b",
    re.IGNORECASE,
)

# Greeting prefix followed by a real request — strip the greeting for effort detection.
_GREETING_PREFIX = re.compile(
    r"^(he+y+|hi+|hello|yo+|sup|hola|hey+\s+there)\s+",
    re.IGNORECASE,
)


def detect_effort(message: str, has_tools: bool = True) -> EffortLevel:
    """Infer the appropriate effort level for a message.

    Rules (applied in order):
      1. No tools available → LOW (no planning needed without tools).
      2. Very short message (≤5 words) with no high-effort keywords → LOW.
      3. Starts with low-effort pattern and no high-effort keywords → LOW.
      4. 3+ high-effort keyword matches, or long message (>60 words) → MAX.
      5. Any high-effort keyword → HIGH.
      6. Default → MEDIUM.
    """
    if not has_tools:
        return EffortLevel.LOW

    stripped = message.strip()

    # Strip greeting prefix so "heyy how is going X" → "how is going X"
    _no_greeting = _GREETING_PREFIX.sub("", stripped).strip()
    if _no_greeting:
        stripped = _no_greeting

    word_count = len(stripped.split())

    # Short messages with no complex intent → LOW
    if word_count <= 5 and not _HIGH_EFFORT.search(stripped):
        return EffortLevel.LOW

    # Simple informational queries with no complex action → LOW
    if _LOW_EFFORT.match(stripped) and not _HIGH_EFFORT.search(stripped):
        return EffortLevel.LOW

    high_matches = _HIGH_EFFORT.findall(stripped)
    if not high_matches:
        return EffortLevel.MEDIUM

    # Many high-effort keywords or very long message → MAX
    if len(high_matches) >= 3 or word_count > 60:
        return EffortLevel.MAX

    return EffortLevel.HIGH


def make_plan_prompt(
    message: str,
    effort: EffortLevel,
    retry_context: str | None = None,
) -> str:
    """Build a Claude-optimized XML planning prompt.

    This is injected as a system message at iteration 0 of the agentic
    loop. Claude handles structured XML prompts particularly well — the
    <plan> block gets the model to reason before acting, improving
    first-attempt success rate on complex tasks.

    The model is told to output the plan and then immediately execute —
    no extra LLM round-trip required.

    Args:
        message: The original user message.
        effort: Controls how deeply to decompose the task.
        retry_context: If set, describes why a previous attempt failed.
            The model is instructed to adjust its approach accordingly.

    Returns:
        A system message string containing the XML planning prompt.
    """
    if effort == EffortLevel.MEDIUM:
        depth_instruction = (
            "Briefly decompose the task into 2-4 concrete steps."
        )
    elif effort == EffortLevel.HIGH:
        depth_instruction = (
            "Decompose thoroughly into up to 6 concrete steps. "
            "Note dependencies between steps and potential failure points."
        )
    else:  # MAX
        depth_instruction = (
            "Decompose exhaustively. Consider edge cases, failure modes, "
            "and alternative approaches. Self-critique the plan: is there "
            "a simpler way? What could go wrong? Then proceed with the "
            "best approach."
        )

    retry_block = ""
    if retry_context:
        retry_block = (
            "\n<previous_failure>\n"
            f"{retry_context}\n"
            "</previous_failure>\n"
            "<retry_instruction>Learn from this failure. "
            "Adjust your plan to avoid repeating the same mistake."
            "</retry_instruction>\n"
        )

    return (
        "<taor_plan>\n"
        "<instruction>\n"
        "Before executing, think through this task:\n"
        f"<task>{message}</task>\n"
        f"{retry_block}"
        f"<depth>{depth_instruction}</depth>\n"
        "\nOutput your plan in this exact format, "
        "then immediately execute it — do not wait for approval:\n"
        "<plan>\n"
        "  <goal>One sentence: what success looks like</goal>\n"
        "  <steps>\n"
        "    <step id=\"1\">First concrete action</step>\n"
        "    <step id=\"2\">Second concrete action (add more as needed)</step>\n"
        "  </steps>\n"
        "  <tools_needed>Comma-separated tool names you will use</tools_needed>\n"
        "</plan>\n"
        "</instruction>\n"
        "</taor_plan>"
    )


def verify_response(
    original_message: str,
    final_response: str,
    tool_results: list[str],
    effort: EffortLevel,
) -> tuple[bool, str | None]:
    """Lightweight heuristic verification of the final response.

    Pure function — no LLM calls. Fast and cheap. Checks for obvious
    failure signals: empty response, unacknowledged tool errors, deferred
    tasks. On failure the caller makes one correction LLM call.

    Args:
        original_message: The user's original request.
        final_response: The assistant's final text response.
        tool_results: All tool result strings from the execute phase.
        effort: Controls how strict the check is.

    Returns:
        (passed, failure_reason) — failure_reason is None when passed.
    """
    if effort == EffortLevel.LOW:
        return True, None

    if not final_response or not final_response.strip():
        return False, "Empty response — agent produced no output."

    lower = final_response.lower()

    # For HIGH/MAX: check if tool errors went unacknowledged in the response.
    if effort in (EffortLevel.HIGH, EffortLevel.MAX) and tool_results:
        _error_indicators = (
            "error:", "exception:", "traceback (most", "failed:",
            "connection refused", "permission denied", "no such file",
        )
        unhandled = [
            r[:120] for r in tool_results
            if any(ind in r.lower() for ind in _error_indicators)
        ]
        if unhandled:
            _ack_words = (
                "error", "fail", "couldn't", "unable", "problem",
                "issue", "wrong", "unfortunately",
            )
            if not any(w in lower for w in _ack_words):
                return False, (
                    "Tool errors were detected but the response does not "
                    f"address them: {unhandled[0]}"
                )

    # Detect deferred/incomplete responses at any effort level.
    _deferral_phrases = (
        "i'll do that later",
        "i'll get back to you",
        "let me know when you",
        "i need more information before i can",
    )
    if any(p in lower for p in _deferral_phrases):
        return False, "Response deferred the task instead of completing it."

    return True, None
