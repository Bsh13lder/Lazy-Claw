"""Multi-layer memory system: Session → User → Channel → Project → Global.

Layers and persistence:
  GLOBAL   — system-wide markdown, always loaded. Plaintext (admin-managed).
             Path: {data}/memory/GLOBAL.md
  PROJECT  — per-project markdown, encrypted.
             Path: {data}/memory/projects/{project_id}/MEMORY.md
  CHANNEL  — per-channel markdown, encrypted.
             Path: {data}/memory/channels/{channel_id}/MEMORY.md
  USER     — per-user markdown, encrypted.
             Path: {data}/memory/users/{user_id}/MEMORY.md
  SESSION  — ephemeral in-memory dict. No file persistence.

Loading order: Global → Project → Channel → User → Session
Priority (conflicts): User > Channel > Project > Global

First 200 lines of each file are loaded at session start. Pass max_lines=0
to read the whole file (used by search and append operations).

Encryption: USER/CHANNEL/PROJECT layers use AES-256-GCM via derive_server_key.
            GLOBAL is plaintext (admin-managed server config).

Backward compatibility: The existing DB-backed personal memory (personal.py) is
unchanged. The USER layer here is a complementary file-based context — free-form
markdown preferences/notes alongside the structured DB entries.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lazyclaw.config import Config

logger = logging.getLogger(__name__)

MEMORY_LOAD_LINES = 200  # First N lines loaded at session start


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class MemoryLayer(str, Enum):
    SESSION = "session"
    USER = "user"
    CHANNEL = "channel"
    PROJECT = "project"
    GLOBAL = "global"


@dataclass(frozen=True)
class MemoryResult:
    layer: MemoryLayer
    scope_id: str
    content: str
    line_number: int


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _memory_path(config: Config, layer: MemoryLayer, scope_id: str) -> Path:
    """Return the filesystem path for a memory layer file."""
    base = config.database_dir / "memory"
    if layer == MemoryLayer.GLOBAL:
        return base / "GLOBAL.md"
    if layer == MemoryLayer.USER:
        return base / "users" / scope_id / "MEMORY.md"
    if layer == MemoryLayer.CHANNEL:
        return base / "channels" / scope_id / "MEMORY.md"
    if layer == MemoryLayer.PROJECT:
        return base / "projects" / scope_id / "MEMORY.md"
    raise ValueError(f"SESSION layer has no file path (ephemeral)")


def _scope_key_id(layer: MemoryLayer, scope_id: str) -> str:
    """Return the identifier used as the second arg for derive_server_key.

    USER layer uses scope_id (user_id) directly for compatibility with
    the existing personal memory key derivation. Other layers prefix with
    the layer name to produce a unique, non-colliding key.
    """
    if layer == MemoryLayer.USER:
        return scope_id
    return f"{layer.value}:{scope_id}"


# ---------------------------------------------------------------------------
# Low-level file I/O
# ---------------------------------------------------------------------------

def _truncate_lines(content: str, max_lines: int) -> str:
    """Return first max_lines lines of content. Returns all if max_lines <= 0."""
    if max_lines <= 0:
        return content
    lines = content.splitlines(keepends=True)
    return "".join(lines[:max_lines])


def _read_plaintext(path: Path, max_lines: int) -> str | None:
    """Read a plaintext file. Returns None if the file does not exist."""
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")
    return _truncate_lines(content, max_lines)


def _read_encrypted(path: Path, key: bytes, max_lines: int) -> str | None:
    """Read and decrypt a memory file. Returns None if the file does not exist."""
    from lazyclaw.crypto.encryption import decrypt, is_encrypted

    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    if is_encrypted(raw):
        try:
            content = decrypt(raw, key)
        except Exception:
            logger.warning("Failed to decrypt layer file %s", path)
            return None
    else:
        # Accept unencrypted files (manually written or migrated content)
        content = raw
    return _truncate_lines(content, max_lines)


def _write_plaintext(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_encrypted(path: Path, key: bytes, content: str) -> None:
    from lazyclaw.crypto.encryption import encrypt

    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = encrypt(content, key)
    path.write_text(encrypted, encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API — read / write / append / search
# ---------------------------------------------------------------------------

def read_memory(
    config: Config,
    layer: MemoryLayer,
    scope_id: str,
    max_lines: int = MEMORY_LOAD_LINES,
) -> str | None:
    """Read memory for a layer and scope. Returns None if no memory exists yet.

    Args:
        config: App config (provides server_secret and database_dir).
        layer: Which layer to read (GLOBAL, PROJECT, CHANNEL, USER).
        scope_id: Scope identifier (user_id, channel_id, project_id, or "global").
        max_lines: Limit output to first N lines. 0 = no limit (read all).

    Raises:
        ValueError: If called with MemoryLayer.SESSION (ephemeral — no file).
    """
    if layer == MemoryLayer.SESSION:
        raise ValueError("SESSION layer is ephemeral — use session_state dict directly")

    path = _memory_path(config, layer, scope_id)

    if layer == MemoryLayer.GLOBAL:
        return _read_plaintext(path, max_lines)

    from lazyclaw.crypto.encryption import derive_server_key

    key = derive_server_key(config.server_secret, _scope_key_id(layer, scope_id))
    return _read_encrypted(path, key, max_lines)


def write_memory(
    config: Config,
    layer: MemoryLayer,
    scope_id: str,
    content: str,
) -> None:
    """Overwrite the memory file for a layer and scope.

    Creates parent directories if needed. Encrypts all layers except GLOBAL.

    Raises:
        ValueError: If called with MemoryLayer.SESSION (ephemeral).
    """
    if layer == MemoryLayer.SESSION:
        raise ValueError("SESSION layer is ephemeral — use session_state dict directly")

    path = _memory_path(config, layer, scope_id)

    if layer == MemoryLayer.GLOBAL:
        _write_plaintext(path, content)
        logger.info("Wrote GLOBAL memory (%d chars)", len(content))
        return

    from lazyclaw.crypto.encryption import derive_server_key

    key = derive_server_key(config.server_secret, _scope_key_id(layer, scope_id))
    _write_encrypted(path, key, content)
    logger.info("Wrote %s memory for scope=%s (%d chars)", layer.value, scope_id, len(content))


def append_memory(
    config: Config,
    layer: MemoryLayer,
    scope_id: str,
    new_content: str,
) -> None:
    """Append new_content to a memory file, creating it if it doesn't exist.

    Reads current content, appends with a blank-line separator, writes back.
    Immutable pattern — never mutates the read value, always produces a new combined string.
    """
    existing = read_memory(config, layer, scope_id, max_lines=0) or ""
    if existing and not existing.endswith("\n"):
        combined = existing + "\n\n" + new_content
    elif existing:
        combined = existing + "\n" + new_content
    else:
        combined = new_content
    write_memory(config, layer, scope_id, combined)


def search_memory(
    config: Config,
    query: str,
    user_id: str,
    channel_id: str | None = None,
    project_id: str | None = None,
    layers: list[MemoryLayer] | None = None,
    max_results: int = 10,
) -> list[MemoryResult]:
    """Search across memory layers by case-insensitive keyword match.

    Loads each layer file in priority order (Global → Project → Channel → User),
    scans line by line, and returns matching lines as MemoryResult objects.

    Args:
        config: App config.
        query: Search term (substring match, case-insensitive).
        user_id: Always searched (USER layer).
        channel_id: Search CHANNEL layer when provided.
        project_id: Search PROJECT layer when provided.
        layers: Override which layers to search. Defaults to all persistent layers.
        max_results: Cap on total results returned.

    Returns:
        List of MemoryResult sorted by layer priority (Global first, User last).
    """
    if layers is None:
        layers = [
            MemoryLayer.GLOBAL,
            MemoryLayer.PROJECT,
            MemoryLayer.CHANNEL,
            MemoryLayer.USER,
        ]

    scope_map: dict[MemoryLayer, str] = {
        MemoryLayer.GLOBAL: "global",
        MemoryLayer.USER: user_id,
        MemoryLayer.CHANNEL: channel_id or "",
        MemoryLayer.PROJECT: project_id or "",
    }

    query_lower = query.lower()
    results: list[MemoryResult] = []

    for layer in layers:
        if layer == MemoryLayer.SESSION:
            continue
        scope_id = scope_map.get(layer, "")
        if not scope_id:
            continue

        content = read_memory(config, layer, scope_id, max_lines=0)
        if not content:
            continue

        for i, line in enumerate(content.splitlines(), start=1):
            if query_lower in line.lower():
                results.append(MemoryResult(
                    layer=layer,
                    scope_id=scope_id,
                    content=line,
                    line_number=i,
                ))
            if len(results) >= max_results:
                return results

    return results


# ---------------------------------------------------------------------------
# Session context loader — assembles all layers for system prompt injection
# ---------------------------------------------------------------------------

def load_session_context(
    config: Config,
    user_id: str,
    channel_id: str | None = None,
    project_id: str | None = None,
) -> str:
    """Load all persistent memory layers and return as combined markdown.

    Loading order: Global → Project → Channel → User (each up to 200 lines).
    Only includes layers that have content. Returns "" if nothing is loaded.

    Designed to be called synchronously from context_builder.build_context().
    File I/O for small markdown files is fast enough to not need async.
    """
    sections: list[str] = []

    # 1. Global (always — system-wide rules, shared knowledge base)
    global_content = read_memory(config, MemoryLayer.GLOBAL, "global")
    if global_content and global_content.strip():
        sections.append(f"## Global Context\n{global_content.strip()}")

    # 2. Project (when project context is active)
    if project_id:
        project_content = read_memory(config, MemoryLayer.PROJECT, project_id)
        if project_content and project_content.strip():
            sections.append(
                f"## Project Context ({project_id})\n{project_content.strip()}"
            )

    # 3. Channel (when agent is in a channel/group)
    if channel_id:
        channel_content = read_memory(config, MemoryLayer.CHANNEL, channel_id)
        if channel_content and channel_content.strip():
            sections.append(
                f"## Channel Context ({channel_id})\n{channel_content.strip()}"
            )

    # 4. User (always — personal preferences, communication style, etc.)
    user_content = read_memory(config, MemoryLayer.USER, user_id)
    if user_content and user_content.strip():
        sections.append(f"## Personal Context\n{user_content.strip()}")

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Auto-extraction — persist learnings from a session into the right layers
# ---------------------------------------------------------------------------

async def auto_extract(
    config: Config,
    user_id: str,
    session_messages: list[dict],
    channel_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, str]:
    """Extract learnings from a session and persist them to the appropriate layers.

    Runs a fast LLM pass (worker model) over recent session messages to identify:
    - User preferences, facts, communication style → USER layer
    - Channel group context, shared decisions → CHANNEL layer (if channel_id)
    - Project goals, tech stack, conventions → PROJECT layer (if project_id)

    Returns a dict mapping layer name → extracted markdown content (empty dict
    if nothing was extracted). Never raises — errors are logged and {} returned.

    Usage: call at session end (fire-and-forget) to accumulate learnings over time.
    """
    if not session_messages:
        return {}

    # Build a compact conversation excerpt for extraction (last 50 messages, capped at 300 chars each)
    conversation_lines: list[str] = []
    for msg in session_messages[-50:]:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            snippet = str(content)[:300].replace("\n", " ")
            conversation_lines.append(f"[{role}]: {snippet}")

    conversation_text = "\n".join(conversation_lines)
    if not conversation_text.strip():
        return {}

    try:
        from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
        from lazyclaw.llm.providers.base import LLMMessage
        from lazyclaw.llm.router import LLMRouter

        channel_key = '- "channel": group context, shared decisions, channel rules\n' if channel_id else ""
        project_key = '- "project": project goals, tech stack, conventions, team info\n' if project_id else ""

        extract_prompt = (
            "Analyze this conversation and extract memorable learnings for future sessions.\n\n"
            "Output ONLY a JSON object with these optional keys (omit if nothing to extract):\n"
            '- "user": preferences, facts, communication style, timezone, language\n'
            + channel_key
            + project_key
            + "\nFormat each value as a brief markdown bullet list (2–5 bullets max). "
            "Omit a key entirely if there is nothing worth saving for it.\n\n"
            f"Conversation:\n{conversation_text}"
        )

        messages = [
            LLMMessage(
                role="system",
                content="Extract structured learnings from conversations. Output only valid JSON.",
            ),
            LLMMessage(role="user", content=extract_prompt),
        ]

        try:
            eco = EcoRouter(config, LLMRouter(config))
            response = await eco.chat(messages, user_id=user_id, role=ROLE_WORKER)
        except Exception:
            logger.warning("EcoRouter unavailable for auto_extract, falling back to direct LLM", exc_info=True)
            router = LLMRouter(config)
            response = await router.chat(
                messages, model=config.worker_model, user_id=user_id
            )

        raw = response.content or ""

        # Extract the JSON object from the response
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            return {}

        extracted: dict = json.loads(json_match.group())
        results: dict[str, str] = {}

        if "user" in extracted:
            content = str(extracted["user"]).strip()
            if content:
                append_memory(config, MemoryLayer.USER, user_id, content)
                results["user"] = content
                logger.info(
                    "auto_extract: saved USER preferences for %s (%d chars)",
                    user_id, len(content),
                )

        if channel_id and "channel" in extracted:
            content = str(extracted["channel"]).strip()
            if content:
                append_memory(config, MemoryLayer.CHANNEL, channel_id, content)
                results["channel"] = content
                logger.info("auto_extract: saved CHANNEL context for %s", channel_id)

        if project_id and "project" in extracted:
            content = str(extracted["project"]).strip()
            if content:
                append_memory(config, MemoryLayer.PROJECT, project_id, content)
                results["project"] = content
                logger.info("auto_extract: saved PROJECT context for %s", project_id)

        # Mirror each extracted layer into LazyBrain as a note. In Phase 18.4
        # the markdown layer above becomes opt-in; until then we keep both so
        # rollback is painless.
        try:
            from lazyclaw.lazybrain import events as lb_events
            from lazyclaw.lazybrain import store as lb_store

            for layer_key, layer_content in results.items():
                layer_tag = f"layer/{layer_key}"
                if layer_key == "channel" and channel_id:
                    layer_tag = f"layer/channel/{channel_id}"
                elif layer_key == "project" and project_id:
                    layer_tag = f"layer/project/{project_id}"
                note = await lb_store.save_note(
                    config,
                    user_id,
                    content=layer_content,
                    title=f"Session learnings — {layer_key}",
                    tags=["auto", "session-end", "owner/agent", layer_tag],
                    importance=6,
                )
                lb_events.publish_note_saved(
                    user_id, note["id"], note["title"], note["tags"], source="session-end",
                )
        except Exception:
            logger.debug("lazybrain session-end mirror failed", exc_info=True)

        return results

    except Exception as exc:
        logger.warning("auto_extract failed: %s", exc)
        return {}
