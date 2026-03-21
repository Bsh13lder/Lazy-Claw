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
            "'eco' uses free API providers ($0), 'hybrid' tries local then paid, "
            "'full' always uses paid AI for best quality."
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
                "eco": "ECO mode enabled — all AI requests routed to free providers ($0 cost). Quality may vary.",
                "hybrid": "HYBRID mode enabled — local models first, paid fallback for complex tasks.",
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
            mode = settings.get("mode", "full")

            lines = ["== ECO Status =="]
            lines.append(f"Mode: {mode.upper()}")

            # Mode descriptions
            mode_desc = {
                "local": "Ollama models only, $0 always",
                "eco": "Free API providers only, $0",
                "hybrid": "Local first, paid fallback for complex",
                "full": "Always paid AI, maximum quality",
            }
            lines.append(f"  ({mode_desc.get(mode, 'unknown')})")

            # Local model assignments (brain/specialist)
            lines.append("")
            lines.append("== Local Models (4-Brain Architecture) ==")
            try:
                from lazyclaw.llm.model_registry import (
                    BRAIN_MODEL, SPECIALIST_MODEL, get_model,
                )
                # User overrides from settings
                user_brain = settings.get("brain_model") or BRAIN_MODEL
                user_spec = settings.get("specialist_model") or SPECIALIST_MODEL
                brain_profile = get_model(user_brain)
                spec_profile = get_model(user_spec)
                brain_ram = f" ({brain_profile.ram_mb}MB)" if brain_profile else ""
                spec_ram = f" ({spec_profile.ram_mb}MB)" if spec_profile else ""
                lines.append(f"  Brain:      {user_brain}{brain_ram}")
                lines.append(f"  Specialist: {user_spec}{spec_ram}")
                if user_brain != BRAIN_MODEL:
                    lines.append(f"  (default brain: {BRAIN_MODEL})")
                if user_spec != SPECIALIST_MODEL:
                    lines.append(f"  (default specialist: {SPECIALIST_MODEL})")
                lines.append("")
                lines.append("  Change with: eco_set_model(role='brain', model='...')")
                lines.append("  Change with: eco_set_model(role='specialist', model='...')")
            except Exception:
                lines.append("  (model registry unavailable)")

            # Routing info for paid modes
            if mode in ("full", "hybrid"):
                lines.append("")
                lines.append("== Paid Routing ==")
                lines.append("  Simple/standard tasks → gpt-5-mini (cheap)")
                lines.append("  Complex (analyze/debug/plan) → gpt-5 (best quality)")
                lines.append("  Coding → Claude Code MCP (free via subscription)")

            # Recommended local models (for local/hybrid mode)
            lines.append("")
            lines.append("== Local Models (for local/hybrid mode) ==")
            lines.append("  Requires 16GB+ RAM for specialist models.")
            lines.append("  On 8GB Mac: use 'full' mode (paid) instead.")
            lines.append("")
            lines.append("  Recommended (16GB+ RAM):")
            lines.append("    ollama pull softw8/nanbeige4.1-3b-tools  (2.5GB, tool-optimized)")
            lines.append("    ollama pull qwen3:1.7b                   (1.4GB, brain)")
            lines.append("  Lightweight (8GB RAM, limited):")
            lines.append("    ollama pull qwen3:0.6b                   (0.5GB, basic chat only)")
            lines.append("  Install via chat: ollama_install or 'install ollama <model>'")

            # Ollama status + installed models
            lines.append("")
            lines.append("== Ollama Status ==")
            try:
                from lazyclaw.llm.providers.ollama_provider import OllamaProvider
                provider = OllamaProvider()
                if await provider.health_check():
                    import httpx
                    async with httpx.AsyncClient(timeout=5) as client:
                        resp = await client.get(f"{_OLLAMA_BASE}/api/tags")
                        data = resp.json()
                    models = data.get("models", [])
                    if models:
                        lines.append(f"  Running: yes ({len(models)} models installed)")
                        for m in models:
                            name = m.get("name", "?")
                            size_gb = m.get("size", 0) / 1_000_000_000
                            # Mark brain/specialist
                            tag = ""
                            if name == BRAIN_MODEL or name.startswith(BRAIN_MODEL.split(":")[0]):
                                tag = " ← BRAIN"
                            elif name == SPECIALIST_MODEL or name.startswith(SPECIALIST_MODEL.split("/")[-1].split(":")[0]):
                                tag = " ← SPECIALIST"
                            lines.append(f"    - {name} ({size_gb:.1f}GB){tag}")
                    else:
                        lines.append("  Running: yes (no models installed)")
                        lines.append(f"  Install: ollama pull {BRAIN_MODEL}")
                        lines.append(f"  Install: ollama pull {SPECIALIST_MODEL}")
                else:
                    lines.append("  Running: NO")
                    lines.append("  Install Ollama: https://ollama.ai")
                await provider.close()
            except Exception as exc:
                lines.append(f"  Error: {exc}")

            lines.append("")
            lines.append(f"Show badges: {settings.get('show_badges', True)}")
            budget = settings.get("monthly_paid_budget", 0)
            lines.append(f"Paid budget: {'unlimited' if budget == 0 else f'${budget}/mo'}")

            locked = settings.get("locked_provider")
            if locked:
                lines.append(f"Locked provider: {locked}")

            # Free providers
            lines.append("")
            lines.append("== Free API Providers ==")
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
                lines.append(f"  Error: {exc}")

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
            from lazyclaw.llm.model_registry import BRAIN_MODEL, SPECIALIST_MODEL

            role = params["role"]
            model = params["model"].strip()

            # Reset to default
            if model.lower() == "default":
                key = "brain_model" if role == "brain" else "specialist_model"
                await update_eco_settings(self._config, user_id, {key: None})
                default = BRAIN_MODEL if role == "brain" else SPECIALIST_MODEL
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

            key = "brain_model" if role == "brain" else "specialist_model"
            await update_eco_settings(self._config, user_id, {key: model})

            return (
                f"{role.title()} model set to '{model}'.\n"
                f"This model will be used for {'simple chat and routing' if role == 'brain' else 'tool calling and complex tasks'} "
                f"in local/hybrid mode."
            )
        except Exception as exc:
            return f"Error setting model: {exc}"
