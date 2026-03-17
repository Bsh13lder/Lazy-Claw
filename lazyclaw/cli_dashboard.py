"""Rich process dashboard for CLI agent observability.

Renders a static snapshot of current agent state as a Rich Panel.
Called on-demand via /? or status queries while agent is working.
"""

from __future__ import annotations

import time

from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def render_dashboard(callback) -> Panel:
    """Build a Rich Panel showing current agent state.

    Args:
        callback: A _CliCallback instance with live state tracking.

    Returns:
        A Rich Panel renderable for console.print().
    """
    lines = Text()

    # Header — phase and elapsed time
    elapsed_s = 0.0
    if hasattr(callback, "started_at") and callback.started_at:
        elapsed_s = time.monotonic() - callback.started_at

    phase_display = _format_phase(callback.current_phase)
    lines.append("Phase: ", style="bold")
    lines.append(phase_display)
    lines.append(f"  {elapsed_s:.1f}s\n", style="dim")

    # Model and LLM calls
    if callback.current_model:
        lines.append("Model: ", style="bold")
        lines.append(f"{callback.current_model}", style="cyan")
        lines.append(f"    LLM calls: {callback.llm_calls}\n")

    # Token counts
    if callback.total_tokens > 0:
        lines.append("Tokens: ", style="bold")
        lines.append(f"{callback.total_tokens:,}")
        lines.append(
            f" ({callback.prompt_tokens:,} in / "
            f"{callback.completion_tokens:,} out)\n",
            style="dim",
        )

    # Current tool (if running one)
    if callback.current_phase == "tool" and callback.current_tool:
        lines.append("\n")
        lines.append("Current: ", style="bold")
        lines.append(f"{callback.current_tool}\n", style="yellow")

    # Team specialists
    if callback._is_team and callback._team_specialists:
        lines.append("\n")
        lines.append("Specialists:\n", style="bold")
        for name, state in callback._team_specialists.items():
            _append_specialist_line(lines, name, state)

    # Recent activity log
    if callback.tool_log:
        lines.append("\n")
        lines.append("Recent:\n", style="bold")
        recent = callback.tool_log[-6:]
        for entry in recent:
            lines.append(f"  {entry}\n", style="dim")

    return Panel(
        lines,
        title="[bold cyan]Agent Status[/bold cyan]",
        border_style="cyan",
        width=60,
        padding=(0, 1),
    )


def _format_phase(phase: str) -> str:
    """Map phase string to a human-readable display name."""
    return {
        "preparing": "Preparing...",
        "thinking": "Thinking",
        "tool": "Running Tool",
        "team": "Team Mode",
        "streaming": "Streaming Response",
        "done": "Done",
    }.get(phase, phase.title())


def _append_specialist_line(lines: Text, name: str, state: dict) -> None:
    """Append a single specialist status line to the Text object."""
    status = state.get("status", "queued")

    icon_map = {"queued": "\u25cb", "running": "\u25cf", "done": "\u2713", "error": "\u2717"}
    style_map = {"queued": "dim", "running": "cyan", "done": "green", "error": "red"}

    icon = icon_map.get(status, "\u25cb")
    style = style_map.get(status, "dim")

    # Elapsed or duration
    timing = ""
    if status == "running" and state.get("start_time"):
        elapsed = time.monotonic() - state["start_time"]
        timing = f"  {elapsed:.1f}s"
    elif state.get("duration_ms"):
        timing = f"  {state['duration_ms'] / 1000:.1f}s"

    # Tools used
    tools = state.get("tools_used", [])
    tools_str = f"  [{', '.join(tools[-3:])}]" if tools else ""

    lines.append(f"  {icon} ", style=style)
    lines.append(f"{name:<20}", style=style)
    lines.append(f"{status}", style=style)
    lines.append(f"{timing}{tools_str}\n", style="dim")
