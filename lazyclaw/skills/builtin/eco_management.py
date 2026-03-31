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
            "Set the AI routing mode. 'hybrid' uses Haiku brain + local "
            "Nanbeige worker (cheap). 'full' uses user-configured paid models "
            "for maximum quality."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["hybrid", "full"],
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
                "hybrid": (
                    "HYBRID mode enabled — Haiku brain + local Nanbeige worker ($0). "
                    "Best balance of cost and quality."
                ),
                "full": (
                    "FULL mode enabled — all requests use paid AI for maximum quality. "
                    "Configure models with /eco brain MODEL, /eco worker MODEL."
                ),
            }
            return descriptions.get(mode, f"Mode set to '{mode}'.")
        except ValueError as exc:
            return str(exc)
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
            from lazyclaw.llm.free_providers import get_provider_info

            settings = await get_eco_settings(self._config, user_id)
            mode = settings.get("mode", "hybrid")

            lines = [f"ECO Status: {mode.upper()}"]
            lines.append("\u2501" * 20)

            from lazyclaw.llm.model_registry import get_mode_models
            models = get_mode_models(mode)

            # Mode-specific routing info
            if mode == "hybrid":
                brain = settings.get("brain_model") or models["brain"]
                worker = settings.get("worker_model") or models["worker"]
                fallback = settings.get("fallback_model") or models["fallback"]
                lines.append(f"Brain: {brain} (paid)")
                lines.append(f"Worker: {worker} (local, try Ollama first)")
                lines.append(f"Fallback: {fallback}")
            else:  # full
                brain = settings.get("full_brain_model") or settings.get("brain_model") or models["brain"]
                worker = settings.get("full_worker_model") or settings.get("worker_model") or models["worker"]
                fallback = settings.get("full_fallback_model") or settings.get("fallback_model") or models["fallback"]
                lines.append(f"Brain: {brain} (paid)")
                lines.append(f"Worker: {worker} (paid)")
                lines.append(f"Fallback: {fallback}")

            # Free providers status
            lines.append("")
            lines.append("Free Providers:")
            provider_info = get_provider_info()
            for p in provider_info:
                status = "\u2713" if p["configured"] else "\u2717"
                detail = ""
                if p["configured"]:
                    models_str = ", ".join(
                        m.split("/")[-1].replace(":free", "")
                        for m in p["models"]
                    )
                    detail = f" \u2014 {models_str}"
                else:
                    detail = f" \u2014 not configured (/eco add {p['name']})"
                lines.append(f"  {status} {p['name']:12s}{detail}")

            # Preferred model
            pref = settings.get("preferred_free_model")
            if pref:
                lines.append(f"\nPreferred model: {pref}")

            locked = settings.get("locked_provider")
            if locked:
                lines.append(f"Locked provider: {locked}")

            # Commands
            lines.append("")
            lines.append("Commands:")
            lines.append("  /eco hybrid       Haiku brain + local worker (cheap)")
            lines.append("  /eco full         User-configured paid models")
            lines.append("  /eco brain MODEL  Set brain model")
            lines.append("  /eco worker MODEL Set worker model")
            lines.append("  /eco models       List available free models")

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
            "'google', 'openrouter'), or set to 'auto' to let the system choose "
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
                        "Provider name (groq, google, openrouter, together, "
                        "mistral) or 'auto'"
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
                return "Provider unlocked \u2014 system will auto-select the best available free provider."
            return f"ECO provider locked to '{locked}'. All free requests will use this provider."
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error setting provider: {exc}"


class EcoSetModelSkill(BaseSkill):
    """Set which model is used as brain or worker in HYBRID/FULL mode."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "eco_set_model"

    @property
    def description(self) -> str:
        return (
            "Set the brain or worker model for HYBRID/FULL ECO mode. "
            "Brain handles chat and routing. Worker handles tool calling "
            "and complex tasks. Use 'default' to reset to system defaults."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "enum": ["brain", "worker"],
                    "description": "Which role to assign the model to",
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Ollama model name (must be installed). "
                        "Use 'default' to reset to the system default."
                    ),
                },
            },
            "required": ["role", "model"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.llm.eco_settings import update_eco_settings
            from lazyclaw.llm.model_registry import get_mode_models

            role = params["role"]
            model = params["model"].strip()

            # Reset to default
            if model.lower() == "default":
                key = "brain_model" if role == "brain" else "worker_model"
                await update_eco_settings(self._config, user_id, {key: None})
                defaults = get_mode_models("hybrid")
                default = defaults["brain"] if role == "brain" else defaults["worker"]
                return f"{role.title()} model reset to default: {default}"

            # Verify model is installed in Ollama
            try:
                import httpx
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get("http://localhost:11434/api/tags")
                    data = resp.json()
                installed = [m.get("name", "") for m in data.get("models", [])]
                # Check exact match or prefix match (ollama adds :latest)
                found = any(
                    model == name or model == name.split(":")[0]
                    for name in installed
                )
                if not found:
                    return (
                        f"Model '{model}' is not installed in Ollama.\n"
                        f"Install it first: ollama_install(model=\"{model}\")\n\n"
                        f"Installed models: {', '.join(installed) or 'none'}"
                    )
            except Exception:
                pass  # Ollama might be down, allow setting anyway

            key = "brain_model" if role == "brain" else "worker_model"
            await update_eco_settings(self._config, user_id, {key: model})

            return (
                f"{role.title()} model set to '{model}'.\n"
                f"This model will be used for {'chat and routing' if role == 'brain' else 'tool calling and complex tasks'}."
            )
        except Exception as exc:
            return f"Error setting model: {exc}"


class EcoListModelsSkill(BaseSkill):
    """List all available free models across all configured providers."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "eco_list_models"

    @property
    def description(self) -> str:
        return (
            "List all free models available across configured providers. "
            "Shows which providers are active, their models, and rate limits."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            from lazyclaw.llm.free_providers import get_provider_info

            provider_info = get_provider_info()
            lines = ["Available Free Models:"]
            lines.append("\u2501" * 30)

            for p in provider_info:
                status = "\u2713 configured" if p["configured"] else "\u2717 not configured"
                lines.append(f"\n{p['name'].upper()} ({status})")
                if p["rate_limit_rpm"]:
                    lines.append(f"  Rate limit: {p['rate_limit_rpm']} req/min")
                for model in p["models"]:
                    lines.append(f"  - {model}")
                if not p["configured"]:
                    lines.append(f"  Get key: {p['signup_url']}")

            return "\n".join(lines)
        except Exception as exc:
            return f"Error listing models: {exc}"
