"""LazyClaw CLI — Admin/status functions called from the chat REPL slash commands."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from lazyclaw.config import Config

logger = logging.getLogger(__name__)

console = Console()


async def show_status(config: Config, user_id: str) -> None:
    """Print system dashboard: config, DB stats, agent modes."""
    from lazyclaw.db.connection import db_session

    # Config summary
    cfg_table = Table(title="Configuration", style="cyan")
    cfg_table.add_column("Setting", style="bold")
    cfg_table.add_column("Value")

    providers = []
    if config.openai_api_key:
        providers.append("openai")
    if config.anthropic_api_key:
        providers.append("anthropic")
    cfg_table.add_row("AI Providers", ", ".join(providers) if providers else "[red]none[/red]")
    cfg_table.add_row("Brain Model", config.brain_model)
    cfg_table.add_row("API Port", str(config.port))
    cfg_table.add_row("Database", str(config.database_dir / "lazyclaw.db"))
    cfg_table.add_row("Telegram", "[green]configured[/green]" if config.telegram_bot_token else "[dim]not set[/dim]")
    console.print(cfg_table)
    console.print()

    # DB stats
    async with db_session(config) as db:
        user_count = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        msg_count = (await (await db.execute("SELECT COUNT(*) FROM agent_messages")).fetchone())[0]

        # Count built-in skills from registry + user-created from DB
        from lazyclaw.skills.registry import SkillRegistry
        registry = SkillRegistry()
        registry.register_defaults(config=config)
        builtin_skills = len(registry.list_tools())

        user_skills = 0
        try:
            user_skills = (await (await db.execute("SELECT COUNT(*) FROM skills")).fetchone())[0]
        except Exception:
            logger.warning("Could not count user skills from DB", exc_info=True)

        counts = {}
        for label, query in [
            ("Memories", "SELECT COUNT(*) FROM personal_memory"),
            ("Trace Sessions", "SELECT COUNT(DISTINCT trace_session_id) FROM agent_traces"),
            ("Compression Summaries", "SELECT COUNT(*) FROM message_summaries"),
        ]:
            try:
                counts[label] = (await (await db.execute(query)).fetchone())[0]
            except Exception:
                logger.warning("Failed to query %s count", label, exc_info=True)
                counts[label] = 0

    stats_table = Table(title="Database Stats", style="cyan")
    stats_table.add_column("Metric", style="bold")
    stats_table.add_column("Count", justify="right")
    stats_table.add_row("Users", str(user_count))
    stats_table.add_row("Messages", str(msg_count))
    skills_display = f"{builtin_skills} built-in" + (f" + {user_skills} custom" if user_skills else "")
    stats_table.add_row("Skills", skills_display)
    for label, count in counts.items():
        stats_table.add_row(label, str(count))
    console.print(stats_table)
    console.print()

    # Agent modes
    from lazyclaw.llm.eco_settings import get_eco_settings
    from lazyclaw.teams.settings import get_team_settings

    eco = await get_eco_settings(config, user_id)
    teams = await get_team_settings(config, user_id)

    mode_table = Table(title="Agent Modes", style="cyan")
    mode_table.add_column("Feature", style="bold")
    mode_table.add_column("Status")
    mode_table.add_row("ECO Mode", eco.get("mode", "full"))
    mode_table.add_row("Team Mode", teams.get("mode", "auto"))
    mode_table.add_row("Critic Mode", teams.get("critic_mode", "auto"))
    mode_table.add_row("Max Parallel", str(teams.get("max_parallel", 3)))
    console.print(mode_table)
    console.print()

    # MCP servers
    async with db_session(config) as db:
        try:
            mcp_rows = await db.execute(
                "SELECT name, transport, enabled, favorite FROM mcp_connections WHERE user_id = ?",
                (user_id,),
            )
            mcp_servers = await mcp_rows.fetchall()
        except Exception:
            logger.warning("Failed to query MCP servers for status display", exc_info=True)
            mcp_servers = []

    mcp_table = Table(title="MCP Servers", style="cyan")
    mcp_table.add_column("Name", style="bold")
    mcp_table.add_column("Transport")
    mcp_table.add_column("Status")

    if mcp_servers:
        for row in mcp_servers:
            name, transport, enabled, fav = row[0], row[1], row[2], row[3]
            parts = []
            if enabled:
                parts.append("[green]enabled[/green]")
            else:
                parts.append("[dim]disabled[/dim]")
            if fav:
                parts.append("[yellow]fav[/yellow]")
            mcp_table.add_row(name, transport or "stdio", " ".join(parts))
    else:
        mcp_table.add_row("[dim]none configured[/dim]", "", "")

    console.print(mcp_table)


async def show_users(config: Config) -> None:
    """List all users with role and message count."""
    from lazyclaw.db.connection import db_session

    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT u.id, u.username, u.role, "
            "(SELECT COUNT(*) FROM agent_messages WHERE user_id = u.id) as msg_count "
            "FROM users u ORDER BY u.username"
        )
        users = await rows.fetchall()

    table = Table(title="Users", style="cyan")
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("Username", style="bold")
    table.add_column("Role")
    table.add_column("Messages", justify="right")

    for row in users:
        uid, username, role, msg_count = row[0], row[1], row[2], row[3]
        role_style = "green" if role == "admin" else "white"
        table.add_row(uid[:12] + "...", username, f"[{role_style}]{role}[/{role_style}]", str(msg_count))

    console.print(table)


async def show_skills(config: Config, user_id: str) -> None:
    """List all registered skills with permission levels."""
    from lazyclaw.permissions.checker import PermissionChecker
    from lazyclaw.skills.registry import SkillRegistry

    registry = SkillRegistry()
    registry.register_defaults(config=config)
    checker = PermissionChecker(config, registry)

    table = Table(title="Registered Skills", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Category")
    table.add_column("Permission")

    categories = registry.list_by_category()
    for cat, names in sorted(categories.items()):
        for name in sorted(names):
            perm = await checker.check(user_id, name)
            perm_style = (
                "green" if perm.level == "allow"
                else "yellow" if perm.level == "ask"
                else "red"
            )
            table.add_row(name, cat, f"[{perm_style}]{perm.level}[/{perm_style}]")

    console.print(table)


async def show_traces(config: Config, user_id: str, limit: int = 10) -> None:
    """Show recent session traces."""
    from lazyclaw.replay.engine import list_traces

    traces = await list_traces(config, user_id, limit=limit)

    if not traces:
        console.print("[dim]No traces found.[/dim]")
        return

    table = Table(title="Session Traces", style="cyan")
    table.add_column("Session ID", style="dim", max_width=12)
    table.add_column("Entries", justify="right")
    table.add_column("Started", style="dim")
    table.add_column("Types")

    for t in traces:
        types_str = ", ".join(t.entry_types[:4])
        if len(t.entry_types) > 4:
            types_str += f" +{len(t.entry_types) - 4}"
        table.add_row(
            t.trace_session_id[:12] + "...",
            str(t.entry_count),
            t.started_at or "",
            types_str,
        )

    console.print(table)


async def show_teams(config: Config, user_id: str) -> None:
    """Show team configuration and specialists."""
    from lazyclaw.teams.settings import get_team_settings
    from lazyclaw.teams.specialist import load_specialists

    settings = await get_team_settings(config, user_id)
    specialists = await load_specialists(config, user_id)

    settings_table = Table(title="Team Settings", style="cyan")
    settings_table.add_column("Setting", style="bold")
    settings_table.add_column("Value")
    for key, value in settings.items():
        settings_table.add_row(key, str(value))
    console.print(settings_table)
    console.print()

    spec_table = Table(title="Specialists", style="cyan")
    spec_table.add_column("Name", style="bold")
    spec_table.add_column("Display Name")
    spec_table.add_column("Skills")
    spec_table.add_column("Built-in")

    for spec in specialists:
        skills_str = ", ".join(spec.allowed_skills[:3])
        if len(spec.allowed_skills) > 3:
            skills_str += f" +{len(spec.allowed_skills) - 3}"
        builtin = "[green]yes[/green]" if spec.is_builtin else "no"
        spec_table.add_row(spec.name, spec.display_name, skills_str, builtin)

    console.print(spec_table)


async def show_compression(config: Config, user_id: str) -> None:
    """Show context compression stats."""
    from lazyclaw.memory.compressor import get_compression_stats

    stats = await get_compression_stats(config, user_id)

    table = Table(title="Compression Stats", style="cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Total Summaries", str(stats.get("summary_count", 0)))
    table.add_row("Messages Compressed", str(stats.get("messages_compressed", 0)))
    table.add_row("Compression Ratio", f"{stats.get('compression_ratio', 0):.1f}x")
    console.print(table)


async def show_mcp(config: Config, user_id: str) -> None:
    """Show configured MCP servers with favorites, connection status, and tool counts."""
    from lazyclaw.mcp.bridge import load_cached_schemas
    from lazyclaw.mcp.manager import BUNDLED_MCPS, _active_clients, list_servers

    try:
        servers = await list_servers(config, user_id)
    except Exception:
        logger.warning("Failed to list MCP servers", exc_info=True)
        servers = []

    if not servers:
        console.print("[dim]No MCP servers configured.[/dim]")
        console.print("[dim]Use /mcp add <name> or ask the agent to add one.[/dim]")
        return

    table = Table(title="MCP Servers", style="cyan")
    table.add_column("", width=1)  # Star for favorites
    table.add_column("Name", style="bold")
    table.add_column("Status", width=12)
    table.add_column("Tools", justify="right", width=5)
    table.add_column("Type", width=10)
    table.add_column("Description", max_width=40)

    # Sort: favorites first, then by name
    sorted_servers = sorted(servers, key=lambda s: (not s.get("favorite"), s["name"]))

    for s in sorted_servers:
        star = "[yellow]*[/yellow]" if s.get("favorite") else " "
        name = s["name"]

        # Connection status
        if s["id"] in _active_clients:
            status = "[green]connected[/green]"
        else:
            status = "[dim]idle[/dim]"

        # Tool count from cache
        tool_count = ""
        cached = await load_cached_schemas(config, name)
        if cached:
            import json as _json
            try:
                tool_count = str(len(_json.loads(cached)))
            except Exception:
                logger.warning("Failed to parse cached MCP tool schemas for %s", name, exc_info=True)
                tool_count = "?"
        elif s["id"] in _active_clients:
            try:
                tools = await _active_clients[s["id"]].list_tools()
                tool_count = str(len(tools))
            except Exception:
                logger.warning("Failed to list tools for MCP server %s", name, exc_info=True)
                tool_count = "?"

        # Type label
        if s.get("favorite"):
            type_label = "[yellow]favorite[/yellow]"
        else:
            type_label = "[dim]installed[/dim]"

        # Description from BUNDLED_MCPS or config
        desc = BUNDLED_MCPS.get(name, {}).get("description", "")
        if not desc:
            desc = s.get("config", {}).get("description", "")

        table.add_row(star, name, status, tool_count, type_label, desc)

    console.print(table)
    console.print()
    console.print("[dim]Use /mcp fav <name> to add favorites, /mcp help for all commands[/dim]")


MCP_HELP = """\
[bold]MCP Server Commands:[/bold]
  /mcp                Show all servers with status
  /mcp fav <name>     Add to favorites (starts at boot)
  /mcp unfav <name>   Remove from favorites (lazy-load)
  /mcp connect <name> Manually connect now
  /mcp disconnect <n> Manually disconnect
  /mcp add <name|url> Install a new MCP server
  /mcp remove <name>  Uninstall an MCP server
  /mcp help           Show this help"""


async def mcp_command(config: Config, user_id: str, args: str) -> None:
    """Handle /mcp subcommands."""
    parts = args.strip().split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""
    target = parts[1].strip() if len(parts) > 1 else ""

    if subcmd in ("", "list"):
        await show_mcp(config, user_id)
        return

    if subcmd == "help":
        console.print(MCP_HELP)
        return

    if subcmd == "fav":
        if not target:
            console.print("[yellow]Usage: /mcp fav <server-name>[/yellow]")
            return
        from lazyclaw.mcp.manager import (
            BUNDLED_MCPS,
            connect_server,
            connect_server_with_oauth,
            list_servers,
            set_favorite,
        )

        servers = await list_servers(config, user_id)
        server = _find_mcp_by_name(servers, target)
        if not server:
            _print_mcp_not_found(target, servers)
            return

        await set_favorite(config, user_id, server["name"], True)
        console.print(f"[green]Added '{server['name']}' to favorites.[/green]")

        if not server.get("connected"):
            console.print(f"[dim]Connecting {server['name']}...[/dim]")
            try:
                info = BUNDLED_MCPS.get(server["name"], {})
                if info.get("oauth"):
                    await connect_server_with_oauth(config, user_id, server["id"])
                else:
                    await connect_server(config, user_id, server["id"])
                console.print(f"[green]Connected.[/green]")
            except Exception as exc:
                console.print(f"[red]Failed to connect: {exc}[/red]")
        return

    if subcmd == "unfav":
        if not target:
            console.print("[yellow]Usage: /mcp unfav <server-name>[/yellow]")
            return
        from lazyclaw.mcp.manager import list_servers, set_favorite

        servers = await list_servers(config, user_id)
        server = _find_mcp_by_name(servers, target)
        if not server:
            _print_mcp_not_found(target, servers)
            return

        await set_favorite(config, user_id, server["name"], False)
        console.print(
            f"[green]Removed '{server['name']}' from favorites. "
            f"Will lazy-load on next startup.[/green]"
        )
        return

    if subcmd == "connect":
        if not target:
            console.print("[yellow]Usage: /mcp connect <server-name>[/yellow]")
            return
        from lazyclaw.mcp.manager import (
            BUNDLED_MCPS,
            connect_server,
            connect_server_with_oauth,
            list_servers,
        )

        servers = await list_servers(config, user_id)
        server = _find_mcp_by_name(servers, target)
        if not server:
            _print_mcp_not_found(target, servers)
            return

        console.print(f"[dim]Connecting {server['name']}...[/dim]")
        try:
            info = BUNDLED_MCPS.get(server["name"], {})
            if info.get("oauth"):
                await connect_server_with_oauth(config, user_id, server["id"])
            else:
                await connect_server(config, user_id, server["id"])
            console.print(f"[green]Connected to '{server['name']}'.[/green]")
        except Exception as exc:
            console.print(f"[red]Failed to connect: {exc}[/red]")
        return

    if subcmd == "disconnect":
        if not target:
            console.print("[yellow]Usage: /mcp disconnect <server-name>[/yellow]")
            return
        from lazyclaw.mcp.manager import disconnect_server, list_servers

        servers = await list_servers(config, user_id)
        server = _find_mcp_by_name(servers, target)
        if not server:
            _print_mcp_not_found(target, servers)
            return

        if not server.get("connected"):
            console.print(f"[yellow]'{server['name']}' is not connected.[/yellow]")
            return

        await disconnect_server(user_id, server["id"])
        console.print(f"[green]Disconnected '{server['name']}'.[/green]")
        return

    if subcmd == "add":
        if not target:
            console.print("[yellow]Usage: /mcp add <server-name or URL>[/yellow]")
            return
        from lazyclaw.mcp.manager import add_server, BUNDLED_MCPS, list_servers

        # Check if it's a known bundled name
        if target.lower() in BUNDLED_MCPS:
            console.print(f"[yellow]'{target}' is bundled and auto-registered at startup.[/yellow]")
            return

        # Check if already registered
        servers = await list_servers(config, user_id)
        existing = _find_mcp_by_name(servers, target)
        if existing:
            console.print(f"[yellow]'{existing['name']}' is already registered.[/yellow]")
            return

        # URL-based add
        if target.startswith("https://"):
            from urllib.parse import urlparse
            parsed = urlparse(target)
            name = parsed.netloc.replace(".", "-")
            transport = "streamable_http"
            server_config = {"url": target}
            sid = await add_server(config, user_id, name, transport, server_config)
            console.print(f"[green]Added remote MCP '{name}' (id={sid[:12]}...)[/green]")
        else:
            console.print(
                f"[yellow]Unknown MCP '{target}'. "
                f"Provide an HTTPS URL or a known bundled name.[/yellow]"
            )
        return

    if subcmd == "remove":
        if not target:
            console.print("[yellow]Usage: /mcp remove <server-name>[/yellow]")
            return
        from lazyclaw.mcp.manager import list_servers, remove_server

        servers = await list_servers(config, user_id)
        server = _find_mcp_by_name(servers, target)
        if not server:
            _print_mcp_not_found(target, servers)
            return

        deleted = await remove_server(config, user_id, server["id"])
        if deleted:
            console.print(f"[green]Removed '{server['name']}'.[/green]")
        else:
            console.print(f"[red]Failed to remove '{server['name']}'.[/red]")
        return

    console.print(f"[yellow]Unknown subcommand '{subcmd}'. Try /mcp help[/yellow]")


def _find_mcp_by_name(servers: list[dict], name: str) -> dict | None:
    """Find an MCP server by name (case-insensitive, supports partial match)."""
    name_lower = name.lower()
    for s in servers:
        if s["name"].lower() == name_lower:
            return s
    for s in servers:
        if name_lower in s["name"].lower():
            return s
    return None


def _print_mcp_not_found(name: str, servers: list[dict]) -> None:
    available = ", ".join(s["name"] for s in servers) or "none"
    console.print(
        f"[yellow]No MCP server matching '{name}'. "
        f"Available: {available}[/yellow]"
    )


async def clear_history(config: Config, user_id: str) -> None:
    """Clear conversation history for the current user."""
    from lazyclaw.db.connection import db_session

    if not Confirm.ask(
        "[bold red]Clear ALL your conversation history?[/bold red]",
        default=False,
    ):
        console.print("[dim]Cancelled.[/dim]")
        return

    async with db_session(config) as db:
        result = await db.execute(
            "DELETE FROM agent_messages WHERE user_id = ?", (user_id,)
        )
        deleted = result.rowcount
        await db.execute(
            "DELETE FROM agent_chat_sessions WHERE user_id = ?", (user_id,)
        )
        await db.execute(
            "DELETE FROM message_summaries WHERE user_id = ?", (user_id,)
        )
        await db.commit()

    console.print(f"[green]Cleared {deleted} messages.[/green]")


async def nuke_account(config: Config, user_id: str) -> None:
    """Selective account data wipe with confirmation checklist."""
    from lazyclaw.db.connection import db_session

    categories = [
        ("1", "Conversations", [
            ("agent_messages", "user_id"),
            ("agent_chat_sessions", "user_id"),
            ("message_summaries", "user_id"),
        ]),
        ("2", "Memories", [
            ("personal_memory", "user_id"),
            ("site_memory", "user_id"),
        ]),
        ("3", "Daily summaries", [
            ("daily_logs", "user_id"),
        ]),
        ("4", "Vault (passwords & API keys)", [
            ("credential_vault", "user_id"),
        ]),
        ("5", "Custom skills", [
            ("skills", "user_id"),
        ]),
        ("6", "Browser history", [
            ("browser_task_logs", "_browser_task_join"),
            ("browser_tasks", "user_id"),
        ]),
        ("7", "Background tasks", [
            ("background_tasks", "user_id"),
        ]),
        ("8", "Jobs & queue", [
            ("agent_jobs", "user_id"),
            ("job_queue", "user_id"),
        ]),
        ("9", "Traces & replays", [
            ("agent_traces", "user_id"),
            ("trace_shares", "user_id"),
        ]),
        ("10", "Team messages & custom specialists", [
            ("agent_team_messages", "user_id"),
            ("specialists", "_specialists_custom"),
        ]),
        ("11", "Approvals & audit log", [
            ("approval_requests", "user_id"),
            ("audit_log", "user_id"),
        ]),
        ("12", "MCP connections", [
            ("mcp_connections", "user_id"),
        ]),
    ]

    console.print("\n[bold red]Account Data Wipe[/bold red]\n")
    console.print("Select what to delete:\n")
    for num, label, _tables in categories:
        console.print(f"  [cyan]{num:>2}[/cyan] — {label}")
    console.print(f"  [cyan] A[/cyan] — [bold]Everything above[/bold]")
    console.print()

    choice = Prompt.ask("Enter numbers (e.g. 1,4,7) or A for all", default="")
    if not choice:
        console.print("[dim]Cancelled.[/dim]")
        return

    selected = {s.strip().upper() for s in choice.split(",")}
    all_nums = {cat[0] for cat in categories}
    if "A" in selected:
        selected = all_nums

    invalid = selected - all_nums
    if invalid:
        console.print(f"[yellow]Unknown selection: {', '.join(invalid)}[/yellow]")
        return

    chosen = [cat for cat in categories if cat[0] in selected]
    if not chosen:
        console.print("[dim]Nothing selected.[/dim]")
        return

    # Single transaction: count, confirm, delete
    async with db_session(config) as db:
        # Show what will be deleted with row counts
        console.print("\n[bold]Will delete:[/bold]")
        for _num, label, tables in chosen:
            total = 0
            for table, col in tables:
                try:
                    if col == "_browser_task_join":
                        row = await db.execute(
                            "SELECT COUNT(*) FROM browser_task_logs "
                            "WHERE task_id IN ("
                            "SELECT id FROM browser_tasks WHERE user_id = ?)",
                            (user_id,),
                        )
                    elif col == "_specialists_custom":
                        row = await db.execute(
                            "SELECT COUNT(*) FROM specialists "
                            "WHERE user_id = ? AND is_builtin = 0",
                            (user_id,),
                        )
                    else:
                        row = await db.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE {col} = ?",
                            (user_id,),
                        )
                    count = (await row.fetchone())[0]
                    total += count
                except Exception:
                    logger.warning("Nuke count query failed for table %s", table, exc_info=True)
                    console.print(f"  [yellow]{table}: not found — skipping[/yellow]")
            console.print(f"  [red]{label}[/red]: {total} records")

        # Vault warning
        if any(cat[0] == "4" for cat in chosen):
            console.print(
                "\n[bold yellow]Warning:[/bold yellow] This will delete all stored "
                "passwords and API keys. You'll need to re-enter them."
            )

        console.print()
        confirm = Prompt.ask("Type 'DELETE' to confirm", default="")
        if confirm != "DELETE":
            console.print("[dim]Cancelled.[/dim]")
            return

        # Execute all deletions
        deleted_total = 0
        for _num, _label, tables in chosen:
            for table, col in tables:
                try:
                    if col == "_browser_task_join":
                        result = await db.execute(
                            "DELETE FROM browser_task_logs "
                            "WHERE task_id IN ("
                            "SELECT id FROM browser_tasks WHERE user_id = ?)",
                            (user_id,),
                        )
                    elif col == "_specialists_custom":
                        result = await db.execute(
                            "DELETE FROM specialists "
                            "WHERE user_id = ? AND is_builtin = 0",
                            (user_id,),
                        )
                    else:
                        result = await db.execute(
                            f"DELETE FROM {table} WHERE {col} = ?",
                            (user_id,),
                        )
                    deleted_total += result.rowcount
                except Exception:
                    logger.warning("Nuke delete failed for table %s", table, exc_info=True)
        await db.commit()

    console.print(f"\n[green]Deleted {deleted_total} records.[/green]")


# ---------------------------------------------------------------------------
# Settings commands
# ---------------------------------------------------------------------------

MODE_MAP = {"off": "never", "on": "always", "auto": "auto"}


async def set_critic_mode(config: Config, user_id: str, mode: str) -> None:
    """Set critic mode: off/on/auto."""
    from lazyclaw.teams.settings import update_team_settings

    mapped = MODE_MAP.get(mode.lower())
    if not mapped:
        console.print(f"[yellow]Invalid mode '{mode}'. Use: off, on, auto[/yellow]")
        return

    await update_team_settings(config, user_id, {"critic_mode": mapped})
    console.print(f"[green]Critic mode set to {mapped}[/green]")


async def set_team_mode(config: Config, user_id: str, mode: str) -> None:
    """Set team mode: off/on/auto."""
    from lazyclaw.teams.settings import update_team_settings

    mapped = MODE_MAP.get(mode.lower())
    if not mapped:
        console.print(f"[yellow]Invalid mode '{mode}'. Use: off, on, auto[/yellow]")
        return

    await update_team_settings(config, user_id, {"mode": mapped})
    console.print(f"[green]Team mode set to {mapped}[/green]")


async def set_eco_mode(config: Config, user_id: str, mode: str) -> None:
    """Set ECO mode: eco/hybrid/full."""
    from lazyclaw.llm.eco_settings import update_eco_settings

    if mode.lower() not in ("eco", "hybrid", "full"):
        console.print(f"[yellow]Invalid mode '{mode}'. Use: eco, hybrid, full[/yellow]")
        return

    await update_eco_settings(config, user_id, {"mode": mode.lower()})
    console.print(f"[green]ECO mode set to {mode.lower()}[/green]")


async def set_model(config: Config, model_name: str) -> None:
    """Change the default model."""
    from lazyclaw.config import save_env

    save_env("DEFAULT_MODEL", model_name)
    console.print(f"[green]Default model set to {model_name}[/green]")
    console.print("[dim]Restart chat for changes to take effect.[/dim]")


# ---------------------------------------------------------------------------
# Diagnostics commands
# ---------------------------------------------------------------------------


async def show_logs(config: Config, user_id: str, limit: int = 20) -> None:
    """Show recent agent activity from traces."""
    from lazyclaw.crypto.encryption import decrypt, derive_server_key
    from lazyclaw.db.connection import db_session

    key = derive_server_key(config.server_secret, user_id)

    async with db_session(config) as db:
        try:
            rows = await db.execute(
                "SELECT entry_type, content, created_at FROM agent_traces "
                "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
            entries = await rows.fetchall()
        except Exception:
            logger.warning("Failed to query agent traces for log display", exc_info=True)
            entries = []

    if not entries:
        console.print("[dim]No agent activity recorded yet.[/dim]")
        return

    table = Table(title="Recent Agent Activity", style="cyan")
    table.add_column("Time", style="dim", width=19)
    table.add_column("Type", style="bold", width=14)
    table.add_column("Detail", max_width=55)

    type_styles = {
        "user_message": "cyan",
        "llm_call": "blue",
        "llm_response": "green",
        "tool_call": "yellow",
        "tool_result": "green",
        "team_delegation": "magenta",
        "final_response": "bold green",
    }

    for row in reversed(entries):
        entry_type, content_enc, created_at = row[0], row[1], row[2]
        try:
            content = decrypt(content_enc, key) if content_enc and content_enc.startswith("enc:") else (content_enc or "")
        except Exception:
            logger.warning("Failed to decrypt agent trace entry", exc_info=True)
            content = "[encrypted]"
        display = content[:70] + "..." if len(content) > 70 else content
        style = type_styles.get(entry_type, "white")
        table.add_row(
            created_at or "",
            f"[{style}]{entry_type}[/{style}]",
            display,
        )

    console.print(table)


async def run_doctor(config: Config, user_id: str) -> None:
    """Run health checks on the LazyClaw system."""
    from lazyclaw.db.connection import db_session

    checks: list[tuple[str, str, str]] = []  # (name, status_icon, detail)

    # 1. Database
    try:
        async with db_session(config) as db:
            row = await db.execute("SELECT COUNT(*) FROM users")
            count = (await row.fetchone())[0]
        checks.append(("Database", "[green]OK[/green]", f"{count} user(s)"))
    except Exception as e:
        checks.append(("Database", "[red]FAIL[/red]", str(e)[:60]))

    # 2. AI Provider
    providers = []
    if config.openai_api_key:
        providers.append("openai")
    if config.anthropic_api_key:
        providers.append("anthropic")
    if providers:
        checks.append(("AI Provider", "[green]OK[/green]", ", ".join(providers)))
    else:
        checks.append(("AI Provider", "[red]FAIL[/red]", "No API key configured"))

    # 3. Brain Model
    checks.append(("Brain Model", "[green]OK[/green]", config.brain_model))

    # 4. Encryption
    try:
        from lazyclaw.crypto.encryption import decrypt, derive_server_key, encrypt

        test_key = derive_server_key(config.server_secret, "doctor-test")
        encrypted = encrypt("health-check", test_key)
        decrypted = decrypt(encrypted, test_key)
        if decrypted == "health-check":
            checks.append(("Encryption", "[green]OK[/green]", "AES-256-GCM roundtrip passed"))
        else:
            checks.append(("Encryption", "[red]FAIL[/red]", "Roundtrip mismatch"))
    except Exception as e:
        checks.append(("Encryption", "[red]FAIL[/red]", str(e)[:60]))

    # 5. MCP Servers
    try:
        async with db_session(config) as db:
            row = await db.execute(
                "SELECT COUNT(*) FROM mcp_connections WHERE user_id = ?",
                (user_id,),
            )
            mcp_count = (await row.fetchone())[0]
        if mcp_count > 0:
            checks.append(("MCP Servers", "[green]OK[/green]", f"{mcp_count} configured"))
        else:
            checks.append(("MCP Servers", "[yellow]WARN[/yellow]", "None configured"))
    except Exception:
        logger.warning("Failed to query MCP server count for doctor check", exc_info=True)
        checks.append(("MCP Servers", "[yellow]WARN[/yellow]", "Table not found"))

    # 6. Bundled MCP servers
    _mcp_packages = {
        "mcp-freeride": ("mcp_freeride", "Free AI router (ECO mode)"),
        "mcp-healthcheck": ("mcp_healthcheck", "AI provider health monitor"),
        "mcp-apihunter": ("mcp_apihunter", "Free API endpoint discovery"),
        "mcp-vaultwhisper": ("mcp_vaultwhisper", "Privacy-safe AI proxy"),
        "mcp-taskai": ("mcp_taskai", "Task intelligence"),
    }
    missing_mcps = []
    for pkg_name, (module_name, description) in _mcp_packages.items():
        try:
            __import__(module_name)
            checks.append((pkg_name, "[green]OK[/green]", f"Installed ({description})"))
        except ImportError:
            checks.append((pkg_name, "[yellow]WARN[/yellow]", f"Not installed ({description})"))
            missing_mcps.append(pkg_name)

    # 6b. Free AI providers (direct integration, no mcp-freeride needed)
    from lazyclaw.llm.free_providers import discover_providers
    free_providers = discover_providers()
    if free_providers:
        names = ", ".join(free_providers.keys())
        checks.append(("Free AI", "[green]OK[/green]", f"Providers: {names}"))
    else:
        checks.append(("Free AI", "[yellow]WARN[/yellow]",
            "No free API keys. Get one free: https://console.groq.com \u2192 GROQ_API_KEY"))

    # 7. Telegram
    if config.telegram_bot_token:
        checks.append(("Telegram", "[green]OK[/green]", "Bot token configured"))
    else:
        checks.append(("Telegram", "[yellow]WARN[/yellow]", "Not configured"))

    # 8. Server Secret
    if config.server_secret and config.server_secret != "change-me-to-a-random-string":
        checks.append(("Server Secret", "[green]OK[/green]", "Set"))
    else:
        checks.append(("Server Secret", "[red]FAIL[/red]", "Not set or using default"))

    # Display
    table = Table(title="LazyClaw Health Check", style="cyan")
    table.add_column("Check", style="bold")
    table.add_column("Status", width=8)
    table.add_column("Detail")

    for name, status, detail in checks:
        table.add_row(name, status, detail)

    console.print(table)

    ok_count = sum(1 for _, s, _ in checks if "green" in s)
    warn_count = sum(1 for _, s, _ in checks if "yellow" in s)
    fail_count = sum(1 for _, s, _ in checks if "red" in s)
    console.print()
    console.print(f"  [green]{ok_count} passed[/green]  [yellow]{warn_count} warnings[/yellow]  [red]{fail_count} failed[/red]")

    if missing_mcps:
        console.print()
        console.print("[yellow]Missing MCP servers can be installed with:[/yellow]")
        console.print(f"  [bold]lazyclaw install-mcps[/bold]")
