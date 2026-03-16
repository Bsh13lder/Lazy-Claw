"""LazyClaw CLI — Unified chat REPL with built-in slash commands."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import uuid

# Suppress httpx async cleanup warnings (Python 3.11 + httpx issue)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.status import Status
from rich.table import Table

from lazyclaw import __version__
from lazyclaw.config import Config, get_project_root, load_config, save_env

console = Console()

LOGO = r"""
 _                     _____ _
| |    __ _ _____   _ / ____| |
| |   / _` |_  / | | | |    | | __ ___      __
| |  | (_| |/ /| |_| | |    | |/ _` \ \ /\ / /
| |___\__,_/___|\__, | |____| | (_| |\ V  V /
|______\        |___/ \_____|_|\__,_| \_/\_/
"""

HELP_TEXT = """\
[bold]Chat:[/bold] Just type your message and press Enter.

[bold]Info:[/bold]
  /status      System dashboard (config, stats, modes)
  /users       List all users
  /skills      List skills with permissions
  /traces      Show recent session traces
  /teams       Team config and specialists
  /mcp         MCP server connections
  /compression Context compression stats
  /history     Recent conversation messages
  /logs        Recent agent activity (tool calls, LLM)
  /usage       Token usage + cost estimate (EUR)
  /doctor      Health check (DB, AI, MCP, encryption)

[bold]Settings:[/bold]
  /critic off|on|auto    Set critic mode
  /team off|on|auto      Set team mode
  /eco eco|hybrid|full   Set ECO mode
  /model <name>          Change default model
  /permissions           Show permission levels for all categories
  /allow <name>          Allow a category or skill (e.g. /allow computer)
  /deny <name>           Deny a category or skill (e.g. /deny vault)

[bold]System:[/bold]
  /update      Pull latest code + reinstall deps
  /version     Show current version

[bold]Session:[/bold]
  /clear       Start fresh chat session
  /wipe        Clear all conversation history
  /help        Show this help
  /exit        Quit (also /quit, /q)"""


# ---------------------------------------------------------------------------
# Async helpers (setup wizard)
# ---------------------------------------------------------------------------

async def verify_provider_async(provider: str, key: str) -> bool:
    from lazyclaw.llm.router import LLMRouter

    tmp_config = Config()
    if provider == "openai":
        tmp_config.openai_api_key = key
    elif provider == "anthropic":
        tmp_config.anthropic_api_key = key

    router = LLMRouter(tmp_config)
    return await router.verify_provider(provider, key)


async def verify_telegram_async(token: str) -> dict | None:
    from lazyclaw.channels.telegram import TelegramAdapter

    return await TelegramAdapter.verify_token(token)


async def setup_database(config: Config) -> None:
    from lazyclaw.db.connection import db_session, init_db

    await init_db(config)
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT id FROM users WHERE username = ?", ("default",)
        )
        if not (await row.fetchone()):
            user_id = str(uuid.uuid4())
            salt = secrets.token_urlsafe(16)
            pw_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
            await db.execute(
                "INSERT INTO users (id, username, password_hash, encryption_salt, role) VALUES (?, ?, ?, ?, ?)",
                (user_id, "default", pw_hash, salt, "admin"),
            )
            await db.commit()


async def run_agent(config: Config) -> None:
    from lazyclaw.db.connection import init_db
    from lazyclaw.llm.router import LLMRouter
    from lazyclaw.runtime.agent import Agent
    from lazyclaw.skills.registry import SkillRegistry

    await init_db(config)

    from lazyclaw.permissions.checker import PermissionChecker

    router = LLMRouter(config)
    registry = SkillRegistry()
    registry.register_defaults(config=config)
    permission_checker = PermissionChecker(config, registry)
    agent = Agent(config, router, registry, permission_checker=permission_checker)

    # Lane Queue
    from lazyclaw.queue.lane import LaneQueue

    lane_queue = LaneQueue()
    lane_queue.set_handler(agent.process_message)
    await lane_queue.start()
    console.print("[green]\u2713[/green] Lane queue started")

    from lazyclaw.gateway.app import set_lane_queue
    set_lane_queue(lane_queue)

    tasks: list = []

    import uvicorn

    uvi_config = uvicorn.Config(
        "lazyclaw.gateway.app:app",
        host="0.0.0.0",
        port=config.port,
        log_level="warning",
    )
    server = uvicorn.Server(uvi_config)
    tasks.append(server.serve())

    telegram = None
    if config.telegram_bot_token:
        from lazyclaw.channels.telegram import TelegramAdapter

        telegram = TelegramAdapter(config.telegram_bot_token, agent, config, lane_queue=lane_queue)
        await telegram.start()
        console.print("[green]\u2713[/green] Telegram bot running")

    from lazyclaw.heartbeat.daemon import HeartbeatDaemon

    heartbeat = HeartbeatDaemon(config, lane_queue)
    await heartbeat.start()
    console.print("[green]\u2713[/green] Heartbeat daemon started")

    console.print(f"[green]\u2713[/green] API running at http://localhost:{config.port}")
    console.print()
    console.print("[bold]LazyClaw is live![/bold] Send a message to your Telegram bot.")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("\n[yellow]Shutting down...[/yellow]")
        await heartbeat.stop()
        if telegram:
            await telegram.stop()
        await lane_queue.stop()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _get_default_user(config: Config) -> str:
    """Get the default user ID, creating one if needed."""
    from lazyclaw.db.connection import db_session

    async with db_session(config) as db:
        row = await db.execute(
            "SELECT id FROM users WHERE username = ?", ("default",)
        )
        result = await row.fetchone()
        if result:
            return result[0]

    await setup_database(config)
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT id FROM users WHERE username = ?", ("default",)
        )
        result = await row.fetchone()
        return result[0]


# ---------------------------------------------------------------------------
# Chat REPL — the main experience
# ---------------------------------------------------------------------------

async def _handle_slash_command(
    cmd: str, config: Config, user_id: str,
) -> bool:
    """Handle a slash command. Returns True if handled, False if not."""
    from lazyclaw.cli_admin import (
        clear_history,
        run_doctor,
        set_critic_mode,
        set_eco_mode,
        set_model,
        set_team_mode,
        show_compression,
        show_logs,
        show_mcp,
        show_skills,
        show_status,
        show_teams,
        show_traces,
        show_users,
    )

    parts = cmd.strip().split()
    command = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else None

    # Info commands
    handlers = {
        "/status": lambda: show_status(config, user_id),
        "/users": lambda: show_users(config),
        "/skills": lambda: show_skills(config, user_id),
        "/traces": lambda: show_traces(config, user_id),
        "/teams": lambda: show_teams(config, user_id),
        "/mcp": lambda: show_mcp(config, user_id),
        "/compression": lambda: show_compression(config, user_id),
        "/logs": lambda: show_logs(config, user_id),
        "/usage": lambda: _show_usage(config),
        "/doctor": lambda: run_doctor(config, user_id),
        "/update": lambda: _run_update(),
        "/version": lambda: _show_version(),
        "/wipe": lambda: clear_history(config, user_id),
        "/history": lambda: _show_chat_history(config, user_id),
    }

    if command in ("/help", "/"):
        console.print(Panel(HELP_TEXT, title="Help", border_style="cyan"))
        return True

    if command in handlers:
        await handlers[command]()
        return True

    # Settings commands (require an argument)
    settings_commands = {
        "/critic": lambda a: set_critic_mode(config, user_id, a),
        "/team": lambda a: set_team_mode(config, user_id, a),
        "/eco": lambda a: set_eco_mode(config, user_id, a),
        "/model": lambda a: set_model(config, a),
    }

    if command in settings_commands:
        if not arg:
            console.print(f"[yellow]Usage: {command} <value>. Try /help[/yellow]")
        else:
            # For /model, preserve original case
            actual_arg = parts[1] if command == "/model" else arg
            await settings_commands[command](actual_arg)
        return True

    # Permission commands
    if command == "/permissions":
        await show_permissions(config, user_id)
        return True

    if cmd.startswith("/allow "):
        target = cmd[7:].strip()
        if target:
            await set_permission(config, user_id, target, "allow")
        else:
            console.print("[yellow]Usage: /allow <category|skill>[/yellow]")
        return True

    if cmd.startswith("/deny "):
        target = cmd[6:].strip()
        if target:
            await set_permission(config, user_id, target, "deny")
        else:
            console.print("[yellow]Usage: /deny <category|skill>[/yellow]")
        return True

    return False


async def _show_chat_history(config: Config, user_id: str) -> None:
    """Show recent messages for the current user."""
    from lazyclaw.crypto.encryption import decrypt, derive_server_key
    from lazyclaw.db.connection import db_session

    key = derive_server_key(config.server_secret, user_id)

    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT role, content, created_at FROM agent_messages "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        )
        messages = await rows.fetchall()

    if not messages:
        console.print("[dim]No conversation history.[/dim]")
        return

    table = Table(title="Recent Messages", style="cyan")
    table.add_column("Time", style="dim", width=19)
    table.add_column("Role", style="bold", width=10)
    table.add_column("Content", max_width=60)

    for row in reversed(messages):
        role, content_enc, created_at = row[0], row[1], row[2]
        try:
            content = decrypt(content_enc, key) if content_enc.startswith("enc:") else content_enc
        except Exception:
            content = "[encrypted]"
        display = content[:80] + "..." if len(content) > 80 else content
        role_style = "cyan" if role == "user" else "green" if role == "assistant" else "dim"
        table.add_row(created_at or "", f"[{role_style}]{role}[/{role_style}]", display)

    console.print(table)


async def _show_version() -> None:
    """Show current version and install info."""
    console.print(f"  [bold cyan]LazyClaw[/bold cyan] v{__version__}")
    console.print(f"  [dim]Install: pip install -e . (editable mode)[/dim]")
    console.print(f"  [dim]Code changes take effect immediately — no reinstall needed.[/dim]")


async def _run_update() -> None:
    """Pull latest code from git and reinstall dependencies."""
    import subprocess

    root = get_project_root()

    console.print("[bold cyan]Updating LazyClaw...[/bold cyan]")
    console.print()

    # 1. Git pull
    console.print("  [dim]Pulling latest code...[/dim]")
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            if "Already up to date" in output:
                console.print("  [green]Already up to date.[/green]")
            else:
                console.print(f"  [green]Pulled:[/green] {output.splitlines()[-1]}")
        else:
            console.print(f"  [red]Git pull failed:[/red] {result.stderr.strip()}")
            return
    except FileNotFoundError:
        console.print("  [red]git not found. Update manually.[/red]")
        return
    except subprocess.TimeoutExpired:
        console.print("  [red]Git pull timed out.[/red]")
        return

    # 2. Reinstall deps
    console.print("  [dim]Reinstalling dependencies...[/dim]")
    try:
        result = subprocess.run(
            ["pip", "install", "-e", ".", "-q"],
            cwd=root, capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            console.print("  [green]Dependencies updated.[/green]")
        else:
            console.print(f"  [yellow]pip install warning:[/yellow] {result.stderr.strip()[:100]}")
    except FileNotFoundError:
        # Try pip3
        try:
            result = subprocess.run(
                ["pip3", "install", "-e", ".", "-q"],
                cwd=root, capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                console.print("  [green]Dependencies updated.[/green]")
            else:
                console.print(f"  [yellow]pip3 warning:[/yellow] {result.stderr.strip()[:100]}")
        except FileNotFoundError:
            console.print("  [red]pip/pip3 not found.[/red]")
    except subprocess.TimeoutExpired:
        console.print("  [yellow]pip install timed out (deps may still be installing).[/yellow]")

    # 3. Show new version
    console.print()

    # Re-read version from file (may have changed after git pull)
    init_path = root / "lazyclaw" / "__init__.py"
    new_version = __version__
    if init_path.exists():
        for line in init_path.read_text().splitlines():
            if line.startswith("__version__"):
                new_version = line.split("=")[1].strip().strip('"').strip("'")
                break

    console.print(f"  [bold green]LazyClaw v{new_version}[/bold green]")
    if new_version != __version__:
        console.print(f"  [yellow]Restart the CLI to use the new version.[/yellow]")
    else:
        console.print("  [dim]No version change. Code updates are live (editable install).[/dim]")


class _CliCallback:
    """Shows live agent activity in the terminal with animated spinner."""

    def __init__(self, out: Console) -> None:
        self._console = out
        self._spinner: Status | None = None
        self._streaming = False
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.llm_calls = 0
        self.free_calls = 0
        self.free_tokens = 0

    def start_thinking(self) -> None:
        """Show spinner immediately when user submits a message."""
        self._spinner = self._console.status(
            "  [dim]Preparing...[/dim]", spinner="dots"
        )
        self._spinner.start()

    def _stop_spinner(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None

    async def on_approval_request(
        self, skill_name: str, arguments: dict
    ) -> bool:
        """Prompt the CLI user for inline y/n approval."""
        self._stop_spinner()
        import json as _json
        args_display = _json.dumps(arguments, indent=2) if arguments else "{}"
        self._console.print()
        self._console.print(
            Panel(
                f"[bold]{skill_name}[/bold]\n[dim]{args_display}[/dim]",
                title="[yellow]Approval Required[/yellow]",
                border_style="yellow",
            )
        )
        result = Confirm.ask("  Allow this action?", default=True)
        if result:
            self._spinner = self._console.status(
                f"  [dim]> Running {skill_name}...[/dim]", spinner="dots"
            )
            self._spinner.start()
        return result

    async def on_event(self, event) -> None:
        kind = event.kind
        if kind == "llm_call":
            self._stop_spinner()
            model = event.metadata.get("model", "?")
            iteration = event.metadata.get("iteration", 1)
            label = f"  [bold cyan]Thinking[/bold cyan] [dim]({model}, step {iteration})...[/dim]"
            self._spinner = self._console.status(label, spinner="dots")
            self._spinner.start()
            self.llm_calls += 1
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
            self._spinner = self._console.status(
                f"  [dim]> {event.detail}[/dim]", spinner="dots"
            )
            self._spinner.start()
        elif kind == "tool_result":
            self._stop_spinner()
            self._console.print(f"  [green]< {event.detail}[/green]")
        elif kind == "team_delegate":
            self._stop_spinner()
            self._spinner = self._console.status(
                f"  [cyan]% {event.detail}[/cyan]", spinner="dots"
            )
            self._spinner.start()
        elif kind == "token":
            if not self._streaming:
                self._stop_spinner()
                self._console.print()  # newline before streamed content
                self._streaming = True
            self._console.print(event.detail, end="", highlight=False)
        elif kind == "stream_done":
            if self._streaming:
                self._console.print()  # newline after streamed content
        elif kind == "approval":
            self._stop_spinner()
            self._console.print(f"  [yellow]! {event.detail}[/yellow]")
        elif kind == "done":
            self._stop_spinner()


async def _show_usage(config: Config) -> None:
    """Show session token usage with EUR cost estimate and ECO savings."""
    total_calls = _session_usage["llm_calls"]
    free_calls = _session_usage["free_calls"]
    paid_calls = total_calls - free_calls
    free_tokens = _session_usage["free_tokens"]
    paid_tokens = _session_usage["total_tokens"] - free_tokens

    table = Table(title="Session Token Usage", style="cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Messages", str(_session_usage["messages"]))
    table.add_row("LLM Calls", f"{total_calls} ({paid_calls} paid, {free_calls} free)")
    table.add_row("Prompt Tokens", f"{_session_usage['prompt_tokens']:,}")
    table.add_row("Completion Tokens", f"{_session_usage['completion_tokens']:,}")
    table.add_row("Total Tokens", f"{_session_usage['total_tokens']:,}")
    if free_tokens > 0:
        table.add_row("[green]Free Tokens (ECO)[/green]", f"[green]{free_tokens:,}[/green]")
        table.add_row("Paid Tokens", f"{paid_tokens:,}")
    console.print(table)
    console.print()

    # Cost estimate — only paid tokens cost money
    model = config.default_model
    pricing = _MODEL_PRICING.get(model, (5.0, 15.0))

    # Actual cost (only paid tokens)
    paid_input = _session_usage["prompt_tokens"] - (free_tokens // 2)  # rough split
    paid_output = _session_usage["completion_tokens"] - (free_tokens - free_tokens // 2)
    paid_input = max(paid_input, 0)
    paid_output = max(paid_output, 0)

    actual_input_usd = (paid_input / 1_000_000) * pricing[0]
    actual_output_usd = (paid_output / 1_000_000) * pricing[1]
    actual_usd = actual_input_usd + actual_output_usd
    actual_eur = actual_usd * _EUR_PER_USD

    # What it would have cost without ECO (all tokens at paid rate)
    full_input_usd = (_session_usage["prompt_tokens"] / 1_000_000) * pricing[0]
    full_output_usd = (_session_usage["completion_tokens"] / 1_000_000) * pricing[1]
    full_usd = full_input_usd + full_output_usd
    full_eur = full_usd * _EUR_PER_USD

    saved_usd = full_usd - actual_usd
    saved_eur = full_eur - actual_eur

    cost_table = Table(title=f"Cost Estimate ({model})", style="cyan")
    cost_table.add_column("", style="bold")
    cost_table.add_column("USD", justify="right")
    cost_table.add_column("EUR", justify="right")

    cost_table.add_row(
        "Input (paid)", f"${actual_input_usd:.4f}", f"\u20ac{actual_input_usd * _EUR_PER_USD:.4f}",
    )
    cost_table.add_row(
        "Output (paid)", f"${actual_output_usd:.4f}", f"\u20ac{actual_output_usd * _EUR_PER_USD:.4f}",
    )
    cost_table.add_row(
        "[bold]Actual Cost[/bold]", f"[bold]${actual_usd:.4f}[/bold]", f"[bold]\u20ac{actual_eur:.4f}[/bold]",
    )

    if saved_usd > 0:
        cost_table.add_row("", "", "")
        cost_table.add_row(
            "[dim]Without ECO[/dim]", f"[dim]${full_usd:.4f}[/dim]", f"[dim]\u20ac{full_eur:.4f}[/dim]",
        )
        cost_table.add_row(
            "[green]ECO Savings[/green]",
            f"[green]-${saved_usd:.4f}[/green]",
            f"[green]-\u20ac{saved_eur:.4f}[/green]",
        )
        pct = (saved_usd / full_usd * 100) if full_usd > 0 else 0
        cost_table.add_row(
            "[green]Saved[/green]", f"[green]{pct:.0f}%[/green]", "",
        )

    console.print(cost_table)

    console.print()
    console.print(f"  [dim]Pricing: ${pricing[0]}/M input, ${pricing[1]}/M output | 1 USD = {_EUR_PER_USD} EUR[/dim]")


# Session-level token usage accumulator (reset on /clear)
_session_usage: dict = {
    "total_tokens": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "llm_calls": 0,
    "messages": 0,
    "free_calls": 0,
    "free_tokens": 0,
}

# Approximate pricing per 1M tokens (USD) — update as models change
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_1M, output_per_1M) — real prices from official sources
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt-4.1": (3.00, 12.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "claude-sonnet-4-20250514": (3.00, 15.0),
    "claude-sonnet-4-6-20250627": (3.00, 15.0),
    "claude-haiku-4-5-20251001": (1.00, 5.0),
    "claude-opus-4-5-20250410": (5.00, 25.0),
    "claude-opus-4-6-20250625": (5.00, 25.0),
}

# EUR/USD exchange rate (approximate)
_EUR_PER_USD = 0.92


async def show_permissions(config: Config, user_id: str) -> None:
    """Show current permission levels."""
    from lazyclaw.permissions.settings import get_permission_settings
    from lazyclaw.permissions.models import DEFAULT_CATEGORY_PERMISSIONS

    settings = await get_permission_settings(config, user_id)
    cat_defaults = settings.get("category_defaults", {})
    skill_overrides = settings.get("skill_overrides", {})

    table = Table(title="Permission Levels", style="cyan")
    table.add_column("Category / Skill", style="bold")
    table.add_column("Level", justify="center")

    for cat in sorted(DEFAULT_CATEGORY_PERMISSIONS.keys()):
        level = cat_defaults.get(cat, DEFAULT_CATEGORY_PERMISSIONS[cat])
        color = "green" if level == "allow" else "yellow" if level == "ask" else "red"
        table.add_row(f"[dim]category:[/dim] {cat}", f"[{color}]{level}[/{color}]")

    if skill_overrides:
        table.add_section()
        for skill, level in sorted(skill_overrides.items()):
            color = "green" if level == "allow" else "yellow" if level == "ask" else "red"
            table.add_row(f"[dim]skill:[/dim] {skill}", f"[{color}]{level}[/{color}]")

    console.print(table)


async def set_permission(config: Config, user_id: str, target: str, level: str) -> None:
    """Set a category or skill permission level."""
    from lazyclaw.permissions.settings import get_permission_settings, update_permission_settings
    from lazyclaw.permissions.models import DEFAULT_CATEGORY_PERMISSIONS

    settings = await get_permission_settings(config, user_id)

    # Check if target is a category
    if target in DEFAULT_CATEGORY_PERMISSIONS:
        cat_defaults = dict(settings.get("category_defaults", {}))
        cat_defaults[target] = level
        await update_permission_settings(config, user_id, {"category_defaults": cat_defaults})
        color = "green" if level == "allow" else "yellow" if level == "ask" else "red"
        console.print(f"  [{color}]Category '{target}' set to {level}[/{color}]")
    else:
        # Treat as skill override
        overrides = dict(settings.get("skill_overrides", {}))
        overrides[target] = level
        await update_permission_settings(config, user_id, {"skill_overrides": overrides})
        color = "green" if level == "allow" else "yellow" if level == "ask" else "red"
        console.print(f"  [{color}]Skill '{target}' set to {level}[/{color}]")


async def _chat_loop() -> None:
    from lazyclaw.db.connection import init_db
    from lazyclaw.llm.router import LLMRouter
    from lazyclaw.permissions.checker import PermissionChecker
    from lazyclaw.runtime.agent import Agent
    from lazyclaw.skills.registry import SkillRegistry

    config = load_config()

    if not config.server_secret or not (config.openai_api_key or config.anthropic_api_key):
        console.print("[red]Not configured. Run 'lazyclaw setup' first.[/red]")
        raise SystemExit(1)

    await init_db(config)

    router = LLMRouter(config)
    registry = SkillRegistry()
    registry.register_defaults(config=config)
    checker = PermissionChecker(config, registry)
    agent = Agent(config, router, registry, permission_checker=checker)
    user_id = await _get_default_user(config)

    console.print(Panel(LOGO, subtitle=f"v{__version__}", style="cyan"))
    console.print("[dim]Type a message to chat. /help for commands. Ctrl+C to quit.[/dim]")
    console.print()

    chat_session_id: str | None = None

    while True:
        try:
            user_input = console.input("[bold cyan]> [/bold cyan]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Goodbye![/yellow]")
            break

        stripped = user_input.strip()
        if not stripped:
            continue

        # Exit
        if stripped.lower() in ("/exit", "/quit", "/q"):
            console.print("[yellow]Goodbye![/yellow]")
            break

        # Clear session
        if stripped.lower() == "/clear":
            chat_session_id = None
            for k in _session_usage:
                _session_usage[k] = 0
            console.print("[green]Chat session cleared.[/green]")
            continue

        # Slash commands
        if stripped.startswith("/"):
            handled = await _handle_slash_command(stripped, config, user_id)
            if handled:
                console.print()
                continue
            console.print(f"[yellow]Unknown command: {stripped.split()[0]}. Try /help[/yellow]")
            continue

        # Chat with agent — live process display
        callback = _CliCallback(console)
        callback.start_thinking()
        try:
            response = await agent.process_message(
                user_id, stripped, chat_session_id=chat_session_id,
                callback=callback,
            )
        except Exception as e:
            callback._stop_spinner()
            console.print(f"[red]Error: {e}[/red]")
            continue

        console.print()
        if callback._streaming:
            # Content was already streamed to terminal — show a separator
            console.print("[dim]───[/dim]")
        else:
            console.print(Panel(Markdown(response), title="LazyClaw", border_style="green"))
        console.print()

        # Accumulate session usage
        _session_usage["total_tokens"] += callback.total_tokens
        _session_usage["prompt_tokens"] += callback.prompt_tokens
        _session_usage["completion_tokens"] += callback.completion_tokens
        _session_usage["llm_calls"] += callback.llm_calls
        _session_usage["messages"] += 1
        _session_usage["free_calls"] += callback.free_calls
        _session_usage["free_tokens"] += callback.free_tokens


# ---------------------------------------------------------------------------
# Click CLI — minimal, chat-first
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """LazyClaw - E2E Encrypted AI Agent Platform."""
    if ctx.invoked_subcommand is None:
        # Default: drop into chat
        try:
            asyncio.run(_chat_loop())
        except KeyboardInterrupt:
            console.print("\n[yellow]Goodbye![/yellow]")


@main.command()
def setup() -> None:
    """Interactive setup wizard for LazyClaw."""

    console.print(
        Panel(
            LOGO,
            subtitle=f"E2E Encrypted AI Agent Platform  v{__version__}",
            style="cyan",
        )
    )
    console.print()

    # SERVER_SECRET
    root = get_project_root()
    env_path = root / ".env"

    existing_secret = ""
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip().startswith("SERVER_SECRET="):
                val = line.strip().split("=", 1)[1]
                if val and val != "change-me-to-a-random-string":
                    existing_secret = val

    if existing_secret:
        console.print("[green]\u2713[/green] SERVER_SECRET already configured")
    else:
        secret = secrets.token_urlsafe(32)
        save_env("SERVER_SECRET", secret)
        console.print("[green]\u2713[/green] Generated SERVER_SECRET")

    console.print()

    # AI Provider
    provider = Prompt.ask(
        "[bold cyan]Choose your AI provider[/bold cyan]",
        choices=["openai", "anthropic"],
        default="openai",
    )

    api_key = ""
    verified = False
    while not verified:
        api_key = Prompt.ask(
            f"[bold cyan]Enter your {provider.capitalize()} API key[/bold cyan]",
            password=True,
        )
        if not api_key.strip():
            console.print("[yellow]Skipping AI provider setup.[/yellow]")
            break

        with console.status("[bold cyan]Verifying API key..."):
            try:
                ok = asyncio.run(verify_provider_async(provider, api_key.strip()))
            except Exception as exc:
                ok = False
                console.print(f"[dim]Error: {exc}[/dim]")

        if ok:
            console.print(f"[green]\u2713[/green] {provider.capitalize()} API key verified")
            verified = True
        else:
            console.print(f"[red]\u2717[/red] API key verification failed")
            if not Confirm.ask("Retry?", default=True):
                break

    if api_key.strip():
        if provider == "openai":
            save_env("OPENAI_API_KEY", api_key.strip())
        else:
            save_env("ANTHROPIC_API_KEY", api_key.strip())
            save_env("DEFAULT_MODEL", "claude-sonnet-4-20250514")

    console.print()

    # Telegram
    telegram_username: str | None = None

    if Confirm.ask("[bold cyan]Set up Telegram bot?[/bold cyan]", default=True):
        console.print()
        console.print(
            Panel(
                "\n".join([
                    "  1. Open Telegram and search for [bold]@BotFather[/bold]",
                    "  2. Send [bold]/newbot[/bold]",
                    "  3. Choose a name for your bot",
                    "  4. Choose a username (must end in 'bot')",
                    "  5. Copy the token BotFather gives you",
                ]),
                title="Telegram Bot Setup",
                style="cyan",
            )
        )
        console.print()

        tg_verified = False
        while not tg_verified:
            tg_token = Prompt.ask(
                "[bold cyan]Enter your Telegram bot token[/bold cyan]",
                password=True,
            )
            if not tg_token.strip():
                console.print("[yellow]Skipping Telegram setup.[/yellow]")
                break

            with console.status("[bold cyan]Verifying Telegram token..."):
                try:
                    bot_info = asyncio.run(verify_telegram_async(tg_token.strip()))
                except Exception as exc:
                    bot_info = None
                    console.print(f"[dim]Error: {exc}[/dim]")

            if bot_info:
                bot_name = bot_info.get("first_name", "Unknown")
                telegram_username = f"@{bot_info.get('username', 'unknown')}"
                console.print(
                    f"[green]\u2713[/green] Connected to [bold]{bot_name}[/bold] ({telegram_username})"
                )
                save_env("TELEGRAM_BOT_TOKEN", tg_token.strip())
                tg_verified = True
            else:
                console.print("[red]\u2717[/red] Token verification failed")
                if not Confirm.ask("Retry?", default=True):
                    break

    console.print()

    # Database
    config = load_config()
    with console.status("[bold cyan]Initializing database..."):
        try:
            asyncio.run(setup_database(config))
        except Exception as exc:
            console.print(f"[red]\u2717[/red] Database initialization failed: {exc}")
            raise SystemExit(1)

    console.print("[green]\u2713[/green] Database initialized")
    console.print()

    # Summary
    config = load_config()

    table = Table(title="Configuration Summary", style="cyan")
    table.add_column("Setting", style="bold")
    table.add_column("Value")

    table.add_row("SERVER_SECRET", "[dim]******* (set)[/dim]" if config.server_secret else "[red]not set[/red]")
    table.add_row(
        "AI Provider",
        provider if api_key.strip() else "[yellow]not configured[/yellow]",
    )
    if api_key.strip():
        masked = api_key.strip()[:6] + "****"
        table.add_row("API Key", masked)
    table.add_row(
        "Telegram Bot",
        telegram_username or "[yellow]not configured[/yellow]",
    )
    db_path = config.database_dir / "lazyclaw.db"
    table.add_row("Database", str(db_path))
    table.add_row("API Port", str(config.port))

    console.print(table)
    console.print()

    console.print("[bold green]Setup complete![/bold green] Run [bold cyan]lazyclaw[/bold cyan] to start chatting.")


@main.command()
def start() -> None:
    """Start the full agent (API + Telegram + Heartbeat)."""
    config = load_config()

    if not config.server_secret:
        console.print("[red]No SERVER_SECRET. Run 'lazyclaw setup' first.[/red]")
        raise SystemExit(1)
    if not (config.openai_api_key or config.anthropic_api_key):
        console.print("[red]No AI provider configured. Run 'lazyclaw setup' first.[/red]")
        raise SystemExit(1)

    console.print(Panel("Starting LazyClaw...", style="cyan"))

    try:
        asyncio.run(run_agent(config))
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")
