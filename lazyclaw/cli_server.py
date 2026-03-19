"""Server-mode live dashboard for terminal visibility.

Shows real-time agent activity when running `lazyclaw start`:
active requests, tool calls, specialist work, completions.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from lazyclaw.runtime.callbacks import AgentEvent


@dataclass
class _ActiveRequest:
    """Tracks state of a single in-flight request."""

    chat_id: str
    message: str
    started: float = field(default_factory=time.monotonic)
    phase: str = "queued"
    model: str = ""
    iteration: int = 0
    tools_used: list[str] = field(default_factory=list)
    specialists: dict[str, str] = field(default_factory=dict)  # name -> status
    specialist_count_done: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    step_current: int = 0
    step_total: int = 0
    trigger: str = "user"
    delegate_to: str = ""


@dataclass(frozen=True)
class _LogEntry:
    """Single activity log line."""

    timestamp: str
    kind: str
    detail: str


class _RequestCallback:
    """Per-request callback that updates the shared dashboard."""

    def __init__(self, dashboard: ServerDashboard, chat_id: str) -> None:
        self._dashboard = dashboard
        self._chat_id = chat_id

    async def on_event(self, event: AgentEvent) -> None:
        self._dashboard.handle_event(self._chat_id, event)

    async def on_approval_request(
        self, skill_name: str, arguments: dict
    ) -> bool:
        return True  # auto-approve in server mode


class ServerDashboard:
    """Shared server dashboard tracking all active Telegram requests.

    Thread-safe for single asyncio event loop (no locks needed).
    """

    def __init__(self) -> None:
        self._active: dict[str, _ActiveRequest] = {}
        self._log: deque[_LogEntry] = deque(maxlen=15)
        self._total_processed: int = 0
        self._started: float = time.monotonic()

    def make_request_cb(self, chat_id: str) -> _RequestCallback:
        """Create a per-request callback bound to this dashboard."""
        return _RequestCallback(self, chat_id)

    def register_request(self, chat_id: str, message: str) -> None:
        """Called when a new Telegram message arrives."""
        self._active[chat_id] = _ActiveRequest(
            chat_id=chat_id,
            message=message[:50],
        )
        self._log.append(_LogEntry(
            timestamp=_now(),
            kind="new",
            detail=f'"{message[:40]}"',
        ))

    def unregister_request(self, chat_id: str) -> None:
        """Called when a request completes."""
        req = self._active.pop(chat_id, None)
        self._total_processed += 1
        if req:
            elapsed = time.monotonic() - req.started
            tools = len(req.tools_used)
            mode = "team" if req.specialists else "direct"
            self._log.append(_LogEntry(
                timestamp=_now(),
                kind="done",
                detail=f'"{req.message[:30]}" {elapsed:.1f}s {tools} tools ({mode})',
            ))

    def handle_event(self, chat_id: str, event: AgentEvent) -> None:
        """Process an agent event for a specific request."""
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
            self._log.append(_LogEntry(_now(), "tool", display))

        elif kind == "tool_result":
            elapsed = event.metadata.get("duration_ms", 0)
            suffix = f" ({elapsed / 1000:.1f}s)" if elapsed else ""
            self._log.append(_LogEntry(_now(), "result", f"{display}{suffix}"))

        elif kind == "team_start":
            req.phase = "team"
            specialists = event.metadata.get("specialists", [])
            req.specialists = {name: "queued" for name in specialists}
            req.specialist_count_done = 0

        elif kind == "specialist_start":
            name = event.metadata.get("specialist", "?")
            if name in req.specialists:
                req.specialists[name] = "running"
            self._log.append(_LogEntry(_now(), "start", name))

        elif kind == "specialist_tool":
            name = event.metadata.get("specialist", "?")
            tool = event.metadata.get("tool", "?")
            self._log.append(_LogEntry(_now(), "spec", f"{name} -> {tool}"))

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
                self._log.append(_LogEntry(
                    _now(), "summary",
                    f"{dur:.1f}s | {summary.llm_calls} LLM | {tools_str}",
                ))

    def render(self):
        """Build Rich renderable for the live display."""
        uptime = int(time.monotonic() - self._started)
        hours, remainder = divmod(uptime, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            uptime_str = f"{hours}h {minutes}m"
        elif minutes:
            uptime_str = f"{minutes}m {seconds}s"
        else:
            uptime_str = f"{seconds}s"

        active_count = len(self._active)

        # Header
        header = Panel(
            Text.from_markup(
                f"  Uptime: [bold]{uptime_str}[/bold]  |  "
                f"Processed: [bold]{self._total_processed}[/bold]  |  "
                f"Active: [bold {'green' if active_count == 0 else 'yellow'}]"
                f"{active_count}[/bold {'green' if active_count == 0 else 'yellow'}]"
            ),
            title="[bold cyan]LazyClaw Server[/bold cyan]",
            border_style="cyan",
        )

        # Active requests table
        if self._active:
            table = Table(
                show_header=False, expand=True, padding=(0, 1),
                border_style="yellow",
            )
            table.add_column("msg", ratio=3)
            table.add_column("status", ratio=2)
            table.add_column("time", justify="right", ratio=1)

            for req in self._active.values():
                elapsed = int(time.monotonic() - req.started)
                status = _format_phase(req)
                table.add_row(
                    Text(f'"{req.message}"', style="white"),
                    status,
                    Text(f"{elapsed}s", style="dim"),
                )
            active_panel = Panel(
                table,
                title="[bold yellow]Active Requests[/bold yellow]",
                border_style="yellow",
            )
        else:
            active_panel = Panel(
                Text("  Waiting for messages...", style="dim italic"),
                title="[bold yellow]Active Requests[/bold yellow]",
                border_style="dim",
            )

        # Activity log
        if self._log:
            log_lines: list[Text] = []
            for entry in reversed(self._log):
                style = _log_style(entry.kind)
                icon = _log_icon(entry.kind)
                log_lines.append(
                    Text.from_markup(
                        f"  [dim]{entry.timestamp}[/dim]  "
                        f"{icon} [{style}]{entry.kind:<7}[/{style}]  "
                        f"{entry.detail}"
                    )
                )
            log_panel = Panel(
                Group(*log_lines),
                title="[bold green]Activity Log[/bold green]",
                border_style="green",
            )
        else:
            log_panel = Panel(
                Text("  No activity yet", style="dim italic"),
                title="[bold green]Activity Log[/bold green]",
                border_style="dim",
            )

        return Group(header, active_panel, log_panel)


def _now() -> str:
    """Current local time as HH:MM:SS."""
    return datetime.now().strftime("%H:%M:%S")


def _format_phase(req: _ActiveRequest) -> Text:
    """Format request phase for display."""
    if req.phase == "thinking":
        return Text.from_markup(
            f"[cyan]thinking[/cyan] ({req.model}, step {req.iteration})"
        )
    if req.phase == "tool":
        last = req.tools_used[-1] if req.tools_used else "?"
        return Text.from_markup(f"[blue]tool[/blue] {last}")
    if req.phase == "team":
        total = len(req.specialists)
        done = req.specialist_count_done
        return Text.from_markup(f"[magenta]team[/magenta] [{done}/{total} done]")
    if req.phase == "merging":
        return Text.from_markup("[magenta]merging results...[/magenta]")
    if req.phase == "streaming":
        return Text.from_markup("[green]writing response...[/green]")
    return Text.from_markup(f"[dim]{req.phase}[/dim]")


def _log_style(kind: str) -> str:
    """Color style for log entry kind."""
    return {
        "new": "bold white",
        "tool": "blue",
        "result": "green",
        "start": "magenta",
        "spec": "magenta",
        "done": "bold green",
        "summary": "cyan",
    }.get(kind, "dim")


def _log_icon(kind: str) -> str:
    """Icon for log entry kind."""
    return {
        "new": ">>",
        "tool": "//",
        "result": "ok",
        "start": "++",
        "spec": "->",
        "done": "<<",
        "summary": "##",
    }.get(kind, "  ")
