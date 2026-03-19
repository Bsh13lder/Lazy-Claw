"""Textual TUI dashboard for `lazyclaw start`.

Full interactive terminal dashboard replacing the basic Rich Live panel.
Shows real-time agent activity, system overview, scrollable logs,
and admin input — all while running FastAPI + Telegram + Heartbeat.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

from rich.text import Text

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.widgets import Footer, Header, Input, RichLog, Static

from lazyclaw.cli_server import _ActiveRequest
from lazyclaw.config import Config
from lazyclaw.runtime.callbacks import AgentEvent

logger = logging.getLogger(__name__)


# ── Data models ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class RequestSnapshot:
    """Immutable snapshot of a request's state for rendering."""

    chat_id: str
    message: str
    phase: str
    model: str
    iteration: int
    tools_used: tuple[str, ...]
    specialists: tuple[tuple[str, str], ...]  # ((name, status), ...)
    elapsed_s: float


@dataclass(frozen=True)
class SystemStats:
    """Immutable system overview."""

    uptime_s: int
    total_processed: int
    active_count: int
    queue_depth: int
    cron_jobs: int
    watchers: int
    browser_mode: str
    browser_alive: bool
    mcp_count: int
    memory_mb: float


# ── Textual Messages ────────────────────────────────────────────────

class RequestRegistered(Message):
    def __init__(self, chat_id: str, user_message: str) -> None:
        super().__init__()
        self.chat_id = chat_id
        self.user_message = user_message


class RequestUpdated(Message):
    def __init__(self, chat_id: str, snapshot: RequestSnapshot) -> None:
        super().__init__()
        self.chat_id = chat_id
        self.snapshot = snapshot


class RequestCompleted(Message):
    def __init__(self, chat_id: str, summary: str) -> None:
        super().__init__()
        self.chat_id = chat_id
        self.summary = summary


class LogAppended(Message):
    def __init__(self, timestamp: str, kind: str, detail: str) -> None:
        super().__init__()
        self.timestamp = timestamp
        self.kind = kind
        self.detail = detail


class StatsRefreshed(Message):
    def __init__(self, stats: SystemStats) -> None:
        super().__init__()
        self.stats = stats


# ── TuiDashboard (bridge: agent events → Textual messages) ─────────

class TuiDashboard:
    """Drop-in replacement for ServerDashboard.

    Same interface so TelegramAdapter works unchanged.
    Posts immutable snapshots to the Textual app.
    """

    def __init__(self, app: LazyClawApp) -> None:
        self._app = app
        self._active: dict[str, _ActiveRequest] = {}
        self._total_processed: int = 0
        self._started: float = time.monotonic()

    def make_request_cb(self, chat_id: str) -> _TuiRequestCallback:
        return _TuiRequestCallback(self, chat_id)

    def register_request(self, chat_id: str, message: str) -> None:
        self._active[chat_id] = _ActiveRequest(
            chat_id=chat_id, message=message[:50],
        )
        self._app.post_message(RequestRegistered(chat_id, message[:50]))
        self._app.post_message(LogAppended(_now(), "new", f'"{message[:40]}"'))

    def unregister_request(self, chat_id: str) -> None:
        req = self._active.pop(chat_id, None)
        self._total_processed += 1
        if req:
            elapsed = time.monotonic() - req.started
            tools = len(req.tools_used)
            self._app.post_message(RequestCompleted(
                chat_id,
                f'"{req.message[:30]}" {elapsed:.1f}s {tools} tools',
            ))

    def handle_event(self, chat_id: str, event: AgentEvent) -> None:
        req = self._active.get(chat_id)
        if not req:
            return

        kind = event.kind
        display = event.metadata.get("display_name", event.detail)

        if kind == "llm_call":
            req.phase = "thinking"
            req.model = event.metadata.get("model", "?")
            req.iteration = event.metadata.get("iteration", 1)

        elif kind == "tool_call":
            req.phase = "tool"
            req.tools_used.append(display)
            self._app.post_message(LogAppended(_now(), "tool", display))

        elif kind == "tool_result":
            elapsed_ms = event.metadata.get("duration_ms", 0)
            suffix = f" ({elapsed_ms / 1000:.1f}s)" if elapsed_ms else ""
            self._app.post_message(LogAppended(_now(), "result", f"{display}{suffix}"))

        elif kind == "team_start":
            req.phase = "team"
            specialists = event.metadata.get("specialists", [])
            req.specialists = {name: "queued" for name in specialists}
            req.specialist_count_done = 0

        elif kind == "specialist_start":
            name = event.metadata.get("specialist", "?")
            if name in req.specialists:
                req.specialists[name] = "running"
            self._app.post_message(LogAppended(_now(), "spec", f"{name} started"))

        elif kind == "specialist_tool":
            name = event.metadata.get("specialist", "?")
            tool = event.metadata.get("tool", "?")
            self._app.post_message(LogAppended(_now(), "spec", f"{name} -> {tool}"))

        elif kind == "specialist_done":
            name = event.metadata.get("specialist", "?")
            success = event.metadata.get("success", True)
            if name in req.specialists:
                req.specialists[name] = "done" if success else "error"
            req.specialist_count_done += 1

        elif kind == "team_merge":
            req.phase = "merging"

        elif kind == "token":
            req.phase = "streaming"

        elif kind == "work_summary":
            summary = event.metadata.get("summary")
            if summary:
                dur = summary.duration_ms / 1000
                tools_str = ", ".join(summary.tools_used) if summary.tools_used else "none"
                self._app.post_message(LogAppended(
                    _now(), "done",
                    f"{dur:.1f}s | {summary.llm_calls} LLM | {tools_str}",
                ))

        # Post snapshot after every event
        self._app.post_message(RequestUpdated(chat_id, self._snapshot(req)))

    def _snapshot(self, req: _ActiveRequest) -> RequestSnapshot:
        return RequestSnapshot(
            chat_id=req.chat_id,
            message=req.message,
            phase=req.phase,
            model=req.model,
            iteration=req.iteration,
            tools_used=tuple(req.tools_used),
            specialists=tuple(req.specialists.items()),
            elapsed_s=time.monotonic() - req.started,
        )

    @property
    def total_processed(self) -> int:
        return self._total_processed

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def uptime_s(self) -> int:
        return int(time.monotonic() - self._started)


class _TuiRequestCallback:
    """Per-request callback for TuiDashboard."""

    def __init__(self, dashboard: TuiDashboard, chat_id: str) -> None:
        self._dashboard = dashboard
        self._chat_id = chat_id

    async def on_event(self, event: AgentEvent) -> None:
        self._dashboard.handle_event(self._chat_id, event)

    async def on_approval_request(
        self, skill_name: str, arguments: dict,
    ) -> bool:
        return True  # auto-approve in server mode


# ── Widgets ─────────────────────────────────────────────────────────

class SystemBar(Static):
    """Top bar showing system stats."""

    def update_stats(self, stats: SystemStats) -> None:
        hours, rem = divmod(stats.uptime_s, 3600)
        minutes, secs = divmod(rem, 60)
        up = f"{hours}h{minutes}m" if hours else f"{minutes}m{secs}s"

        browser_icon = "ok" if stats.browser_alive else "--"
        browser = f"{stats.browser_mode}({browser_icon})"

        active_style = "green" if stats.active_count == 0 else "yellow"

        self.update(Text.from_markup(
            f" Up:[bold]{up}[/bold]"
            f"  Done:[bold]{stats.total_processed}[/bold]"
            f"  Active:[{active_style}]{stats.active_count}[/{active_style}]"
            f"  Q:[bold]{stats.queue_depth}[/bold]"
            f"  Cron:[bold]{stats.cron_jobs}[/bold]"
            f"  Watch:[bold]{stats.watchers}[/bold]"
            f"  Browser:[bold]{browser}[/bold]"
            f"  MCP:[bold]{stats.mcp_count}[/bold]"
            f"  Mem:[bold]{stats.memory_mb:.0f}MB[/bold]"
        ))


class RequestCard(Static):
    """Displays a single active request's live state."""

    def __init__(self, chat_id: str, message: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._chat_id = chat_id
        self._message = message
        self.update(Text.from_markup(f'  [bold]"{message}"[/bold]\n  [dim]queued...[/dim]'))

    def update_snapshot(self, snap: RequestSnapshot) -> None:
        elapsed = f"{snap.elapsed_s:.1f}s"
        phase_text = _format_phase_markup(snap)
        tools = ", ".join(snap.tools_used[-4:]) if snap.tools_used else ""

        lines = [f'  [bold]"{snap.message}"[/bold]']
        lines.append(f"  {phase_text}  [dim]{elapsed}[/dim]")

        if tools:
            lines.append(f"  [dim]Tools: {tools}[/dim]")

        # Specialist grid
        if snap.specialists:
            spec_parts = []
            for name, status in snap.specialists:
                icon = {"queued": ">>", "running": "~~", "done": "ok", "error": "!!"}
                style = {"queued": "dim", "running": "cyan", "done": "green", "error": "red"}
                spec_parts.append(
                    f"[{style.get(status, 'dim')}]"
                    f"{icon.get(status, '??')} {name}[/{style.get(status, 'dim')}]"
                )
            lines.append("  " + "  ".join(spec_parts))

        self.update(Text.from_markup("\n".join(lines)))


class ActivityPanel(VerticalScroll):
    """Scrollable panel of active request cards."""

    _empty_label: Static | None = None

    def on_mount(self) -> None:
        self._empty_label = Static(
            Text("  Waiting for messages...", style="dim italic"),
            id="empty-label",
        )
        self.mount(self._empty_label)

    def add_request(self, chat_id: str, message: str) -> None:
        if self._empty_label:
            self._empty_label.remove()
            self._empty_label = None
        card = RequestCard(chat_id, message, id=f"req-{chat_id}")
        self.mount(card)
        card.scroll_visible()

    def update_request(self, chat_id: str, snapshot: RequestSnapshot) -> None:
        try:
            card = self.query_one(f"#req-{chat_id}", RequestCard)
            card.update_snapshot(snapshot)
        except Exception:
            pass

    def remove_request(self, chat_id: str) -> None:
        try:
            card = self.query_one(f"#req-{chat_id}", RequestCard)
            card.remove()
        except Exception:
            pass
        # Restore empty label if no cards left
        if not self.query(RequestCard):
            self._empty_label = Static(
                Text("  Waiting for messages...", style="dim italic"),
                id="empty-label",
            )
            self.mount(self._empty_label)


class LogPanel(RichLog):
    """Scrollable system log."""

    def append_entry(self, timestamp: str, kind: str, detail: str) -> None:
        style = _log_style(kind)
        icon = _log_icon(kind)
        self.write(Text.from_markup(
            f"[dim]{timestamp}[/dim] {icon} [{style}]{kind:<7}[/{style}] {detail}"
        ))


# ── Main App ────────────────────────────────────────────────────────

class LazyClawApp(App):
    """LazyClaw server TUI dashboard."""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2;
        grid-columns: 3fr 1fr;
        grid-rows: 3 1fr 3;
    }

    #system-bar {
        column-span: 2;
        height: 3;
        background: $surface;
        border: solid $primary;
        content-align: left middle;
        padding: 0 1;
    }

    #activity-panel {
        height: 100%;
        border: solid $accent;
    }

    #log-panel {
        height: 100%;
        border: solid $success;
    }

    #admin-input {
        column-span: 2;
        dock: bottom;
    }

    RequestCard {
        height: auto;
        margin: 0 0 1 0;
        padding: 0;
    }
    """

    TITLE = "LazyClaw Server"
    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
    ]

    def __init__(
        self,
        config: Config,
        agent,
        lane_queue,
        registry,
        task_runner,
        telegram_token: str | None = None,
        permission_checker=None,
        default_user_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._agent = agent
        self._lane_queue = lane_queue
        self._skill_registry = registry
        self._task_runner = task_runner
        self._telegram_token = telegram_token
        self._permission_checker = permission_checker
        self._user_id = default_user_id
        self.dashboard = TuiDashboard(self)

    def compose(self) -> ComposeResult:
        yield Header()
        yield SystemBar(" Starting...", id="system-bar")
        yield ActivityPanel(id="activity-panel")
        yield LogPanel(id="log-panel", highlight=True, markup=True)
        yield Input(placeholder="Type message or /command...", id="admin-input")
        yield Footer()

    def on_mount(self) -> None:
        """Launch all services as background workers."""
        self._post_log("info", "LazyClaw TUI starting...")
        self._launch_services()
        self.set_interval(2.0, self._refresh_stats)

    @work(exclusive=True, name="services")
    async def _launch_services(self) -> None:
        """Start uvicorn, Telegram, heartbeat as concurrent tasks."""
        try:
            import uvicorn

            tasks: list = []

            # Uvicorn
            uvi_config = uvicorn.Config(
                "lazyclaw.gateway.app:app",
                host="0.0.0.0",
                port=self._config.port,
                log_level="warning",
            )
            server = uvicorn.Server(uvi_config)
            tasks.append(server.serve())

            # Telegram
            if self._telegram_token:
                from lazyclaw.channels.telegram import TelegramAdapter

                telegram = TelegramAdapter(
                    self._telegram_token, self._agent, self._config,
                    lane_queue=self._lane_queue,
                    server_dashboard=self.dashboard,
                )
                await telegram.start()
                self._post_log("info", "Telegram bot running")

            # Heartbeat
            from lazyclaw.heartbeat.daemon import HeartbeatDaemon

            heartbeat = HeartbeatDaemon(self._config, self._lane_queue)
            await heartbeat.start()
            self._post_log("info", "Heartbeat daemon started")

            # Persistent browser
            await self._init_persistent_browser()

            self._post_log("info", f"API on http://localhost:{self._config.port}")

            # Run uvicorn (blocks until shutdown)
            await asyncio.gather(*tasks)
        except Exception as exc:
            self._post_log("error", f"Service startup failed: {exc}")
            logger.exception("TUI service startup failed")

    async def _init_persistent_browser(self) -> None:
        """Launch persistent browser if mode is 'on'."""
        try:
            from lazyclaw.browser.browser_settings import get_browser_settings

            user_id = self._user_id
            cfg = await get_browser_settings(self._config, user_id)
            mode = cfg.get("persistent", "auto")

            if mode == "on":
                from lazyclaw.browser.cdp import find_chrome_cdp
                from lazyclaw.browser.cdp_backend import CDPBackend

                port = getattr(self._config, "cdp_port", 9222)
                if not await find_chrome_cdp(port):
                    profile_dir = str(
                        self._config.database_dir / "browser_profiles" / user_id
                    )
                    backend = CDPBackend(port=port, profile_dir=profile_dir)
                    ws_url = await backend._auto_launch_chrome()
                    if ws_url:
                        self._post_log("info", "Persistent browser running")
        except Exception:
            pass

    async def _refresh_stats(self) -> None:
        """Periodic system stats refresh (every 2s)."""
        try:
            import resource

            from lazyclaw.browser.browser_settings import get_browser_settings
            from lazyclaw.browser.cdp import find_chrome_cdp
    
            from lazyclaw.db.connection import db_session

            mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)

            queue_depth = sum(
                q.qsize() for q in self._lane_queue._lanes.values()
            )

            # MCP count
            mcp_count = 0
            try:
                from lazyclaw.mcp.manager import _active_clients
                mcp_count = len(_active_clients)
            except Exception:
                pass

            # Browser
            port = getattr(self._config, "cdp_port", 9222)
            browser_alive = bool(await find_chrome_cdp(port))

            user_id = self._user_id
            browser_cfg = await get_browser_settings(self._config, user_id)

            # Job counts
            cron_count = 0
            watcher_count = 0
            async with db_session(self._config) as db:
                cursor = await db.execute(
                    "SELECT job_type, COUNT(*) FROM agent_jobs "
                    "WHERE status = 'active' GROUP BY job_type"
                )
                for row in await cursor.fetchall():
                    if row[0] == "cron":
                        cron_count = row[1]
                    elif row[0] == "watcher":
                        watcher_count = row[1]

            stats = SystemStats(
                uptime_s=self.dashboard.uptime_s,
                total_processed=self.dashboard.total_processed,
                active_count=self.dashboard.active_count,
                queue_depth=queue_depth,
                cron_jobs=cron_count,
                watchers=watcher_count,
                browser_mode=browser_cfg.get("persistent", "auto"),
                browser_alive=browser_alive,
                mcp_count=mcp_count,
                memory_mb=mem_mb,
            )
            self.post_message(StatsRefreshed(stats))
        except Exception:
            pass

    # ── Message handlers ─────────────────────────────────────────────

    def on_request_registered(self, msg: RequestRegistered) -> None:
        panel = self.query_one("#activity-panel", ActivityPanel)
        panel.add_request(msg.chat_id, msg.user_message)

    def on_request_updated(self, msg: RequestUpdated) -> None:
        panel = self.query_one("#activity-panel", ActivityPanel)
        panel.update_request(msg.chat_id, msg.snapshot)

    def on_request_completed(self, msg: RequestCompleted) -> None:
        panel = self.query_one("#activity-panel", ActivityPanel)
        panel.remove_request(msg.chat_id)
        self._post_log("done", msg.summary)

    def on_log_appended(self, msg: LogAppended) -> None:
        log = self.query_one("#log-panel", LogPanel)
        log.append_entry(msg.timestamp, msg.kind, msg.detail)

    def on_stats_refreshed(self, msg: StatsRefreshed) -> None:
        bar = self.query_one("#system-bar", SystemBar)
        bar.update_stats(msg.stats)

    # ── Admin input ──────────────────────────────────────────────────

    @on(Input.Submitted, "#admin-input")
    async def on_admin_input(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""

        if not text:
            return

        if text == "/clear":
            self.query_one("#log-panel", LogPanel).clear()
            return

        if text == "/jobs":
            await self._show_jobs()
            return

        if text == "/watchers":
            await self._show_watchers()
            return

        if text == "/status":
            self._post_log("info", f"Up:{self.dashboard.uptime_s}s "
                          f"Done:{self.dashboard.total_processed} "
                          f"Active:{self.dashboard.active_count}")
            return

        # Enqueue as admin message
        try:
    
            user_id = self._user_id
            self._post_log("admin", f"Sent: {text[:40]}")
            asyncio.create_task(self._lane_queue.enqueue(user_id, text))
        except Exception as exc:
            self._post_log("error", f"Failed: {exc}")

    async def _show_jobs(self) -> None:
        """Show active cron jobs in log panel."""

        from lazyclaw.heartbeat.orchestrator import list_jobs

        user_id = self._user_id
        jobs = await list_jobs(self._config, user_id)
        crons = [j for j in jobs if j.get("job_type") == "cron"
                 and j.get("status") == "active"]

        if not crons:
            self._post_log("info", "No active cron jobs")
            return

        for j in crons:
            self._post_log("info", f"  {j.get('name', '?')} | {j.get('cron_expression', '?')}")

    async def _show_watchers(self) -> None:
        """Show active watchers in log panel."""
        import json


        from lazyclaw.heartbeat.orchestrator import list_jobs

        user_id = self._user_id
        jobs = await list_jobs(self._config, user_id)
        watchers = [j for j in jobs if j.get("job_type") == "watcher"
                    and j.get("status") == "active"]

        if not watchers:
            self._post_log("info", "No active watchers")
            return

        for w in watchers:
            ctx = {}
            try:
                ctx = json.loads(w.get("context", "{}"))
            except Exception:
                pass
            url = ctx.get("url", "?")
            self._post_log("info", f"  {w.get('name', '?')} | {url}")

    def _post_log(self, kind: str, detail: str) -> None:
        """Post a log entry from within the app."""
        self.post_message(LogAppended(_now(), kind, detail))

    async def action_quit(self) -> None:
        """Graceful shutdown."""
        self._post_log("info", "Shutting down...")
        try:
            await self._task_runner.cancel_all()
            await self._lane_queue.stop()
            from lazyclaw.mcp.manager import disconnect_all
            await asyncio.wait_for(disconnect_all(), timeout=3)
            from lazyclaw.db.connection import close_pool
            await close_pool()
        except Exception:
            pass
        self.exit()


# ── Helpers ─────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _format_phase_markup(snap: RequestSnapshot) -> str:
    if snap.phase == "thinking":
        return f"[cyan]thinking[/cyan] ({snap.model}, step {snap.iteration})"
    if snap.phase == "tool":
        last = snap.tools_used[-1] if snap.tools_used else "?"
        return f"[blue]tool[/blue] {last}"
    if snap.phase == "team":
        total = len(snap.specialists)
        done = sum(1 for _, s in snap.specialists if s in ("done", "error"))
        return f"[magenta]team[/magenta] [{done}/{total}]"
    if snap.phase == "merging":
        return "[magenta]merging...[/magenta]"
    if snap.phase == "streaming":
        return "[green]writing...[/green]"
    return f"[dim]{snap.phase}[/dim]"


def _log_style(kind: str) -> str:
    return {
        "new": "bold white",
        "tool": "blue",
        "result": "green",
        "start": "magenta",
        "spec": "magenta",
        "done": "bold green",
        "info": "cyan",
        "admin": "yellow",
        "error": "red",
    }.get(kind, "dim")


def _log_icon(kind: str) -> str:
    return {
        "new": ">>",
        "tool": "//",
        "result": "ok",
        "start": "++",
        "spec": "->",
        "done": "<<",
        "info": "**",
        "admin": ">>",
        "error": "!!",
    }.get(kind, "  ")
