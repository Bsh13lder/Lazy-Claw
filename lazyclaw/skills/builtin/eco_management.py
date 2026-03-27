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
            "Set the AI routing mode. 'local' uses Ollama models only ($0), "
            "'eco' uses free API providers ($0), 'hybrid' uses free workers + "
            "paid brain (Haiku), 'full' always uses paid AI for best quality."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["local", "eco", "hybrid", "full"],
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
                "local": "LOCAL mode enabled — all AI requests use Ollama models ($0). Requires Ollama running.",
                "eco": (
                    "ECO mode enabled — all AI requests routed to free providers ($0 cost). "
                    "Cascades: Groq → OpenRouter → Google → Together → Mistral. "
                    "Waits if all rate-limited (never pays)."
                ),
                "hybrid": (
                    "HYBRID mode enabled — free providers for workers, Haiku (cheap paid) for brain/fallback. "
                    "Cost: ~$0.002/message instead of $0.05."
                ),
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
            from lazyclaw.llm.free_providers import get_provider_info

            settings = await get_eco_settings(self._config, user_id)
            mode = settings.get("mode", "full")

            lines = [f"ECO Status: {mode.upper()}"]
            lines.append("\u2501" * 20)

            # Mode-specific routing info
            if mode == "eco":
                lines.append("Brain: best free model (Groq > OpenRouter > Gemini)")
                lines.append("Worker: fastest free model")
                lines.append("Fallback: wait and retry (never pays)")
            elif mode == "hybrid":
                lines.append("Brain: claude-haiku-4-5 (paid)")
                lines.append("Worker: free provider (Groq/OpenRouter/Gemini)")
                lines.append("Fallback: claude-haiku-4-5")
            elif mode == "full":
                lines.append("Brain: gpt-5 / config brain_model (paid)")
                lines.append("Worker: gpt-5-mini / config worker_model (paid)")
                lines.append("No free providers used")
            elif mode == "local":
                brain = settings.get("brain_model") or "qwen3:0.6b"
                spec = settings.get("specialist_model") or "qwen3:0.6b"
                lines.append(f"Brain: {brain} (Ollama)")
                lines.append(f"Specialist: {spec} (Ollama)")

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
            lines.append("  /eco full         Switch to full paid")
            lines.append("  /eco hybrid       Free workers + paid brain")
            lines.append("  /eco eco          Free only ($0)")
            lines.append("  /eco add <name>   Add free provider")
            lines.append("  /eco remove <name> Remove provider")
            lines.append("  /eco setup        Interactive setup wizard")
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
    """Set which Ollama model is used as brain or specialist in local/hybrid mode."""

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
            "Set the brain or specialist model for local/hybrid ECO mode. "
            "Brain handles simple chat (fast, small). Specialist handles tool "
            "calling and complex tasks (larger, smarter). "
            "Example: set brain to 'qwen3:1.7b', set specialist to "
            "'softw8/nanbeige4.1-3b-tools'. Use 'default' to reset."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "enum": ["brain", "specialist"],
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
                defaults = get_mode_models("eco_on")
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
                f"This model will be used for {'simple chat and routing' if role == 'brain' else 'tool calling and complex tasks'} "
                f"in local/hybrid mode."
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
