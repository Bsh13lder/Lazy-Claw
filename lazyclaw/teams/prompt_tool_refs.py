"""Prompt-body ↔ allowlist drift detection for declarative specialists.

LazyClaw's #1 recurring specialist incident has ONE shape: a specialist's
markdown prompt tells the worker to call a tool that its ``tools:``
allowlist cannot reach. ``search_tools`` discovery does NOT grant
callability — the allowlist is the contract (``teams/runner.py``), so a
prompt-named-but-unlisted tool is a guaranteed stuck loop:

* 2026-06-10  email_specialist   — every read tool, no send tool
* 2026-07-31  freelance          — ``upwork_send_message`` missing (×2)
* 2026-08-13  browser            — ``use_host_browser`` missing
* 2026-08-14  messaging / email  — ``telegram_*`` / ``email_read_thread``
                                   phantoms that never existed at all

``specialist_loader.validate_specialist_tools`` already checks the OTHER
direction (allowlist vs live registry). This module closes the loop by
reading the prompt body itself. It is deliberately pure and dependency-free
so the repo test suite can run it over every builtin ``.md`` with no DB, no
registry, and no event loop.

Extraction contract (all heuristics, all documented)
----------------------------------------------------
A "tool reference" is a backtick-quoted span in the prompt body that is

1. either a bare identifier (``` `email_send` ```) or the head of a call
   (``` `upwork_get_messages(limit=20)` ``` → ``upwork_get_messages``);
2. shaped like ``[a-z][a-z0-9_]{2,}``; and
3. tool-like — it contains ``_`` OR it is one of :data:`META_TOOL_NAMES`
   (the handful of real single-word tools).

Four exemptions keep the signal clean:

* **Argument names.** Any identifier the body ever shows in an argument
  position inside a backticked span (``` `f(room_id=...)` ```,
  ``` `draft_only=true` ```) is a PARAMETER for the whole body. Tool names
  are never written that way, and prompts routinely mention a parameter
  bare in prose (``` `room_id` ``` comes from the read you just did).
* **Negated references.** Prompts often name a tool precisely to forbid it
  ("There are NO ``telegram_*`` tools", "NEVER use ``google_run_task``").
  A reference is exempt when a negation marker appears EARLIER in the same
  sentence. Sentence-scoped so the next sentence is still checked, and
  order-scoped so a trailing "…with no room" cannot retro-exempt a name
  written before it.
* **Non-tool shapes.** ``enc:v1:…``, ``path/to/file.py:line``, cron lines,
  URLs, ``[JOB:…]``, ``[[Wikilinks]]``, ``site:domain.com q``,
  ``mcp_*``/``mcp_…_x`` placeholders and shell snippets all fail the
  identifier shape and are dropped for free. A trailing underscore marks a
  NAMESPACE STUB (``lazybrain_``), never a callable name.
* **Runtime-injected tools.** ``include_scraper: true`` unions every
  mcp-scraper tool in at dispatch time (``teams/runner.py::_filter_tools``),
  so those names are legitimately callable without appearing in ``tools:``.

False negatives are cheap here (the boot self-check and the runtime block
message still catch a bad call); false POSITIVES are expensive, because a
noisy gate gets disabled. The heuristics lean accordingly.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable
from typing import TYPE_CHECKING

from lazyclaw.skills.tool_namespace import bare_tool_name

if TYPE_CHECKING:  # pragma: no cover - typing only
    from lazyclaw.teams.specialist import SpecialistConfig

# Real tools whose names carry no underscore. Without this set the "looks
# like a tool" rule would miss the meta-tools every specialist leans on.
META_TOOL_NAMES: frozenset[str] = frozenset(
    {"browser", "agent", "delegate", "search_tools", "computer"}
)

# mcp-scraper tools, unioned in at dispatch when ``include_scraper: true``
# (matched there by description, which needs a live registry). Mirrors
# ``mcp-scraper/mcp_scraper/server_tools/*.py`` — pinned by
# ``tests/teams/test_specialist_prompt_sweep.py`` so a stale entry cannot
# silently mask a real violation.
SCRAPER_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "batch_crawl",
        "batch_extract_youtube_transcripts",
        "batch_search_google",
        "crawl_url",
        "crawl_url_with_fallback",
        "deep_crawl_site",
        "enhanced_process_large_content",
        "extract_business_info",
        "extract_entities",
        "extract_structured_data",
        "extract_with_adaptive_selector",
        "extract_youtube_comments",
        "extract_youtube_transcript",
        "get_search_genres",
        "get_supported_file_formats",
        "get_youtube_video_info",
        "intelligent_extract",
        "multi_url_crawl",
        "process_file",
        "search_and_crawl",
        "search_google",
    }
)

# Wildcard allowlist marker — mirrors ``teams/runner.py::WILDCARD_TOOLS``.
# A wildcard specialist reaches every registry tool, so it cannot drift.
_WILDCARD = "*"

# Must END on an alphanumeric: a trailing underscore marks a NAMESPACE STUB
# ("every LazyBrain tool carries the `lazybrain_` prefix"), never a tool.
# Minimum length 3, same as before.
_IDENT = r"[a-z][a-z0-9_]{1,}[a-z0-9]"
# Single-line backtick spans only: specialist prompts carry no fenced code
# blocks, and a newline inside a span means it is prose, not a tool name.
_BACKTICK_SPAN = re.compile(r"`([^`\n]{1,200})`")
_BARE_IDENT = re.compile(rf"\A{_IDENT}\Z")
_CALL_SPAN = re.compile(rf"\A({_IDENT})\s*\((.*)\)\Z", re.DOTALL)
_KWARG = re.compile(rf"\b({_IDENT})\s*=")

# A sentence is a run of text between terminators. Newlines split too, so a
# forbidding bullet never exempts the bullet under it.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:])\s+|\n+")

# Markers that flip a sentence into "this names a tool in order to forbid
# it". ``n't`` covers isn't / doesn't / don't / can't; ``no`` / ``not`` /
# ``nor`` are word-bounded so "note" / "nothing" don't trip them.
_NEGATION = re.compile(r"never|n't|ignore|\b(?:no|not|nor)\b", re.IGNORECASE)

# Sentinel: "this sentence never forbids anything", so no span can precede it.
_NO_NEGATION = float("inf")


def _negation_offsets(body: str) -> list[int]:
    """One entry per sentence: the offset where forbidding starts.

    ``inf`` means the sentence never negates. Sentence windows are computed
    on the INTACT body (never on fragments) so a span that happens to
    contain a terminator — ``` `status: "empty_or_blocked"` ``` — is still
    matched whole. Returned parallel to :func:`_sentence_starts`.
    """
    offsets: list[int] = []
    starts = _sentence_starts(body)
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        negation = _NEGATION.search(body, start, end)
        offsets.append(negation.start() if negation else _NO_NEGATION)
    return offsets


def _sentence_starts(body: str) -> list[int]:
    """Offsets at which each sentence begins."""
    return [0, *(m.end() for m in _SENTENCE_SPLIT.finditer(body))]


def _looks_like_a_tool(name: str) -> bool:
    """Tool-like = namespaced (has ``_``) or a known single-word meta tool."""
    return "_" in name or name in META_TOOL_NAMES


def _span_head(span: str) -> str | None:
    """The tool name a backtick span refers to, if any."""
    span = span.strip()
    if _BARE_IDENT.match(span):
        return span
    call = _CALL_SPAN.match(span)
    return call.group(1) if call else None


def _argument_names(body: str) -> set[str]:
    """Identifiers the body shows in an argument position (``name=``).

    Collected across the WHOLE body — a prompt that writes
    ``upwork_send_message(room_id=...)`` in one line and a bare
    ``` `room_id` ``` in the next means the same parameter both times.
    """
    return {
        m
        for span in _BACKTICK_SPAN.findall(body)
        for m in _KWARG.findall(span)
    }


def extract_prompt_tool_refs(body: str) -> set[str]:
    """Tool names a specialist prompt body tells the worker to call.

    Pure and side-effect free. See the module docstring for the full
    extraction contract and the four exemptions.
    """
    if not body:
        return set()

    arguments = _argument_names(body)
    starts = _sentence_starts(body)
    negations = _negation_offsets(body)

    refs: set[str] = set()
    for match in _BACKTICK_SPAN.finditer(body):
        # bisect_right - 1 = index of the sentence this span opens in.
        sentence = bisect_right(starts, match.start()) - 1
        if match.start() > negations[sentence]:
            continue  # the prompt is forbidding this name, not prescribing it
        head = _span_head(match.group(1))
        if head and head not in arguments and _looks_like_a_tool(head):
            refs.add(head)
    return refs


def validate_prompt_tool_refs(
    spec: SpecialistConfig,
    *,
    known_extra: Iterable[str] = (),
) -> list[str]:
    """Tools ``spec``'s prompt names that its allowlist cannot reach.

    Returns a sorted list (empty = clean). Matching is by BARE name — MCP
    tool ids carry a dynamic ``mcp_<uuid>_`` prefix and the runtime matches
    the suffix (``teams/runner.py``), so the validator must too.

    ``known_extra`` widens the reachable set with names supplied by the
    caller (e.g. the live registry at boot). Wildcard allowlists and, for
    ``include_scraper`` specialists, :data:`SCRAPER_TOOL_NAMES` are handled
    automatically.
    """
    allowed = tuple(spec.allowed_skills or ())
    if _WILDCARD in allowed:
        return []

    reachable = {bare_tool_name(t) for t in allowed}
    reachable |= {bare_tool_name(t) for t in known_extra}
    if getattr(spec, "include_scraper", False):
        reachable |= SCRAPER_TOOL_NAMES

    return sorted(
        ref
        for ref in extract_prompt_tool_refs(spec.system_prompt)
        if bare_tool_name(ref) not in reachable
    )


__all__ = [
    "META_TOOL_NAMES",
    "SCRAPER_TOOL_NAMES",
    "extract_prompt_tool_refs",
    "validate_prompt_tool_refs",
]
