"""MCP server exposing the `panel_*` admin-panel bridge tools."""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool
from pydantic import ValidationError

from mcp_apihunter.config import ApiHunterConfig
from mcp_apihunter.manifest import ManifestStore
from mcp_apihunter.session import CdpSessionProvider, SessionProvider
from mcp_apihunter.tools import PanelTools

logger = logging.getLogger(__name__)


def _text(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


def create_server(
    config: ApiHunterConfig,
    provider: SessionProvider | None = None,
) -> Server:
    store = ManifestStore(config.manifest_root, config.server_secret, config.user_id)
    session_provider = provider or CdpSessionProvider(config.cdp_host, config.cdp_port)
    tools = PanelTools(store, session_provider)
    server = Server("mcp-apihunter")

    # Tool name → (bound method, whether it takes an arguments dict).
    _dispatch = {
        "panel_learn_endpoint": (tools.learn, True),
        "panel_learn_from_capture": (tools.learn_from_capture, True),
        "panel_probe_api": (tools.probe_api, True),
        "panel_list_sites": (tools.list_sites, False),
        "panel_list_endpoints": (tools.list_endpoints, True),
        "panel_describe": (tools.describe, True),
        "panel_call": (tools.call, True),
        "panel_forget": (tools.forget, True),
        "panel_discover": (tools.discover, True),
    }

    def _require_key() -> dict[str, Any] | None:
        if not config.has_manifest_key:
            return {
                "error": "apihunter is missing SERVER_SECRET or LAZYCLAW_USER_ID "
                "in its environment — it must run as a LazyClaw bundled MCP."
            }
        return None

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return _TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        guard = _require_key()
        if guard is not None and name != "panel_list_sites":
            return _text(guard)
        handler = _dispatch.get(name)
        if handler is None:
            return _text({"error": f"unknown tool: {name}"})
        method, takes_args = handler
        try:
            result = await (method(arguments) if takes_args else method())
            return _text(result)
        except ValidationError as exc:
            return _text({"error": "invalid endpoint definition", "detail": exc.errors()})
        except KeyError as exc:
            return _text({"error": f"missing required argument: {exc}"})
        except Exception as exc:  # noqa: BLE001 - surface any failure as tool output
            logger.exception("apihunter tool %s failed", name)
            return _text({"error": f"{type(exc).__name__}: {exc}"})

    return server


# --- Tool schemas ----------------------------------------------------------

_CSRF_PROPS = {
    "csrf_source": {"type": "string", "enum": ["none", "cookie"],
                    "description": "Where the CSRF token comes from (default none)"},
    "csrf_cookie_name": {"type": "string", "description": "Cookie holding the CSRF token (default csrftoken)"},
    "csrf_target": {"type": "string", "enum": ["header", "form", "query"],
                    "description": "Where to place the token (default header)"},
    "csrf_target_name": {"type": "string", "description": "Header/field name for the token (default X-CSRFToken)"},
}

_TOOLS = [
    Tool(
        name="panel_learn_endpoint",
        description=(
            "Record one admin-panel endpoint into the site manifest so it can "
            "be replayed later with panel_call. Creates the site on first use "
            "(base_url required then). Mark write/delete/publish calls "
            "mutating=true."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "site": {"type": "string", "description": "Site slug (lowercase, no dots), e.g. 'himap-admin'. The domain goes in base_url."},
                "base_url": {"type": "string", "description": "Panel origin, e.g. https://himap.co (required on first endpoint)"},
                "owner_confirmed": {"type": "boolean", "description": "Set true ONLY when the user has confirmed they own/administer this panel. Required to record a NEW site (apihunter records only owned/administered panels)."},
                "cookie_domain": {"type": "string", "description": "Domain to pull session cookies for (defaults to base_url host)"},
                "login_url": {"type": "string", "description": "Where to re-login when the session expires"},
                "name": {"type": "string", "description": "Endpoint slug, e.g. 'post_blog'"},
                "description": {"type": "string"},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                "url_template": {"type": "string", "description": "Path or full URL, may contain {placeholders}, e.g. /api/posts/{id}/"},
                "query": {"type": "object", "description": "Default query params (values may contain {placeholders})"},
                "headers": {"type": "object", "description": "Static headers to send"},
                "body_type": {"type": "string", "enum": ["none", "json", "form"]},
                "body_template": {"type": "object", "description": "Request body; values may contain {placeholders}"},
                "mutating": {"type": "boolean", "description": "True if the call changes server state (requires confirm at call time)"},
                "success_status": {"type": "array", "items": {"type": "integer"}, "description": "HTTP statuses that mean success"},
                "response_hint": {"type": "string", "description": "How to read the response"},
                **_CSRF_PROPS,
            },
            "required": ["site", "name", "method", "url_template"],
        },
    ),
    Tool(
        name="panel_learn_from_capture",
        description=(
            "Bulk-ingest a browser network capture (from browser action='network') "
            "into endpoint SKELETONS: filters out assets/analytics/failures, "
            "parameterizes id-like path segments, marks writes mutating. The "
            "capture has no request bodies/headers, so verify each skeleton with "
            "panel_learn_endpoint + panel_call before relying on it."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "site": {"type": "string"},
                "base_url": {"type": "string", "description": "Required if the site is new"},
                "owner_confirmed": {"type": "boolean", "description": "Set true ONLY when the user confirmed they own/administer this panel. Required to record a NEW site."},
                "cookie_domain": {"type": "string"},
                "login_url": {"type": "string"},
                "records": {
                    "type": "array",
                    "description": "The 'records' array from the browser network action",
                    "items": {"type": "object"},
                },
            },
            "required": ["site", "records"],
        },
    ),
    Tool(
        name="panel_probe_api",
        description=(
            "Check a panel for an OFFICIAL API (OpenAPI/Swagger schema, WordPress "
            "/wp-json, GraphQL, REST root) before reverse-engineering. Official "
            "APIs are far more stable — prefer them. Pass a known site or a base_url."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "site": {"type": "string"},
                "base_url": {"type": "string"},
                "cookie_domain": {"type": "string"},
            },
        },
    ),
    Tool(
        name="panel_list_sites",
        description="List admin panels apihunter has manifests for.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="panel_list_endpoints",
        description="List the recorded endpoints for a site.",
        inputSchema={
            "type": "object",
            "properties": {"site": {"type": "string"}},
            "required": ["site"],
        },
    ),
    Tool(
        name="panel_describe",
        description="Show the full definition of one recorded endpoint.",
        inputSchema={
            "type": "object",
            "properties": {"site": {"type": "string"}, "name": {"type": "string"}},
            "required": ["site", "name"],
        },
    ),
    Tool(
        name="panel_call",
        description=(
            "Invoke a recorded endpoint as a direct API call, reusing the "
            "browser's live session. Returns status ok / needs_confirm / "
            "needs_relogin / error. Mutating endpoints require confirm=true."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "site": {"type": "string"},
                "name": {"type": "string"},
                "args": {"type": "object", "description": "Values for the endpoint's {placeholders}"},
                "confirm": {"type": "boolean", "description": "Approve a mutating call (set only after user approval)"},
            },
            "required": ["site", "name"],
        },
    ),
    Tool(
        name="panel_forget",
        description="Remove one endpoint (pass name) or an entire site (omit name) from apihunter.",
        inputSchema={
            "type": "object",
            "properties": {"site": {"type": "string"}, "name": {"type": "string"}},
            "required": ["site"],
        },
    ),
    Tool(
        name="panel_discover",
        description=(
            "Open a panel for endpoint recording: creates the manifest shell "
            "and returns guidance for mapping the panel via the browser. "
            "Recording a NEW panel requires owner_confirmed=true — apihunter "
            "records only panels the user owns or administers."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "site": {"type": "string"},
                "base_url": {"type": "string"},
                "owner_confirmed": {"type": "boolean", "description": "Set true ONLY when the user confirmed they own/administer this panel. Required to open a NEW site for recording."},
                "cookie_domain": {"type": "string"},
                "login_url": {"type": "string"},
            },
            "required": ["site", "base_url"],
        },
    ),
]
