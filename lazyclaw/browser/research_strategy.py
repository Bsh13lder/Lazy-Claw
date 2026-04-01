"""Research Strategy — structured web research methodology for the agent.

When the user asks to research a topic, this module:
1. Detects the research intent from the message
2. Injects a prompt asking the LLM to list its information requirements
3. Parses the requirements from the LLM response
4. Tracks sources checked and gaps remaining
5. Injects progress messages to tell the LLM when to stop

No LLM calls — pure context injection that guides the existing brain LLM.
All state is immutable (frozen dataclasses). Consumers use dataclasses.replace().
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from enum import Enum

logger = logging.getLogger(__name__)


# ── Research task detection ────────────────────────────────────────────

_RESEARCH_KEYWORDS: frozenset[str] = frozenset({
    "research", "find out", "look up", "look into",
    "what is", "what are", "how does", "how do",
    "compare", "analyze", "analyse", "investigate",
    "best", "top", "review", "reviews",
    "difference between", "vs ", " vs.", "pros and cons",
    "summarize", "summarise", "overview of",
    "latest news", "recent news", "current state",
    "explain", "tell me about",
})

_MIN_WORDS_FOR_RESEARCH = 4


def is_research_task(message: str) -> bool:
    """Return True if the message looks like a research task.

    Rules:
    - Must be at least _MIN_WORDS_FOR_RESEARCH words
    - Must contain at least one research keyword
    """
    if not message or len(message.split()) < _MIN_WORDS_FOR_RESEARCH:
        return False
    message_lower = message.lower()
    return any(kw in message_lower for kw in _RESEARCH_KEYWORDS)


# ── Data models ────────────────────────────────────────────────────────


class ResearchStatus(Enum):
    GATHERING = "gathering"     # Still finding information
    SUFFICIENT = "sufficient"   # Enough info found to answer
    EXHAUSTED = "exhausted"     # Too many sources, should synthesize


@dataclass(frozen=True)
class SourceRecord:
    """A single checked source."""

    url: str
    title: str
    requirements_met: tuple[str, ...]  # which requirements this source addressed


@dataclass(frozen=True)
class ResearchStrategy:
    """Immutable research state. Use replace() to advance state."""

    query: str
    info_requirements: tuple[str, ...]  # populated from first LLM response
    sources_checked: tuple[SourceRecord, ...]
    gaps: tuple[str, ...]               # requirements not yet addressed
    status: ResearchStatus = ResearchStatus.GATHERING

    @property
    def sources_count(self) -> int:
        return len(self.sources_checked)

    @property
    def requirements_met_count(self) -> int:
        return len(self.info_requirements) - len(self.gaps)


# ── Prompt construction ────────────────────────────────────────────────

def make_requirements_prompt(query: str) -> str:
    """Return the system message to inject before the first research action.

    Asks the LLM to list information requirements before starting to browse.
    These are parsed from the response content by parse_requirements_from_response().
    """
    return (
        f"You're researching: {query}\n\n"
        f"Before browsing, identify 3-5 specific pieces of information you need "
        f"to find. Output them as a JSON array:\n"
        f'```json\n["requirement 1", "requirement 2", "requirement 3"]\n```\n\n'
        f"Then immediately start your first search or browser action. "
        f"One response: requirements JSON + first action."
    )


# ── Requirements parsing ───────────────────────────────────────────────

# Match ```json [...] ``` or a bare JSON array
_JSON_ARRAY_RE = re.compile(
    r"```(?:json)?\s*(\[.*?\])\s*```|(\[[^\[]*?\"[^\[]*?\"\s*\])",
    re.DOTALL,
)


def parse_requirements_from_response(content: str) -> list[str]:
    """Extract info requirements from an LLM response.

    Returns an empty list if no valid requirements block is found.
    """
    if not content:
        return []

    match = _JSON_ARRAY_RE.search(content)
    if not match:
        # Try to find any JSON array of strings
        start = content.find("[")
        if start == -1:
            return []
        bracket_depth = 0
        end = start
        for i, ch in enumerate(content[start:], start):
            if ch == "[":
                bracket_depth += 1
            elif ch == "]":
                bracket_depth -= 1
                if bracket_depth == 0:
                    end = i + 1
                    break
        raw = content[start:end]
    else:
        raw = match.group(1) or match.group(2)

    if not raw:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.debug("Research requirements JSON parse failed: %s", exc)
        return []

    if not isinstance(data, list):
        return []

    requirements = [str(r).strip() for r in data if r and str(r).strip()]
    if requirements:
        logger.info("Research strategy: parsed %d requirements", len(requirements))
    return requirements


# ── State updates ──────────────────────────────────────────────────────

# Thresholds for when to declare sufficient / exhausted
_SUFFICIENT_SOURCES = 3
_EXHAUSTED_SOURCES = 5


def note_source(
    state: ResearchStrategy,
    url: str,
    title: str,
    tool_result: str,
) -> ResearchStrategy:
    """Record a checked source and update gaps.

    Heuristic: if a requirement's keywords appear in the tool result,
    mark that requirement as tentatively addressed. No LLM call.
    """
    if not url and not title and not tool_result:
        return state

    # Check which gaps this source might address
    result_lower = tool_result.lower()
    met_now: list[str] = []
    still_gaps: list[str] = []

    for gap in state.gaps:
        gap_keywords = [w for w in gap.lower().split() if len(w) > 3]
        # A gap is tentatively met if most of its keywords appear in the result
        matches = sum(1 for kw in gap_keywords if kw in result_lower)
        if gap_keywords and matches >= max(1, len(gap_keywords) // 2):
            met_now.append(gap)
        else:
            still_gaps.append(gap)

    source = SourceRecord(
        url=url[:200],
        title=title[:100],
        requirements_met=tuple(met_now),
    )
    new_sources = state.sources_checked + (source,)
    new_gaps = tuple(still_gaps)
    new_count = len(new_sources)

    # Determine new status
    if new_count >= _EXHAUSTED_SOURCES:
        new_status = ResearchStatus.EXHAUSTED
    elif not new_gaps and state.info_requirements:
        new_status = ResearchStatus.SUFFICIENT
    elif new_count >= _SUFFICIENT_SOURCES and len(new_gaps) <= 1:
        new_status = ResearchStatus.SUFFICIENT
    else:
        new_status = ResearchStatus.GATHERING

    return replace(
        state,
        sources_checked=new_sources,
        gaps=new_gaps,
        status=new_status,
    )


# ── Progress messages ──────────────────────────────────────────────────

def make_progress_message(state: ResearchStrategy) -> str | None:
    """Return a system message summarizing research progress.

    Returns None if no requirements have been set yet (too early).
    """
    if not state.info_requirements:
        # No requirements parsed yet — just count sources
        if state.sources_count >= _EXHAUSTED_SOURCES:
            return (
                f"Research progress: Checked {state.sources_count} sources. "
                f"SYNTHESIZE what you have — do not search further."
            )
        return None

    total = len(state.info_requirements)
    met = state.requirements_met_count
    n_sources = state.sources_count

    if state.status == ResearchStatus.SUFFICIENT:
        return (
            f"Research progress: Found evidence for {met}/{total} requirements "
            f"across {n_sources} source(s). "
            f"You have ENOUGH information — synthesize your findings now."
        )

    if state.status == ResearchStatus.EXHAUSTED:
        gaps_preview = ", ".join(f'"{g}"' for g in state.gaps[:3])
        return (
            f"Research progress: Checked {n_sources} sources. "
            f"Found {met}/{total} requirements. "
            f"You've checked enough sources — SYNTHESIZE what you have. "
            f"Note gaps if relevant: {gaps_preview or 'none'}"
        )

    # Still gathering
    gaps_preview = ", ".join(f'"{g}"' for g in state.gaps[:3])
    if met > 0:
        return (
            f"Research progress: Found {met}/{total} requirements, "
            f"{n_sources} source(s) checked. "
            f"Still looking for: {gaps_preview}. Continue searching."
        )
    return None
