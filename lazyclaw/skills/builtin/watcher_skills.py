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
        from lazyclaw.watchers import history as watcher_history
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

            stats = watcher_history.get_stats(user_id, w["id"])
            last_ck = ctx.get("last_check")
            last_ck = last_ck[:16].replace("T", " ") if last_ck else "never"
            last_val = stats.get("last_value_preview") or ctx.get("last_value")
            if last_val and len(last_val) > 80:
                last_val = last_val[:80] + "…"

            status = w.get("status", "?")
            lines.append(
                f"- {w.get('name', '?')} [{status}]\n"
                f"  URL: {ctx.get('url', '?')}\n"
                f"  Every {interval}min | Expires: {expires}\n"
                f"  Last check: {last_ck} | checks: {stats['check_count']}, "
                f"triggers: {stats['trigger_count']}"
                + (f", errors: {stats['error_count']}" if stats['error_count'] else "")
                + "\n"
                f"  Last value: {last_val or '—'}\n"
                f"  ID: {w['id'][:8]}..."
            )

        return f"Active watchers ({len(watchers)}):\n\n" + "\n\n".join(lines)


# ── control skills — edit / pause / resume / test live watchers ──────────


async def _find_watcher(config, user_id: str, needle: str) -> dict | None:
    """Find one active/paused watcher by name substring or ID prefix."""
    from lazyclaw.heartbeat.orchestrator import list_jobs
    needle = (needle or "").strip().lower()
    if not needle:
        return None
    jobs = await list_jobs(config, user_id)
    watchers = [
        j for j in jobs
        if j.get("job_type") == "watcher"
        and j.get("status") in ("active", "paused")
    ]
    # 1) exact ID prefix wins
    for w in watchers:
        if w["id"].startswith(needle):
            return w
    # 2) case-insensitive name substring
    for w in watchers:
        if needle in (w.get("name") or "").lower():
            return w
    return None


class PauseWatcherSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "pause_watcher"

    @property
    def category(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Pause a running watcher without deleting it. Use when the user "
            "says 'pause the DGT watcher', 'stop watching for now', "
            "'mute the doctor watcher'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "watcher": {
                    "type": "string",
                    "description": "Watcher name (partial match) or ID prefix.",
                },
            },
            "required": ["watcher"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        w = await _find_watcher(self._config, user_id, params.get("watcher", ""))
        if w is None:
            return f"No watcher matching '{params.get('watcher')}'."
        if w["status"] == "paused":
            return f"'{w['name']}' is already paused."
        from lazyclaw.heartbeat.orchestrator import pause_job
        await pause_job(self._config, user_id, w["id"])
        return f"Paused watcher '{w['name']}'. Use resume_watcher to start it again."


class ResumeWatcherSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "resume_watcher"

    @property
    def category(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Resume a paused watcher. Use when the user says 'resume the X "
            "watcher', 'start watching again', 'unpause the monitor'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "watcher": {
                    "type": "string",
                    "description": "Watcher name (partial match) or ID prefix.",
                },
            },
            "required": ["watcher"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        w = await _find_watcher(self._config, user_id, params.get("watcher", ""))
        if w is None:
            return f"No watcher matching '{params.get('watcher')}'."
        if w["status"] == "active":
            return f"'{w['name']}' is already active."
        from lazyclaw.db.connection import db_session
        async with db_session(self._config) as db:
            await db.execute(
                "UPDATE agent_jobs SET status = 'active' "
                "WHERE id = ? AND user_id = ? AND job_type = 'watcher'",
                (w["id"], user_id),
            )
            await db.commit()
        return f"Resumed watcher '{w['name']}'. Next check runs on the current interval."


class EditWatcherSkill(BaseSkill):
    """Combined edit: interval / extractor JS / what-to-watch condition."""

    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "edit_watcher"

    @property
    def category(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Edit an existing watcher: change its check interval, rewrite the "
            "JS extractor, or update the human-readable condition. Use when "
            "the user says 'check the DGT one every minute', 'change the "
            "extractor for X', 'edit the watcher'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "watcher": {
                    "type": "string",
                    "description": "Watcher name (partial match) or ID prefix.",
                },
                "interval_minutes": {
                    "type": "number",
                    "description": "New check interval in minutes. Minimum 0.25 (15s).",
                },
                "extractor_js": {
                    "type": "string",
                    "description": (
                        "New JavaScript extractor. Must return a value that "
                        "changes when the trigger condition is met."
                    ),
                },
                "what_to_watch": {
                    "type": "string",
                    "description": "New human-readable condition description.",
                },
            },
            "required": ["watcher"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        w = await _find_watcher(self._config, user_id, params.get("watcher", ""))
        if w is None:
            return f"No watcher matching '{params.get('watcher')}'."

        import json
        try:
            ctx = json.loads(w.get("context") or "{}")
        except (json.JSONDecodeError, TypeError):
            ctx = {}

        changed: list[str] = []
        if params.get("interval_minutes") is not None:
            try:
                secs = max(15, int(float(params["interval_minutes"]) * 60))
            except (TypeError, ValueError):
                return "interval_minutes must be a number."
            ctx["check_interval"] = secs
            ctx["last_check"] = None
            changed.append(f"interval → {secs // 60}min")
        if params.get("extractor_js"):
            ctx["custom_js"] = params["extractor_js"].strip()
            ctx["last_value"] = None
            changed.append("extractor rewritten")
        if params.get("what_to_watch"):
            ctx["what_to_watch"] = params["what_to_watch"].strip()
            changed.append("condition updated")

        if not changed:
            return "Nothing to change — pass interval_minutes, extractor_js, or what_to_watch."

        from lazyclaw.heartbeat.orchestrator import update_job
        await update_job(
            self._config, user_id, w["id"], context=json.dumps(ctx),
        )
        # Mirror to parent template when present.
        try:
            tpl_id = ctx.get("template_id")
            if tpl_id and (params.get("extractor_js") or params.get("what_to_watch")):
                from lazyclaw.browser import templates as tpl_store
                tpl_fields: dict = {}
                if params.get("extractor_js"):
                    tpl_fields["watch_extractor"] = ctx["custom_js"]
                if params.get("what_to_watch"):
                    tpl_fields["watch_condition"] = ctx["what_to_watch"]
                await tpl_store.update_template(
                    self._config, user_id, tpl_id, **tpl_fields,
                )
        except Exception:
            logger.debug("mirror to template failed", exc_info=True)
        return f"Updated '{w['name']}': {', '.join(changed)}."


class TestWatcherSkill(BaseSkill):
    """Run the extractor once against the watcher's URL and report the result."""

    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "test_watcher"

    @property
    def category(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Test a watcher once — runs its JS extractor against its URL and "
            "shows what came back, without modifying state. Use when the user "
            "says 'test the watcher', 'what would it find right now', "
            "'check that the extractor works'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "watcher": {
                    "type": "string",
                    "description": "Watcher name (partial match) or ID prefix.",
                },
            },
            "required": ["watcher"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        w = await _find_watcher(self._config, user_id, params.get("watcher", ""))
        if w is None:
            return f"No watcher matching '{params.get('watcher')}'."

        import json
        try:
            ctx = json.loads(w.get("context") or "{}")
        except (json.JSONDecodeError, TypeError):
            ctx = {}
        probe = dict(ctx)
        probe["last_value"] = None

        from lazyclaw.browser.browser_settings import touch_browser_activity
        from lazyclaw.browser.cdp_backend import CDPBackend
        from lazyclaw.browser.watcher import check_watcher

        touch_browser_activity()
        backend = CDPBackend(user_id=user_id)
        try:
            _changed, _notification, new_ctx = await check_watcher(backend, probe)
        except Exception as exc:
            return f"Extractor failed on {ctx.get('url', '?')}: {exc}"

        value = new_ctx.get("last_value") or "(empty)"
        if isinstance(value, str) and len(value) > 400:
            value = value[:400] + "…"
        return (
            f"Test run of '{w['name']}':\n"
            f"  URL: {ctx.get('url', '?')}\n"
            f"  Extracted: {value}"
        )
