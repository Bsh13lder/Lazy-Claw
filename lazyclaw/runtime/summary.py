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
    )


def format_summary_cli(summary: WorkSummary) -> str:
    """Format a WorkSummary for Rich CLI display.

    Returns a string with Rich markup for styled terminal output.
    """
    duration_s = summary.duration_ms / 1000
    lines = [
        f"[bold]Done[/bold] in {duration_s:.1f}s | "
        f"{summary.llm_calls} LLM calls | "
        f"{summary.total_tokens:,} tokens",
    ]

    if summary.tools_used:
        tools_str = ", ".join(summary.tools_used)
        lines.append(f"[dim]Tools: {tools_str}[/dim]")

    if summary.specialists_used:
        lines.append("")
        lines.append("[bold]Specialists:[/bold]")
        for name in summary.specialists_used:
            lines.append(f"  [green]\u2713[/green] {name}")

    return "\n".join(lines)


def format_summary_telegram(summary: WorkSummary) -> str:
    """Format a WorkSummary for Telegram plain text with emoji.

    Returns a plain text string suitable for Telegram messages.
    """
    duration_s = summary.duration_ms / 1000
    lines = [
        f"\u2705 Done in {duration_s:.1f}s | "
        f"{summary.llm_calls} LLM calls | "
        f"{summary.total_tokens:,} tokens",
    ]

    if summary.tools_used:
        tools_str = ", ".join(summary.tools_used)
        lines.append(f"Tools: {tools_str}")

    if summary.specialists_used:
        lines.append("")
        lines.append("Specialists:")
        for name in summary.specialists_used:
            lines.append(f"  \u2713 {name}")

    return "\n".join(lines)
