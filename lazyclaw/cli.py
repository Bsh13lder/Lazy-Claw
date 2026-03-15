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
  /compression Context compression stats
  /history     Recent conversation messages

[bold]Settings:[/bold]
  /critic off|on|auto    Set critic mode
  /team off|on|auto      Set team mode
  /eco eco|hybrid|full   Set ECO mode
  /model <name>          Change default model

[bold]Session:[/bold]
  /clear       Start fresh chat session
  /wipe        Clear all conversation history
  /help        Show this help
  /exit        Quit"""


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
        set_critic_mode,
        set_eco_mode,
        set_model,
        set_team_mode,
        show_compression,
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
        "/compression": lambda: show_compression(config, user_id),
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

        # Chat with agent
        with console.status("[dim]Thinking...[/dim]"):
            try:
                response = await agent.process_message(
                    user_id, stripped, chat_session_id=chat_session_id,
                )
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                continue

        console.print()
        console.print(Panel(Markdown(response), title="LazyClaw", border_style="green"))
        console.print()


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
