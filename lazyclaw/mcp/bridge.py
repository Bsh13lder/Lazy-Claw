from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import re
import time
from typing import Any

from lazyclaw.mcp.client import MCPClient
from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_MCP_PREFIX = "mcp_"

# ── Scraper pool plumbing ────────────────────────────────────────────────
# `mcp-scraper-1`, `mcp-scraper-2`, … are N parallel subprocesses that
# expose identical schemas. They're abstracted as a single pooled skill
# set in the registry under canonical names `mcp_scraper_<tool>`. The
# pool lives here (not in manager.py) because it's a registry-skill
# concern, not a connection concern. See:
#   ~/.claude/plans/i-prefer-hard-and-stateful-alpaca.md
_SCRAPER_SHARD_RE = re.compile(r"^mcp-scraper-\d+$")
_SCRAPER_CANONICAL = "mcp-scraper"  # name used in skill_lesson + display
_SCRAPER_TOOL_PREFIX = "mcp_scraper_"  # canonical registry name prefix
_pool_counter = itertools.count()  # process-wide round-robin index


def is_scraper_shard(name: str) -> bool:
    """True if `name` looks like `mcp-scraper-1`, `mcp-scraper-2`, …"""
    return bool(_SCRAPER_SHARD_RE.match(name or ""))


# ── Per-user search backend injection ───────────────────────────────────
# When the user changes their search backend (Web UI dropdown / Telegram
# `/search serper` / NL `set_search_provider serper`), it lands in
# users.settings.general.search_provider. Bridge auto-injects it into
# scraper search calls so the AI doesn't need to know.
# Maps user-facing names to the scraper's internal mode names.
_USER_PROVIDER_TO_SCRAPER_BACKEND = {
    "auto": "auto",
    "serper": "serper",
    "serpapi": "serpapi",
    # 'duckduckgo' user pref → scraper has no DDG backend; fall back to auto
    # (Serper-first chain). LazyClaw's own web_search skill respects DDG.
    "duckduckgo": "auto",
}


async def _inject_user_search_backend(
    config: Any,
    user_id: str | None,
    tool_name: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Inject the user's ``general.search_provider`` into scraper search
    tool params. Per-call ``backend`` arg from the LLM always wins.

    For ``batch_search_google`` (flat signature) the field is a top-level
    ``backend``. For ``search_google`` (request-envelope signature) the
    field lands inside ``request.backend``. Returns the (possibly modified)
    params dict.
    """
    if config is None or not user_id:
        return params
    # If the LLM already passed an explicit backend, leave it alone.
    if tool_name == "batch_search_google" and params.get("backend"):
        return params
    if tool_name == "search_google":
        req = params.get("request")
        if isinstance(req, dict) and req.get("backend"):
            return params
    try:
        from lazyclaw.settings.general import get_general_settings
        general = await get_general_settings(config, user_id)
    except Exception:
        logger.debug("search backend injection: settings load failed", exc_info=True)
        return params
    pref = str(general.get("search_provider") or "auto").lower().strip()
    backend = _USER_PROVIDER_TO_SCRAPER_BACKEND.get(pref, "auto")
    if tool_name == "batch_search_google":
        params["backend"] = backend
    else:  # search_google — request envelope
        req = params.get("request")
        if isinstance(req, dict):
            req["backend"] = backend
            params["request"] = req
        else:
            # Caller passed flat? Stash it in a request dict for forward-compat.
            params.setdefault("request", {})["backend"] = backend
    return params

# ── Per-server call concurrency cap ──────────────────────────────────────
# Each MCP server is one stdio subprocess. crawl4ai (mcp-scraper) holds a
# stateful Playwright session inside that subprocess; firing 4+ concurrent
# `extract_entities` / `crawl_url` calls from parallel EXPLORE specialists
# OOM-crashed the Chromium child and dropped the whole stdio connection
# (5 restarts in 30 min on 2GB; see plans/rosy-meandering-parnas.md).
# A per-server-id semaphore caps in-flight calls so Playwright has room to
# breathe. Cap is configurable via env so we can tighten on a stuck batch
# without rebuilding.
_server_call_semaphores: dict[str, asyncio.Semaphore] = {}


def _mcp_call_concurrency() -> int:
    raw = os.environ.get("LAZYCLAW_MCP_CALL_CONCURRENCY")
    if raw:
        try:
            n = int(raw)
            if n >= 1:
                return n
        except ValueError:
            pass
    return 4


def _get_call_semaphore(server_id: str) -> asyncio.Semaphore:
    """Lazy per-server-id call semaphore. Mirrors the connect-lock pattern
    in lazyclaw/mcp/manager.py:_get_connect_lock — but for in-flight calls
    rather than connect handshakes."""
    sem = _server_call_semaphores.get(server_id)
    if sem is None:
        sem = asyncio.Semaphore(_mcp_call_concurrency())
        _server_call_semaphores[server_id] = sem
    return sem

# Tools whose JSON Schema lists params as optional but the server-side
# validator requires conditional combinations. Without these hints
# inline in the description, the brain has to learn the requirement
# the hard way — by triggering UserInputError and retrying. The agent
# loop already has a recovery path that re-injects the schema on
# UserInputError, but surfacing the hint upfront avoids the round trip
# entirely. Keep this map narrow — only known schema-vs-validator
# mismatches; a generic hint dump would bloat every tool description.
_DESCRIPTION_HINTS: dict[str, str] = {
    "modify_sheet_values": (
        "REQUIRED for write ops: pass `values` as a 2D array of rows "
        "(e.g. `[[\"foo\", \"bar\"]]`). For clear ops, pass "
        "`clear_values=true` instead. Calls without either fail with "
        "UserInputError."
    ),
    "append_table_rows": (
        "REQUIRED: `values` as a 2D array of rows. Row width must "
        "match the table's column count or the call fails."
    ),
    "manage_event": (
        "REQUIRED for create/update: `summary`, `start`, `end`. "
        "For delete: `event_id` only. Mixing modes (e.g. event_id "
        "with summary) fails."
    ),
}


def _decorate_description(base_name: str, description: str) -> str:
    """Append a usage hint to known problem-tools' descriptions."""
    hint = _DESCRIPTION_HINTS.get(base_name)
    if hint:
        return f"{description}\n\nNOTE: {hint}"
    return description


def _mcp_topic_for(tool_name: str, server_name: str) -> str | None:
    """Map an MCP tool to a learning topic, or None to skip recording.

    Kept narrow — only the topics LazyBrain recall is wired for. Inspects
    both the tool name and the server name so e.g. `mcp-email` with a
    custom tool like `send_draft` still maps to `email`.
    """
    hay = f"{server_name or ''} {tool_name or ''}".lower()
    for topic in ("whatsapp", "instagram", "email", "gmail"):
        if topic in hay:
            # Collapse "gmail" into "email" — same topic bucket for recall.
            return "email" if topic == "gmail" else topic
    return None


def _mcp_result_indicates_error(text: str) -> bool:
    """Heuristic: the MCP bridge returns error prose as a normal string,
    so we can't rely on an exception to decide success. A handful of
    stable prefixes cover the 99% case without parsing JSON."""
    if not text:
        return True
    lead = text.lstrip()[:32]
    return lead.startswith(("[MCP ERROR]", "[NO DATA]", "Error:", "error:"))


class MCPToolSkill(BaseSkill):
    """Wraps a single MCP tool as a LazyClaw BaseSkill.

    When config and user_id are provided, enables automatic token
    refresh on 401 errors from remote OAuth-protected MCP servers.
    """

    def __init__(
        self,
        client: MCPClient,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
        config: Any = None,
        user_id: str | None = None,
    ) -> None:
        self._client = client
        self._tool_name = tool_name
        self._tool_description = tool_description
        self._tool_schema = tool_schema
        self._config = config
        self._user_id = user_id

    @property
    def name(self) -> str:
        return f"{_MCP_PREFIX}{self._client.server_id}_{self._tool_name}"

    @property
    def display_name(self) -> str:
        return f"{self._client.name}:{self._tool_name}"

    @property
    def description(self) -> str:
        return f"[MCP: {self._client.name}] {self._tool_description}"

    @property
    def category(self) -> str:
        return "mcp"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self._tool_schema

    async def execute(self, user_id: str, params: dict[str, Any]) -> str:
        """Execute the MCP tool via the client connection.

        On 401, attempts token refresh and retries once.
        Records a skill_lesson on success for learning-enabled topics
        (whatsapp / instagram / email) so future calls can replay a
        known-good parameter shape. Lesson save is fire-and-forget.

        Per-server semaphore caps simultaneous in-flight calls to one
        stdio subprocess (see `_get_call_semaphore`).
        """
        async with _get_call_semaphore(self._client.server_id):
            try:
                result = await self._client.call_tool(self._tool_name, params)
            except Exception as exc:
                if not self._config or not _is_auth_error(exc):
                    raise
                logger.info(
                    "MCP tool %s got 401 — attempting token refresh",
                    self._tool_name,
                )
                new_client = await self._refresh_and_reconnect(user_id)
                # Update to the new active client (old one is disconnected)
                self._client = new_client
                result = await self._client.call_tool(self._tool_name, params)

        # Fire-and-forget: log what worked, once per successful call, so
        # small models can replay the shape on the next similar request.
        try:
            topic = _mcp_topic_for(self._tool_name, self._client.name)
            if (
                topic is not None
                and self._config is not None
                and not _mcp_result_indicates_error(result)
            ):
                from lazyclaw.runtime.skill_lesson import save_skill_lesson
                await save_skill_lesson(
                    self._config, user_id,
                    topic=topic,
                    action=self._tool_name,
                    intent=f"call {self._tool_name} via {self._client.name}",
                    params=params,
                    outcome="success",
                )
        except Exception:
            logger.debug("mcp bridge lesson save failed", exc_info=True)

        return result

    async def _refresh_and_reconnect(self, user_id: str) -> MCPClient:
        """Refresh OAuth token and reconnect the MCP client.

        Returns the new active MCPClient (old one is disconnected).
        """
        from lazyclaw.mcp.manager import connect_with_bearer, get_server
        from lazyclaw.mcp.oauth import refresh_access_token
        from lazyclaw.mcp.token_store import OAuthTokenData, load_tokens, save_tokens

        server_name = self._client.name
        tokens = await load_tokens(self._config, user_id, server_name)
        if not tokens or not tokens.refresh_token:
            raise RuntimeError(
                f"No refresh token for {server_name}. "
                "Re-authenticate: 'reconnect to " + server_name + "'"
            )

        token_dict = await refresh_access_token(
            tokens.token_endpoint, tokens.refresh_token, tokens.client_id,
        )

        new_tokens = OAuthTokenData(
            access_token=token_dict["access_token"],
            refresh_token=token_dict.get("refresh_token", tokens.refresh_token),
            expires_at=time.time() + token_dict.get("expires_in", 3600),
            scope=token_dict.get("scope", tokens.scope),
            metadata_url=tokens.metadata_url,
            token_endpoint=tokens.token_endpoint,
            client_id=tokens.client_id,
        )
        await save_tokens(self._config, user_id, server_name, new_tokens)
        logger.info("OAuth token refreshed for %s", server_name)

        server = await get_server(self._config, user_id, self._client.server_id)
        if not server:
            raise RuntimeError(f"Server {self._client.server_id} not found after refresh")
        return await connect_with_bearer(
            self._config, user_id, self._client.server_id,
            server, new_tokens.access_token,
        )


class PooledMCPToolSkill(BaseSkill):
    """Round-robin wrapper over N MCPClient shards exposing the same tool.

    The mcp-scraper bundle ships as multiple identical subprocesses
    (`mcp-scraper-1`, `mcp-scraper-2`, …) so parallel callers don't
    serialize behind one client's `_call_lock`. This skill picks a
    shard via a process-wide `itertools.count()` counter, falls
    through to the next shard if a call returns an error, and skips
    disconnected shards entirely.

    Registered under canonical names `mcp_scraper_<tool>` — one entry
    per tool, no UUID. The brain therefore sees one set of scraper
    tools regardless of pool size.
    """

    def __init__(
        self,
        clients: list[MCPClient],
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
        config: Any = None,
        user_id: str | None = None,
    ) -> None:
        # Shared mutable list — manager.py appends lazy-spawned shards here
        self._clients = clients
        self._tool_name = tool_name
        self._tool_description = tool_description
        self._tool_schema = tool_schema
        self._config = config
        self._user_id = user_id

    @property
    def name(self) -> str:
        return f"{_SCRAPER_TOOL_PREFIX}{self._tool_name}"

    @property
    def display_name(self) -> str:
        return f"{_SCRAPER_CANONICAL}:{self._tool_name}"

    @property
    def description(self) -> str:
        return f"[MCP: {_SCRAPER_CANONICAL}] {self._tool_description}"

    @property
    def category(self) -> str:
        return "mcp"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self._tool_schema

    def _ordered_shards(self) -> list[MCPClient]:
        """Return shards starting at the current round-robin index.

        Skips disconnected shards. The list never mutates mid-call —
        callers iterate this list, not the underlying `_clients`.
        """
        live = [c for c in self._clients if c.is_connected]
        if not live:
            return []
        start = next(_pool_counter) % len(live)
        return live[start:] + live[:start]

    async def execute(self, user_id: str, params: dict[str, Any]) -> str:
        """Pick a shard, call the tool, retry on next shard on error.

        Lazy-spawn hook: if shard-0's call semaphore is saturated
        (`locked()`), trigger `_ensure_scraper_shard(idx)` for the
        next un-spawned shard. Falls back to running on shard-0
        regardless — the lazy spawn is best-effort capacity growth.

        Search-backend injection: for ``search_google`` and
        ``batch_search_google``, auto-inject the user's chosen
        backend (``general.search_provider``) so changing the
        setting in Web UI / Telegram propagates to the next call
        without any LLM-side change.
        """
        # Lazy-spawn check (best effort, never blocks the call)
        if self._clients and self._clients[0].is_connected:
            sem0 = _get_call_semaphore(self._clients[0].server_id)
            if sem0.locked():
                try:
                    from lazyclaw.mcp.manager import maybe_grow_scraper_pool
                    asyncio.create_task(
                        maybe_grow_scraper_pool(self._config, user_id),
                    )
                except Exception:
                    logger.debug("scraper pool growth skipped", exc_info=True)

        # Inject user's per-user search backend preference for search tools
        # (only when the LLM didn't already pass one explicitly).
        if self._tool_name in ("search_google", "batch_search_google"):
            params = await _inject_user_search_backend(
                self._config, user_id, self._tool_name, dict(params),
            )

        ordered = self._ordered_shards()
        if not ordered:
            return (
                "[MCP ERROR] scraper pool fully unavailable — all shards "
                "disconnected. Try again in a few seconds."
            )

        last_error: str | None = None
        for idx, client in enumerate(ordered):
            try:
                async with _get_call_semaphore(client.server_id):
                    result = await client.call_tool(self._tool_name, params)
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "scraper shard %s failed on %s: %s — trying next",
                    client.name, self._tool_name, exc,
                )
                # Only retry once across shards — keep latency bounded
                if idx >= 1:
                    break
                continue

            if _mcp_result_indicates_error(result) and idx == 0:
                # First shard returned an error string — try the next one
                last_error = result[:200]
                logger.info(
                    "scraper shard %s returned error on %s, retrying on next shard",
                    client.name, self._tool_name,
                )
                continue

            # Fire-and-forget skill_lesson — record under canonical name
            # so recall lookups don't fragment across N shard variants.
            try:
                topic = _mcp_topic_for(self._tool_name, _SCRAPER_CANONICAL)
                if (
                    topic is not None
                    and self._config is not None
                    and not _mcp_result_indicates_error(result)
                ):
                    from lazyclaw.runtime.skill_lesson import save_skill_lesson
                    await save_skill_lesson(
                        self._config, user_id,
                        topic=topic,
                        action=self._tool_name,
                        intent=f"call {self._tool_name} via {_SCRAPER_CANONICAL}",
                        params=params,
                        outcome="success",
                    )
            except Exception:
                logger.debug("scraper pool lesson save failed", exc_info=True)

            return result

        return (
            f"[MCP ERROR] scraper pool exhausted on {self._tool_name} — "
            f"last error: {last_error or 'unknown'}"
        )


# Per-canonical-name pool registry — lets manager.py look up the existing
# pooled skill when a lazy-spawned shard becomes available, so we can
# append the new client to the shared list without re-registering.
_pooled_skills_by_tool: dict[str, PooledMCPToolSkill] = {}


def _is_auth_error(exc: BaseException) -> bool:
    """Check if an exception is a 401 HTTP error."""
    for candidate in (exc, getattr(exc, "__cause__", None)):
        if candidate is None:
            continue
        status = getattr(
            getattr(candidate, "response", None), "status_code", None,
        )
        if status == 401:
            return True
    return False


class LazyMCPToolSkill(BaseSkill):
    """MCP tool stub that connects the server on first invocation.

    Registered from cached tool schemas — no subprocess spawned until
    the agent actually calls this tool. After connecting, subsequent
    calls go through the live client. If the server is idle-disconnected,
    the next call reconnects automatically.
    """

    def __init__(
        self,
        server_id: str,
        server_name: str,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
        config: Any,
        user_id: str,
        is_oauth: bool = False,
    ) -> None:
        self._server_id = server_id
        self._server_name = server_name
        self._tool_name = tool_name
        self._tool_description = tool_description
        self._tool_schema = tool_schema
        self._config = config
        self._user_id = user_id
        self._is_oauth = is_oauth

    @property
    def name(self) -> str:
        return f"{_MCP_PREFIX}{self._server_id}_{self._tool_name}"

    @property
    def display_name(self) -> str:
        return f"{self._server_name}:{self._tool_name}"

    @property
    def description(self) -> str:
        return f"[MCP: {self._server_name}] {self._tool_description}"

    @property
    def category(self) -> str:
        return "mcp"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self._tool_schema

    async def execute(self, user_id: str, params: dict[str, Any]) -> str:
        """Connect on demand, call the tool, reset idle timer."""
        from lazyclaw.mcp.manager import (
            _active_clients,
            _get_connect_lock,
            connect_server,
            connect_server_with_oauth,
            touch_client,
        )

        # Serialize connects per server to prevent duplicate subprocesses
        async with _get_connect_lock(self._server_id):
            client = _active_clients.get(self._server_id)
            need_connect = client is None or not client.is_connected
            if need_connect:
                logger.info(
                    "Lazy-connecting MCP server %s for tool %s (reason=%s)",
                    self._server_name, self._tool_name,
                    "no client" if client is None else "session dead",
                )
                if self._is_oauth:
                    client = await connect_server_with_oauth(
                        self._config, self._user_id, self._server_id,
                    )
                else:
                    client = await connect_server(
                        self._config, self._user_id, self._server_id,
                    )

        touch_client(self._server_id)
        # Per-server call semaphore — gate concurrent in-flight calls so
        # the underlying stdio subprocess (and any stateful library it
        # holds, e.g. crawl4ai's Playwright session in mcp-scraper) isn't
        # stomped by parallel EXPLORE specialists.
        async with _get_call_semaphore(self._server_id):
            try:
                return await client.call_tool(self._tool_name, params)
            except RuntimeError as exc:
                # Session died between the check and the call — reconnect once
                if "not connected" not in str(exc):
                    raise
                logger.warning(
                    "MCP %s session died mid-call, reconnecting for %s",
                    self._server_name, self._tool_name,
                )
                async with _get_connect_lock(self._server_id):
                    if self._is_oauth:
                        client = await connect_server_with_oauth(
                            self._config, self._user_id, self._server_id,
                        )
                    else:
                        client = await connect_server(
                            self._config, self._user_id, self._server_id, force=True,
                        )
                return await client.call_tool(self._tool_name, params)


async def register_mcp_tools_lazy(
    server_id: str,
    server_name: str,
    tools_json: str,
    registry: SkillRegistry,
    config: Any,
    user_id: str,
    is_oauth: bool = False,
) -> int:
    """Register MCP tools as lazy stubs from cached schemas.

    No subprocess is spawned. Each tool connects the server on first call.
    Returns the number of tools registered.
    """
    tools = json.loads(tools_json)
    count = 0
    for tool in tools:
        base_name = tool["name"]
        # Skip if another MCP server already provides this tool
        existing_mcp = registry.get_mcp_by_base_name(base_name)
        if existing_mcp is not None:
            logger.info("Skipping lazy MCP tool %s_%s — already provided by %s",
                        server_id, base_name, existing_mcp.name)
            continue
        # Note: built-in skills with same base_name are NOT skipped.
        # MCP tools get prefixed names so they coexist with built-ins.
        skill = LazyMCPToolSkill(
            server_id=server_id,
            server_name=server_name,
            tool_name=tool["name"],
            tool_description=_decorate_description(
                tool["name"], tool.get("description", ""),
            ),
            tool_schema=tool.get("inputSchema", {}),
            config=config,
            user_id=user_id,
            is_oauth=is_oauth,
        )
        registry.register(skill)
        count += 1
    logger.info(
        "Registered %d lazy tool stubs for MCP server %s", count, server_name,
    )
    return count


# -- Tool schema cache -------------------------------------------------------


async def cache_tool_schemas(config: Any, server_name: str, tools: list[dict]) -> None:
    """Save tool schemas to the mcp_tool_cache table."""
    from lazyclaw.db.connection import db_session

    tools_json = json.dumps(tools)
    async with db_session(config) as db:
        await db.execute(
            "INSERT OR REPLACE INTO mcp_tool_cache (server_name, tools_json, cached_at) "
            "VALUES (?, ?, datetime('now'))",
            (server_name, tools_json),
        )
        await db.commit()
    logger.debug("Cached %d tool schemas for %s", len(tools), server_name)


async def load_cached_schemas(config: Any, server_name: str) -> str | None:
    """Load cached tool schemas JSON. Returns None if not cached."""
    from lazyclaw.db.connection import db_session

    async with db_session(config) as db:
        row = await db.execute(
            "SELECT tools_json FROM mcp_tool_cache WHERE server_name = ?",
            (server_name,),
        )
        result = await row.fetchone()
    return result[0] if result else None


async def register_mcp_tools(
    client: MCPClient,
    registry: SkillRegistry,
    config: Any = None,
    user_id: str | None = None,
) -> int:
    """Discover tools from an MCP client and register each as a skill.

    When config and user_id are provided, registered skills gain
    automatic 401 token refresh capability.

    Returns the number of tools registered.
    """
    tools = await client.list_tools()
    count = 0
    for tool in tools:
        base_name = tool["name"]
        # Skip if another MCP server already provides this tool
        existing_mcp = registry.get_mcp_by_base_name(base_name)
        if existing_mcp is not None:
            logger.info("Skipping MCP tool %s_%s — already provided by %s",
                        client.server_id, base_name, existing_mcp.name)
            continue
        # Note: built-in skills with same base_name are NOT skipped.
        # MCP tools get a prefixed name (mcp_{server_id}_{name}) so they
        # coexist with built-ins. Built-ins can then call the MCP tool
        # internally (e.g. search_jobs calls mcp-jobspy's search_jobs).
        skill = MCPToolSkill(
            client=client,
            tool_name=tool["name"],
            tool_description=_decorate_description(
                tool["name"], tool.get("description", ""),
            ),
            tool_schema=tool.get("inputSchema", {}),
            config=config,
            user_id=user_id,
        )
        registry.register(skill)
        logger.info("Registered MCP tool: %s", skill.name)
        count += 1
    logger.info(
        "Registered %d tools from MCP server %s", count, client.server_id
    )
    return count


async def register_scraper_pool(
    clients: list[MCPClient],
    registry: SkillRegistry,
    config: Any = None,
    user_id: str | None = None,
) -> int:
    """Register canonical scraper tools backed by a shared client pool.

    Enumerates tools from the first connected client (all shards expose
    identical schemas) and creates one `PooledMCPToolSkill` per tool
    under canonical name `mcp_scraper_<tool>`. The shared `clients`
    list lets `add_scraper_shard_to_pool` append lazy-spawned shards
    later without re-registering.

    Returns the number of pooled tools registered.
    """
    if not clients:
        logger.warning("register_scraper_pool called with no clients")
        return 0

    first_live = next((c for c in clients if c.is_connected), None)
    if first_live is None:
        logger.warning("register_scraper_pool: no connected clients yet")
        return 0

    tools = await first_live.list_tools()
    count = 0
    for tool in tools:
        base_name = tool["name"]
        canonical_name = f"{_SCRAPER_TOOL_PREFIX}{base_name}"
        # Idempotent — re-registering the same canonical name is a no-op
        # so both eager startup and lazy late-bind paths can call this.
        if canonical_name in _pooled_skills_by_tool:
            continue
        skill = PooledMCPToolSkill(
            clients=clients,
            tool_name=base_name,
            tool_description=_decorate_description(
                base_name, tool.get("description", ""),
            ),
            tool_schema=tool.get("inputSchema", {}),
            config=config,
            user_id=user_id,
        )
        registry.register(skill)
        _pooled_skills_by_tool[canonical_name] = skill
        count += 1
    logger.info(
        "Registered %d pooled scraper tools (pool size=%d)",
        count, len(clients),
    )
    return count


def add_scraper_shard_to_pool(client: MCPClient) -> None:
    """Append a lazy-spawned shard to the existing pool's shared list.

    Pool skills hold a reference to the same `clients` list, so a new
    shard becomes visible to round-robin on the next `execute()` call.
    No-op if no pool has been registered yet (skip-add).
    """
    if not _pooled_skills_by_tool:
        logger.debug(
            "add_scraper_shard_to_pool: no pool registered yet, skip-add",
        )
        return
    # All pooled skills share the same list — pick any to append to
    sample = next(iter(_pooled_skills_by_tool.values()))
    if client not in sample._clients:
        sample._clients.append(client)
        logger.info(
            "Added scraper shard %s to pool (size now=%d)",
            client.name, len(sample._clients),
        )


def unregister_mcp_tools(server_id: str, registry: SkillRegistry) -> int:
    """Remove all MCP skills for a given server_id from the registry.

    Returns the number of skills removed.
    """
    prefix = f"{_MCP_PREFIX}{server_id}_"
    # Collect matching names from the internal dict
    to_remove = [
        name for name in list(registry._skills) if name.startswith(prefix)
    ]
    for name in to_remove:
        del registry._skills[name]
        logger.info("Unregistered MCP tool: %s", name)
    if to_remove:
        registry._invalidate_cache()
    logger.info(
        "Unregistered %d tools for MCP server %s", len(to_remove), server_id
    )
    return len(to_remove)
