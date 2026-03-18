from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class ProviderListSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "provider_list"

    @property
    def description(self) -> str:
        return (
            "List all configured free AI providers with their status, "
            "latency, and available models."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "description": "Filter by status: active, pending, failed, all (default: all)",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            from mcp_apihunter.config import load_config as load_ah_config
            from mcp_apihunter.registry import Registry

            config = load_ah_config()
            registry = Registry(config.db_path)
            await registry.init_db()

            status_filter = params.get("status_filter", "all")
            entries = await registry.list_all(status_filter=status_filter)

            if not entries:
                return f"No providers found (filter: {status_filter})."

            lines = [f"== AI Providers ({len(entries)} found) ==", ""]
            lines.append(f"{'Name':<20} {'Status':<10} {'Models':<30} {'Latency'}")
            lines.append("-" * 75)

            for entry in entries:
                name = getattr(entry, "name", "?")
                status = getattr(entry, "status", "?")
                models = getattr(entry, "models", [])
                latency = getattr(entry, "latency_avg_ms", None)

                models_str = ", ".join(models[:3]) if models else "(none)"
                if models and len(models) > 3:
                    models_str += f" +{len(models) - 3}"
                latency_str = f"{latency}ms" if latency is not None else "n/a"

                lines.append(f"{name:<20} {status:<10} {models_str:<30} {latency_str}")

            return "\n".join(lines)
        except ImportError:
            return "Error: mcp-apihunter is not installed. Install it to manage AI providers."
        except Exception as exc:
            return f"Error listing providers: {exc}"


class ProviderAddSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "provider_add"

    @property
    def description(self) -> str:
        return (
            "Add a new free AI provider endpoint to the registry. Requires "
            "a name, base URL, and at least one model ID."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Provider name (e.g., 'my-ollama', 'local-vllm')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Base URL for the provider API",
                },
                "models": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of model IDs available at this provider",
                },
                "api_key_env": {
                    "type": "string",
                    "description": "Environment variable name containing the API key (optional)",
                },
            },
            "required": ["name", "base_url", "models"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            from mcp_apihunter.config import load_config as load_ah_config
            from mcp_apihunter.registry import Registry

            config = load_ah_config()
            registry = Registry(config.db_path)
            await registry.init_db()

            entry = await registry.add(
                name=params["name"],
                base_url=params["base_url"],
                models=params["models"],
                api_key_env=params.get("api_key_env"),
            )

            # Optionally validate the endpoint
            try:
                from mcp_apihunter.validator import validate_entry

                validation = await validate_entry(entry, timeout=15)
                if validation.success:
                    status_msg = f"Validation: OK ({validation.latency_ms:.0f}ms)"
                else:
                    status_msg = f"Validation: failed ({validation.error})"
            except Exception:
                status_msg = "Validation: skipped"

            return (
                f"Provider '{params['name']}' added successfully.\n"
                f"URL: {params['base_url']}\n"
                f"Models: {', '.join(params['models'])}\n"
                f"{status_msg}"
            )
        except ImportError:
            return "Error: mcp-apihunter is not installed. Install it to manage AI providers."
        except Exception as exc:
            return f"Error adding provider: {exc}"


class ProviderScanSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "provider_scan"

    @property
    def description(self) -> str:
        return (
            "Auto-discover free AI providers by scanning OpenRouter for free "
            "models, detecting local Ollama models, and probing known free-tier APIs."
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
            from mcp_apihunter.scanner import run_full_scan
            from mcp_apihunter.config import load_config as load_ah_config
            from mcp_apihunter.registry import Registry

            config = load_ah_config()
            registry = Registry(config.db_path)
            await registry.init_db()

            report = await run_full_scan(registry, config)

            lines = ["== Provider Scan Complete ==", ""]
            lines.append(f"Providers discovered: {report.discovered}")
            lines.append(f"New providers added: {report.added}")
            lines.append(f"Updated: {report.updated}")

            if report.errors:
                lines.append(f"Errors: {len(report.errors)}")
                for err in report.errors[:5]:
                    lines.append(f"  - {err}")

            # Show current registry after scan
            try:
                entries = await registry.list_all(status_filter="active")
                if entries:
                    lines.append("")
                    lines.append(f"Active providers ({len(entries)}):")
                    for entry in entries[:20]:
                        model_count = len(entry.models) if entry.models else 0
                        lines.append(f"  - {entry.name} ({model_count} models)")
            except Exception:
                pass

            return "\n".join(lines)
        except ImportError:
            return "Error: mcp-apihunter is not installed. Install it to scan for AI providers."
        except Exception as exc:
            return f"Error scanning providers: {exc}"
