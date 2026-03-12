"""LazyClaw CLI — Setup wizard and agent launcher."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid

import click
from rich.console import Console
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


# ---------------------------------------------------------------------------
# Async helpers
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
                "INSERT INTO users (id, username, password_hash, encryption_salt) VALUES (?, ?, ?, ?)",
                (user_id, "default", pw_hash, salt),
            )
            await db.commit()


async def run_agent(config: Config) -> None:
    from lazyclaw.db.connection import init_db
    from lazyclaw.llm.router import LLMRouter
    from lazyclaw.runtime.agent import Agent
    from lazyclaw.skills.registry import SkillRegistry

    await init_db(config)

    router = LLMRouter(config)
    registry = SkillRegistry()
    registry.register_defaults()
    agent = Agent(config, router, registry)

    tasks: list = []

    # FastAPI via uvicorn
    import uvicorn

    uvi_config = uvicorn.Config(
        "lazyclaw.gateway.app:app",
        host="0.0.0.0",
        port=config.port,
        log_level="warning",
    )
    server = uvicorn.Server(uvi_config)
    tasks.append(server.serve())

    # Telegram (if configured)
    telegram = None
    if config.telegram_bot_token:
        from lazyclaw.channels.telegram import TelegramAdapter

        telegram = TelegramAdapter(config.telegram_bot_token, agent, config)
        await telegram.start()
        console.print("[green]\u2713[/green] Telegram bot running")

    console.print(f"[green]\u2713[/green] API running at http://localhost:{config.port}")
    console.print()
    console.print("[bold]LazyClaw is live![/bold] Send a message to your Telegram bot.")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("\n[yellow]Shutting down...[/yellow]")
        if telegram:
            await telegram.stop()


# ---------------------------------------------------------------------------
# Click CLI
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """LazyClaw - E2E Encrypted AI Agent Platform."""
    if ctx.invoked_subcommand is None:
        console.print(
            Panel(
                LOGO,
                subtitle=f"v{__version__}",
                style="cyan",
            )
        )
        console.print("Run [bold cyan]lazyclaw setup[/bold cyan] to get started.")
        console.print("Run [bold cyan]lazyclaw start[/bold cyan] to launch the agent.")


@main.command()
def setup() -> None:
    """Interactive setup wizard for LazyClaw."""

    # ── 1. Welcome ─────────────────────────────────────────────────────
    console.print(
        Panel(
            LOGO,
            subtitle=f"E2E Encrypted AI Agent Platform  v{__version__}",
            style="cyan",
        )
    )
    console.print()

    # ── 2. SERVER_SECRET ───────────────────────────────────────────────
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

    # ── 3. AI Provider ─────────────────────────────────────────────────
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

    # ── 4. Telegram ────────────────────────────────────────────────────
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

    # ── 5. Database ────────────────────────────────────────────────────
    config = load_config()
    with console.status("[bold cyan]Initializing database..."):
        try:
            asyncio.run(setup_database(config))
        except Exception as exc:
            console.print(f"[red]\u2717[/red] Database initialization failed: {exc}")
            raise SystemExit(1)

    console.print("[green]\u2713[/green] Database initialized")
    console.print()

    # ── 6. Summary ─────────────────────────────────────────────────────
    config = load_config()  # reload after all saves

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

    # ── 7. Auto-start offer ────────────────────────────────────────────
    if Confirm.ask("[bold cyan]Start LazyClaw now?[/bold cyan]", default=True):
        _do_start()


@main.command()
def start() -> None:
    """Start the LazyClaw agent."""
    _do_start()


def _do_start() -> None:
    """Shared start logic for both the setup wizard and the start command."""
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
