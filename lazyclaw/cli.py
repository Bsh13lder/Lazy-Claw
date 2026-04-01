"""LazyClaw CLI — Unified chat REPL with built-in slash commands."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import uuid

# Early logging setup — suppress noisy libraries before any imports trigger them
from lazyclaw.logging_config import configure_logging as _configure_logging
_configure_logging()  # Defaults to WARNING, no file yet — reconfigured after config load

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

HELP_TEXT = """\
[bold]Chat:[/bold] Just type your message and press Enter.
  You can type while the agent works — messages get queued.
  Type [bold]/?[/bold] or "what's happening" to see live agent status.

[bold]Info:[/bold]
  /status      System dashboard (config, stats, modes)
  /users       List all users
  /skills      List skills with permissions
  /traces      Show recent session traces
  /teams       Team config and specialists
  /mcp         MCP servers (fav/unfav/connect/disconnect/add/remove)
  /compression Context compression stats
  /history     Recent conversation messages
  /logs        Recent agent activity (tool calls, LLM)
  /usage       Token usage + cost estimate (EUR)
  /tasks       Background tasks (running + recent)
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
  /connect-browser Connect to your real Chrome browser (CDP)
  /install-mcps  Install all bundled MCP servers
  /update        Pull latest code + reinstall deps
  /version       Show current version

[bold]Session:[/bold]
  /clear       Start fresh chat session
  /wipe        Clear all conversation history
  /nuke        Selective account data wipe (with confirmation)
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

    # Configure logging with file output for server mode
    log_file = str(config.database_dir / "lazyclaw.log")
    _configure_logging(config.log_level, log_file)

    await init_db(config)

    from lazyclaw.permissions.checker import PermissionChecker

    router = LLMRouter(config)
    registry = SkillRegistry()
    registry.register_defaults(config=config)

    # Get default user for MCP/ECO init
    user_id = await _get_default_user(config)

    # Seed apihunter with known free providers
    try:
        from mcp_apihunter.config import ApiHunterConfig
        from mcp_apihunter.registry import Registry as ApiHunterRegistry
        ah_config = ApiHunterConfig()
        ah_registry = ApiHunterRegistry(ah_config.db_path)
        await ah_registry.init_db()
        seeded = await ah_registry.seed_known_providers()
        if seeded:
            console.print(f"[green]\u2713[/green] Seeded {seeded} providers into apihunter")
    except Exception:
        pass  # intentional: apihunter is optional, not installed in all setups

    # Auto-register + connect bundled MCP servers
    from lazyclaw.mcp.manager import connect_and_register_bundled_mcps
    mcp_tool_count = 0
    try:
        mcp_tool_count = await connect_and_register_bundled_mcps(config, user_id, registry)
        if mcp_tool_count > 0:
            console.print(f"[green]\u2713[/green] Loaded {mcp_tool_count} MCP tools")
    except Exception as exc:
        logging.getLogger(__name__).warning("MCP auto-connect failed: %s", exc)

    # Auto-detect ECO mode
    from lazyclaw.llm.eco_settings import auto_detect_eco_mode
    try:
        eco_mode = await auto_detect_eco_mode(config, user_id)
        if eco_mode:
            console.print(f"[green]\u2713[/green] ECO mode: {eco_mode}")
    except Exception:
        logging.getLogger(__name__).debug("ECO mode auto-detect failed", exc_info=True)

    permission_checker = PermissionChecker(config, registry)

    # TeamLead — persistent session coordinator (shared singleton)
    from lazyclaw.runtime.team_lead import TeamLead
    team_lead = TeamLead()

    agent = Agent(config, router, registry, permission_checker=permission_checker, team_lead=team_lead)

    # Share registry with gateway
    from lazyclaw.gateway.app import set_registry
    set_registry(registry)

    # Lane Queue
    from lazyclaw.queue.lane import LaneQueue

    lane_queue = LaneQueue()
    lane_queue.set_handler(agent.process_message)
    await lane_queue.start()
    console.print("[green]\u2713[/green] Lane queue started")

    # Background task runner (parallel execution)
    from lazyclaw.runtime.task_runner import TaskRunner

    task_runner = TaskRunner(
        config=config, router=router, registry=registry,
        eco_router=agent.eco_router,
        permission_checker=permission_checker,
        team_lead=team_lead,
    )
    agent._task_runner = task_runner  # Enable fast dispatch

    # Register run_background skill
    from lazyclaw.skills.builtin.background import RunBackgroundSkill

    bg_skill = RunBackgroundSkill(config=config)
    bg_skill._task_runner = task_runner
    registry.register(bg_skill)

    from lazyclaw.gateway.app import set_lane_queue
    set_lane_queue(lane_queue)

    # ── Textual TUI Dashboard ────────────────────────────────────────
    # Full interactive terminal: live agent activity, system overview,
    # scrollable logs, admin input. Replaces the old Rich Live panel.
    # All services (uvicorn, Telegram, heartbeat) run as Textual workers.

    from lazyclaw.cli_tui import LazyClawApp

    app = LazyClawApp(
        config=config,
        agent=agent,
        lane_queue=lane_queue,
        registry=registry,
        task_runner=task_runner,
        telegram_token=config.telegram_bot_token,
        permission_checker=permission_checker,
        default_user_id=user_id,
        team_lead=team_lead,
    )

    try:
        await app.run_async()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass  # Graceful shutdown handled by app.action_quit()


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
        mcp_command,
        nuke_account,
        run_doctor,
        set_critic_mode,
        set_eco_mode,
        set_model,
        set_team_mode,
        show_compression,
        show_logs,
        show_skills,
        show_status,
        show_teams,
        show_traces,
        show_users,
    )

    parts = cmd.strip().split()
    command = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else None

    # /mcp has subcommands — handle separately
    if command == "/mcp":
        mcp_args = cmd.strip()[4:].strip()  # Everything after "/mcp"
        await mcp_command(config, user_id, mcp_args)
        return True

    # Info commands
    handlers = {
        "/status": lambda: show_status(config, user_id),
        "/users": lambda: show_users(config),
        "/skills": lambda: show_skills(config, user_id),
        "/traces": lambda: show_traces(config, user_id),
        "/teams": lambda: show_teams(config, user_id),
        "/compression": lambda: show_compression(config, user_id),
        "/logs": lambda: show_logs(config, user_id),
        "/usage": lambda: _show_usage(config),
        "/tasks": lambda: _show_tasks(task_runner, user_id),
        "/doctor": lambda: run_doctor(config, user_id),
        "/install-mcps": lambda: _install_mcps(),
        "/installmcps": lambda: _install_mcps(),
        "/update": lambda: _run_update(),
        "/version": lambda: _show_version(),
        "/wipe": lambda: clear_history(config, user_id),
        "/nuke": lambda: nuke_account(config, user_id),
        "/history": lambda: _show_chat_history(config, user_id),
        "/connect-browser": lambda: _connect_browser(config),
        "/connectbrowser": lambda: _connect_browser(config),
        "/restart": lambda: _restart_server(),
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
    """Show recent conversation — user messages and assistant responses only."""
    from lazyclaw.crypto.encryption import decrypt, derive_server_key
    from lazyclaw.db.connection import db_session

    key = derive_server_key(config.server_secret, user_id)

    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT role, content, created_at FROM agent_messages "
            "WHERE user_id = ? AND role IN ('user', 'assistant') "
            "ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        )
        messages = await rows.fetchall()

    if not messages:
        console.print("[dim]No conversation history.[/dim]")
        return

    console.print()
    for row in reversed(messages):
        role, content_enc, created_at = row[0], row[1], row[2]
        try:
            content = decrypt(content_enc, key) if content_enc.startswith("enc:") else content_enc
        except Exception:
            content = "[encrypted]"

        ts = (created_at or "")[:16]  # "2026-03-20 00:28"
        if role == "user":
            preview = content[:120].replace("\n", " ")
            # Strip channel hints from display
            if "[Channel:" in preview:
                preview = preview[:preview.index("[Channel:")].strip()
            console.print(f"  [dim]{ts}[/dim]  [bold cyan]\u276f[/bold cyan] {preview}")
        else:
            preview = content[:200].replace("\n", " ")
            console.print(f"  [dim]{ts}[/dim]  [green]\u25c0[/green] {preview}")
            if len(content) > 200:
                console.print(f"               [dim]...({len(content)} chars)[/dim]")

    console.print()


async def _connect_browser(config: Config) -> None:
    """Check/establish CDP connection to user's browser (Brave/Chrome)."""
    from lazyclaw.browser.cdp import find_chrome_cdp, list_chrome_tabs

    port = config.cdp_port
    browser_name = "Brave" if "brave" in (config.browser_executable or "").lower() else "Chrome"
    console.print(f"  [dim]Checking {browser_name} on port {port}...[/dim]")

    ws_url = await find_chrome_cdp(port)
    if not ws_url:
        console.print(f"  [yellow]{browser_name} not running with CDP, launching...[/yellow]")

        # Auto-launch headless with CDP
        from lazyclaw.browser.cdp_backend import CDPBackend
        user_id = await _get_default_user(config)
        profile_dir = str(config.database_dir / "browser_profiles" / user_id)
        backend = CDPBackend(port=port, profile_dir=profile_dir)
        ws_url = await backend._auto_launch_chrome()

        if not ws_url:
            console.print(f"  [red]\u2717 Failed to launch {browser_name}[/red]")
            console.print()
            console.print(f"  Manual launch:")
            bin_path = config.browser_executable or "brave-browser"
            console.print(
                f"  [bold cyan]{bin_path} "
                f"--remote-debugging-port={port}[/bold cyan]"
            )
            return

    tabs = await list_chrome_tabs(port)
    console.print(f"  [green]\u2713 Connected to {browser_name}[/green] ({len(tabs)} tabs)")
    for i, tab in enumerate(tabs[:5], 1):
        console.print(f"    {i}. [dim]{tab.title[:50]}[/dim]")
        console.print(f"       [dim]{tab.url[:60]}[/dim]")
    if len(tabs) > 5:
        console.print(f"    [dim]... and {len(tabs) - 5} more[/dim]")

    # Also set browser persistence to auto if currently off
    from lazyclaw.browser.browser_settings import get_browser_settings, update_browser_settings
    user_id = await _get_default_user(config)
    settings = await get_browser_settings(config, user_id)
    if settings.get("persistent") == "off":
        await update_browser_settings(config, user_id, {"persistent": "auto"})
        console.print("  [dim]Browser mode set to auto (stays alive after use)[/dim]")

    console.print()
    console.print(
        "  [dim]Agent can now use: browser (read, open, click, type, "
        "screenshot, tabs, scroll)[/dim]"
    )


async def _restart_server() -> None:
    """Restart the LazyClaw server process."""
    import os
    import sys

    console.print("[yellow]Restarting LazyClaw...[/yellow]")

    # Clean up MCP connections
    try:
        from lazyclaw.mcp.manager import disconnect_all
        await disconnect_all()
    except Exception as exc:
        logging.getLogger(__name__).debug("MCP cleanup failed during restart: %s", exc)

    # Re-exec the current process with same args
    os.execv(sys.executable, [sys.executable] + sys.argv)


async def _show_version() -> None:
    """Show current version and install info."""
    console.print(f"  [bold cyan]LazyClaw[/bold cyan] v{__version__}")
    console.print(f"  [dim]Install: pip install -e . (editable mode)[/dim]")
    console.print(f"  [dim]Code changes take effect immediately — no reinstall needed.[/dim]")


async def _install_mcps() -> None:
    """Install all bundled MCP servers — local first, GitHub fallback."""
    import subprocess
    import sys

    root = get_project_root()
    github_repo = "https://github.com/Bsh13lder/Lazy-Claw.git"
    mcp_packages = {
        "mcp-freeride": "mcp_freeride",
        "mcp-healthcheck": "mcp_healthcheck",
        "mcp-apihunter": "mcp_apihunter",
        "mcp-vaultwhisper": "mcp_vaultwhisper",
        "mcp-taskai": "mcp_taskai",
    }

    console.print("[bold cyan]Installing bundled MCP servers...[/bold cyan]")
    console.print()

    pip_cmd = [sys.executable, "-m", "pip"]
    installed = 0
    skipped = 0
    need_github: list[str] = []

    for pkg_dir, module_name in mcp_packages.items():
        # Already installed?
        try:
            __import__(module_name)
            console.print(f"  [green]{pkg_dir}[/green] — already installed")
            skipped += 1
            continue
        except ImportError:
            pass  # intentional: module not yet installed, that's the point of this check

        # Try local directory first
        pkg_path = root / pkg_dir
        if pkg_path.exists():
            console.print(f"  [dim]Installing {pkg_dir} (local)...[/dim]", end="")
            try:
                result = subprocess.run(
                    [*pip_cmd, "install", "-e", str(pkg_path), "-q"],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0:
                    console.print(f"\r  [green]{pkg_dir}[/green] — installed (local)       ")
                    installed += 1
                    continue
                else:
                    console.print(f"\r  [yellow]{pkg_dir}[/yellow] — local failed, trying GitHub...")
            except subprocess.TimeoutExpired:
                console.print(f"\r  [yellow]{pkg_dir}[/yellow] — local timed out, trying GitHub...")

            need_github.append(pkg_dir)
        else:
            need_github.append(pkg_dir)

    # GitHub fallback for packages not found locally
    if need_github:
        console.print()
        console.print(f"  [dim]Fetching {len(need_github)} package(s) from GitHub...[/dim]")
        for pkg_dir in need_github:
            pip_url = f"git+{github_repo}#subdirectory={pkg_dir}"
            console.print(f"  [dim]Installing {pkg_dir} (GitHub)...[/dim]", end="")
            try:
                result = subprocess.run(
                    [*pip_cmd, "install", pip_url, "-q"],
                    capture_output=True, text=True, timeout=180,
                )
                if result.returncode == 0:
                    console.print(f"\r  [green]{pkg_dir}[/green] — installed (GitHub)      ")
                    installed += 1
                else:
                    console.print(f"\r  [red]{pkg_dir}[/red] — failed: {result.stderr.strip()[:80]}")
            except subprocess.TimeoutExpired:
                console.print(f"\r  [red]{pkg_dir}[/red] — timed out")

    console.print()
    console.print(f"  [bold]{installed} installed, {skipped} already present[/bold]")


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


async def _show_tasks(runner, user_id: str) -> None:
    """Show background tasks (running + recent)."""
    from rich.table import Table

    running = runner.list_running(user_id)
    recent = await runner.list_all(user_id, limit=10)

    if not running and not recent:
        console.print("  [dim]No background tasks.[/dim]")
        return

    if running:
        table = Table(title="Running Background Tasks", style="cyan")
        table.add_column("ID", width=8)
        table.add_column("Name")
        table.add_column("Elapsed", justify="right")

        for t in running:
            table.add_row(t["id"][:8], t["name"], t["elapsed"])
        console.print(table)
        console.print()

    if recent:
        table = Table(title="Recent Tasks", style="dim")
        table.add_column("ID", width=8)
        table.add_column("Name")
        table.add_column("Status")
        table.add_column("Error")

        for t in recent:
            status_style = {
                "done": "[green]done[/green]",
                "failed": "[red]failed[/red]",
                "cancelled": "[yellow]cancelled[/yellow]",
                "running": "[cyan]running[/cyan]",
            }.get(t["status"], t["status"])
            error = (t.get("error") or "")[:40]
            table.add_row(t["id"][:8], t["name"] or "—", status_style, error)
        console.print(table)


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
    model = config.brain_model
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

    # Reconfigure logging with file output now that we have config
    log_file = str(config.database_dir / "lazyclaw.log")
    _configure_logging(config.log_level, log_file)

    if not config.server_secret or not (config.openai_api_key or config.anthropic_api_key):
        console.print("[red]Not configured. Run 'lazyclaw setup' first.[/red]")
        raise SystemExit(1)

    await init_db(config)

    router = LLMRouter(config)
    registry = SkillRegistry()
    registry.register_defaults(config=config)
    user_id = await _get_default_user(config)

    # Seed apihunter with known free providers
    try:
        from mcp_apihunter.config import ApiHunterConfig
        from mcp_apihunter.registry import Registry as ApiHunterRegistry
        ah_config = ApiHunterConfig()
        ah_registry = ApiHunterRegistry(ah_config.db_path)
        await ah_registry.init_db()
        seeded = await ah_registry.seed_known_providers()
        if seeded:
            logging.getLogger(__name__).info("Seeded %d providers into apihunter", seeded)
    except Exception:
        pass  # intentional: apihunter is optional, not installed in all setups

    # Auto-register + connect bundled MCP servers
    from lazyclaw.mcp.manager import connect_and_register_bundled_mcps
    try:
        mcp_tool_count = await connect_and_register_bundled_mcps(config, user_id, registry)
    except Exception as exc:
        mcp_tool_count = 0
        logging.getLogger(__name__).warning("MCP auto-connect failed: %s", exc)

    # Auto-detect ECO mode if free providers are available
    from lazyclaw.llm.eco_settings import auto_detect_eco_mode
    eco_mode = None
    try:
        eco_mode = await auto_detect_eco_mode(config, user_id)
    except Exception:
        logging.getLogger(__name__).debug("ECO mode auto-detect failed", exc_info=True)

    checker = PermissionChecker(config, registry)

    # TeamLead — persistent session coordinator (shared singleton)
    from lazyclaw.runtime.team_lead import TeamLead
    team_lead = TeamLead()

    agent = Agent(config, router, registry, permission_checker=checker, team_lead=team_lead)

    # Wire task runner for fast dispatch
    from lazyclaw.runtime.task_runner import TaskRunner
    task_runner = TaskRunner(
        config=config, router=router, registry=registry,
        eco_router=agent.eco_router, permission_checker=checker,
        team_lead=team_lead,
    )
    agent._task_runner = task_runner

    # Share registry with gateway for API fallback path
    from lazyclaw.gateway.app import set_registry
    set_registry(registry)

    # Start heartbeat daemon for watcher/cron jobs in REPL mode
    heartbeat_task = None
    try:
        from lazyclaw.heartbeat.daemon import HeartbeatDaemon
        from lazyclaw.queue.lane import LaneQueue

        lane_queue = LaneQueue()
        lane_queue.set_handler(agent.process_message)
        heartbeat = HeartbeatDaemon(config, lane_queue)
        heartbeat_task = asyncio.create_task(heartbeat.start())
    except Exception as exc:
        logging.getLogger(__name__).warning("Heartbeat daemon failed to start: %s", exc)

    console.print(Panel(LOGO, subtitle=f"v{__version__}", style="cyan"))

    # Status banner — one glance to see everything is working
    eco_label = eco_mode.upper() if eco_mode else "Full"
    eco_color = {"eco": "green", "hybrid": "cyan"}.get(eco_mode or "", "white")
    banner_parts = [
        f"[bold]Mode:[/bold] [{eco_color}]{eco_label}[/{eco_color}]",
        f"[bold]Brain:[/bold] [cyan]{config.brain_model.split('/')[-1]}[/cyan]",
        f"[bold]Worker:[/bold] [dim]{config.worker_model.split('/')[-1]}[/dim]",
    ]
    console.print("  " + "  \u2502  ".join(banner_parts))

    # MCP + services line
    svc_parts = []
    if mcp_tool_count > 0:
        svc_parts.append(f"[bold]MCP:[/bold] [green]{mcp_tool_count} tools[/green]")
    else:
        svc_parts.append("[bold]MCP:[/bold] [dim]none[/dim]")
    svc_parts.append("[bold]Browser:[/bold] [dim]idle[/dim]")
    if config.telegram_bot_token:
        svc_parts.append("[bold]Telegram:[/bold] [green]\u2713[/green]")
    console.print("  " + "  \u2502  ".join(svc_parts))

    console.print()
    console.print("[dim]Type a message to chat. /help for commands.[/dim]")
    console.print("[dim]Up/Down: history  |  Esc: clear  |  Ctrl+C: cancel  |  Tab: complete  |  /?: status[/dim]")
    console.print()

    # prompt_toolkit session — up/down history, Esc clear, tab-completion
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings

    history_path = str(get_project_root() / ".lazyclaw_history")
    kb = KeyBindings()

    @kb.add("escape")
    def _clear_line(event):
        """Esc clears the current input line."""
        event.current_buffer.reset()

    _slash_commands = [
        "/help", "/status", "/users", "/skills", "/traces", "/teams",
        "/mcp", "/compression", "/history", "/logs", "/usage", "/tasks",
        "/doctor", "/critic", "/team", "/eco", "/model", "/permissions",
        "/allow", "/deny", "/connect-browser", "/install-mcps", "/update",
        "/version", "/clear", "/wipe", "/nuke", "/exit", "/quit",
    ]
    _slash_completer = WordCompleter(
        _slash_commands, sentence=True,
    )

    pt_session: PromptSession = PromptSession(
        history=FileHistory(history_path),
        key_bindings=kb,
        enable_history_search=True,
        completer=_slash_completer,
        complete_while_typing=False,
    )

    # Delegate to non-blocking chat loop in cli_chat.py
    from lazyclaw.cli_chat import ChatContext, run_chat_loop

    ctx = ChatContext(
        config=config,
        agent=agent,
        user_id=user_id,
        console=console,
        pt_session=pt_session,
        team_lead=team_lead,
        session_usage=_session_usage,
    )

    await run_chat_loop(ctx, _handle_slash_command)


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
        except (KeyboardInterrupt, asyncio.CancelledError, SystemExit):
            pass  # intentional: clean exit signals, no traceback needed
        except Exception:
            pass  # intentional: suppress tracebacks on exit to keep clean UX
        finally:
            # Kill any lingering MCP subprocesses
            try:
                from lazyclaw.mcp.manager import disconnect_all
                loop = asyncio.new_event_loop()
                loop.run_until_complete(asyncio.wait_for(disconnect_all(), timeout=2))
                loop.close()
            except Exception as exc:
                logging.getLogger(__name__).debug("MCP cleanup on exit failed: %s", exc)


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


@main.command(name="install-mcps")
def install_mcps_cmd() -> None:
    """Install all bundled MCP servers."""
    asyncio.run(_install_mcps())


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
    finally:
        # Clean up MCP subprocesses to avoid "Event loop is closed" errors
        try:
            from lazyclaw.mcp.manager import disconnect_all
            loop = asyncio.new_event_loop()
            loop.run_until_complete(asyncio.wait_for(disconnect_all(), timeout=2))
            loop.close()
        except Exception as exc:
            logging.getLogger(__name__).debug("MCP cleanup on chat exit failed: %s", exc)
