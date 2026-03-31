"""System status and diagnostics skills.

Five skills for inspecting system health, configuration, logs, usage,
and changing the default LLM model.  Data-gathering logic is reimplemented
from cli_admin.py so results are plain-text strings (no Rich tables).
"""

from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


# ---------------------------------------------------------------------------
# 1. ShowStatusSkill
# ---------------------------------------------------------------------------


class ShowStatusSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "system"

    @property
    def name(self) -> str:
        return "show_status"

    @property
    def description(self) -> str:
        return (
            "Show system dashboard with configuration, database stats, "
            "AI providers, and agent modes."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.db.connection import db_session
            from lazyclaw.llm.eco_settings import get_eco_settings
            from lazyclaw.skills.registry import SkillRegistry
            from lazyclaw.teams.settings import get_team_settings

            cfg = self._config
            lines: list[str] = []

            # -- Configuration --
            lines.append("== Configuration ==")
            providers: list[str] = []
            if cfg.openai_api_key:
                providers.append("openai")
            if cfg.anthropic_api_key:
                providers.append("anthropic")
            lines.append(f"AI Providers: {', '.join(providers) if providers else 'none'}")
            lines.append(f"Brain Model: {cfg.brain_model}")
            lines.append(f"API Port: {cfg.port}")
            lines.append(f"Database: {cfg.database_dir / 'lazyclaw.db'}")
            lines.append(
                f"Telegram: {'configured' if cfg.telegram_bot_token else 'not set'}"
            )

            # -- Database stats --
            async with db_session(cfg) as db:
                user_count = (
                    await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
                )[0]
                msg_count = (
                    await (
                        await db.execute("SELECT COUNT(*) FROM agent_messages")
                    ).fetchone()
                )[0]

                registry = SkillRegistry()
                registry.register_defaults(config=cfg)
                builtin_skills = len(registry.list_tools())

                user_skills = 0
                try:
                    user_skills = (
                        await (
                            await db.execute("SELECT COUNT(*) FROM skills")
                        ).fetchone()
                    )[0]
                except Exception:
                    pass

                counts: dict[str, int] = {}
                for label, query in [
                    ("Memories", "SELECT COUNT(*) FROM personal_memory"),
                    (
                        "Trace Sessions",
                        "SELECT COUNT(DISTINCT trace_session_id) FROM agent_traces",
                    ),
                    (
                        "Compression Summaries",
                        "SELECT COUNT(*) FROM message_summaries",
                    ),
                ]:
                    try:
                        counts[label] = (
                            await (await db.execute(query)).fetchone()
                        )[0]
                    except Exception:
                        counts[label] = 0

            lines.append("")
            lines.append("== Database Stats ==")
            lines.append(f"Users: {user_count}")
            lines.append(f"Messages: {msg_count}")
            skills_display = f"{builtin_skills} built-in"
            if user_skills:
                skills_display += f" + {user_skills} custom"
            lines.append(f"Skills: {skills_display}")
            for label, count in counts.items():
                lines.append(f"{label}: {count}")

            # -- Agent modes --
            eco = await get_eco_settings(cfg, user_id)
            teams = await get_team_settings(cfg, user_id)

            lines.append("")
            lines.append("== Agent Modes ==")
            lines.append(f"ECO Mode: {eco.get('mode', 'full')}")
            lines.append(f"Team Mode: {teams.get('mode', 'auto')}")
            lines.append(f"Critic Mode: {teams.get('critic_mode', 'auto')}")
            lines.append(f"Max Parallel: {teams.get('max_parallel', 3)}")

            # -- MCP servers --
            async with db_session(cfg) as db:
                try:
                    rows = await db.execute(
                        "SELECT name, transport, enabled FROM mcp_connections "
                        "WHERE user_id = ?",
                        (user_id,),
                    )
                    mcp_servers = await rows.fetchall()
                except Exception:
                    mcp_servers = []

            lines.append("")
            lines.append("== MCP Servers ==")
            if mcp_servers:
                for row in mcp_servers:
                    name, transport, enabled = row[0], row[1], row[2]
                    status = "enabled" if enabled else "disabled"
                    lines.append(f"  {name} ({transport or 'stdio'}) — {status}")
            else:
                lines.append("  (none configured)")

            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"


# ---------------------------------------------------------------------------
# 2. RunDoctorSkill
# ---------------------------------------------------------------------------


class RunDoctorSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "system"

    @property
    def name(self) -> str:
        return "run_doctor"

    @property
    def description(self) -> str:
        return (
            "Run system health check on database, AI providers, "
            "MCP servers, and encryption."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            cfg = self._config
            checks: list[tuple[str, str, str]] = []

            # 1. Database
            from lazyclaw.db.connection import db_session

            try:
                async with db_session(cfg) as db:
                    row = await db.execute("SELECT COUNT(*) FROM users")
                    count = (await row.fetchone())[0]
                checks.append(("Database", "OK", f"{count} user(s)"))
            except Exception as e:
                checks.append(("Database", "FAIL", str(e)[:60]))

            # 2. AI Providers
            providers: list[str] = []
            if cfg.openai_api_key:
                providers.append("openai")
            if cfg.anthropic_api_key:
                providers.append("anthropic")
            if providers:
                checks.append(("AI Provider", "OK", ", ".join(providers)))
            else:
                checks.append(("AI Provider", "FAIL", "No API key configured"))

            # 3. Brain Model
            checks.append(("Brain Model", "OK", cfg.brain_model))

            # 4. Encryption round-trip
            try:
                from lazyclaw.crypto.encryption import decrypt, derive_server_key, encrypt

                test_key = derive_server_key(cfg.server_secret, "doctor-test")
                encrypted = encrypt("health-check", test_key)
                decrypted = decrypt(encrypted, test_key)
                if decrypted == "health-check":
                    checks.append(
                        ("Encryption", "OK", "AES-256-GCM roundtrip passed")
                    )
                else:
                    checks.append(("Encryption", "FAIL", "Roundtrip mismatch"))
            except Exception as e:
                checks.append(("Encryption", "FAIL", str(e)[:60]))

            # 5. MCP Servers
            try:
                async with db_session(cfg) as db:
                    row = await db.execute(
                        "SELECT COUNT(*) FROM mcp_connections WHERE user_id = ?",
                        (user_id,),
                    )
                    mcp_count = (await row.fetchone())[0]
                if mcp_count > 0:
                    checks.append(
                        ("MCP Servers", "OK", f"{mcp_count} configured")
                    )
                else:
                    checks.append(("MCP Servers", "WARN", "None configured"))
            except Exception:
                checks.append(("MCP Servers", "WARN", "Table not found"))

            # 6. Bundled MCP packages
            mcp_packages = {
                "mcp-freeride": ("mcp_freeride", "Free AI router"),
                "mcp-healthcheck": ("mcp_healthcheck", "AI provider health monitor"),
                "mcp-apihunter": ("mcp_apihunter", "Free API endpoint discovery"),
                "mcp-vaultwhisper": ("mcp_vaultwhisper", "Privacy-safe AI proxy"),
                "mcp-taskai": ("mcp_taskai", "Task intelligence"),
            }
            for pkg_name, (module_name, desc) in mcp_packages.items():
                try:
                    __import__(module_name)
                    checks.append((pkg_name, "OK", f"Installed ({desc})"))
                except ImportError:
                    checks.append((pkg_name, "WARN", f"Not installed ({desc})"))

            # 7. Free AI providers (direct integration)
            from lazyclaw.llm.free_providers import discover_providers
            free_provs = discover_providers()
            if free_provs:
                checks.append(
                    ("Free AI", "OK", f"Providers: {', '.join(free_provs.keys())}")
                )
            else:
                checks.append(("Free AI", "WARN", "No free API keys"))

            # 8. Telegram
            if cfg.telegram_bot_token:
                checks.append(("Telegram", "OK", "Bot token configured"))
            else:
                checks.append(("Telegram", "WARN", "Not configured"))

            # 9. Server Secret
            if (
                cfg.server_secret
                and cfg.server_secret != "change-me-to-a-random-string"
            ):
                checks.append(("Server Secret", "OK", "Set"))
            else:
                checks.append(
                    ("Server Secret", "FAIL", "Not set or using default")
                )

            # Format output
            ok = sum(1 for _, s, _ in checks if s == "OK")
            warn = sum(1 for _, s, _ in checks if s == "WARN")
            fail = sum(1 for _, s, _ in checks if s == "FAIL")

            lines = ["== LazyClaw Health Check =="]
            for name, status, detail in checks:
                lines.append(f"[{status}] {name}: {detail}")
            lines.append("")
            lines.append(f"{ok} passed, {warn} warnings, {fail} failed")

            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"


# ---------------------------------------------------------------------------
# 3. ShowUsageSkill
# ---------------------------------------------------------------------------


class ShowUsageSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "system"

    @property
    def name(self) -> str:
        return "show_usage"

    @property
    def description(self) -> str:
        return "Show token usage statistics for the current session."

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.crypto.encryption import decrypt, derive_server_key
            from lazyclaw.db.connection import db_session

            import json

            key = derive_server_key(self._config.server_secret, user_id)

            async with db_session(self._config) as db:
                cursor = await db.execute(
                    "SELECT entry_type, content, metadata FROM agent_traces "
                    "WHERE user_id = ? ORDER BY created_at DESC LIMIT 200",
                    (user_id,),
                )
                rows = await cursor.fetchall()

            if not rows:
                return "No usage data recorded yet."

            total_prompt = 0
            total_completion = 0
            total_calls = 0
            tool_calls = 0

            for row in rows:
                entry_type = row[0]
                metadata_enc = row[2] if len(row) > 2 else None

                if entry_type in ("llm_call", "llm_response") and metadata_enc:
                    try:
                        meta_str = (
                            decrypt(metadata_enc, key)
                            if metadata_enc
                            and isinstance(metadata_enc, str)
                            and metadata_enc.startswith("enc:")
                            else (metadata_enc or "{}")
                        )
                        meta = json.loads(meta_str) if isinstance(meta_str, str) else {}
                    except Exception:
                        meta = {}

                    total_prompt += meta.get("prompt_tokens", 0)
                    total_completion += meta.get("completion_tokens", 0)
                    if entry_type == "llm_call":
                        total_calls += 1

                if entry_type == "tool_call":
                    tool_calls += 1

            lines = ["== Token Usage (recent session) =="]
            lines.append(f"LLM calls: {total_calls}")
            lines.append(f"Tool calls: {tool_calls}")
            lines.append(f"Prompt tokens: {total_prompt}")
            lines.append(f"Completion tokens: {total_completion}")
            lines.append(f"Total tokens: {total_prompt + total_completion}")

            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"


# ---------------------------------------------------------------------------
# 4. ShowLogsSkill
# ---------------------------------------------------------------------------


class ShowLogsSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "system"

    @property
    def name(self) -> str:
        return "show_logs"

    @property
    def description(self) -> str:
        return (
            "Show recent agent activity logs including tool calls, "
            "LLM requests, and team delegations."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of log entries (default 20)",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.crypto.encryption import decrypt, derive_server_key
            from lazyclaw.db.connection import db_session

            limit = params.get("limit", 20)
            key = derive_server_key(self._config.server_secret, user_id)

            async with db_session(self._config) as db:
                cursor = await db.execute(
                    "SELECT entry_type, content, created_at FROM agent_traces "
                    "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                )
                entries = await cursor.fetchall()

            if not entries:
                return "No agent activity recorded yet."

            lines = ["== Recent Agent Activity =="]
            for row in reversed(entries):
                entry_type, content_enc, created_at = row[0], row[1], row[2]
                try:
                    content = (
                        decrypt(content_enc, key)
                        if content_enc
                        and isinstance(content_enc, str)
                        and content_enc.startswith("enc:")
                        else (content_enc or "")
                    )
                except Exception:
                    content = "[encrypted]"

                snippet = content[:70] + "..." if len(content) > 70 else content
                timestamp = created_at or ""
                lines.append(f"[{timestamp}] {entry_type}: {snippet}")

            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"


# ---------------------------------------------------------------------------
# 5. SetModelSkill
# ---------------------------------------------------------------------------


class SetModelSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "system"

    @property
    def name(self) -> str:
        return "set_model"

    @property
    def description(self) -> str:
        return (
            "Change the default LLM model used for AI responses."
        )

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": (
                        "Model name (e.g., 'gpt-4o', "
                        "'claude-sonnet-4-20250514', 'o3-mini')"
                    ),
                },
            },
            "required": ["model"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.config import save_env

            model_name = params["model"]
            save_env("DEFAULT_MODEL", model_name)
            return (
                f"Default model set to '{model_name}'. "
                "Restart chat for changes to take effect."
            )
        except Exception as exc:
            return f"Error: {exc}"
