"""AI-native LazyBrain skills: autolink, auto-tag, semantic search,
Ask-your-notes, topic rollup, daily recap.

Each skill is a thin wrapper around the pure-Python module under
``lazyclaw.lazybrain`` so the agent has a natural-language tool surface
for the same logic the Web UI consumes via REST.
"""
from __future__ import annotations

from lazyclaw.lazybrain import (
    ask,
    autolink,
    embeddings,
    metadata_suggest,
    recap,
    topic_rollup,
)
from lazyclaw.skills.base import BaseSkill


class SuggestLinksSkill(BaseSkill):
    """Ask the worker LLM to propose ``[[wikilinks]]`` for a draft."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_suggest_links"

    @property
    def display_name(self) -> str:
        return "Suggest wikilinks"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Given a draft or note content, suggest existing page titles "
            "that appear verbatim in it and should be wrapped as "
            "[[wikilinks]]. Returns up to 8 suggestions. Useful before "
            "saving so the new note automatically joins the graph."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Draft body to scan for wikilink candidates.",
                },
                "use_llm": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Set false to force the offline substring matcher "
                        "(useful when Ollama is down)."
                    ),
                },
            },
            "required": ["text"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        data = await autolink.suggest_links(
            self._config,
            user_id,
            params.get("text") or "",
            use_llm=bool(params.get("use_llm", True)),
        )
        if not data["suggestions"]:
            return "No autolink candidates found."
        lines = [f"Autolink candidates (source: {data['source']}):"]
        for s in data["suggestions"]:
            lines.append(f"  • “{s['text']}” → [[{s['page']}]]")
        return "\n".join(lines)


class SuggestMetadataSkill(BaseSkill):
    """Propose a title + tags for a raw note without touching storage."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_suggest_metadata"

    @property
    def display_name(self) -> str:
        return "Suggest title + tags"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Given raw note content, ask the worker model to propose a "
            "short title and 1–5 tags — reusing tags that already exist "
            "in the user's vault when possible. Does not save anything. "
            "Good for confirm-before-save toasts."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
            },
            "required": ["content"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        # Seed the worker with the user's existing tags for reuse.
        from lazyclaw.lazybrain import store

        existing = await store.list_tags(self._config, user_id)
        existing_tags = [row["tag"] for row in existing[:40]]

        data = await metadata_suggest.suggest_metadata(
            self._config,
            user_id,
            params.get("content") or "",
            existing_tags=existing_tags,
        )
        if not (data["title"] or data["tags"]):
            return "No metadata suggestion (content too thin or LLM unavailable)."
        title = data["title"] or "(no title suggested)"
        tags = " ".join(f"#{t}" for t in data["tags"]) or "(no tags suggested)"
        return f"Title: {title}\nTags: {tags}"


class SemanticSearchSkill(BaseSkill):
    """Vector-based retrieval over the user's notes."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_semantic_search"

    @property
    def display_name(self) -> str:
        return "Semantic search notes"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Find notes by meaning, not just keywords. Uses a local "
            "embedding model (nomic-embed-text via Ollama). Falls back to "
            "substring search if the embedding backend isn't available. "
            "Good for 'what did I write about X?' style questions when "
            "the exact wording varies."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                    "default": 10,
                },
            },
            "required": ["query"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        query = (params.get("query") or "").strip()
        k = int(params.get("k") or 10)
        data = await embeddings.semantic_search(
            self._config, user_id, query, k=k,
        )
        if not data["results"]:
            return f"No notes matched “{query}” (source: {data['source']})."
        lines = [f"Top {len(data['results'])} matches (source: {data['source']}):"]
        for n in data["results"]:
            score = n.get("_score")
            label = f" · {score:.3f}" if isinstance(score, (int, float)) else ""
            snippet = (n.get("content") or "").strip().replace("\n", " ")[:140]
            lines.append(
                f"  • [[{n.get('title') or '(untitled)'}]]{label} — {snippet}"
            )
        return "\n".join(lines)


class AskNotesSkill(BaseSkill):
    """RAG over the user's second brain with ``[[citations]]``."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_ask"

    @property
    def display_name(self) -> str:
        return "Ask your notes"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Answer a question using only the user's own notes. Retrieves "
            "the top-k most relevant notes semantically, then asks the "
            "brain to synthesise an answer with [[Note Title]] citations "
            "so the user can click through to the source."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 8,
                },
            },
            "required": ["question"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        data = await ask.ask_notes(
            self._config,
            user_id,
            params.get("question") or "",
            k=int(params.get("k") or 8),
        )
        sources = data.get("sources") or []
        sources_line = ""
        if sources:
            sources_line = "\n\n**Sources**: " + ", ".join(
                f"[[{t}]]" for t in sources[:8]
            )
        return (data.get("answer") or "") + sources_line


class TopicRollupSkill(BaseSkill):
    """Brain-LLM rollup for one topic with structured markdown + citations."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_topic_rollup"

    @property
    def display_name(self) -> str:
        return "Topic rollup"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Create a structured rollup for a topic: summary + decisions + "
            "open questions + sources, each citing the underlying notes "
            "with [[wikilinks]]. Uses every backlink to the topic plus "
            "substring hits as the context window."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Page title, tag, or free-text topic.",
                },
            },
            "required": ["topic"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        data = await topic_rollup.topic_rollup(
            self._config,
            user_id,
            params.get("topic") or "",
        )
        return data.get("rollup") or "Rollup unavailable."


class MorningBriefingSkill(BaseSkill):
    """Append today's morning briefing callout to the journal."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_morning_briefing"

    @property
    def display_name(self) -> str:
        return "Morning briefing"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def description(self) -> str:
        return (
            "Build today's morning briefing — worker LLM summarises "
            "yesterday's journal + open pinned notes, then appends a "
            "> [!tip] Morning Briefing callout to today's journal page. "
            "Idempotent: skips if the briefing already landed today."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "Force rebuild even if today already has one.",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        data = await recap.build_morning_briefing(
            self._config, user_id, force=bool((params or {}).get("force")),
        )
        status = data.get("status")
        if status == "appended":
            return f"📓 Appended morning briefing to {data.get('date')}."
        if status == "skipped":
            return f"Already briefed {data.get('date')} today. Use force=true to rebuild."
        return f"Briefing error: {data.get('reason')}"


class ReindexEmbeddingsSkill(BaseSkill):
    """Recompute embeddings for every note (admin-ish)."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_reindex_embeddings"

    @property
    def display_name(self) -> str:
        return "Rebuild semantic index"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def description(self) -> str:
        return (
            "Recompute the local embedding vector for every note in the "
            "vault. Run this once after installing nomic-embed-text, or "
            "if the embedding model was upgraded. Safe to re-run; it "
            "upserts per-note rows. Requires Ollama to be running."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        data = await embeddings.reindex_user(self._config, user_id)
        return (
            f"Indexed {data['indexed']} / {data['total']} notes "
            f"(skipped {data['skipped']}, model {data['model']})."
        )
