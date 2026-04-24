"""Unified browser skill — single CDP-based tool for all browser interactions.

Actions are grouped into four categories:
    READ:     read, snapshot, screenshot, console_logs, network, ocr
    INTERACT: click, type, press_key, hover, drag
    NAVIGATE: open, close, show, tabs, scroll, chain
    ESCALATE: ask_vision

Controls user's real Brave browser via Chrome DevTools Protocol.
Action implementations live in browser_actions/ submodules.

Error handling: every handler returns a string. When something goes wrong,
the response starts with a structured ``[error_code]`` prefix (see
``lazyclaw/browser/action_errors.py``). The agent should branch retry
strategy on that code — e.g. wait on ``[timeout]``, scroll on
``[occluded]``, re-snapshot on ``[stale_snapshot]``, give up on
``[not_found]``, escalate to ``action=ask_vision`` on
``[dependency_missing]``.
"""

from __future__ import annotations

import logging

from lazyclaw.browser.action_verifier import ActionVerifier
from lazyclaw.browser.browser_settings import touch_browser_activity
from lazyclaw.runtime.tool_result import ToolResult
from lazyclaw.skills.base import BaseSkill

from .browser_actions.ask_vision import action_ask_vision
from .browser_actions.backends import get_cdp_backend, reset_backend
from .browser_actions.capture import action_console_logs, action_screenshot, action_snapshot
from .browser_actions.interact import action_click, action_drag, action_hover, action_press_key, action_type
from .browser_actions.navigation import action_chain, action_close, action_scroll, action_show, action_tabs
from .browser_actions.network import action_network
from .browser_actions.ocr import action_ocr
from .browser_actions.read_open import action_open, action_read

logger = logging.getLogger(__name__)


class BrowserSkill(BaseSkill):
    """Single CDP-based tool for all browser interactions.

    Pure action tool — buy tickets, check in, pay bills, order from Amazon,
    read Gmail, navigate. Controls user's real visible Brave.
    """

    def __init__(self, config=None) -> None:
        self._config = config
        self._snapshot_mgr = None
        self._verifier = ActionVerifier()

    def _get_snapshot_manager(self):
        """Lazy-init snapshot manager."""
        if self._snapshot_mgr is None:
            from lazyclaw.browser.snapshot import SnapshotManager
            self._snapshot_mgr = SnapshotManager()
        return self._snapshot_mgr

    @property
    def name(self) -> str:
        return "browser"

    @property
    def display_name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Control the user's REAL Brave browser. Visible on their screen. "
            "Use for reading pages, navigating, clicking, typing, "
            "taking screenshots, listing tabs, scrolling. Shortcuts: "
            "'twitter', 'facebook', 'linkedin'. "
            "NOT for messaging or email apps — those have dedicated MCP tools. "
            "\n\nIMPORTANT — identity routing: if the user says 'use my browser', "
            "'use mybrowser', 'my brave', 'use my cookies', 'login as me', or "
            "similar (any language, any typo), call the `use_host_browser` skill "
            "FIRST with action='start' before any browser action — that bridges "
            "to their real Brave with their cookies. Without that call, this "
            "skill drives a fresh container Brave with no user cookies."
        )

    @property
    def category(self) -> str:
        return "browser"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "read", "open", "click", "type", "press_key",
                        "screenshot", "tabs", "scroll", "close", "show",
                        "snapshot", "hover", "drag", "console_logs", "chain",
                        "network", "ocr", "ask_vision",
                    ],
                    "description": (
                        "READ actions — gather information:\n"
                        "  read: get page CONTENT (text, emails, messages) — no interactive refs.\n"
                        "  snapshot: get interactive element refs [e1],[e2] ONLY — use before clicking/typing.\n"
                        "  screenshot: capture screenshot (ONLY when user asks).\n"
                        "  console_logs: get browser console output.\n"
                        "  network: list recent XHR/fetch requests (url, status, size, body preview). "
                        "Best tool on SPAs where the DOM looks empty but data loaded via fetch. "
                        "Filters: url_substring, method, status_min/max, only_failed, limit (default 20), "
                        "include_body (default false). Returns JSON.\n"
                        "  ocr: local Tesseract text extraction from the viewport. Use for canvas-heavy "
                        "pages, in-browser PDFs, banking/gov portals where accessibility tree is weak. "
                        "Optional region={x, y, width, height}. Free, ~200ms. Falls back to ask_vision "
                        "with a clear [dependency_missing] error if tesseract isn't installed.\n"
                        "INTERACT actions — change page state:\n"
                        "  click: click element by ref (e5) or description. Returns fresh refs if page changed.\n"
                        "  type: type text into element. Returns fresh refs if page changed.\n"
                        "  press_key: press a keyboard key (Enter, Escape, Tab, Backspace, ArrowDown).\n"
                        "  hover: hover over element.\n"
                        "  drag: drag element from source to target.\n"
                        "NAVIGATE actions — move between pages/tabs:\n"
                        "  open: navigate + get content summary AND interactive refs [e1],[e2].\n"
                        "  close: close/hide the browser.\n"
                        "  show: make the browser window visible on screen.\n"
                        "  tabs: list all open tabs.\n"
                        "  scroll: scroll up or down.\n"
                        "  chain: execute multiple steps in one call.\n"
                        "ESCALATE — when nothing else works:\n"
                        "  ask_vision: delegate a visual question to a local vision model "
                        "(gemma4:e2b). Use ONLY when snapshot/read/ocr can't answer — layout "
                        "bugs, visual-only elements, CAPTCHAs, unexpected popups, disabled "
                        "buttons, image content. Requires 'question' param. Free, ~3-5s.\n\n"
                        "All errors start with a `[code]` prefix (e.g. [not_found], [timeout], "
                        "[stale_snapshot], [dependency_missing]) plus a Retry hint. Branch retry "
                        "strategy on the code — don't retry [not_found], re-snapshot on "
                        "[stale_snapshot], escalate to ask_vision on [dependency_missing]."
                    ),
                },
                "target": {
                    "type": "string",
                    "description": (
                        "For read/open: URL, shortcut (twitter, facebook, linkedin), or tab query. "
                        "For click/type/hover: CSS selector OR natural description. "
                        "For drag: source CSS selector. "
                        "Leave empty for current tab."
                    ),
                },
                "ref": {
                    "type": "string",
                    "description": (
                        "Element ref ID from snapshot (e.g. 'e5'). PREFERRED over target for click/type/hover."
                    ),
                },
                "text": {
                    "type": "string",
                    "description": "Text to type (for 'type' action only).",
                },
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "For chain action: list of steps to execute sequentially. "
                        "Examples: 'click e5', 'type e2 hello world', 'press_key Enter', "
                        "'wait 2', 'snapshot'."
                    ),
                },
                "task_hint": {
                    "type": "string",
                    "description": "For snapshot: task description to filter relevant page sections.",
                },
                "landmark": {
                    "type": "string",
                    "description": "For snapshot: expand only this section (navigation, main, etc).",
                },
                "destination": {
                    "type": "string",
                    "description": "Target CSS selector for drag action (drop destination).",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction (default: down).",
                },
                "visible": {
                    "type": "boolean",
                    "description": "Force visible browser window.",
                },
                "question": {
                    "type": "string",
                    "description": (
                        "For ask_vision: the specific visual question to answer "
                        "(e.g. 'is the submit button enabled?', 'what error does "
                        "the modal show?'). Be specific — avoid 'describe this'."
                    ),
                },
                "region": {
                    "type": "object",
                    "description": (
                        "For ocr: optional crop region {x, y, width, height} in viewport pixels. "
                        "Omit for the whole viewport. Regions bigger than ~2000×2000 are rejected "
                        "— use ask_vision for full-page reads instead."
                    ),
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                    },
                },
                "url_substring": {
                    "type": "string",
                    "description": (
                        "For network: case-insensitive substring filter on request URL."
                    ),
                },
                "method": {
                    "type": "string",
                    "description": (
                        "For network: HTTP method filter (GET, POST, PUT, DELETE…)."
                    ),
                },
                "status_min": {
                    "type": "integer",
                    "description": "For network: minimum response status (e.g. 400 for errors).",
                },
                "status_max": {
                    "type": "integer",
                    "description": "For network: maximum response status.",
                },
                "only_failed": {
                    "type": "boolean",
                    "description": "For network: only include requests that failed (loadingFailed).",
                },
                "limit": {
                    "type": "integer",
                    "description": "For network: max records to return (1-50, default 20).",
                },
                "include_body": {
                    "type": "boolean",
                    "description": (
                        "For network: when true, lazily fetches a ~2KB response body preview "
                        "for JSON/text requests. Capped at 200KB total per query."
                    ),
                },
            },
            "required": ["action"],
        }

    # Services with MCP connectors — hard block browser usage
    _MCP_SERVICES = {
        "whatsapp": "whatsapp",
        "wa": "whatsapp",
        "web.whatsapp.com": "whatsapp",
        "instagram": "instagram",
        "ig": "instagram",
        "instagram.com": "instagram",
        "gmail": "email",
        "mail": "email",
        "email": "email",
        "mail.google.com": "email",
    }

    async def execute(self, user_id: str, params: dict) -> str | ToolResult:
        # Hard block: redirect MCP-backed services away from browser
        target = (params.get("target") or "").lower().strip()
        for keyword, mcp_name in self._MCP_SERVICES.items():
            if keyword in target:
                return (
                    f"STOP: Do not use browser for this. Use search_tools('{mcp_name}') "
                    f"to find the {mcp_name}_* MCP tools instead."
                )

        tab_context = params.pop("_tab_context", None)
        is_background = params.pop("_background", False)
        action = params.get("action", "")
        touch_browser_activity()
        mgr = self._get_snapshot_manager()

        try:
            return await self._dispatch(
                action, user_id, params, tab_context, mgr, is_background,
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.info("browser: CDP connection lost (%s), relaunching", e)
            reset_backend()
            try:
                await get_cdp_backend(user_id)
                return await self.execute(user_id, params)
            except Exception:
                logger.warning("CDP relaunch failed, trying auto-connect", exc_info=True)
                return await self._auto_connect_and_retry(user_id, params)
        except Exception as e:
            logger.error("browser %s failed: %s", action, e, exc_info=True)
            return f"Error: {e}"

    async def _dispatch(
        self, action: str, user_id: str, params: dict,
        tab_context, mgr, is_background: bool,
    ) -> str | ToolResult:
        """Route action to the appropriate handler module."""
        v = self._verifier

        if action == "read":
            return await action_read(user_id, params, tab_context, self._config, mgr)
        elif action == "open":
            return await action_open(user_id, params, tab_context, self._config, mgr, v, is_background)
        elif action == "click":
            return await action_click(user_id, params, tab_context, mgr, v)
        elif action == "type":
            return await action_type(user_id, params, tab_context, mgr, v)
        elif action == "press_key":
            return await action_press_key(user_id, params, tab_context, mgr, v)
        elif action == "screenshot":
            return await action_screenshot(user_id, params, tab_context)
        elif action == "tabs":
            return await action_tabs(user_id, params)
        elif action == "scroll":
            return await action_scroll(user_id, params, tab_context)
        elif action == "close":
            return await action_close(user_id, params)
        elif action == "show":
            return await action_show(user_id)
        elif action == "snapshot":
            return await action_snapshot(user_id, params, tab_context, mgr)
        elif action == "hover":
            return await action_hover(user_id, params, tab_context)
        elif action == "drag":
            return await action_drag(user_id, params, tab_context)
        elif action == "console_logs":
            return await action_console_logs(user_id, params, tab_context)
        elif action == "chain":
            return await action_chain(user_id, params, tab_context, mgr)
        elif action == "network":
            return await action_network(user_id, params, tab_context, self._config, mgr)
        elif action == "ocr":
            return await action_ocr(user_id, params, tab_context, self._config, mgr)
        elif action == "ask_vision":
            return await action_ask_vision(user_id, params, tab_context)
        else:
            return (
                f"Unknown action: {action}. Use: read, open, click, type, "
                f"press_key, screenshot, tabs, scroll, close, snapshot, "
                f"hover, drag, console_logs, chain, network, ocr, ask_vision"
            )

    async def _auto_connect_and_retry(self, user_id: str, params: dict) -> str:
        """Auto-restart browser with CDP if approved, then retry."""
        from lazyclaw.browser.browser_settings import get_browser_settings
        from lazyclaw.config import load_config

        config = load_config()
        settings = await get_browser_settings(config, user_id)

        if not settings.get("cdp_approved"):
            return (
                "I need to restart Brave with debugging enabled so I can "
                "read your browser tabs. All your "
                "tabs and logins will be preserved — just a 2-3 second "
                "restart. Say 'yes, connect browser' to allow."
            )

        from lazyclaw.browser.cdp_backend import CDPBackend, restart_browser_with_cdp

        port = getattr(config, "cdp_port", 9222)
        profile_dir = str(config.database_dir / "browser_profiles" / user_id)
        ws_url = await restart_browser_with_cdp(port=port, profile_dir=profile_dir)

        if not ws_url:
            return "Failed to restart browser with debugging. Check if Brave is installed."

        from .browser_actions.backends import _cdp_backend
        import lazyclaw.skills.builtin.browser_actions.backends as _backends_mod
        _backends_mod._cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)

        try:
            return await self.execute(user_id, params)
        except Exception as e:
            return f"Browser restarted but action failed: {e}"
