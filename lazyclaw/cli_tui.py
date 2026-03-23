"""Textual TUI dashboard for `lazyclaw start`.

Full interactive terminal dashboard replacing the basic Rich Live panel.
Shows real-time agent activity, system overview, scrollable logs,
and admin input — all while running FastAPI + Telegram + Heartbeat.
"""

from __future__ import annotations

import asyncio
import json
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
from textual.suggester import SuggestFromList
from textual.widgets import Footer, Header, Input, RichLog, Static

from lazyclaw.cli_server import _ActiveRequest
from lazyclaw.config import Config
from lazyclaw.llm.pricing import calculate_cost
from lazyclaw.runtime.callbacks import AgentEvent
from lazyclaw.runtime.team_lead import TeamLead

logger = logging.getLogger(__name__)

# ── Color palette ─────────────────────────────────────────────────
_C_BORDER = "#6B7280"
_C_HEADER = "#F59E0B"
_C_ACTIVE = "#14B8A6"
_C_SUCCESS = "#84CC16"
_C_ERROR = "#F87171"
_C_THINKING = "#FBBF24"
_C_IDLE = "#9CA3AF"
_C_COST = "#34D399"
_C_SPECIALIST = "#A78BFA"


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
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    step_current: int = 0
    step_total: int = 0
    trigger: str = "user"
    delegate_to: str = ""


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
    total_cost_today: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    cost_by_model: dict[str, float] = field(default_factory=dict)
    browser_tabs: int = 0
    telegram_status: str = "disconnected"
    eco_mode: str = "full"
    ollama_models: tuple[str, ...] = ()


# ── Helpers ─────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _fmt_tokens(n: int) -> str:
    """Format token count: 1200 → '1.2K', 800 → '800'."""
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


def _fmt_cost(c: float) -> str:
    """Format cost as $0.003."""
    if c < 0.001:
        return f"${c:.4f}"
    return f"${c:.3f}"


def _phase_icon(phase: str) -> tuple[str, str]:
    """Return (icon, color) for a phase."""
    return {
        "thinking": ("●", _C_THINKING),
        "tool": ("◆", "#60A5FA"),
        "team": ("◆", _C_SPECIALIST),
        "merging": ("◆", _C_SPECIALIST),
        "dispatched": ("→", _C_SPECIALIST),
        "streaming": ("●", _C_ACTIVE),
        "queued": ("○", _C_IDLE),
        "done": ("✓", _C_SUCCESS),
        "error": ("✗", _C_ERROR),
        "stuck": ("⚠", _C_ERROR),
    }.get(phase, ("○", _C_IDLE))


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
        # Global token/cost accumulators
        self._total_tokens_in: int = 0
        self._total_tokens_out: int = 0
        self._total_cost_today: float = 0.0
        self._cost_by_model: dict[str, float] = {}
        # Dedup: skip consecutive identical log entries
        self._last_log_key: str = ""

    def make_request_cb(self, chat_id: str) -> _TuiRequestCallback:
        return _TuiRequestCallback(self, chat_id)

    def register_request(self, chat_id: str, message: str) -> None:
        self._active[chat_id] = _ActiveRequest(
            chat_id=chat_id, message=message[:50],
        )
        self._app.post_message(RequestRegistered(chat_id, message[:50]))
        # Escape brackets for log panel (uses Rich markup)
        safe_log = message[:40].replace("[", "\\[") if message else ""
        self._app.post_message(LogAppended(_now(), "new", f'"{safe_log}"'))

    def unregister_request(self, chat_id: str) -> None:
        req = self._active.pop(chat_id, None)
        self._total_processed += 1
        if req:
            elapsed = time.monotonic() - req.started
            tools = len(req.tools_used)
            cost_str = _fmt_cost(req.cost_usd)
            self._app.post_message(RequestCompleted(
                chat_id,
                f'"{req.message[:30]}" {elapsed:.1f}s {tools} tools {cost_str}',
            ))

    def handle_event(self, chat_id: str, event: AgentEvent) -> None:
        req = self._active.get(chat_id)
        kind = event.kind

        # Dedup consecutive identical log events (tool_result, tool_call)
        if kind in ("tool_result", "tool_call"):
            log_key = f"{chat_id}:{kind}:{event.detail}"
            if log_key == self._last_log_key:
                return  # Skip duplicate
            self._last_log_key = log_key
        else:
            self._last_log_key = ""

        # Background task events may arrive after the original request was unregistered
        if not req:
            if kind in ("background_done", "background_failed", "fast_dispatch"):
                # Create a temporary request so the event can be displayed
                name = event.metadata.get("name", event.metadata.get("specialist", "background"))
                self.register_request(chat_id, f"[bg] {name}")
                req = self._active.get(chat_id)
            else:
                return
        display = event.metadata.get("display_name", event.detail)

        if kind == "llm_call":
            req.phase = "thinking"
            req.model = event.metadata.get("model", "?")
            req.iteration = event.metadata.get("iteration", 1)
            req.step_current = event.metadata.get("iteration", 1)
            req.step_total = event.metadata.get("max_iterations", 0)
            cost_usd = event.metadata.get("cost_usd", 0.0)
            if cost_usd:
                req.cost_usd += cost_usd
                self._total_cost_today += cost_usd
                model = event.metadata.get("model", "unknown")
                self._cost_by_model[model] = self._cost_by_model.get(model, 0.0) + cost_usd

        elif kind == "tokens":
            # Token usage event from agent runtime
            prompt = event.metadata.get("prompt", 0)
            completion = event.metadata.get("completion", 0)
            model = event.metadata.get("model", "unknown")

            req.tokens_in += prompt
            req.tokens_out += completion
            self._total_tokens_in += prompt
            self._total_tokens_out += completion

            # Calculate cost
            cost = calculate_cost(model, prompt, completion)
            req.cost_usd += cost
            self._total_cost_today += cost
            self._cost_by_model[model] = self._cost_by_model.get(model, 0.0) + cost

            # Log LLM call with token info
            self._app.post_message(LogAppended(
                _now(), "llm",
                f"{model}  ↑{_fmt_tokens(prompt)} ↓{_fmt_tokens(completion)} = {_fmt_cost(cost)}",
            ))

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
            req.delegate_to = name
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
            req.delegate_to = ""

        elif kind == "team_merge":
            req.phase = "merging"

        elif kind == "token":
            req.phase = "streaming"

        elif kind == "fast_dispatch":
            req.phase = "dispatched"
            specialist = event.metadata.get("specialist", "?")
            req.delegate_to = specialist
            self._app.post_message(LogAppended(
                _now(), "dispatch", f"→ background ({specialist})",
            ))

        elif kind == "background_done":
            req.phase = "done"
            name = event.metadata.get("name", "?")
            self._app.post_message(LogAppended(
                _now(), "done", f"Background '{name}' completed",
            ))
            # Post final snapshot then remove card
            self._app.post_message(RequestUpdated(chat_id, self._snapshot(req)))
            self.unregister_request(chat_id)
            return  # already posted snapshot + removed

        elif kind == "background_failed":
            req.phase = "error"
            name = event.metadata.get("name", "?")
            error = event.metadata.get("error", "unknown")
            self._app.post_message(LogAppended(
                _now(), "error", f"Background '{name}' failed: {error}",
            ))
            # Post final snapshot then remove card
            self._app.post_message(RequestUpdated(chat_id, self._snapshot(req)))
            self.unregister_request(chat_id)
            return  # already posted snapshot + removed

        elif kind == "help_needed":
            req.phase = "stuck"
            reason = event.metadata.get("reason", "unknown")
            tool = event.metadata.get("tool", "?")
            self._app.post_message(LogAppended(
                _now(), "error",
                f"STUCK: {reason} on {tool} — {event.detail[:60]}",
            ))

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
            tokens_in=req.tokens_in,
            tokens_out=req.tokens_out,
            cost_usd=req.cost_usd,
            step_current=req.step_current,
            step_total=req.step_total,
            trigger=req.trigger,
            delegate_to=req.delegate_to,
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

    @property
    def total_tokens_in(self) -> int:
        return self._total_tokens_in

    @property
    def total_tokens_out(self) -> int:
        return self._total_tokens_out

    @property
    def total_cost_today(self) -> float:
        return self._total_cost_today

    @property
    def cost_by_model(self) -> dict[str, float]:
        return dict(self._cost_by_model)


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
    """Top bar showing model names, ECO mode, services, cost, and tokens."""

    def update_stats(self, stats: SystemStats, config=None) -> None:
        cost = _fmt_cost(stats.total_cost_today)
        t_in = _fmt_tokens(stats.total_tokens_in)
        t_out = _fmt_tokens(stats.total_tokens_out)
        active_color = _C_ACTIVE if stats.active_count > 0 else _C_IDLE

        # ECO mode badge
        eco = stats.eco_mode.upper()
        eco_color = _C_SUCCESS if eco in ("LOCAL", "ECO") else (_C_ACTIVE if eco == "HYBRID" else _C_IDLE)

        # Model names from config
        brain = ""
        worker = ""
        if config:
            brain = getattr(config, "brain_model", "").split("/")[-1]
            worker = getattr(config, "worker_model", "").split("/")[-1]

        # Service status indicators
        tg_dot = f"[{_C_SUCCESS}]\u2713[/{_C_SUCCESS}]" if stats.telegram_status == "connected" else f"[{_C_ERROR}]\u2717[/{_C_ERROR}]"
        br_dot = f"[{_C_SUCCESS}]\u2713[/{_C_SUCCESS}]" if stats.browser_alive else f"[{_C_IDLE}]\u2014[/{_C_IDLE}]"
        mcp_dot = f"[{_C_SUCCESS}]{stats.mcp_count}[/{_C_SUCCESS}]" if stats.mcp_count > 0 else f"[{_C_IDLE}]0[/{_C_IDLE}]"

        # Line 1: Mode + Models + Cost
        line1_parts = [f"[{eco_color}]{eco}[/{eco_color}]"]
        if brain:
            line1_parts.append(f"Brain:[bold]{brain}[/bold]")
        if worker:
            line1_parts.append(f"Worker:[dim]{worker}[/dim]")
        line1_parts.append(f"[{_C_COST}]{cost}[/{_C_COST}]")
        line1_parts.append(f"\u2191{t_in} \u2193{t_out}")
        line1_parts.append(f"[{active_color}]{stats.active_count} active[/{active_color}]")

        # Line 2: Services
        line2 = (
            f"TG:{tg_dot}  Browser:{br_dot}"
            f"  MCP:{mcp_dot}"
            f"  Q:{stats.queue_depth}"
            f"  Mem:{stats.memory_mb:.0f}MB"
        )

        self.update(Text.from_markup(
            " " + "  \u2502  ".join(line1_parts) + "\n " + line2
        ))


class RequestCard(Static):
    """Displays a single active request's live state.

    ╭─ #1 "check my whatsapp" ─────────────────╮
    │ ● thinking  gpt-5-mini  step 2            │
    │ ████████░░░░ 3/7 steps                    │
    │ Tools: browser, memory                    │
    │ ↑4.2K ↓1.1K  $0.003  24.1s               │
    ╰──────────────────────────────────────────╯
    """

    _counter: int = 0

    def __init__(self, chat_id: str, message: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._chat_id = chat_id
        self._message = message or ""
        RequestCard._counter += 1
        self._number = RequestCard._counter
        self.update(self._render_initial())

    def _render_initial(self) -> Text:
        # Build Text manually to avoid Rich markup parsing of user content
        t = Text()
        t.append("╭─ ", style=_C_BORDER)
        t.append(f"#{self._number}", style=_C_HEADER)
        safe_msg = self._message.replace("[", "\\[") if self._message else ""
        t.append(f' "{safe_msg}"\n')
        t.append("│ ", style=_C_BORDER)
        t.append("○ queued...", style=_C_IDLE)
        t.append("\n")
        t.append(f"╰{'─' * 44}╯", style=_C_BORDER)
        return t

    def update_snapshot(self, snap: RequestSnapshot) -> None:
        icon, color = _phase_icon(snap.phase)
        elapsed = f"{snap.elapsed_s:.1f}s"
        t_in = _fmt_tokens(snap.tokens_in)
        t_out = _fmt_tokens(snap.tokens_out)
        cost = _fmt_cost(snap.cost_usd)

        # Color-coded border by status
        border_color = {
            "done": _C_SUCCESS, "error": _C_ERROR, "stuck": _C_ERROR,
            "queued": _C_IDLE,
        }.get(snap.phase, _C_ACTIVE if snap.phase in ("thinking", "tool", "team", "streaming") else _C_BORDER)

        # Escape user message for Rich markup
        safe_msg = snap.message.replace("[", "\\[") if snap.message else ""
        # Show task description, not just "request #N"
        display_msg = safe_msg[:50] + "..." if len(safe_msg) > 50 else safe_msg

        # Header
        lines = [
            f"[{border_color}]╭─[/{border_color}]"
            f" [{_C_HEADER}]#{self._number}[/{_C_HEADER}]"
            f' "{display_msg}"'
        ]

        # Phase line with model shortname
        phase_label = snap.phase
        model_short = snap.model.split("/")[-1] if snap.model else ""
        step_str = f"  step {snap.step_current}" if snap.step_current else ""
        lines.append(
            f"[{border_color}]│[/{border_color}]"
            f" [{color}]{icon} {phase_label}[/{color}]"
            f"  [dim]{model_short}[/dim]{step_str}"
        )

        # Delegate chain
        if snap.delegate_to:
            lines.append(
                f"[{border_color}]│[/{border_color}]"
                f" [{_C_SPECIALIST}]\u25cf delegate \u2192 {snap.delegate_to}[/{_C_SPECIALIST}]"
            )

        # Progress bar when step_total > 0
        if snap.step_total > 0:
            filled = min(snap.step_current, snap.step_total)
            bar_width = 16
            filled_chars = int(bar_width * filled / snap.step_total)
            empty_chars = bar_width - filled_chars
            bar = "\u2588" * filled_chars + "\u2591" * empty_chars
            lines.append(
                f"[{border_color}]│[/{border_color}]"
                f" [{_C_ACTIVE}]{bar}[/{_C_ACTIVE}]"
                f" {filled}/{snap.step_total} steps"
            )

        # Tools
        if snap.tools_used:
            unique_tools = list(dict.fromkeys(snap.tools_used[-6:]))
            tools_str = ", ".join(unique_tools)
            lines.append(
                f"[{border_color}]│[/{border_color}]"
                f" [dim]Tools: {tools_str}[/dim]"
            )

        # Specialist grid
        if snap.specialists:
            spec_parts = []
            for name, status in snap.specialists:
                s_icon, s_color = {
                    "queued": ("\u25cb", _C_IDLE),
                    "running": ("\u25cf", _C_ACTIVE),
                    "done": ("\u2713", _C_SUCCESS),
                    "error": ("\u2717", _C_ERROR),
                }.get(status, ("\u25cb", _C_IDLE))
                spec_parts.append(f"[{s_color}]{s_icon} {name}[/{s_color}]")
            lines.append(
                f"[{border_color}]│[/{border_color}]"
                f"  {'  '.join(spec_parts)}"
            )

        # Token/cost/time line
        trigger_badge = ""
        if snap.trigger != "user":
            trigger_badge = f" [{_C_SPECIALIST}][{snap.trigger}][/{_C_SPECIALIST}]"
        lines.append(
            f"[{border_color}]│[/{border_color}]"
            f" \u2191{t_in} \u2193{t_out}"
            f"  [{_C_COST}]{cost}[/{_C_COST}]"
            f"  [dim]{elapsed}[/dim]"
            f"{trigger_badge}"
        )

        # Footer
        horiz_line = "\u2500" * 44
        lines.append(f"[{border_color}]\u2570{horiz_line}\u256f[/{border_color}]")

        self.update(Text.from_markup("\n".join(lines)))


class JobsBar(Static):
    """Shows active cron jobs and watchers inline."""

    def render_jobs(self, cron_jobs: list[dict], watchers: list[dict]) -> None:
        if not cron_jobs and not watchers:
            self.update(Text.from_markup(f"  [{_C_IDLE}]No scheduled jobs[/{_C_IDLE}]"))
            return

        parts: list[str] = []
        for j in cron_jobs:
            name = j.get("name", "?")
            cron = j.get("cron_expression", "")
            parts.append(f"[{_C_THINKING}]\u23f0[/{_C_THINKING}] {name} [dim]{cron}[/dim]")
        for w in watchers:
            name = w.get("name", "?")
            import json as _json
            try:
                ctx = _json.loads(w.get("context", "{}"))
            except Exception:
                ctx = {}
            url = ctx.get("url", "")
            # Show just domain
            domain = url.split("//")[-1].split("/")[0] if url else "?"
            parts.append(f"[{_C_ACTIVE}]\u25ce[/{_C_ACTIVE}] {name} [dim]{domain}[/dim]")

        self.update(Text.from_markup("  ".join(parts)))


class ActivityPanel(VerticalScroll):
    """Scrollable panel of active request cards."""

    _empty_label: Static | None = None

    def on_mount(self) -> None:
        self._empty_label = Static(
            Text.from_markup(f"  [{_C_IDLE}]No active agents[/{_C_IDLE}]"),
            id="empty-label",
        )
        self.mount(self._empty_label)

    def add_request(self, chat_id: str, message: str) -> None:
        if self._empty_label:
            self._empty_label.remove()
            self._empty_label = None
        # Use unique card ID — prevents DuplicateIds crash with concurrent requests
        card_id = f"req-{chat_id}"
        try:
            existing = self.query_one(f"#{card_id}", RequestCard)
            # Card already exists — just update its content instead of crashing
            existing._render_initial()
            return
        except Exception:
            pass
        card = RequestCard(chat_id, message, id=card_id)
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
                Text.from_markup(f"  [{_C_IDLE}]No active agents[/{_C_IDLE}]"),
                id="empty-label",
            )
            self.mount(self._empty_label)


class LogPanel(RichLog):
    """Scrollable activity feed — color-coded by type, auto-scroll.

    Event types get distinct colors: green=success, yellow=tool,
    cyan=delegate, red=error. Recent entries (last 30s) are bright.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(wrap=True, **kwargs)
        self._filter: str = "all"  # all, tools, errors, llm

    def set_filter(self, filter_name: str) -> None:
        """Set log filter: all, tools, errors, llm."""
        self._filter = filter_name.lower()

    def append_entry(self, timestamp: str, kind: str, detail: str) -> None:
        # Filter entries if a filter is active
        if self._filter == "tools" and kind not in ("tool", "result"):
            return
        if self._filter == "errors" and kind not in ("error", "stuck"):
            return
        if self._filter == "llm" and kind not in ("llm", "done"):
            return

        style = _log_style(kind)
        icon = _log_icon(kind)
        safe_detail = detail.replace("[", "\\[")

        # Truncate long details for readability
        if len(safe_detail) > 120:
            safe_detail = safe_detail[:117] + "..."

        self.write(Text.from_markup(
            f"[dim]{timestamp}[/dim] {icon} [{style}]{kind:<7}[/{style}] {safe_detail}"
        ))
        self.scroll_end(animate=False)


class ServicesPanel(Static):
    """Shows connected services status, updated every 2s."""

    def render_services(
        self,
        telegram_status: str = "disconnected",
        api_port: int = 18789,
        browser_alive: bool = False,
        browser_tabs: int = 0,
        cron_jobs: int = 0,
        watchers: int = 0,
        mcp_count: int = 0,
        mcp_tools: int = 0,
    ) -> None:
        def _dot(ok: bool, degraded: bool = False) -> str:
            if ok:
                return f"[{_C_SUCCESS}]●[/{_C_SUCCESS}]"
            if degraded:
                return f"[{_C_THINKING}]●[/{_C_THINKING}]"
            return f"[{_C_ERROR}]●[/{_C_ERROR}]"

        tg_ok = telegram_status == "connected"
        lines = [
            f"{_dot(tg_ok)} Telegram   {telegram_status}",
            f"{_dot(True)} API        :{api_port}     ok",
            f"{_dot(browser_alive)} Brave CDP  {browser_tabs} tabs",
            f"{_dot(cron_jobs > 0 or watchers > 0, degraded=True)} Heartbeat  {cron_jobs} cron  {watchers} watcher",
            f"{_dot(mcp_count > 0)} MCP        {mcp_count} ok    {mcp_tools} tools",
        ]
        self.update(Text.from_markup("\n".join(lines)))


class CostBar(Static):
    """Per-model cost breakdown with horizontal bars."""

    def render_costs(
        self,
        cost_by_model: dict[str, float],
        total_cost: float,
        budget: float = 5.0,
    ) -> None:
        if not cost_by_model and total_cost == 0:
            self.update(Text.from_markup(f"[{_C_IDLE}]No costs yet[/{_C_IDLE}]"))
            return

        parts: list[str] = []
        for model, cost in sorted(cost_by_model.items(), key=lambda x: -x[1]):
            pct = (cost / total_cost * 100) if total_cost > 0 else 0
            bar_width = 20
            filled = int(bar_width * pct / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            parts.append(
                f"{model} [{_C_ACTIVE}]{bar}[/{_C_ACTIVE}]"
                f" [{_C_COST}]{_fmt_cost(cost)}[/{_C_COST}] ({pct:.0f}%)"
            )

        # Budget line
        budget_pct = (total_cost / budget * 100) if budget > 0 else 0
        bw = 20
        bf = min(int(bw * budget_pct / 100), bw)
        budget_bar = "█" * bf + "░" * (bw - bf)
        budget_color = _C_SUCCESS if budget_pct < 50 else (_C_THINKING if budget_pct < 80 else _C_ERROR)
        parts.append(
            f"Budget: [{_C_COST}]{_fmt_cost(total_cost)}[/{_C_COST}] / {_fmt_cost(budget)}"
            f"  [{budget_color}]{budget_bar}[/{budget_color}] {budget_pct:.1f}%"
        )

        self.update(Text.from_markup("  │  ".join(parts[:2]) + "\n" + parts[-1] if len(parts) > 1 else "\n".join(parts)))


class TeamLeadBar(Static):
    """Live TeamLead status — shows running + recent tasks."""

    def render_status(self, team_lead: TeamLead | None) -> None:
        if team_lead is None:
            self.update(Text.from_markup(f"[{_C_IDLE}]TeamLead: offline[/{_C_IDLE}]"))
            return
        active_tasks = team_lead.active_tasks
        recent_tasks = team_lead.recent_tasks[:3]
        if not active_tasks and not recent_tasks:
            self.update(Text.from_markup(f"[{_C_IDLE}]All clear \u2014 no tasks[/{_C_IDLE}]"))
            return

        parts: list[str] = []

        # Active tasks
        now = time.monotonic()
        for t in active_tasks:
            elapsed = now - t.started_at
            icon = "\U0001f310" if "browser" in t.name.lower() else ("\u26a1" if t.lane == "background" else "\u25cf")
            step = f" step {t.step_count}: {t.current_step}" if t.current_step else ""
            color = _C_ACTIVE
            if elapsed > 60:
                color = _C_THINKING
            if elapsed > 120:
                color = _C_ERROR
            parts.append(
                f"[{color}]{icon} {t.description[:40]} ({t.lane}, {elapsed:.0f}s){step}[/{color}]"
            )

        # Recent (last 3 for compact bar)
        for t in recent_tasks:
            ago = now - (t.completed_at or now)
            icon = {
                "done": "\u2713", "failed": "\u2717", "cancelled": "\u2014",
            }.get(t.status, "?")
            color = _C_SUCCESS if t.status == "done" else _C_ERROR
            preview = t.result_preview[:30] or t.error[:30] or ""
            if preview:
                preview = f" \u2014 {preview}"
            parts.append(
                f"[{color}]{icon} {t.description[:30]} ({ago:.0f}s ago){preview}[/{color}]"
            )

        self.update(Text.from_markup("\n".join(parts)))


class AIRoutingBar(Static):
    """AI routing panel showing per-model stats with local/paid breakdown."""

    def render_routing(self, routing_stats: dict, budget: float = 5.0) -> None:
        """Render routing stats from EcoRouter.get_routing_stats().

        routing_stats = {
            "models": {"qwen3:0.6b": {"calls": 12, "cost": 0.0, "icon": "...", ...}},
            "total_cost": 0.003,
            "total_calls": 19,
            "local_pct": 92,
        }
        """
        models = routing_stats.get("models", {})
        total_calls = routing_stats.get("total_calls", 0)
        total_cost = routing_stats.get("total_cost", 0.0)
        local_pct = routing_stats.get("local_pct", 0)

        if not models:
            self.update(Text.from_markup(f"[{_C_IDLE}]No AI calls yet[/{_C_IDLE}]"))
            return

        lines: list[str] = []
        for name, m in sorted(models.items(), key=lambda x: -x[1]["calls"]):
            pct = (m["calls"] / total_calls * 100) if total_calls else 0
            bar_width = 20
            filled = int(bar_width * pct / 100)
            bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
            tag = "[bold green]LOCAL[/bold green]" if m.get("is_local") else f"[{_C_ERROR}]PAID[/{_C_ERROR}]"
            display = m.get("display_name", name)
            icon = m.get("icon", "\U0001f916")
            cost_str = "FREE" if m.get("cost", 0) == 0 else _fmt_cost(m["cost"])
            lines.append(
                f"{icon} {display} [{tag}]  {m['calls']} calls  "
                f"[{_C_COST}]{cost_str}[/{_C_COST}]  [{_C_ACTIVE}]{bar}[/{_C_ACTIVE}] {pct:.0f}%"
            )

        # Summary line
        budget_pct = (total_cost / budget * 100) if budget > 0 else 0
        budget_color = _C_SUCCESS if budget_pct < 50 else (_C_THINKING if budget_pct < 80 else _C_ERROR)
        lines.append(
            f"Today: [{_C_COST}]{_fmt_cost(total_cost)}[/{_C_COST}] "
            f"\u2502 [{budget_color}]{local_pct}% local[/{budget_color}] "
            f"\u2502 Budget: {_fmt_cost(total_cost)} / {_fmt_cost(budget)}"
        )

        self.update(Text.from_markup("\n".join(lines)))


# ── Main App ────────────────────────────────────────────────────────

class LazyClawApp(App):
    """LazyClaw server TUI dashboard."""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2;
        grid-columns: 1fr 1fr;
        grid-rows: 3 1fr auto auto 1fr 3;
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
        column-span: 2;
        height: 100%;
        border: solid $accent;
    }

    #team-lead-bar {
        column-span: 2;
        height: auto;
        max-height: 6;
        padding: 0 1;
        background: $surface;
        border: solid #14B8A6;
    }

    #jobs-bar {
        column-span: 2;
        height: auto;
        max-height: 3;
        padding: 0 1;
        background: $surface;
    }

    #log-panel {
        column-span: 2;
        height: 100%;
        border: solid $success;
    }

    #cost-bar {
        column-span: 2;
        height: 3;
        background: $surface;
        border: solid $primary;
        content-align: left middle;
        padding: 0 1;
    }

    #ai-routing-bar {
        column-span: 2;
        height: auto;
        max-height: 8;
        background: $surface;
        border: solid #F59E0B;
        content-align: left middle;
        padding: 0 1;
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
        Binding("slash", "focus_filter", "/Filter"),
        Binding("tab", "focus_next", "Next Panel"),
        Binding("1", "focus_agents", "Agents"),
        Binding("2", "focus_logs", "Logs"),
        Binding("c", "copy_logs", "Copy Logs"),
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
        team_lead: TeamLead | None = None,
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
        self._team_lead = team_lead
        self.dashboard = TuiDashboard(self)
        self._eco_budget: float = 5.0
        self._telegram_connected: bool = False
        self._telegram_adapter = None
        self._telegram_notifier = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield SystemBar(" Starting...", id="system-bar")
        yield ActivityPanel(id="activity-panel")
        yield TeamLeadBar("  All clear", id="team-lead-bar")
        yield JobsBar("  Loading jobs...", id="jobs-bar")
        yield LogPanel(id="log-panel", highlight=True, markup=True)
        yield CostBar("", id="cost-bar")
        yield AIRoutingBar("", id="ai-routing-bar")
        yield Input(
            placeholder="Type message or /command...",
            id="admin-input",
            suggester=SuggestFromList(
                ["/help", "/clear", "/status", "/history", "/jobs", "/watchers",
                 "/filter all", "/filter tools", "/filter errors", "/filter llm"],
                case_sensitive=False,
            ),
        )
        yield Footer()

    def on_mount(self) -> None:
        """Launch all services as background workers."""
        try:
            self._post_log("info", "LazyClaw TUI starting...")
            self._launch_services()
            self.set_interval(2.0, self._refresh_stats)
        except Exception as exc:
            logger.exception("on_mount FAILED: %s", exc)

    @work(exclusive=True, name="eco-budget")
    async def _load_eco_budget(self) -> None:
        """Load ECO budget from user settings."""
        try:
            from lazyclaw.llm.eco_settings import get_eco_settings
            eco = await get_eco_settings(self._config, self._user_id)
            self._eco_budget = eco.get("monthly_paid_budget", 5.0) or 5.0
        except Exception:
            pass

    @work(exclusive=True, name="services")
    async def _launch_services(self) -> None:
        """Start uvicorn, Telegram, heartbeat as concurrent tasks."""
        logger.info("TUI: _launch_services worker started")
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
            logger.info("TUI: uvicorn configured on port %d", self._config.port)

            # Telegram
            if self._telegram_token:
                logger.info("TUI: starting Telegram adapter...")
                from lazyclaw.channels.telegram import TelegramAdapter

                telegram = TelegramAdapter(
                    self._telegram_token, self._agent, self._config,
                    lane_queue=self._lane_queue,
                    server_dashboard=self.dashboard,
                    task_runner=self._task_runner,
                    team_lead=self._team_lead,
                )
                await telegram.start()
                self._telegram_adapter = telegram
                self._telegram_connected = True
                logger.info("TUI: Telegram adapter started OK")
                self._post_log("info", "Telegram bot running")

                # Universal notifier — pushes done/failed/help to Telegram
                # from ANY platform (CLI admin input, TUI, background tasks)
                from lazyclaw.notifications.telegram_notifier import TelegramNotifier

                self._telegram_notifier = TelegramNotifier(
                    bot=telegram._app.bot,
                    admin_chat_id_fn=lambda: telegram._admin_chat_id,
                )
                # Wire into TaskRunner so even tasks with no explicit callback
                # (e.g. run_background skill) still notify Telegram
                if self._task_runner and self._task_runner._default_callback is None:
                    self._task_runner._default_callback = self._telegram_notifier
            else:
                logger.warning("TUI: no telegram_token, skipping Telegram")

            # Heartbeat — with Telegram push for watcher notifications
            from lazyclaw.heartbeat.daemon import HeartbeatDaemon

            telegram_push = None
            if self._telegram_connected and telegram:
                _tg = telegram  # capture reference

                async def _telegram_push_fn(text: str) -> None:
                    # Check admin_chat_id at call time (may be set after /start)
                    chat_id = _tg._admin_chat_id
                    if not chat_id:
                        logger.debug("Telegram push skipped: no admin_chat_id yet")
                        return
                    if not _tg._app or not _tg._app.bot:
                        return
                    try:
                        from lazyclaw.channels.telegram import _telegram_send_with_retry
                        await _telegram_send_with_retry(
                            lambda: _tg._app.bot.send_message(
                                chat_id=int(chat_id), text=text,
                            )
                        )
                        logger.info("Telegram push sent to chat %s", chat_id)
                    except Exception as exc:
                        logger.warning("Telegram push failed: %s", exc)

                telegram_push = _telegram_push_fn

            heartbeat = HeartbeatDaemon(self._config, self._lane_queue, telegram_push=telegram_push)
            await heartbeat.start()
            logger.info("TUI: Heartbeat daemon started")
            self._post_log("info", "Heartbeat daemon started")

            # Persistent browser
            await self._init_persistent_browser()

            logger.info("TUI: all services started, running uvicorn")
            self._post_log("info", f"API on http://localhost:{self._config.port}")

            # Run uvicorn (blocks until shutdown)
            await asyncio.gather(*tasks)
        except Exception as exc:
            logger.exception("TUI service startup FAILED: %s", exc)
            self._post_log("error", f"Service startup failed: {exc}")

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

            # Memory: lazyclaw process
            mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)

            # Memory: browser (Brave/Chrome) + ollama processes
            try:
                proc = await asyncio.create_subprocess_shell(
                    "ps aux | grep -E 'Brave|Chrome|ollama' | grep -v grep | awk '{sum += $6} END {print sum/1024}'",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await proc.communicate()
                extra_mb = float(stdout.decode().strip() or "0")
                mem_mb += extra_mb
            except Exception:
                pass

            queue_depth = sum(
                q.qsize() for q in self._lane_queue._lanes.values()
            )

            # MCP count
            mcp_count = 0
            mcp_tools = 0
            try:
                from lazyclaw.mcp.manager import _active_clients
                mcp_count = len(_active_clients)
            except Exception:
                pass

            # Browser
            port = getattr(self._config, "cdp_port", 9222)
            browser_alive = bool(await find_chrome_cdp(port))

            # Browser tabs count
            browser_tabs = 0
            if browser_alive:
                try:
                    import aiohttp
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            f"http://127.0.0.1:{port}/json/list", timeout=aiohttp.ClientTimeout(total=1)
                        ) as resp:
                            if resp.status == 200:
                                tabs = await resp.json()
                                browser_tabs = len(tabs)
                except Exception:
                    pass

            user_id = self._user_id
            browser_cfg = await get_browser_settings(self._config, user_id)

            # Job details for jobs bar + counts
            from lazyclaw.crypto.encryption import decrypt, derive_server_key

            cron_count = 0
            watcher_count = 0
            cron_jobs_list: list[dict] = []
            watchers_list: list[dict] = []
            enc_key = derive_server_key(
                self._config.server_secret, self._user_id,
            )

            def _dec(val: str | None) -> str:
                if not val:
                    return ""
                try:
                    return decrypt(val, enc_key) if val.startswith("enc:") else val
                except Exception:
                    return val

            async with db_session(self._config) as db:
                cursor = await db.execute(
                    "SELECT job_type, name, cron_expression, context FROM agent_jobs "
                    "WHERE status = 'active' AND user_id = ?",
                    (self._user_id,),
                )
                for row in await cursor.fetchall():
                    job_type = row[0]
                    job_dict = {
                        "name": _dec(row[1]),
                        "cron_expression": _dec(row[2]),
                        "context": _dec(row[3]),
                    }
                    if job_type == "cron":
                        cron_count += 1
                        cron_jobs_list.append(job_dict)
                    elif job_type == "watcher":
                        watcher_count += 1
                        watchers_list.append(job_dict)

            # Update jobs bar
            try:
                jobs_bar = self.query_one("#jobs-bar", JobsBar)
                jobs_bar.render_jobs(cron_jobs_list, watchers_list)
            except Exception:
                pass

            telegram_status = "connected" if self._telegram_connected else "disconnected"

            # ECO mode + Ollama models for header
            eco_mode = "full"
            ollama_models: tuple[str, ...] = ()
            try:
                from lazyclaw.llm.eco_settings import get_eco_settings
                eco = await get_eco_settings(self._config, self._user_id)
                eco_mode = eco.get("mode", "full")
            except Exception:
                pass
            try:
                eco_router = getattr(self._agent, "eco_router", None)
                if eco_router:
                    ollama = await eco_router._ensure_ollama()
                    if ollama:
                        running = await ollama.list_running()
                        ollama_models = tuple(m["name"] for m in running)
            except Exception:
                pass

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
                total_cost_today=self.dashboard.total_cost_today,
                total_tokens_in=self.dashboard.total_tokens_in,
                total_tokens_out=self.dashboard.total_tokens_out,
                cost_by_model=self.dashboard.cost_by_model,
                browser_tabs=browser_tabs,
                telegram_status=telegram_status,
                eco_mode=eco_mode,
                ollama_models=ollama_models,
            )
            self.post_message(StatsRefreshed(stats))

            # Update cost bar
            try:
                cost_bar = self.query_one("#cost-bar", CostBar)
                cost_bar.render_costs(
                    cost_by_model=self.dashboard.cost_by_model,
                    total_cost=self.dashboard.total_cost_today,
                    budget=self._eco_budget,
                )
            except Exception:
                pass

            # Update AI routing bar (from eco_router stats)
            try:
                routing_bar = self.query_one("#ai-routing-bar", AIRoutingBar)
                eco_router = getattr(self._agent, "eco_router", None)
                if eco_router:
                    routing_bar.render_routing(
                        eco_router.get_routing_stats(),
                        budget=self._eco_budget,
                    )
            except Exception:
                pass

            # Update TeamLead bar (live task tracker)
            try:
                tl_bar = self.query_one("#team-lead-bar", TeamLeadBar)
                tl_bar.render_status(self._team_lead)
            except Exception:
                pass

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
        bar.update_stats(msg.stats, config=self._config)

    # ── Focus actions ─────────────────────────────────────────────────

    def action_focus_agents(self) -> None:
        self.query_one("#activity-panel").focus()

    def action_focus_logs(self) -> None:
        self.query_one("#log-panel").focus()

    def action_focus_filter(self) -> None:
        self.query_one("#admin-input").focus()

    def action_copy_logs(self) -> None:
        """Copy recent log entries to clipboard (last 50 lines)."""
        try:
            log_panel = self.query_one("#log-panel", LogPanel)
            # RichLog stores lines internally — export as plain text
            lines = []
            for line in log_panel.lines[-50:]:
                lines.append(line.text if hasattr(line, "text") else str(line))
            text = "\n".join(lines)
            if not text.strip():
                text = "(no log entries yet)"
            import subprocess
            subprocess.run(
                ["pbcopy"], input=text.encode(), check=True, timeout=2,
            )
            self._post_log("info", f"Copied {len(lines)} log lines to clipboard")
        except Exception as exc:
            # Fallback: write to /tmp
            try:
                path = "/tmp/lazyclaw_logs.txt"
                with open(path, "w") as f:
                    f.write(text)
                self._post_log("info", f"Logs saved to {path}")
            except Exception:
                self._post_log("error", f"Copy failed: {exc}")

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

        if text == "/history":
            self._show_history()
            return

        if text.startswith("/filter"):
            filter_arg = text[7:].strip().lower() or "all"
            if filter_arg in ("all", "tools", "errors", "llm"):
                self.query_one("#log-panel", LogPanel).set_filter(filter_arg)
                self._post_log("info", f"Log filter: {filter_arg}")
            else:
                self._post_log("info", "Filters: all, tools, errors, llm")
            return

        if text in ("/help", "/"):
            self._post_log("info",
                           "Commands: /clear /status /history /jobs /watchers "
                           "/filter <all|tools|errors|llm> /help")
            return

        # Enqueue as admin message with dashboard tracking + response display
        try:
            user_id = self._user_id
            # Check if another task is running for this user
            active_count = sum(
                1 for r in self.dashboard._active.values()
                if r.chat_id.startswith("admin-")
            )
            if active_count > 0:
                self._post_log("admin", f"Queued: {text[:40]} (waiting for current task)")
            else:
                self._post_log("admin", f"Sent: {text[:40]}")
            self._process_admin_message(user_id, text)
        except Exception as exc:
            self._post_log("error", f"Failed: {exc}")

    @work(name="admin-msg")
    async def _process_admin_message(self, user_id: str, text: str) -> None:
        """Process admin input: register with dashboard, show response."""
        chat_id = f"admin-{id(text)}"
        cb = self.dashboard.make_request_cb(chat_id)
        self.dashboard.register_request(chat_id, text)
        try:
            from lazyclaw.runtime.callbacks import MultiCallback
            cbs = [cb]
            if self._telegram_notifier is not None:
                cbs.append(self._telegram_notifier)
            effective_cb = MultiCallback(*cbs)
            response = await self._lane_queue.enqueue(
                user_id, text, callback=effective_cb,
            )
            if response and response.strip():
                for line in response.strip().split("\n"):
                    if line.strip():
                        self._post_log("reply", line)
            # Check if task was dispatched to background — if so, keep card alive
            req = self.dashboard._active.get(chat_id)
            dispatched = req and req.phase == "dispatched"
            if not dispatched:
                self.dashboard.unregister_request(chat_id)
            # If dispatched, background_done event will unregister it
        except Exception as exc:
            self._post_log("error", f"Agent error: {exc}")
            self.dashboard.unregister_request(chat_id)

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

    @work(name="history")
    async def _show_history(self) -> None:
        """Show recent conversation history in log panel."""
        from lazyclaw.crypto.encryption import decrypt, derive_server_key
        from lazyclaw.db.connection import db_session

        key = derive_server_key(self._config.server_secret, self._user_id)
        async with db_session(self._config) as db:
            rows = await db.execute(
                "SELECT role, content, created_at FROM agent_messages "
                "WHERE user_id = ? AND role IN ('user', 'assistant') "
                "ORDER BY created_at DESC LIMIT 10",
                (self._user_id,),
            )
            messages = await rows.fetchall()

        if not messages:
            self._post_log("info", "No conversation history.")
            return

        self._post_log("info", "\u2500\u2500\u2500 Recent History \u2500\u2500\u2500")
        for row in reversed(messages):
            role, content_enc, created_at = row[0], row[1], row[2]
            try:
                content = decrypt(content_enc, key) if content_enc.startswith("enc:") else content_enc
            except Exception:
                content = "[encrypted]"
            ts = (created_at or "")[-8:]  # HH:MM:SS
            preview = content[:100].replace("\n", " ")
            if "[Channel:" in preview:
                preview = preview[:preview.index("[Channel:")].strip()
            kind = "admin" if role == "user" else "reply"
            self._post_log(kind, f"{ts}  {preview}")

    def _post_log(self, kind: str, detail: str) -> None:
        """Post a log entry from within the app."""
        self.post_message(LogAppended(_now(), kind, detail))

    async def action_quit(self) -> None:
        """Graceful shutdown with cost persistence."""
        self._post_log("info", "Shutting down...")
        try:
            # Save today's cost to DB
            await self._persist_daily_cost()
            await self._task_runner.cancel_all()
            await self._lane_queue.stop()
            from lazyclaw.mcp.manager import disconnect_all
            await asyncio.wait_for(disconnect_all(), timeout=3)
            from lazyclaw.db.connection import close_pool
            await close_pool()
        except Exception:
            pass
        self.exit()

    async def _persist_daily_cost(self) -> None:
        """Save today's cost total to user settings."""
        try:
            from lazyclaw.db.connection import db_session
            today = datetime.now().strftime("%Y-%m-%d")
            cost_data = json.dumps({
                "date": today,
                "total": self.dashboard.total_cost_today,
                "by_model": self.dashboard.cost_by_model,
                "tokens_in": self.dashboard.total_tokens_in,
                "tokens_out": self.dashboard.total_tokens_out,
            })
            async with db_session(self._config) as db:
                row = await db.execute(
                    "SELECT settings FROM users WHERE id = ?",
                    (self._user_id,),
                )
                result = await row.fetchone()
                settings = {}
                if result and result[0]:
                    try:
                        settings = json.loads(result[0])
                    except (json.JSONDecodeError, TypeError):
                        settings = {}
                new_settings = dict(settings)
                new_settings["cost_today"] = json.loads(cost_data)
                await db.execute(
                    "UPDATE users SET settings = ? WHERE id = ?",
                    (json.dumps(new_settings), self._user_id),
                )
                await db.commit()
        except Exception:
            logger.debug("Failed to persist daily cost", exc_info=True)


# ── Log helpers ─────────────────────────────────────────────────────

def _log_style(kind: str) -> str:
    return {
        "new": "bold white",
        "tool": "#60A5FA",
        "result": _C_SUCCESS,
        "start": _C_SPECIALIST,
        "spec": _C_SPECIALIST,
        "llm": _C_THINKING,
        "done": f"bold {_C_SUCCESS}",
        "dispatch": _C_SPECIALIST,
        "info": _C_ACTIVE,
        "admin": _C_HEADER,
        "reply": _C_SUCCESS,
        "error": _C_ERROR,
        "stuck": f"bold {_C_ERROR}",
    }.get(kind, "dim")


def _log_icon(kind: str) -> str:
    return {
        "new": ">>",
        "tool": "◆",
        "result": "✓",
        "start": "++",
        "spec": "→",
        "dispatch": "→→",
        "llm": "●",
        "done": "✓✓",
        "info": "**",
        "admin": ">>",
        "reply": "←",
        "error": "✗",
        "stuck": "⚠",
    }.get(kind, "  ")
