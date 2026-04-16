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
    started_at: datetime | None = None
    finished_at: datetime | None = None
    step_name: str = ""
    compact: bool = False


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
    # Active model names for status bar display
    brain_model_name: str = ""   # e.g. "Sonnet 4.6" or last routing display name
    worker_model_name: str = ""  # e.g. "gemma4:e2b" or "Haiku 4.5"
    # RAM monitor (ECO v2)
    ram_system_pct: float = 0.0
    ram_ai_mb: int = 0
    ram_free_mb: int = 0


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


def _fmt_duration(seconds: float) -> str:
    """Format duration: 1.2s, 24s, 2m 14s."""
    if seconds < 60:
        return f"{seconds:.1f}s" if seconds < 10 else f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs}s"


def _fmt_time(dt: datetime | None) -> str:
    """Format datetime as HH:MM:SS."""
    if dt is None:
        return ""
    return dt.strftime("%H:%M:%S")


def _seconds_to_human(s: int | float) -> str:
    """Convert seconds to human-readable: 300 → '5m', 3600 → '1h'."""
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    hours = s // 3600
    mins = (s % 3600) // 60
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def _cron_to_human(expr: str) -> str:
    """Best-effort cron expression to human string."""
    parts = expr.strip().split()
    if len(parts) < 5:
        return expr
    minute, hour = parts[0], parts[1]
    # Daily at HH:MM
    if parts[2] == "*" and parts[3] == "*" and parts[4] == "*":
        if minute.isdigit() and hour.isdigit():
            return f"daily {hour}:{minute.zfill(2)}"
    # Every N minutes: */N * * * *
    if minute.startswith("*/") and hour == "*":
        return f"every {minute[2:]}m"
    return expr


def _time_ago(iso_str: str | None) -> str:
    """Convert ISO timestamp to 'Nm ago' or 'Nh ago'."""
    if not iso_str:
        return "never"
    try:
        dt = datetime.fromisoformat(iso_str)
        delta = (datetime.now() - dt).total_seconds()
        if delta < 0:
            return "just now"
        return _seconds_to_human(delta) + " ago"
    except Exception:
        logger.debug("Failed to parse timestamp for time_ago: %s", iso_str, exc_info=True)
        return "?"


def _time_until(iso_str: str | None) -> str:
    """Convert ISO timestamp to 'in Nm' or 'in Nh'."""
    if not iso_str:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_str)
        delta = (dt - datetime.now()).total_seconds()
        if delta < 0:
            return "overdue"
        return _seconds_to_human(delta)
    except Exception:
        logger.debug("Failed to parse timestamp for time_until: %s", iso_str, exc_info=True)
        return "?"


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
        "cancelled": ("✗", _C_ERROR),
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


class TodosUpdated(Message):
    def __init__(self, todos: list) -> None:
        super().__init__()
        self.todos = todos


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
        # Per-request callback refs for cancel token access
        self._callbacks: dict[str, _TuiRequestCallback] = {}

    def make_request_cb(self, chat_id: str) -> _TuiRequestCallback:
        cb = _TuiRequestCallback(self, chat_id)
        self._callbacks[chat_id] = cb
        return cb

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
        self._callbacks.pop(chat_id, None)
        self._total_processed += 1
        if req:
            req.finished_at = datetime.now()
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
            req.step_name = event.metadata.get("step_name", "") or "thinking"
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
            req.step_name = display
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
            req.step_name = f"delegate → {name}"
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
            req.step_name = "merging specialist results"

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
        # Determine if card should be compact (done/error, single tool, no specialists)
        is_terminal = req.phase in ("done", "error", "cancelled")
        has_specialists = bool(req.specialists)
        many_tools = len(req.tools_used) > 1
        compact = is_terminal and not has_specialists and not many_tools

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
            started_at=req.started_at,
            finished_at=req.finished_at,
            step_name=req.step_name,
            compact=compact,
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
        self.cancel_token = None  # Set by agent.py via hasattr check

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

        # ECO mode badge — normalize display name
        _eco_labels = {"hybrid": "HYBRID", "full": "FULL", "claude": "CLI"}
        eco = _eco_labels.get(stats.eco_mode, stats.eco_mode.upper())
        eco_color = _C_ACTIVE if eco == "HYBRID" else (_C_THINKING if eco == "CLI" else _C_IDLE)

        # Model names from active routing (populated by _collect_stats)
        brain = stats.brain_model_name
        worker = stats.worker_model_name

        # Service status indicators
        tg_dot = f"[{_C_SUCCESS}]\u2713[/{_C_SUCCESS}]" if stats.telegram_status == "connected" else f"[{_C_ERROR}]\u2717[/{_C_ERROR}]"
        br_dot = f"[{_C_SUCCESS}]\u2713[/{_C_SUCCESS}]" if stats.browser_alive else f"[{_C_IDLE}]\u2014[/{_C_IDLE}]"
        mcp_dot = f"[{_C_SUCCESS}]{stats.mcp_count}[/{_C_SUCCESS}]" if stats.mcp_count > 0 else f"[{_C_IDLE}]0[/{_C_IDLE}]"

        # RAM — always visible on line 1
        ram_pct = stats.ram_system_pct
        ram_color = _C_SUCCESS if ram_pct < 70 else (_C_ACTIVE if ram_pct < 85 else _C_ERROR)
        ram_text = f"[{ram_color}]RAM:{ram_pct:.0f}%[/{ram_color}]"
        if stats.ram_ai_mb > 0:
            ram_text += f"[{ram_color}](AI:{stats.ram_ai_mb}MB)[/{ram_color}]"

        # Line 1: Mode + Models + RAM + Cost
        line1_parts = [f"[{eco_color}]{eco}[/{eco_color}]"]
        if brain:
            line1_parts.append(f"Brain:[bold]{brain}[/bold]")
        if worker:
            line1_parts.append(f"Worker:[dim]{worker}[/dim]")
        line1_parts.append(ram_text)
        line1_parts.append(f"[{_C_COST}]{cost}[/{_C_COST}]")
        line1_parts.append(f"\u2191{t_in} \u2193{t_out}")
        line1_parts.append(f"[{active_color}]{stats.active_count} active[/{active_color}]")

        # Line 2: Services
        line2 = (
            f"TG:{tg_dot}  Browser:{br_dot}"
            f"  MCP:{mcp_dot}"
            f"  Q:{stats.queue_depth}"
            f"  Free:{stats.ram_free_mb}MB"
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
        t_in = _fmt_tokens(snap.tokens_in)
        t_out = _fmt_tokens(snap.tokens_out)
        cost = _fmt_cost(snap.cost_usd)
        duration = _fmt_duration(snap.elapsed_s)

        is_cancelled = snap.phase == "cancelled"
        is_compact = snap.compact

        # Color-coded border by status
        border_color = {
            "done": _C_SUCCESS, "error": _C_ERROR, "stuck": _C_ERROR,
            "queued": _C_IDLE, "cancelled": _C_IDLE,
        }.get(snap.phase, _C_ACTIVE if snap.phase in ("thinking", "tool", "team", "streaming") else _C_BORDER)

        # Escape user message for Rich markup
        safe_msg = snap.message.replace("[", "\\[") if snap.message else ""
        max_msg_len = 22 if is_compact else 50
        display_msg = safe_msg[:max_msg_len] + "\u2026" if len(safe_msg) > max_msg_len else safe_msg

        # Header — show × cancel hint for active tasks, [cancelled] badge for cancelled
        cancel_hint = ""
        if is_cancelled:
            cancel_hint = f"  [{_C_ERROR}]\\[cancelled][/{_C_ERROR}]"
        elif snap.phase not in ("done", "error"):
            cancel_hint = f"  [dim]\u00d7[/dim]"

        # Dim entire message if cancelled (strikethrough effect)
        msg_style = "dim strike" if is_cancelled else ""
        msg_markup = f"[{msg_style}]{display_msg}[/{msg_style}]" if msg_style else f'"{display_msg}"'

        # Header
        lines = [
            f"[{border_color}]\u256d\u2500[/{border_color}]"
            f" [{_C_HEADER}]#{self._number}[/{_C_HEADER}]"
            f" {msg_markup}{cancel_hint}"
        ]

        # Phase line with model shortname + step with name
        phase_label = snap.phase
        model_short = snap.model.split("/")[-1] if snap.model else ""
        step_str = ""
        if snap.step_current:
            step_str = f"  step {snap.step_current}"
            if snap.step_total:
                step_str += f"/{snap.step_total}"
            if snap.step_name:
                step_str += f": {snap.step_name}"
        lines.append(
            f"[{border_color}]\u2502[/{border_color}]"
            f" [{color}]{icon} {phase_label}[/{color}]"
            f"  [dim]{model_short}[/dim]{step_str}"
        )

        if not is_compact:
            # Step name standalone (when no step_current but name exists)
            if snap.step_name and not snap.step_current:
                lines.append(
                    f"[{border_color}]\u2502[/{border_color}]"
                    f" \u21b3 \"{snap.step_name}\""
                )

            # Delegate chain
            if snap.delegate_to:
                lines.append(
                    f"[{border_color}]\u2502[/{border_color}]"
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
                    f"[{border_color}]\u2502[/{border_color}]"
                    f" [{_C_ACTIVE}]{bar}[/{_C_ACTIVE}]"
                    f" {filled}/{snap.step_total} steps"
                )

            # Tools
            if snap.tools_used:
                unique_tools = list(dict.fromkeys(snap.tools_used[-6:]))
                tools_str = ", ".join(unique_tools)
                lines.append(
                    f"[{border_color}]\u2502[/{border_color}]"
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
                        "cancelled": ("\u2717", _C_ERROR),
                    }.get(status, ("\u25cb", _C_IDLE))
                    spec_parts.append(f"[{s_color}]{s_icon} {name}[/{s_color}]")
                lines.append(
                    f"[{border_color}]\u2502[/{border_color}]"
                    f"  {'  '.join(spec_parts)}"
                )

        # Token/cost/time footer with wall-clock timestamps
        trigger_badge = ""
        if snap.trigger != "user":
            trigger_badge = f" [{_C_SPECIALIST}]\\[{snap.trigger}][/{_C_SPECIALIST}]"

        started_str = _fmt_time(snap.started_at)
        finished_str = _fmt_time(snap.finished_at)
        time_parts = f"[dim]{started_str}[/dim]"
        if finished_str:
            time_parts += f" [dim]\u2192 {finished_str}[/dim]"
        duration_color = _C_ERROR if is_cancelled else (_C_SUCCESS if snap.phase == "done" else _C_ACTIVE)

        lines.append(
            f"[{border_color}]\u2502[/{border_color}]"
            f" \u2191{t_in} \u2193{t_out}"
            f"  [{_C_COST}]{cost}[/{_C_COST}]"
            f"  {time_parts}"
            f"  [bold {duration_color}]{duration}[/bold {duration_color}]"
            f"{trigger_badge}"
        )

        # Footer
        horiz_line = "\u2500" * 44
        lines.append(f"[{border_color}]\u2570{horiz_line}\u256f[/{border_color}]")

        self.update(Text.from_markup("\n".join(lines)))


class JobsBar(Static):
    """Shows active cron jobs and watchers with timing details."""

    def render_jobs(self, cron_jobs: list[dict], watchers: list[dict]) -> None:
        if not cron_jobs and not watchers:
            self.update(Text.from_markup(f"  [{_C_IDLE}]No scheduled jobs[/{_C_IDLE}]"))
            return

        lines: list[str] = []

        # Cron jobs — title, interval, last run, next run
        if cron_jobs:
            cron_parts: list[str] = []
            for j in cron_jobs:
                name = j.get("name", "?")
                cron_expr = j.get("cron_expression", "")
                interval = _cron_to_human(cron_expr) if cron_expr else cron_expr
                last_ago = _time_ago(j.get("last_run"))
                next_in = _time_until(j.get("next_run"))
                cron_parts.append(
                    f"[{_C_THINKING}]\u23f0[/{_C_THINKING}]"
                    f" [bold]{name}[/bold]"
                    f" [dim]\u2014 {interval}"
                    f" \u2014 last: {last_ago}"
                    f" \u2014 next: {next_in}[/dim]"
                )
            lines.append("  ".join(cron_parts))

        # Watchers — title, interval, last/next, prompt in quotes
        for w in watchers:
            name = w.get("name", "?")
            import json as _json
            try:
                ctx = _json.loads(w.get("context", "{}"))
            except Exception:
                logger.debug("Failed to parse watcher context JSON", exc_info=True)
                ctx = {}
            interval_s = ctx.get("interval_seconds", 0)
            interval_str = _seconds_to_human(interval_s) if interval_s else "?"
            prompt = ctx.get("instruction", ctx.get("prompt", ""))
            last_ago = _time_ago(w.get("last_run"))
            next_in = _time_until(w.get("next_run"))

            line = (
                f"[{_C_ACTIVE}]\u25ce[/{_C_ACTIVE}]"
                f" [bold]{name}[/bold]"
                f" [dim]\u2014 every {interval_str}"
                f" \u2014 last: {last_ago}"
                f" \u2014 next: {next_in} \u2014[/dim]"
            )
            if prompt:
                safe_prompt = prompt[:60].replace("[", "\\[")
                line += f" [{_C_IDLE}]\"{safe_prompt}\"[/{_C_IDLE}]"
            lines.append(line)

        self.update(Text.from_markup("\n".join(lines)))


class _CardRow(Horizontal):
    """A horizontal row of request cards in the activity grid."""
    pass


class ActivityPanel(VerticalScroll):
    """Scrollable grid panel of active request cards.

    Cards are arranged in a flex-wrap grid using Horizontal rows:
    - Complex cards (specialists or multiple tools): ~half width (2 per row)
    - Compact completed cards (single tool, done): ~third width (3 per row)
    """

    _empty_label: Static | None = None
    _card_compactness: dict[str, bool] = {}
    # Track which row each card belongs to
    _card_rows: dict[str, str] = {}  # chat_id -> row_id
    _row_counter: int = 0

    def on_mount(self) -> None:
        self._card_compactness = {}
        self._card_rows = {}
        self._row_counter = 0
        self._empty_label = Static(
            Text.from_markup(f"  [{_C_IDLE}]No active agents[/{_C_IDLE}]"),
            id="empty-label",
        )
        self.mount(self._empty_label)

    def _find_or_create_row(self, compact: bool) -> _CardRow:
        """Find a row with room for another card, or create a new one.

        Full cards: max 2 per row. Compact cards: max 3 per row.
        """
        max_per_row = 3 if compact else 2
        target_class = "compact-row" if compact else "full-row"

        # Try to find an existing row with room
        for row in self.query(_CardRow):
            if row.has_class(target_class):
                card_count = len(row.query(RequestCard))
                if card_count < max_per_row:
                    return row

        # Create new row
        self._row_counter += 1
        row_id = f"card-row-{self._row_counter}"
        row = _CardRow(id=row_id, classes=target_class)
        self.mount(row)
        return row

    def add_request(self, chat_id: str, message: str) -> None:
        if self._empty_label:
            self._empty_label.remove()
            self._empty_label = None
        # Use unique card ID — prevents DuplicateIds crash with concurrent requests
        card_id = f"req-{chat_id}"
        try:
            self.query_one(f"#{card_id}", RequestCard)
            return  # Card already exists
        except Exception:
            pass  # intentional: NoMatches expected when card doesn't exist yet
        self._card_compactness[chat_id] = False
        row = self._find_or_create_row(compact=False)
        card = RequestCard(chat_id, message, id=card_id, classes="card-full")
        row.mount(card)
        self._card_rows[chat_id] = row.id or ""
        card.scroll_visible()

    def update_request(self, chat_id: str, snapshot: RequestSnapshot) -> None:
        try:
            card = self.query_one(f"#req-{chat_id}", RequestCard)
            card.update_snapshot(snapshot)
            # Move card to appropriate row type if compactness changed
            was_compact = self._card_compactness.get(chat_id, False)
            if snapshot.compact != was_compact:
                self._card_compactness[chat_id] = snapshot.compact
                # Remove from current row, add to correct row type
                old_row_id = self._card_rows.get(chat_id)
                card.remove()
                new_row = self._find_or_create_row(compact=snapshot.compact)
                cls = "card-compact" if snapshot.compact else "card-full"
                card.remove_class("card-full")
                card.remove_class("card-compact")
                card.add_class(cls)
                new_row.mount(card)
                self._card_rows[chat_id] = new_row.id or ""
                # Clean up empty old row
                if old_row_id:
                    self._cleanup_empty_row(old_row_id)
        except Exception as exc:
            logger.debug("Failed to update request card for %s: %s", chat_id, exc)

    def remove_request(self, chat_id: str) -> None:
        try:
            card = self.query_one(f"#req-{chat_id}", RequestCard)
            card.remove()
        except Exception:
            pass  # intentional: NoMatches expected if card was never registered
        row_id = self._card_rows.pop(chat_id, None)
        self._card_compactness.pop(chat_id, None)
        if row_id:
            self._cleanup_empty_row(row_id)
        # Restore empty label if no cards left
        if not self.query(RequestCard):
            self._empty_label = Static(
                Text.from_markup(f"  [{_C_IDLE}]No active agents[/{_C_IDLE}]"),
                id="empty-label",
            )
            self.mount(self._empty_label)

    def _cleanup_empty_row(self, row_id: str) -> None:
        """Remove a row if it has no more cards."""
        try:
            row = self.query_one(f"#{row_id}", _CardRow)
            if not row.query(RequestCard):
                row.remove()
        except Exception:
            pass  # intentional: NoMatches expected if row was already removed


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

        # Convert markdown bold (**text**) to Rich markup, then escape brackets
        import re as _re
        safe_detail = _re.sub(r'\*\*(.+?)\*\*', r'[bold]\1[/bold]', detail)
        safe_detail = safe_detail.replace("[", "\\[").replace("\\[bold]", "[bold]").replace("\\[/bold]", "[/bold]")

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


class TodoWidget(Static):
    """Real-time task checklist showing agent plan progress.

    [ ] pending   [→] in_progress (shown prominently)   [✓] completed
    Hidden when the list is empty. Updates via TodosUpdated message.
    """

    def render_todos(self, todos: list) -> None:
        if not todos:
            self.display = False
            return

        self.display = True
        lines: list[str] = []

        # Count for header
        completed = sum(1 for t in todos if t.status == "completed")
        in_prog = [t for t in todos if t.status == "in_progress"]
        header = (
            f"[{_C_HEADER}][bold]Tasks[/bold][/{_C_HEADER}]"
            f"  [{_C_IDLE}]{completed}/{len(todos)} done[/{_C_IDLE}]"
        )
        if in_prog:
            header += (
                f"  [{_C_ACTIVE}]→ {in_prog[0].active_form}[/{_C_ACTIVE}]"
            )
        lines.append(header)

        # Individual rows
        for t in todos:
            status = getattr(t, "status", "pending")
            content = getattr(t, "content", "")
            active_form = getattr(t, "active_form", content)
            if status == "completed":
                icon = f"[{_C_SUCCESS}][✓][/{_C_SUCCESS}]"
                text = f"[dim]{content}[/dim]"
            elif status == "in_progress":
                icon = f"[{_C_ACTIVE}][→][/{_C_ACTIVE}]"
                text = f"[bold {_C_ACTIVE}]{active_form}[/bold {_C_ACTIVE}]"
            else:
                icon = f"[{_C_IDLE}][ ][/{_C_IDLE}]"
                text = content
            lines.append(f"  {icon} {text}")

        self.update(Text.from_markup("\n".join(lines)))


class AIRoutingBar(Static):
    """AI routing panel showing per-model stats with local/paid breakdown."""

    def render_routing(self, routing_stats: dict, budget: float = 5.0) -> None:
        """Render routing stats from EcoRouter.get_routing_stats().

        routing_stats = {
            "models": {"gemma4:e2b": {"calls": 12, "cost": 0.0, "icon": "...", ...}},
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
            if m.get("is_local"):
                tag = "[bold green]LOCAL[/bold green]"
            elif m.get("cost", 0) == 0:
                # $0 API model — user has a subscription (MiniMax Plus, Claude Max CLI, etc.)
                tag = "[bold cyan]SUB[/bold cyan]"
            else:
                tag = f"[{_C_ERROR}]PAID[/{_C_ERROR}]"
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


# ── Settings Panel ──────────────────────────────────────────────────

class SettingsPanel(Static):
    """Rich 5-column settings panel matching the mockup design.

    Shows toggle indicators (◉/○), select values with ▾, permission
    colors (green=allow, yellow=ask, red=deny), and proper alignment.
    Changes via /set commands.
    """

    def render_settings(
        self,
        eco: dict,
        agent: dict,
        browser: dict,
        team: dict | None = None,
        perms: dict | None = None,
        telegram_connected: bool = False,
        cdp_port: int = 9222,
    ) -> None:
        """Render settings in 5 side-by-side columns using Rich markup."""

        # ── Helpers ──
        def _toggle(on: bool, label: str = "") -> str:
            """Render toggle: [◉ on] or [○ off]."""
            if on:
                return f"[{_C_SUCCESS}]◉ {label or 'on'}[/{_C_SUCCESS}]"
            return f"[{_C_IDLE}]○ {label or 'off'}[/{_C_IDLE}]"

        def _select(value: str, color: str = _C_ACTIVE) -> str:
            """Render select: [value ▾]."""
            return f"[{color}]{value} ▾[/{color}]"

        def _perm(value: str) -> str:
            """Render permission with auto-color."""
            colors = {"allow": _C_SUCCESS, "ask": _C_HEADER, "deny": _C_ERROR}
            c = colors.get(value, _C_IDLE)
            return f"[{c}]{value} ▾[/{c}]"

        def _row(label: str, value: str, width: int = 18) -> str:
            """Render aligned setting row."""
            padded = label.ljust(width)
            return f"  [dim]{padded}[/dim] {value}"

        def _header(title: str) -> str:
            return f"[{_C_HEADER}][bold]{title}[/bold][/{_C_HEADER}]\n  {'─' * 24}"

        # ── Extract values ──
        mode = eco.get("mode", "hybrid")
        mode_color = _C_ACTIVE if mode == "hybrid" else _C_IDLE
        brain = eco.get("brain_model") or "default"
        worker = eco.get("worker_model") or "default"
        fallback = eco.get("fallback_model") or "default"
        budget = eco.get("monthly_paid_budget", 5.0) or 5.0
        auto_fb = eco.get("auto_fallback", False)
        show_badges = eco.get("show_badges", True)
        max_w = eco.get("max_workers", 10)

        auto_del = agent.get("auto_delegate", True)
        max_spec = agent.get("max_concurrent_specialists", 3)
        max_ram = agent.get("max_ram_mb", 512)
        timeout_s = agent.get("specialist_timeout_s", 120)

        persistent = browser.get("persistent", "auto")
        idle_timeout = browser.get("idle_timeout", 3600)

        team = team or {}
        team_mode = team.get("mode", "never")
        critic_mode = team.get("critic_mode", "auto")

        perms = perms or {}
        cat = perms.get("category_defaults", {})

        # ── Build columns ──
        col1_lines = [
            _header("AI / MODELS"),
            _row("ECO Mode", _select(mode, mode_color)),
            _row("Brain Model", _select(brain, "#7dcfff")),
            _row("Worker Model", _select(worker, "#7dcfff")),
            _row("Fallback", _select(fallback, "#ff9e64")),
            _row("Show Badges", _toggle(show_badges)),
            _row("Auto Fallback", _toggle(auto_fb)),
            _row("Max Workers", f"{max_w}"),
            _row("Budget", f"[{_C_COST}]${budget:.2f}[/{_C_COST}]"),
        ]

        col2_lines = [
            _header("TEAMS / AGENT"),
            _row("Team Mode", _select(team_mode)),
            _row("Critic Mode", _select(critic_mode)),
            _row("Auto Delegate", _toggle(auto_del)),
            _row("Max Specialists", f"{max_spec}"),
            _row("Max RAM (MB)", f"{max_ram}"),
            _row("Timeout", f"{timeout_s}s"),
        ]

        col3_lines = [
            _header("BROWSER"),
            _row("Persistent", _select(persistent, "#ff9e64")),
            _row("Idle Timeout", f"{idle_timeout}s"),
            _row("CDP Port", f"{cdp_port}"),
        ]

        col4_lines = [
            _header("PERMISSIONS"),
            _row("General", _perm(cat.get("general", "allow"))),
            _row("Browser", _perm(cat.get("browser", "allow"))),
            _row("Computer", _perm(cat.get("computer", "ask"))),
            _row("Vault", _perm(cat.get("vault", "ask"))),
            _row("MCP / Shell", _perm(cat.get("mcp", "allow"))),
            _row("Security", _perm(cat.get("security", "ask"))),
        ]

        tg_label = "connected" if telegram_connected else "off"
        col5_lines = [
            _header("CHANNELS"),
            _row("Telegram", _toggle(telegram_connected, tg_label)),
            _row("Discord", _toggle(False)),
            _row("WhatsApp", _toggle(False)),
        ]

        # ── Merge columns side-by-side ──
        # Pad each column to same height
        all_cols = [col1_lines, col2_lines, col3_lines, col4_lines, col5_lines]
        max_rows = max(len(c) for c in all_cols)
        for col in all_cols:
            while len(col) < max_rows:
                col.append("")

        # Terminal is ~120-160 chars wide. Show as 2-3 columns or stacked.
        # For Textual Static widget, stack vertically in groups.
        output_parts: list[str] = []

        # Group 1: AI + Teams side-by-side (using ║ separator)
        for i in range(max_rows):
            left = col1_lines[i] if i < len(col1_lines) else ""
            right = col2_lines[i] if i < len(col2_lines) else ""
            # Pad left column to ~35 chars (approximate)
            output_parts.append(f"{left}     {right}" if right else left)

        output_parts.append("")  # spacer

        # Group 2: Browser + Permissions + Channels
        max_rows_2 = max(len(col3_lines), len(col4_lines), len(col5_lines))
        for i in range(max_rows_2):
            c3 = col3_lines[i] if i < len(col3_lines) else ""
            c4 = col4_lines[i] if i < len(col4_lines) else ""
            c5 = col5_lines[i] if i < len(col5_lines) else ""
            parts = [p for p in [c3, c4, c5] if p]
            output_parts.append("     ".join(parts))

        # Footer hint
        output_parts.append("")
        output_parts.append(
            f"[dim]Change via: /set eco <on|hybrid|off> │ "
            f"/set brain <model> │ /set budget <N> │ "
            f"/set team <on|off>[/dim]"
        )
        output_parts.append(
            f"[dim]Press [{_C_HEADER}]3[/{_C_HEADER}] or Esc to return to dashboard[/dim]"
        )

        self.update(Text.from_markup("\n".join(output_parts)))


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

    #todo-widget {
        column-span: 2;
        height: auto;
        max-height: 12;
        padding: 0 1;
        background: $surface;
        border: solid #6366F1;
        display: none;
    }

    #todo-widget.visible {
        display: block;
    }

    #jobs-bar {
        column-span: 2;
        height: auto;
        max-height: 8;
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

    _CardRow {
        height: auto;
        width: 100%;
    }

    .full-row {
        height: auto;
    }

    .compact-row {
        height: auto;
    }

    RequestCard {
        height: auto;
        margin: 0 1 1 0;
        padding: 0;
    }

    .card-full {
        width: 1fr;
        min-width: 40;
    }

    .card-compact {
        width: 1fr;
        min-width: 26;
    }

    #settings-panel {
        column-span: 2;
        height: auto;
        max-height: 100%;
        background: $surface;
        border: solid #565f89;
        padding: 1 2;
        display: none;
        overflow-y: auto;
    }

    #settings-panel.visible {
        display: block;
    }

    .settings-open #activity-panel,
    .settings-open #team-lead-bar,
    .settings-open #todo-widget,
    .settings-open #jobs-bar,
    .settings-open #log-panel,
    .settings-open #cost-bar,
    .settings-open #ai-routing-bar {
        display: none;
    }
    """

    TITLE = "LazyClaw Server"
    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("slash", "focus_filter", "/Filter"),
        Binding("tab", "focus_next", "Navigate"),
        Binding("1", "focus_agents", "Activity"),
        Binding("2", "focus_logs", "Logs"),
        Binding("3", "toggle_settings", "Settings"),
        Binding("escape", "close_settings", "Back", show=False),
        Binding("x", "cancel_focused", "Cancel Task"),
        Binding("c", "copy_logs", "Copy"),
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
        self._settings_visible: bool = False

        # TodoManager — task plan tracking, persisted per user
        from lazyclaw.runtime.todo_manager import get_todo_manager
        self.todo_manager = get_todo_manager(config.database_dir, default_user_id)
        self.todo_manager.add_listener(
            lambda todos: self.post_message(TodosUpdated(todos))
        )
        # Inject TodoManager into TodoWriteSkill so it notifies the TUI
        try:
            from lazyclaw.skills.builtin.todo_write import TodoWriteSkill
            skill = registry.get("todo_write") if registry else None
            if isinstance(skill, TodoWriteSkill):
                skill._todo_manager = self.todo_manager
        except Exception:
            pass  # intentional: todo_write skill is optional

    def compose(self) -> ComposeResult:
        yield Header()
        yield SystemBar(" Starting...", id="system-bar")
        yield ActivityPanel(id="activity-panel")
        yield TeamLeadBar("  All clear", id="team-lead-bar")
        yield TodoWidget("", id="todo-widget")
        yield JobsBar("  Loading jobs...", id="jobs-bar")
        yield LogPanel(id="log-panel", highlight=True, markup=True)
        yield CostBar("", id="cost-bar")
        yield AIRoutingBar("", id="ai-routing-bar")
        yield SettingsPanel("", id="settings-panel")
        yield Input(
            placeholder="Type message or /command...",
            id="admin-input",
            suggester=SuggestFromList(
                ["/help", "/clear", "/status", "/history", "/jobs", "/watchers",
                 "/filter all", "/filter tools", "/filter errors", "/filter llm",
                 "/cancel", "/cancel all", "/cancel bg",
                 "/set eco on", "/set eco hybrid", "/set eco off",
                 "/set brain", "/set worker", "/set budget",
                 "/settings"],
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
            logger.debug("Failed to load ECO budget setting", exc_info=True)

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

                async def _telegram_push_fn(text: str, reply_markup=None) -> None:
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
                                reply_markup=reply_markup,
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

            # Auto-start MLX worker if HYBRID mode
            await self._auto_start_mlx()

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
        except Exception as exc:
            logger.debug("Persistent browser setup failed: %s", exc)

    async def _auto_start_mlx(self) -> None:
        """Auto-start MLX worker server if ECO mode is HYBRID and no server running."""
        try:
            from lazyclaw.llm.eco_settings import get_eco_settings
            from lazyclaw.llm.model_registry import get_mode_models
            from lazyclaw.llm.providers.mlx_provider import MLXProvider

            eco = await get_eco_settings(self._config, self._user_id)
            mode = eco.get("mode", "full")
            if mode not in ("hybrid",):
                return

            # Check if MLX worker already running on :8081
            probe = MLXProvider("http://127.0.0.1:8081")
            if await probe.health_check():
                self._post_log("info", "MLX worker already running on :8081")
                return

            # Check RAM — need headroom for local model
            try:
                from lazyclaw.llm.ram_monitor import get_ram_status
                ram = await get_ram_status()
                free_mb = ram.headroom_mb
                if free_mb < 2000:
                    self._post_log(
                        "info",
                        f"MLX skipped — only {free_mb}MB free (need ~4.5GB)",
                    )
                    logger.warning(
                        "MLX auto-start skipped: %dMB free < 2000MB threshold",
                        free_mb,
                    )
                    return
            except Exception:
                pass  # intentional: RAM check failed, try starting MLX anyway

            # Start MLX worker
            models = get_mode_models("hybrid")
            worker_model = models["worker"]

            from lazyclaw.llm.mlx_manager import MLXManager
            manager = MLXManager()
            self._post_log("info", f"Starting MLX worker ({worker_model})...")
            logger.info("TUI: auto-starting MLX worker: %s", worker_model)

            healthy = await manager.start_worker(worker_model)
            if healthy:
                self._post_log("info", "MLX worker running on :8081")
                logger.info("TUI: MLX worker healthy on :8081")
                # Store manager for graceful shutdown
                self._mlx_manager = manager
            else:
                self._post_log("error", "MLX worker failed to start")
                logger.error("TUI: MLX worker failed health check")
        except Exception as exc:
            logger.warning("MLX auto-start failed: %s", exc)
            self._post_log("info", f"MLX not available: {exc}")

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
            except Exception as exc:
                logger.debug("Failed to measure browser/ollama memory: %s", exc)

            queue_depth = sum(
                q.qsize() for q in self._lane_queue._lanes.values()
            )

            # MCP count
            mcp_count = 0
            mcp_tools = 0
            try:
                from lazyclaw.mcp.manager import _active_clients
                mcp_count = len(_active_clients)
            except Exception as exc:
                logger.debug("Failed to count active MCP clients: %s", exc)

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
                except Exception as exc:
                    logger.debug("Failed to count browser tabs: %s", exc)

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
                    logger.warning("Failed to decrypt job field value", exc_info=True)
                    return val

            async with db_session(self._config) as db:
                cursor = await db.execute(
                    "SELECT job_type, name, cron_expression, context, "
                    "last_run, next_run, instruction FROM agent_jobs "
                    "WHERE status = 'active' AND user_id = ?",
                    (self._user_id,),
                )
                for row in await cursor.fetchall():
                    job_type = row[0]
                    job_dict = {
                        "name": _dec(row[1]),
                        "cron_expression": _dec(row[2]),
                        "context": _dec(row[3]),
                        "last_run": row[4] or "",
                        "next_run": row[5] or "",
                        "instruction": _dec(row[6]),
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
                pass  # intentional: NoMatches if jobs bar not yet mounted

            telegram_status = "connected" if self._telegram_connected else "disconnected"

            # ECO mode + Ollama models + active model names for header
            eco_mode = "full"
            ollama_models: tuple[str, ...] = ()
            brain_model_name = ""
            worker_model_name = ""
            try:
                from lazyclaw.llm.eco_settings import get_eco_settings
                from lazyclaw.llm.model_registry import get_mode_models, get_model
                eco = await get_eco_settings(self._config, self._user_id)
                eco_mode = eco.get("mode", "full")
                # Resolve default model names for current mode
                mode_models = get_mode_models(eco_mode)
                brain_id = eco.get("brain_model") or mode_models.get("brain", "")
                worker_id = eco.get("worker_model") or mode_models.get("worker", "")
                brain_profile = get_model(brain_id)
                worker_profile = get_model(worker_id)
                brain_model_name = brain_profile.display_name if brain_profile else brain_id.split("/")[-1]
                worker_model_name = worker_profile.display_name if worker_profile else worker_id.split(":")[0]
            except Exception as exc:
                logger.debug("Failed to load ECO model names for stats bar: %s", exc)
            # Only poll Ollama if in hybrid mode (uses local models).
            # In claude/full modes Ollama is unused — skip to avoid log spam.
            if eco_mode == "hybrid":
                try:
                    eco_router = getattr(self._agent, "eco_router", None)
                    if eco_router:
                        ollama = await eco_router._ensure_ollama()
                        if ollama:
                            running = await ollama.list_running()
                            ollama_models = tuple(m["name"] for m in running)
                            if running:
                                worker_model_name = running[0]["name"]
                except Exception as exc:
                    logger.debug("Failed to get Ollama model names for stats bar: %s", exc)

            # RAM monitor — always collect, never block
            ram_pct = 0.0
            ram_ai = 0
            ram_free = 0
            try:
                from lazyclaw.llm.ram_monitor import get_ram_status
                ram = await get_ram_status()
                ram_pct = ram.system_used_pct
                ram_ai = ram.ai_total_mb
                ram_free = ram.headroom_mb
            except Exception as _ram_err:
                logger.debug("RAM monitor error: %s", _ram_err)

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
                brain_model_name=brain_model_name,
                worker_model_name=worker_model_name,
                ram_system_pct=ram_pct,
                ram_ai_mb=ram_ai,
                ram_free_mb=ram_free,
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
                pass  # intentional: NoMatches if cost bar not yet mounted

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
                pass  # intentional: NoMatches if routing bar not yet mounted

            # Update TeamLead bar (live task tracker)
            try:
                tl_bar = self.query_one("#team-lead-bar", TeamLeadBar)
                tl_bar.render_status(self._team_lead)
            except Exception:
                pass  # intentional: NoMatches if team-lead bar not yet mounted

        except Exception as exc:
            logger.debug("_refresh_stats failed: %s", exc)

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

    def on_todos_updated(self, msg: TodosUpdated) -> None:
        try:
            widget = self.query_one("#todo-widget", TodoWidget)
            widget.render_todos(msg.todos)
            # Toggle visible CSS class so the border shows only when populated
            has_todos = bool(msg.todos)
            if has_todos:
                widget.add_class("visible")
            else:
                widget.remove_class("visible")
        except Exception:
            pass  # intentional: NoMatches if todo widget not yet mounted

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
                logger.error("Failed to save logs to fallback file", exc_info=True)
                self._post_log("error", f"Copy failed: {exc}")

    # ── Cancel / Settings actions ────────────────────────────────────

    def action_cancel_focused(self) -> None:
        """Cancel the first active request (x key)."""
        active = self.dashboard._active
        if not active:
            self._post_log("info", "Nothing to cancel")
            return
        # Cancel the first non-done active request
        for chat_id, req in list(active.items()):
            if req.phase not in ("done", "error", "cancelled"):
                self._cancel_request(chat_id)
                return
        self._post_log("info", "No active tasks to cancel")

    def _cancel_request(self, chat_id: str) -> None:
        """Cancel a single request by chat_id."""
        req = self.dashboard._active.get(chat_id)
        if not req:
            return
        name = req.message[:30]
        # Cancel via CancellationToken (cooperative signal to agent loop)
        cb = self.dashboard._callbacks.get(chat_id)
        if cb and cb.cancel_token:
            cb.cancel_token.cancel()
        # Cancel via TeamLead
        if self._team_lead:
            task_id = self._team_lead.find_cancel_target(name)
            if task_id:
                self._team_lead.cancel(task_id)
        # Cancel via TaskRunner (background)
        if self._task_runner:
            for tid, uid in list(self._task_runner._task_users.items()):
                tname = self._task_runner._task_names.get(tid, "")
                if chat_id in tid or name in tname:
                    from lazyclaw.runtime.aio_helpers import fire_and_forget

                    fire_and_forget(
                        self._task_runner.cancel(tid, uid),
                        name=f"cancel-{tid}",
                    )
                    break
        # Update card to cancelled
        req.phase = "cancelled"
        snap = self.dashboard._snapshot(req)
        self.post_message(RequestUpdated(chat_id, snap))
        self._post_log("cancel", f"Cancelled: \"{name}\"")
        # Remove after brief display
        self.set_timer(2.0, lambda: self.dashboard.unregister_request(chat_id))

    @work(name="cancel-all")
    async def _cancel_all_active(self) -> None:
        """Cancel all active foreground requests."""
        active = list(self.dashboard._active.keys())
        count = 0
        for chat_id in active:
            req = self.dashboard._active.get(chat_id)
            if req and req.phase not in ("done", "error", "cancelled"):
                self._cancel_request(chat_id)
                count += 1
        if count == 0:
            self._post_log("info", "Nothing to cancel")
        else:
            self._post_log("cancel", f"Cancelled {count} task(s)")

    @work(name="cancel-bg")
    async def _cancel_all_bg(self) -> None:
        """Cancel all background tasks."""
        if self._task_runner:
            count = await self._task_runner.cancel_all()
            self._post_log("cancel", f"Cancelled {count} background task(s)")
        else:
            self._post_log("info", "No task runner available")

    def action_close_settings(self) -> None:
        """Close settings panel if open (Escape key)."""
        if self._settings_visible:
            self.action_toggle_settings()

    def action_toggle_settings(self) -> None:
        """Toggle settings panel — replaces dashboard panels (3 key)."""
        panel = self.query_one("#settings-panel", SettingsPanel)
        self._settings_visible = not self._settings_visible
        if self._settings_visible:
            panel.add_class("visible")
            self.add_class("settings-open")
            self._load_settings_panel()
            self._post_log("info", "Settings — press 3 or Esc to return to dashboard")
        else:
            panel.remove_class("visible")
            self.remove_class("settings-open")
            self._post_log("info", "Dashboard restored")

    @work(name="load-settings")
    async def _load_settings_panel(self) -> None:
        """Load current settings and render in the settings panel."""
        try:
            from lazyclaw.browser.browser_settings import get_browser_settings
            from lazyclaw.llm.eco_settings import get_eco_settings
            from lazyclaw.permissions.settings import get_permission_settings
            from lazyclaw.runtime.agent_settings import get_agent_settings
            from lazyclaw.teams.settings import get_team_settings

            eco = await get_eco_settings(self._config, self._user_id)
            agent = await get_agent_settings(self._config, self._user_id)
            browser = await get_browser_settings(self._config, self._user_id)
            team = await get_team_settings(self._config, self._user_id)
            perms = await get_permission_settings(self._config, self._user_id)

            panel = self.query_one("#settings-panel", SettingsPanel)
            panel.render_settings(
                eco, agent, browser,
                team=team,
                perms=perms,
                telegram_connected=self._telegram_connected,
                cdp_port=getattr(self._config, "cdp_port", 9222),
            )
        except Exception as exc:
            self._post_log("error", f"Failed to load settings: {exc}")

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

        if text.startswith("/cancel"):
            await self._handle_cancel_command(text)
            return

        if text.startswith("/set "):
            await self._handle_set_command(text)
            return

        if text == "/settings":
            self.action_toggle_settings()
            return

        if text in ("/help", "/"):
            self._post_log("info",
                           "Commands: /clear /status /history /jobs /watchers "
                           "/filter <all|tools|errors|llm> "
                           "/cancel [id|all|bg] /set <key> <value> /settings /help")
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

    async def _handle_cancel_command(self, text: str) -> None:
        """Handle /cancel [id|all|bg] commands."""
        arg = text[7:].strip().lower()

        if not arg or arg == "all":
            # Cancel everything
            await self._cancel_all_active()
            await self._cancel_all_bg()
            return

        if arg == "bg":
            await self._cancel_all_bg()
            return

        # Try cancel by number (card number) or name
        # First try matching against active requests
        for chat_id, req in list(self.dashboard._active.items()):
            if req.phase in ("done", "error", "cancelled"):
                continue
            # Match by card message content or partial name
            if arg in req.message.lower() or arg in chat_id.lower():
                self._cancel_request(chat_id)
                return

        # Try via TeamLead
        if self._team_lead:
            task_id = self._team_lead.find_cancel_target(arg)
            if task_id:
                self._team_lead.cancel(task_id)
                if self._task_runner:
                    await self._task_runner.cancel(task_id, self._user_id)
                self._post_log("cancel", f"Cancelled: \"{arg}\"")
                return

        self._post_log("info", f"No match for \"{arg}\"")

    async def _handle_set_command(self, text: str) -> None:
        """Handle /set <key> <value> commands."""
        parts = text.split(None, 2)
        if len(parts) < 3:
            self._post_log("info", "Usage: /set <eco|brain|worker|budget|team> <value>")
            return

        key = parts[1].lower()
        val = parts[2].strip()

        try:
            if key == "eco":
                from lazyclaw.llm.eco_settings import update_eco_settings
                valid = ("hybrid", "full")
                if val.lower() not in valid:
                    self._post_log("info", f"ECO modes: {', '.join(valid)}")
                    return
                await update_eco_settings(self._config, self._user_id, {"mode": val.lower()})
                self._post_log("info", f"ECO mode → {val.lower()}")

            elif key == "brain":
                from lazyclaw.llm.eco_settings import update_eco_settings
                await update_eco_settings(self._config, self._user_id, {"brain_model": val})
                self._post_log("info", f"Brain model → {val}")

            elif key == "worker":
                from lazyclaw.llm.eco_settings import update_eco_settings
                await update_eco_settings(self._config, self._user_id, {"worker_model": val})
                self._post_log("info", f"Worker model → {val}")

            elif key == "budget":
                from lazyclaw.llm.eco_settings import update_eco_settings
                budget = float(val)
                await update_eco_settings(self._config, self._user_id, {"monthly_paid_budget": budget})
                self._eco_budget = budget
                self._post_log("info", f"Budget → ${budget:.2f}")

            elif key == "team":
                from lazyclaw.runtime.agent_settings import update_agent_settings
                on = val.lower() in ("on", "auto", "true", "1")
                await update_agent_settings(self._config, self._user_id, {"auto_delegate": on})
                self._post_log("info", f"Auto delegate → {'on' if on else 'off'}")

            else:
                self._post_log("info", f"Unknown setting: {key}. Use: eco, brain, worker, budget, team")
                return

            # Refresh settings panel if visible
            if self._settings_visible:
                self._load_settings_panel()

        except Exception as exc:
            self._post_log("error", f"Failed to update {key}: {exc}")

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
                pass  # intentional: context may be empty or malformed, default {} is fine
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
                logger.warning("Failed to decrypt history message for TUI display", exc_info=True)
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
            # Stop MLX server if we started it
            mlx = getattr(self, "_mlx_manager", None)
            if mlx:
                await mlx.stop_all()
            from lazyclaw.db.connection import close_pool
            await close_pool()
        except Exception as exc:
            logger.debug("Cleanup on quit failed: %s", exc)
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
        "cancel": f"bold {_C_ERROR}",
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
        "cancel": "🛑",
    }.get(kind, "  ")
