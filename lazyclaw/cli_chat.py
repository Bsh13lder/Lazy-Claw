"""Chat loop with inline activity stream and compact approvals.

Extracted from cli.py. Handles: CliCallback (inline event display),
compact approval prompts, and agent polling with Ctrl+C support.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.status import Status

from lazyclaw.config import Config
from lazyclaw.runtime.callbacks import AgentEvent
from lazyclaw.cli_dashboard import render_dashboard

logger = logging.getLogger(__name__)

from lazyclaw.runtime.team_lead import TeamLead


def is_status_query(text: str) -> bool:
    """Check if user input is a status query."""
    return TeamLead.is_status_query(text)


_side_input_task: asyncio.Task | None = None


# ---------------------------------------------------------------------------
# Chat context
# ---------------------------------------------------------------------------

@dataclass
class ChatContext:
    """Mutable context shared between chat loop and callbacks."""

    config: Config
    agent: object  # lazyclaw.runtime.agent.Agent
    user_id: str
    console: Console
    pt_session: object  # prompt_toolkit.PromptSession
    chat_session_id: str | None = None
    team_lead: TeamLead | None = None

    session_usage: dict = field(default_factory=lambda: {
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "llm_calls": 0,
        "messages": 0,
        "free_calls": 0,
        "free_tokens": 0,
    })


# ---------------------------------------------------------------------------
# Compact args formatter
# ---------------------------------------------------------------------------

def _format_args_compact(args: dict) -> str:
    """Format tool arguments as compact key=value string."""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        if isinstance(v, str):
            display_v = v[:60] + "..." if len(v) > 60 else v
            parts.append(f'{k}="{display_v}"')
        else:
            parts.append(f"{k}={v}")
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# CLI Callback — inline activity stream with spinner for long waits
# ---------------------------------------------------------------------------

class CliCallback:
    """Inline event stream + spinner for thinking/tool phases.

    Prints each agent step as a permanent line (visible history).
    Spinner only runs during long waits (LLM thinking, tool execution).
    """

    def __init__(self, out: Console) -> None:
        self._console = out
        self._spinner: Status | None = None
        self._streaming = False
        self._paused = False
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.llm_calls = 0
        self.free_calls = 0
        self.free_tokens = 0
        # Live state tracking
        self.busy = False
        self.current_phase = "preparing"
        self.current_model = ""
        self.current_iteration = 0
        self.current_tool = ""
        self.tool_log: list[str] = []
        self.specialists_active: list[str] = []
        self.started_at: float = 0.0
        # Per-tool timing
        self._tool_start_times: dict[str, float] = {}
        # Team tracking
        self._is_team = False
        self._team_specialists: dict[str, dict] = {}
        # Cancellation token (set by agent.py)
        self.cancel_token = None
        # Approval coordination
        self._pending_approval: tuple[str, dict] | None = None
        self._pending_display_name: str = ""
        self._approval_event: asyncio.Event | None = None
        self._approval_result: bool = False
        # Help request coordination (human-in-the-loop)
        self._pending_help: str | None = None
        self._help_needs_browser: bool = False
        self._help_event: asyncio.Event | None = None
        self._help_result: str = "skip"
        # Side channel — messages typed while agent/team works
        self.side_messages: list[str] = []
        # Work summary (stored for inline footer)
        self._work_summary = None

    def start_thinking(self) -> None:
        """Show spinner for initial loading phase."""
        self.busy = True
        self.current_phase = "preparing"
        self.started_at = time.monotonic()
        self._console.print("  [dim]Loading context...[/dim]")
        self._start_spinner(
            "  [bold cyan]\u25cf Preparing...[/bold cyan]"
        )

    def _stop_spinner(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None

    def _start_spinner(self, label: str) -> None:
        """Start animated spinner (shows system is alive during long waits)."""
        self._stop_spinner()
        self._spinner = self._console.status(label, spinner="dots")
        self._spinner.start()

    def _print_team_panel(self) -> None:
        lines = []
        for name in self._team_specialists:
            lines.append(f"  [dim]\u25cb {name}[/dim]")
        self._console.print(
            Panel(
                "\n".join(lines),
                title="[bold cyan]Team[/bold cyan]",
                border_style="cyan",
                width=55,
            )
        )

    async def on_approval_request(
        self, skill_name: str, arguments: dict,
    ) -> bool:
        """Request approval via main loop."""
        self._stop_spinner()
        self._pending_approval = (skill_name, arguments)
        self._approval_event = asyncio.Event()
        self._approval_result = False
        await self._approval_event.wait()
        self._pending_approval = None
        self._approval_event = None
        return self._approval_result

    async def on_help_request(
        self, context: str, needs_browser: bool,
    ) -> str:
        """Request human help via main loop — waits indefinitely."""
        self._stop_spinner()
        self._pending_help = context
        self._help_needs_browser = needs_browser
        self._help_event = asyncio.Event()
        self._help_result = "skip"
        await self._help_event.wait()
        self._pending_help = None
        self._help_event = None
        return self._help_result

    async def on_event(self, event) -> None:  # noqa: C901
        kind = event.kind
        display = event.metadata.get("display_name", event.detail)

        if kind == "llm_call":
            self._stop_spinner()
            model = event.metadata.get("model", "?")
            iteration = event.metadata.get("iteration", 1)
            self.current_phase = "thinking"
            self.current_model = model
            self.current_iteration = iteration
            self.llm_calls += 1
            # Only show thinking line from step 2+ (step 1 is always expected)
            if iteration > 1:
                self._console.print(
                    f"  [cyan]\u25cf[/cyan] [dim]Thinking "
                    f"({model}, step {iteration})...[/dim]"
                )
            self._start_spinner(
                f"  [dim]\u25cf Thinking ({model}, step {iteration})...[/dim]"
            )

        elif kind == "tokens":
            self._stop_spinner()
            tokens = event.metadata.get("total", 0)
            self.total_tokens += tokens
            self.prompt_tokens += event.metadata.get("prompt", 0)
            self.completion_tokens += event.metadata.get("completion", 0)
            eco_mode = event.metadata.get("eco_mode")
            if eco_mode in ("eco", "hybrid_free"):
                self.free_calls += 1
                self.free_tokens += tokens

        elif kind == "tool_call":
            self._stop_spinner()
            self.current_phase = "tool"
            self.current_tool = display
            self.tool_log.append(display)
            tool_key = event.metadata.get("tool", display)
            self._tool_start_times[tool_key] = time.monotonic()
            args = event.metadata.get("args", {})
            args_str = _format_args_compact(args)
            if args_str:
                self._console.print(
                    f"  [yellow]\u25c6 {display}[/yellow]  "
                    f"[dim]{args_str}[/dim]"
                )
            else:
                self._console.print(f"  [yellow]\u25c6 {display}[/yellow]")
            self._start_spinner(f"  [dim]\u25cf {display}...[/dim]")

        elif kind == "tool_result":
            self._stop_spinner()
            self.current_phase = "thinking"
            tool_key = event.metadata.get("tool", display)
            start = self._tool_start_times.pop(tool_key, None)
            dur_str = f" ({time.monotonic() - start:.1f}s)" if start else ""

            error = event.metadata.get("error")
            if error:
                self._console.print(
                    f"  [red]\u2717 {display}{dur_str} \u2014 "
                    f"{str(error)[:80]}[/red]"
                )
            else:
                self._console.print(
                    f"  [green]\u2713[/green] [dim]{display}{dur_str}[/dim]"
                )

        elif kind == "team_delegate":
            self._stop_spinner()
            self.current_phase = "team"
            self.specialists_active.append(event.detail)
            self._console.print(f"  [cyan]% {event.detail}[/cyan]")
            self._start_spinner("  [dim]\u25cf Team evaluating...[/dim]")

        elif kind == "team_start":
            self._stop_spinner()
            self._is_team = True
            self.current_phase = "team"
            specialists = event.metadata.get("specialists", [])
            self._team_specialists = {
                name: {
                    "status": "queued", "start_time": None,
                    "duration_ms": 0, "tools_used": [], "error": None,
                }
                for name in specialists
            }
            self._print_team_panel()
            self._start_spinner("  [cyan]\u25cf Team working...[/cyan]")

        elif kind == "specialist_start":
            self._stop_spinner()
            name = event.metadata.get("specialist", "?")
            if name in self._team_specialists:
                self._team_specialists[name]["status"] = "running"
                self._team_specialists[name]["start_time"] = time.monotonic()
            self._console.print(
                f"  [cyan]\u25cf {name}[/cyan] [dim]started[/dim]"
            )
            self._start_spinner("  [cyan]\u25cf Team working...[/cyan]")

        elif kind == "specialist_thinking":
            # Suppress noisy per-iteration lines — spinner is enough
            name = event.metadata.get("specialist", "?")
            iteration = event.metadata.get("iteration", 1)
            if name in self._team_specialists:
                self._team_specialists[name]["iteration"] = iteration

        elif kind == "specialist_tool":
            name = event.metadata.get("specialist", "?")
            tool = event.metadata.get("tool", "?")
            if name in self._team_specialists:
                self._team_specialists[name].setdefault(
                    "tools_used", []
                ).append(tool)
            self._stop_spinner()
            self._console.print(f"    [dim]{name}: {tool}[/dim]")
            self._start_spinner("  [cyan]\u25cf Team working...[/cyan]")

        elif kind == "specialist_done":
            self._stop_spinner()
            name = event.metadata.get("specialist", "?")
            duration_ms = event.metadata.get("duration_ms", 0)
            success = event.metadata.get("success", True)
            tools = event.metadata.get("tools_used", [])
            error = event.metadata.get("error")
            if name in self._team_specialists:
                self._team_specialists[name]["status"] = (
                    "done" if success else "error"
                )
                self._team_specialists[name]["duration_ms"] = duration_ms
                self._team_specialists[name]["tools_used"] = tools
                self._team_specialists[name]["error"] = error
            if success:
                tools_str = f", {len(tools)} tools" if tools else ""
                self._console.print(
                    f"  [green]\u2713 {name}[/green] "
                    f"[dim]({duration_ms / 1000:.1f}s{tools_str})[/dim]"
                )
            else:
                err_str = f": {error}" if error else ""
                self._console.print(
                    f"  [red]\u2717 {name}[/red] [dim]({err_str})[/dim]"
                )
            self._start_spinner("  [cyan]\u25cf Team working...[/cyan]")

        elif kind == "team_merge":
            self._stop_spinner()
            self._console.print("  [cyan]\u25cf Merging results...[/cyan]")
            self._start_spinner("  [dim]\u25cf Merging...[/dim]")

        elif kind == "work_summary":
            # Store summary for inline footer — no separate panel
            self._stop_spinner()
            self._work_summary = event.metadata.get("summary")

        elif kind == "token":
            if not self._streaming:
                self._stop_spinner()
                self._console.print()
                self._console.print("[green]\u256d\u2500 LazyClaw[/green]")
                self._streaming = True
                self.current_phase = "streaming"
            self._console.print(event.detail, end="", highlight=False)

        elif kind == "stream_done":
            if self._streaming:
                self._console.print()

        elif kind == "background_done":
            self._stop_spinner()
            name = event.metadata.get("name", "?")
            result = event.metadata.get("result", "")
            self._console.print(
                f"\n[green]✅ Background task '{name}' completed[/green]"
            )
            if result:
                preview = result[:200]
                if len(result) > 200:
                    preview += "..."
                self._console.print(f"  [dim]{preview}[/dim]")

        elif kind == "background_failed":
            self._stop_spinner()
            name = event.metadata.get("name", "?")
            error = event.metadata.get("error", "unknown")
            self._console.print(
                f"\n[red]❌ Background task '{name}' failed: {error}[/red]"
            )

        elif kind == "fast_dispatch":
            self._stop_spinner()
            self._console.print(f"\n[cyan]⚡ {event.detail}[/cyan]")

        elif kind == "approval":
            # Handled by main loop — no-op here to avoid duplicate display
            pass

        elif kind == "done":
            self._stop_spinner()
            self.busy = False
            self.current_phase = "done"
            self._is_team = False
            self._team_specialists.clear()
            self.specialists_active.clear()


# ---------------------------------------------------------------------------
# Result display
# ---------------------------------------------------------------------------

def show_user_message(console: Console, msg: str) -> None:
    console.print()
    console.print(f"  [bold cyan]\u276f[/bold cyan] [bold]{msg}[/bold]")


def show_agent_result(console: Console, response: str, cb: CliCallback) -> None:
    cb._stop_spinner()
    console.print()
    if cb._streaming:
        # Streaming response already printed — close the box
        console.print("[green]\u2570\u2500[/green]")
    else:
        if response and response.strip():
            console.print(
                Panel(
                    Markdown(response),
                    title="[bold green]LazyClaw[/bold green]",
                    border_style="green",
                    padding=(0, 1),
                )
            )
        else:
            console.print("  [dim]No response.[/dim]")

    # Inline footer — compact one-line summary
    footer = _build_cli_footer(cb)
    if footer:
        console.print(f"  [dim]{footer}[/dim]")
    console.print()


def _build_cli_footer(cb: CliCallback) -> str:
    """Build compact one-line footer from callback stats."""
    summary = cb._work_summary
    if summary:
        duration_s = summary.duration_ms / 1000
        parts = [f"\u2713 {duration_s:.1f}s"]
        parts.append(f"{summary.llm_calls} LLM")
        if summary.total_tokens:
            parts.append(f"{summary.total_tokens:,} tokens")
        if summary.tools_used:
            parts.append(", ".join(summary.tools_used))
        return " \u2502 ".join(parts)
    # Fallback if no summary (shouldn't happen normally)
    if cb.llm_calls:
        elapsed = time.monotonic() - cb.started_at
        parts = [f"\u2713 {elapsed:.1f}s"]
        parts.append(f"{cb.llm_calls} LLM")
        if cb.total_tokens:
            parts.append(f"{cb.total_tokens:,} tokens")
        if cb.tool_log:
            parts.append(", ".join(cb.tool_log[-4:]))
        return " \u2502 ".join(parts)
    return ""


def accumulate_usage(ctx: ChatContext, cb: CliCallback) -> None:
    ctx.session_usage["total_tokens"] += cb.total_tokens
    ctx.session_usage["prompt_tokens"] += cb.prompt_tokens
    ctx.session_usage["completion_tokens"] += cb.completion_tokens
    ctx.session_usage["llm_calls"] += cb.llm_calls
    ctx.session_usage["messages"] += 1
    ctx.session_usage["free_calls"] += cb.free_calls
    ctx.session_usage["free_tokens"] += cb.free_tokens


# ---------------------------------------------------------------------------
# Chat loop — reliable polling with inline events
# ---------------------------------------------------------------------------

async def run_chat_loop(
    ctx: ChatContext,
    handle_slash_command,
) -> None:
    """Main chat loop. Agent events display inline. Ctrl+C cancels."""
    from prompt_toolkit.formatted_text import HTML

    con = ctx.console
    agent_task: asyncio.Task | None = None
    active_callback: CliCallback | None = None

    async def _get_input() -> str:
        prompt = HTML("<cyan><b>&gt; </b></cyan>")
        return await ctx.pt_session.prompt_async(prompt)

    async def _run_agent(msg: str, cb: CliCallback) -> str:
        return await ctx.agent.process_message(
            ctx.user_id, msg, chat_session_id=ctx.chat_session_id,
            callback=cb,
        )

    global _side_input_task

    # Ctrl+C handling — double-press support (first graceful, second force)
    _cancel_requested = False

    def _sigint_handler():
        nonlocal _cancel_requested
        if _cancel_requested:
            # Second Ctrl+C — force cancel immediately
            if agent_task and not agent_task.done():
                agent_task.cancel()
            return
        _cancel_requested = True
        # Kill side input so prompt_toolkit releases stdin
        if _side_input_task and not _side_input_task.done():
            _side_input_task.cancel()

    loop = asyncio.get_event_loop()

    while True:
        # ----- Agent running: poll for completion + approvals -----
        if agent_task is not None and not agent_task.done():
            _cancel_requested = False

            _signal_installed = False
            try:
                import signal as _sig
                loop.add_signal_handler(_sig.SIGINT, _sigint_handler)
                _signal_installed = True
            except (NotImplementedError, OSError, AttributeError):
                pass

            _input_hint_shown = False

            try:
                while not agent_task.done():
                    # Check Ctrl+C
                    if _cancel_requested:
                        if active_callback and active_callback.cancel_token:
                            active_callback.cancel_token.cancel()
                        agent_task.cancel()
                        if active_callback:
                            active_callback._stop_spinner()
                        con.print("\n  [yellow]Cancelled.[/yellow]")
                        break

                    # Check for pending approval — compact format
                    if active_callback and active_callback._pending_approval:
                        # Cancel side prompt if active
                        if _side_input_task and not _side_input_task.done():
                            _side_input_task.cancel()
                        _side_input_task = None
                        active_callback._stop_spinner()
                        skill_name, args = active_callback._pending_approval
                        display = (
                            active_callback._pending_display_name or skill_name
                        )
                        args_str = _format_args_compact(args)
                        con.print()
                        if args_str:
                            con.print(
                                f"  [yellow]\u26a1 {display}[/yellow]  "
                                f"[dim]{args_str}[/dim]"
                            )
                        else:
                            con.print(
                                f"  [yellow]\u26a1 {display}[/yellow]"
                            )
                        approved = await loop.run_in_executor(
                            None,
                            lambda: Confirm.ask(
                                "  Allow?", default=True,
                            ),
                        )
                        active_callback._approval_result = approved
                        active_callback._approval_event.set()
                        continue

                    # Check for pending help request (human-in-the-loop)
                    if active_callback and active_callback._pending_help:
                        # Cancel side prompt if active
                        if _side_input_task and not _side_input_task.done():
                            _side_input_task.cancel()
                        _side_input_task = None
                        active_callback._stop_spinner()
                        con.print()
                        con.print(
                            f"  [bold yellow]\U0001f198 {active_callback._pending_help}[/bold yellow]"
                        )
                        if active_callback._help_needs_browser:
                            con.print(
                                "  [dim]Type 'ready' to take over the browser, "
                                "or 'skip' to move on:[/dim]"
                            )
                        else:
                            con.print(
                                "  [dim]Type 'done' when finished, "
                                "or 'skip' to move on:[/dim]"
                            )
                        # Loop until non-empty answer (prevents terminal
                        # race conditions from auto-skipping on empty input)
                        _help_answer = ""
                        while not _help_answer:
                            _help_answer = await loop.run_in_executor(
                                None,
                                lambda: con.input("  > "),
                            )
                            _help_answer = _help_answer.strip().lower()
                        active_callback._help_result = _help_answer
                        # Clear _pending_help BEFORE signalling the event
                        # to prevent the main loop from re-detecting it
                        # on the next iteration (race condition).
                        active_callback._pending_help = None
                        if active_callback._help_event is not None:
                            active_callback._help_event.set()
                        continue

                    # Start async side-channel input via prompt_toolkit
                    if _side_input_task is None or _side_input_task.done():
                        if not _input_hint_shown:
                            if active_callback:
                                active_callback._stop_spinner()
                            con.print(
                                "  [dim]\u2500\u2500\u2500 type to add context "
                                "(Enter to send) | Ctrl+C to cancel "
                                "\u2500\u2500\u2500[/dim]"
                            )
                            _input_hint_shown = True

                        async def _side_prompt():
                            try:
                                from prompt_toolkit.formatted_text import HTML
                                return await ctx.pt_session.prompt_async(
                                    HTML("<dim>  &gt; </dim>"),
                                    handle_sigint=False,
                                )
                            except KeyboardInterrupt:
                                nonlocal _cancel_requested
                                _cancel_requested = True
                                return ""
                            except (EOFError, asyncio.CancelledError):
                                return ""

                        _side_input_task = asyncio.create_task(_side_prompt())

                    # Check if user typed something
                    if _side_input_task is not None and _side_input_task.done():
                        try:
                            user_text = _side_input_task.result()
                        except (EOFError, KeyboardInterrupt, asyncio.CancelledError, Exception):
                            user_text = ""
                        _side_input_task = None

                        stripped = user_text.strip()
                        if stripped:
                            if active_callback:
                                active_callback._stop_spinner()
                            if stripped.lower() in ("/cancel", "/stop"):
                                if active_callback and active_callback.cancel_token:
                                    active_callback.cancel_token.cancel()
                                agent_task.cancel()
                                con.print(
                                    "\n  [yellow]Cancelled.[/yellow]"
                                )
                                break
                            elif is_status_query(stripped):
                                if ctx.team_lead:
                                    con.print(ctx.team_lead.format_status())
                                else:
                                    con.print(render_dashboard(active_callback))
                            else:
                                # Side channel — add to merge context
                                if active_callback:
                                    active_callback.side_messages.append(
                                        stripped
                                    )
                                con.print(
                                    f"  [dim]\u2192 Noted: "
                                    f"{stripped[:60]}[/dim]"
                                )
                            # Resume spinner after handling input
                            if active_callback:
                                active_callback._start_spinner(
                                    "  [dim]\u25cf Working...[/dim]"
                                )

                    # Poll agent (0.1s for responsive Ctrl+C)
                    done_set, _ = await asyncio.wait(
                        {agent_task}, timeout=0.1,
                    )
                    if done_set:
                        break

                # Handle result
                if agent_task.done() and not agent_task.cancelled():
                    try:
                        response = agent_task.result()
                        show_agent_result(con, response, active_callback)
                        accumulate_usage(ctx, active_callback)
                    except Exception as e:
                        if active_callback:
                            active_callback._stop_spinner()
                        con.print(f"\n  [red]Error: {e}[/red]")
                elif agent_task.cancelled():
                    pass

            except asyncio.CancelledError:
                if active_callback:
                    active_callback._stop_spinner()
                con.print("  [yellow]Cancelled.[/yellow]")
            except Exception as e:
                if active_callback:
                    active_callback._stop_spinner()
                con.print(f"[red]Error: {e}[/red]")
            finally:
                if _signal_installed:
                    try:
                        import signal as _sig
                        loop.remove_signal_handler(_sig.SIGINT)
                    except (NotImplementedError, OSError):
                        pass
                # Cancel side-channel prompt if still waiting
                if _side_input_task is not None and not _side_input_task.done():
                    _side_input_task.cancel()
                    try:
                        await _side_input_task
                    except (asyncio.CancelledError, Exception):
                        pass
                _side_input_task = None

            agent_task = None
            active_callback = None
            continue

        # ----- No agent running: normal input -----
        try:
            user_input = await _get_input()
        except (EOFError, KeyboardInterrupt):
            con.print("\n[yellow]Goodbye![/yellow]")
            break

        stripped = user_input.strip()
        if not stripped:
            continue

        if stripped.lower() in ("/exit", "/quit", "/q"):
            con.print("[yellow]Goodbye![/yellow]")
            break

        if stripped.lower() == "/clear":
            ctx.chat_session_id = None
            for k in ctx.session_usage:
                ctx.session_usage[k] = 0
            con.print("[green]Chat session cleared.[/green]")
            continue

        if stripped.startswith("/"):
            handled = await handle_slash_command(
                stripped, ctx.config, ctx.user_id,
            )
            if handled:
                con.print()
                continue
            con.print(
                f"[yellow]Unknown command: {stripped.split()[0]}. "
                f"Try /help[/yellow]"
            )
            continue

        # Chat with agent
        show_user_message(con, stripped)
        callback = CliCallback(con)
        callback.start_thinking()
        active_callback = callback
        agent_task = asyncio.create_task(_run_agent(stripped, callback))

    # Graceful shutdown
    from lazyclaw.mcp.manager import disconnect_all
    try:
        await asyncio.wait_for(disconnect_all(), timeout=3)
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):
        pass
