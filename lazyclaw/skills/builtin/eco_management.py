from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class EcoSetModeSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "eco_set_mode"

    @property
    def description(self) -> str:
        return (
            "Set the AI routing mode. 'eco' uses only free AI ($0 cost), "
            "'hybrid' auto-chooses free or paid per task, 'full' always uses "
            "paid AI for best quality."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["eco", "hybrid", "full"],
                    "description": "The ECO mode to set",
                },
            },
            "required": ["mode"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.llm.eco_settings import update_eco_settings

            mode = params["mode"]
            await update_eco_settings(self._config, user_id, {"mode": mode})

            descriptions = {
                "eco": "ECO mode enabled — all AI requests routed to free providers ($0 cost). Quality may vary.",
                "hybrid": "HYBRID mode enabled — simple tasks use free AI, complex tasks use paid AI automatically.",
                "full": "FULL mode enabled — all requests use paid AI for maximum quality.",
            }
            return descriptions.get(mode, f"Mode set to '{mode}'.")
        except Exception as exc:
            return f"Error setting ECO mode: {exc}"


class EcoShowStatusSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "eco_show_status"

    @property
    def description(self) -> str:
        return (
            "Show current AI routing status including ECO mode, configured "
            "free providers, usage statistics, and provider health."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.llm.eco_settings import get_eco_settings

            settings = await get_eco_settings(self._config, user_id)

            lines = ["== ECO Status =="]
            lines.append(f"Mode: {settings.get('mode', 'full').upper()}")
            lines.append(f"Show badges: {settings.get('show_badges', True)}")

            locked = settings.get("locked_provider")
            if locked:
                lines.append(f"Locked provider: {locked}")
            else:
                lines.append("Locked provider: auto (best available)")

            budget = settings.get("monthly_paid_budget", 0)
            lines.append(f"Monthly paid budget: {'unlimited' if budget == 0 else f'${budget}'}")

            # Try to get configured free providers
            lines.append("")
            lines.append("== Free Providers ==")
            try:
                from mcp_freeride.config import load_config as load_freeride_config
                from mcp_freeride.config import get_configured_providers

                freeride_config = load_freeride_config()
                providers = get_configured_providers(freeride_config)
                if providers:
                    for p in providers:
                        lines.append(f"  - {p}")
                else:
                    lines.append("  (none configured)")
            except ImportError:
                lines.append("  mcp-freeride not installed")
            except Exception as exc:
                lines.append(f"  Error loading providers: {exc}")

            return "\n".join(lines)
        except Exception as exc:
            return f"Error loading ECO status: {exc}"


class EcoSetProviderSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "eco_set_provider"

    @property
    def description(self) -> str:
        return (
            "Lock ECO mode to a specific free AI provider (e.g., 'groq', "
            "'gemini', 'ollama'), or set to 'auto' to let the system choose "
            "the best available."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": (
                        "Provider name (groq, gemini, openrouter, together, "
                        "mistral, huggingface, ollama) or 'auto'"
                    ),
                },
            },
            "required": ["provider"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.llm.eco_settings import update_eco_settings

            provider = params["provider"]
            locked = None if provider == "auto" else provider

            await update_eco_settings(
                self._config, user_id, {"locked_provider": locked}
            )

            if locked is None:
                return "Provider unlocked — system will auto-select the best available free provider."
            return f"ECO provider locked to '{locked}'. All free requests will use this provider."
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error setting provider: {exc}"
