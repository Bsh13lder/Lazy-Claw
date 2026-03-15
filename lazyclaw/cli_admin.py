"""LazyClaw CLI — Admin/status functions called from the chat REPL slash commands."""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from lazyclaw.config import Config

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
    cfg_table.add_row("Default Model", config.default_model)
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
            pass

        counts = {}
        for label, query in [
            ("Memories", "SELECT COUNT(*) FROM personal_memory"),
            ("Trace Sessions", "SELECT COUNT(DISTINCT trace_session_id) FROM agent_traces"),
            ("Compression Summaries", "SELECT COUNT(*) FROM message_summaries"),
        ]:
            try:
                counts[label] = (await (await db.execute(query)).fetchone())[0]
            except Exception:
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
