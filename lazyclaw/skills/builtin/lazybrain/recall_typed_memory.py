"""Recall typed memory — NL skill wrapping
:func:`lazyclaw.lazybrain.typed_recall.recall_typed_memory`.

This is the brain's on-demand fetch path for canonical typed memories.
Once Phase 2 lands, the cached system prompt only auto-injects
``user`` / ``feedback`` / ``project`` / ``reference`` notes. Everything
else (``fact`` / ``session-log`` / ``other``) becomes invisible until
the brain explicitly asks for it via this skill.

Description text is written for an LLM consumer — explicit triggers,
canonical examples, and a "never fabricate" prompt-tone reinforcement.
"""
from __future__ import annotations

from lazyclaw.lazybrain import typed_recall
from lazyclaw.lazybrain.paraphrase_sanitizer import (
    is_paraphrase_class,
    sanitize_recall_content,
)
from lazyclaw.skills.base import BaseSkill


def _snippet(text, width=200):
    body = (text or "").strip()
    if len(body) <= width:
        return body
    return body[: width - 1] + "…"


class RecallTypedMemorySkill(BaseSkill):
    """Fetch typed memories by knowledge-scope label.

    Returns ONLY notes with the requested ``memory_type`` — never the
    paraphrased session-log dumps that mimic facts. This is the
    canonical path the brain takes when it needs authoritative state
    (rather than guessing from cached context).
    """

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_recall_typed_memory"

    @property
    def display_name(self) -> str:
        return "Recall typed memory"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Fetch typed memories from your LazyBrain by knowledge-scope "
            "label. Returns ONLY notes with the requested memory_type — "
            "never paraphrased summaries from session logs.\n\n"
            "Use when you need canonical knowledge:\n"
            "- memory_type='project' + topic='James Blue' "
            "→ project state about James (scope, deadline, deliverable)\n"
            "- memory_type='reference' + topic='claude api' "
            "→ URL bookmarks and external pointers\n"
            "- memory_type='feedback' + topic='git' "
            "→ user's git workflow rules and corrections\n"
            "- memory_type='fact' + query='James Blue Upwork rate' "
            "→ known facts that were learned but aren't auto-injected\n"
            "- memory_type='session-log' + topic='upwork 2026-05-19' "
            "→ past recaps (use SPARINGLY — these are paraphrased, not "
            "authoritative; quote channels directly instead when possible)\n\n"
            "When the `topic` argument is provided, this skill also walks "
            "the wikilink graph one hop: any note whose [[wikilink]] "
            "points at the topic title is included alongside the topic's "
            "own page. That's how the brain reaches every project / "
            "contact / decision that references the topic.\n\n"
            "Call this BEFORE assuming you know something from training. "
            "Always preferred over guessing. At least one of memory_type "
            "/ topic / query must be set."
        )

    @property
    def parameters_schema(self):
        return _PARAMS_SCHEMA

    async def execute(self, user_id, params):
        memory_type = params.get("memory_type")
        topic = params.get("topic")
        query = params.get("query")
        limit = int(params.get("limit") or 10)
        return await _render(self._config, user_id, memory_type, topic, query, limit)


async def _render(config, user_id, memory_type, topic, query, limit):
    if not memory_type and not topic and not query:
        return (
            "(no input) — pass at least one of memory_type / topic / query."
        )
    try:
        hits = await typed_recall.recall_typed_memory(
            config,
            user_id,
            memory_type=memory_type,
            topic=topic,
            query=query,
            limit=limit,
        )
    except ValueError as exc:
        return f"(invalid input) {exc}"
    if not hits:
        label_parts = []
        if memory_type:
            label_parts.append(f"memory_type={memory_type}")
        if topic:
            label_parts.append(f"topic={topic!r}")
        if query:
            label_parts.append(f"query={query!r}")
        return "(no matches) " + ", ".join(label_parts)
    return _format_hits(hits)


def _format_hits(hits):
    lines = [f"Found {len(hits)} typed-memory hit(s):"]
    for n in hits:
        mt = n.get("memory_type") or "(unknown)"
        score = n.get("_score")
        score_label = ""
        if isinstance(score, (int, float)):
            score_label = f" · {score:.3f}"
        title = n.get("title") or "(untitled)"
        tags = " ".join(f"#{t}" for t in (n.get("tags") or []))
        tag_label = f" {tags}" if tags else ""
        lines.append(
            f"  - [{mt}] {title} [{n['id'][:8]}]{score_label}{tag_label}"
        )
        # Sanitize paraphrase-class content (session-log / fact / other /
        # NULL) BEFORE snippet truncation, so embedded `**Sender (HH:MM):**`
        # patterns are mangled before the brain reads them. See
        # lazybrain/paraphrase_sanitizer.py for the leak path this closes.
        raw_content = sanitize_recall_content(
            n.get("content"), n.get("memory_type"), title=title,
        )
        snippet = _snippet(raw_content, 160).replace("\n", " ")
        if snippet:
            lines.append(f"    {snippet}")
        # When the hit IS a paraphrase, emit a one-line warning right
        # after the bullet so the brain sees the "this is paraphrased"
        # signal even if the wrapped snippet got truncated short.
        if is_paraphrase_class(n.get("memory_type")):
            lines.append(
                "    ⚠ paraphrase-class memory — DO NOT quote any "
                "embedded (HH:MM) markers as if they were live; "
                "re-fetch the channel to quote verbatim."
            )
    return "\n".join(lines)


def _build_params_schema():
    props = {}
    props["memory_type"] = dict(
        description=(
            "Knowledge-scope filter. Canonical values: user, "
            "feedback, project, reference, fact, session-log, "
            "other. Pass a single string or a JSON array of "
            "strings. Unknown values collapse to 'fact'."
        ),
    )
    props["topic"] = dict(
        type="string",
        description=(
            "Fuzzy-match against note titles. When provided, the "
            "skill also walks the wikilink graph one hop and "
            "includes notes whose [[wikilink]] points at this title."
        ),
    )
    props["query"] = dict(
        type="string",
        description="Semantic search query — ranks results by meaning.",
    )
    props["limit"] = dict(
        type="integer", minimum=1, maximum=50, default=10,
    )
    out = dict()
    out["type"] = "object"
    out["properties"] = props
    return out


_PARAMS_SCHEMA = _build_params_schema()
