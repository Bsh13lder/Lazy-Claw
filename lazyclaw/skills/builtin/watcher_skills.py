"""Watcher skills — watch sites for changes, zero tokens per check."""

from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class WatchSiteSkill(BaseSkill):
    """Start watching a site for changes."""

    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "watch_site"

    @property
    def category(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Watch a website for changes. Zero token cost per check — uses "
            "JavaScript extraction directly, no LLM calls during polling. "
            "Sends notification via Telegram when a change is detected. "
            "Sites get auto-generated JS extractors. "
            "Use when user says 'notify me when price "
            "drops', 'monitor this page'. "
            "Do NOT use for WhatsApp, Instagram, or Email — those have MCP tools."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "URL to watch. NOT for WhatsApp/Instagram/Email (use MCP tools)"
                    ),
                },
                "what_to_watch": {
                    "type": "string",
                    "description": (
                        "What to watch for in natural language. "
                        "E.g. 'new messages', 'price changes', 'new listings'"
                    ),
                },
                "check_interval_minutes": {
                    "type": "integer",
                    "description": "How often to check in minutes. Default: 5",
                },
                "duration_hours": {
                    "type": "number",
                    "description": (
                        "How long to watch in hours. Default: 2. "
                        "Use 0 for one-shot (stop after first notification)."
                    ),
                },
                "custom_js": {
                    "type": "string",
                    "description": (
                        "Optional: custom JavaScript to evaluate on the page. "
                        "Must return a value that changes when the condition is met. "
                        "Only provide if you know the exact JS needed."
                    ),
                },
            },
            "required": ["url", "what_to_watch"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"

        url = params["url"].strip()
        what = params["what_to_watch"]
        interval = params.get("check_interval_minutes", 5) * 60
        duration = params.get("duration_hours", 2)
        custom_js = params.get("custom_js")

        # Block MCP channels — use watch_messages skill instead
        if url.lower() in ("whatsapp", "wa"):
            return "Error: Use watch_messages skill for WhatsApp monitoring, not browser watcher."
        elif url.lower() in ("gmail", "email", "mail"):
            return "Error: Use watch_messages skill for Email monitoring, not browser watcher."
        elif url.lower() in ("instagram", "ig", "insta"):
            return "Error: Use watch_messages skill for Instagram monitoring, not browser watcher."

        # Calculate expiration
        expires_at = None
        one_shot = False
        if duration == 0:
            one_shot = True
        elif duration > 0:
            from datetime import datetime, timedelta, timezone
            expires_at = (
                datetime.now(timezone.utc) + timedelta(hours=duration)
            ).isoformat()

        # For unknown sites without custom JS, generate it via LLM
        from lazyclaw.browser.page_reader import _detect_page_type
        page_type = _detect_page_type(url)

        if not custom_js and page_type == "auto":
            custom_js = await self._generate_extractor_js(what, url)

        # Build watcher context
        from lazyclaw.browser.watcher import build_watcher_context
        context = build_watcher_context(
            url=url,
            custom_js=custom_js,
            check_interval=interval,
            expires_at=expires_at,
            notify_template=None,
            one_shot=one_shot,
        )

        # Create job
        from lazyclaw.heartbeat.orchestrator import create_job
        job_id = await create_job(
            config=self._config,
            user_id=user_id,
            name=f"Watch: {what}",
            instruction=what,
            job_type="watcher",
            context=context,
        )

        # Ensure browser is running (auto-upgrade from 'off' if needed)
        await self._ensure_browser(user_id)

        # Format response
        interval_min = interval // 60
        if one_shot:
            duration_str = "until first change detected"
        elif expires_at:
            duration_str = f"for {duration} hours"
        else:
            duration_str = "indefinitely"

        return (
            f"Watcher started: {what}\n"
            f"URL: {url}\n"
            f"Checking every {interval_min} minutes, {duration_str}.\n"
            f"Notifications via Telegram. Zero token cost per check.\n"
            f"ID: {job_id[:8]}..."
        )

    async def _generate_extractor_js(self, what: str, url: str) -> str | None:
        """One-time LLM call to generate a JS extractor for unknown sites."""
        try:
            from lazyclaw.llm.providers.base import LLMMessage

            messages = [
                LLMMessage(
                    role="system",
                    content=(
                        "Generate a JavaScript expression that extracts the "
                        "relevant data from a web page. The expression must "
                        "return a string or number that will CHANGE when the "
                        "user's condition is met. Return ONLY the JS code, "
                        "no explanation. Wrap in an IIFE: (() => { ... })()"
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=(
                        f"URL: {url}\n"
                        f"Watch for: {what}\n"
                        f"Generate a JS expression that returns a value that "
                        f"changes when this condition occurs."
                    ),
                ),
            ]
            # Use eco_router via ROLE_WORKER (cheap), fallback to direct router
            try:
                from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
                from lazyclaw.llm.router import LLMRouter
                eco = EcoRouter(self._config, LLMRouter(self._config))
                response = await eco.chat(messages, user_id="system", role=ROLE_WORKER)
            except Exception:
                logger.warning("EcoRouter unavailable for extractor JS generation, falling back to direct LLM", exc_info=True)
                from lazyclaw.llm.router import LLMRouter
                router = LLMRouter(self._config)
                response = await router.chat(messages, model=self._config.worker_model)
            js = response.content.strip()
            # Strip markdown code blocks if present
            if js.startswith("```"):
                js = "\n".join(js.split("\n")[1:-1])
            return js
        except Exception as exc:
            logger.warning("Failed to generate extractor JS: %s", exc)
            return None

    async def _ensure_browser(self, user_id: str) -> None:
        """Make sure browser is running for watcher polling."""
        from lazyclaw.browser.browser_settings import (
            get_browser_settings,
            update_browser_settings,
        )
        from lazyclaw.browser.cdp import find_chrome_cdp
        from lazyclaw.browser.cdp_backend import CDPBackend

        settings = await get_browser_settings(self._config, user_id)
        mode = settings.get("persistent", "auto")

        # If browser is off, auto-upgrade to auto mode
        if mode == "off":
            await update_browser_settings(
                self._config, user_id, {"persistent": "auto"},
            )

        # Launch if not running
        port = getattr(self._config, "cdp_port", 9222)
        if not await find_chrome_cdp(port):
            profile_dir = str(
                self._config.database_dir / "browser_profiles" / user_id
            )
            backend = CDPBackend(port=port, profile_dir=profile_dir)
            await backend._auto_launch_chrome()


import logging

logger = logging.getLogger(__name__)


class StopWatcherSkill(BaseSkill):
    """Stop a running watcher."""

    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "stop_watcher"

    @property
    def category(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Stop a running site watcher. Use when user says "
            "'stop watching', 'cancel watcher', 'stop monitoring'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "watcher_id": {
                    "type": "string",
                    "description": (
                        "ID of the watcher to stop (first 8 chars is enough). "
                        "If not provided, stops all watchers."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"

        watcher_id = params.get("watcher_id", "").strip()

        from lazyclaw.heartbeat.orchestrator import delete_job, list_jobs

        jobs = await list_jobs(self._config, user_id)
        watchers = [j for j in jobs if j.get("job_type") == "watcher"
                     and j.get("status") == "active"]

        if not watchers:
            return "No active watchers found."

        if watcher_id:
            # Find by prefix match
            target = next(
                (w for w in watchers if w["id"].startswith(watcher_id)),
                None,
            )
            if not target:
                return f"No watcher found matching '{watcher_id}'."
            await delete_job(self._config, user_id, target["id"])
            return f"Stopped watcher: {target.get('name', 'unknown')}"
        else:
            # Stop all
            for w in watchers:
                await delete_job(self._config, user_id, w["id"])
            return f"Stopped {len(watchers)} watcher(s)."


class ListWatchersSkill(BaseSkill):
    """List active watchers."""

    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "list_watchers"

    @property
    def category(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "List all active site watchers. Use when user asks "
            "'what are you watching', 'show watchers', 'active monitors'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"

        from lazyclaw.heartbeat.orchestrator import list_jobs
        import json

        jobs = await list_jobs(self._config, user_id)
        watchers = [j for j in jobs if j.get("job_type") == "watcher"
                     and j.get("status") in ("active", "paused")]

        if not watchers:
            return "No active watchers."

        lines = []
        for w in watchers:
            ctx = {}
            try:
                ctx = json.loads(w.get("context", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass  # intentional: malformed context JSON, default {} is fine

            interval = ctx.get("check_interval", 300) // 60
            expires = ctx.get("expires_at", "never")
            if expires and expires != "never":
                expires = expires[:16].replace("T", " ")

            status = w.get("status", "?")
            lines.append(
                f"- {w.get('name', '?')} [{status}]\n"
                f"  URL: {ctx.get('url', '?')}\n"
                f"  Every {interval}min | Expires: {expires}\n"
                f"  ID: {w['id'][:8]}..."
            )

        return f"Active watchers ({len(watchers)}):\n\n" + "\n\n".join(lines)
