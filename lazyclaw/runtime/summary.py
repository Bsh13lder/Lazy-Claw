"""Work summary builder and formatters for CLI and Telegram.

Pure data formatting — no LLM calls. Builds WorkSummary from accumulated
callback data and formats it for display in different channels.
"""

from __future__ import annotations

import time

from lazyclaw.runtime.events import WorkSummary


def build_work_summary(
    start_time: float,
    llm_calls: int,
    tools_used: list[str],
    specialists: list[str],
    total_tokens: int,
    user_message: str,
    response: str,
    models_used: list[tuple[str, str, bool]] | None = None,
    total_cost: float = 0.0,
) -> WorkSummary:
    """Build a WorkSummary from accumulated agent data.

    Args:
        start_time: monotonic timestamp when processing started.
        llm_calls: number of LLM iterations completed.
        tools_used: list of tool names invoked (may contain duplicates).
        specialists: list of specialist names used (empty for direct mode).
        total_tokens: total tokens consumed across all LLM calls.
        user_message: the original user message.
        response: the final agent response text.
        models_used: list of (display_name, icon, is_local) tuples.
        total_cost: total USD cost for this request.
    """
    duration_ms = int((time.monotonic() - start_time) * 1000)

    # Deduplicate tools while preserving first-seen order
    seen: set[str] = set()
    unique_tools: list[str] = []
    for tool in tools_used:
        if tool not in seen:
            seen.add(tool)
            unique_tools.append(tool)

    return WorkSummary(
        duration_ms=duration_ms,
        llm_calls=llm_calls,
        tools_used=tuple(unique_tools),
        specialists_used=tuple(specialists),
        total_tokens=total_tokens,
        mode="team" if specialists else "direct",
        task_description=user_message[:100],
        result_preview=response[:200],
        models_used=tuple(models_used or ()),
        total_cost=total_cost,
    )


def format_summary_cli(summary: WorkSummary) -> str:
    """Format a WorkSummary for Rich CLI display.

    Returns a string with Rich markup for styled terminal output.
    Cost details are in /usage only — keep summary clean.
    """
    duration_s = summary.duration_ms / 1000

    lines = [
        f"[bold]Done[/bold] in {duration_s:.1f}s | "
        f"{summary.llm_calls} LLM calls | "
        f"{summary.total_tokens:,} tokens",
    ]

    # Model attribution
    if summary.models_used:
        model_parts = []
        for display_name, icon, is_local in summary.models_used:
            tag = "LOCAL" if is_local else "PAID"
            model_parts.append(f"{icon} {display_name} [{tag}]")
        lines.append(f"[dim]AI: {' + '.join(model_parts)}[/dim]")

    if summary.tools_used:
        tools_str = ", ".join(summary.tools_used)
        lines.append(f"[dim]Tools: {tools_str}[/dim]")

    if summary.specialists_used:
        lines.append("")
        lines.append("[bold]Specialists:[/bold]")
        for name in summary.specialists_used:
            lines.append(f"  [green]\u2713[/green] {name}")

    return "\n".join(lines)


def format_response_footer(summary: WorkSummary) -> str:
    """One-line footer for Telegram response messages.

    With model attribution:
      "✅ 12.3s │ 🤖 gemma4:e2b [LOCAL] │ FREE"
      "✅ 15.2s │ 💰 gpt-5-mini │ $0.003"
    Without (backward compat):
      "✅ 12.3s │ 3 LLM │ 1,847 tokens"
    """
    duration_s = summary.duration_ms / 1000
    parts = [f"\u2705 {duration_s:.1f}s"]

    if summary.models_used:
        # Show primary model with icon and LOCAL/PAID tag
        primary = summary.models_used[0]
        display_name, icon, is_local = primary
        label = f"{icon} {display_name}"
        if is_local:
            label += " [LOCAL]"
        parts.append(label)

        # If multiple models used, show extras compactly
        if len(summary.models_used) > 1:
            extras = [f"{m[1]} {m[0]}" for m in summary.models_used[1:]]
            parts.append(" + ".join(extras))

        # Cost
        if summary.total_cost > 0:
            parts.append(f"${summary.total_cost:.4f}")
        elif all(m[2] for m in summary.models_used):
            parts.append("FREE")
    else:
        # Backward compat: no model info available
        parts.append(f"{summary.llm_calls} LLM")
        if summary.total_tokens:
            parts.append(f"{summary.total_tokens:,} tokens")

    return " \u2502 ".join(parts)


# Keep old name as alias for backward compatibility
def format_summary_telegram(summary: WorkSummary) -> str:
    """Deprecated: use format_response_footer instead."""
    return format_response_footer(summary)
