from __future__ import annotations

from lazyclaw.skills.base import BaseSkill

_OLLAMA_BASE = "http://localhost:11434"


class OllamaListSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "ollama_list"

    @property
    def description(self) -> str:
        return "List all locally installed Ollama models with their sizes."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{_OLLAMA_BASE}/api/tags")
                resp.raise_for_status()

            data = resp.json()
            models = data.get("models", [])

            if not models:
                return "No Ollama models installed. Install one with: ollama_install"

            lines = ["== Installed Ollama Models ==", ""]
            lines.append(f"{'Name':<30} {'Size':<12} {'Modified'}")
            lines.append("-" * 60)

            for m in models:
                name = m.get("name", "?")
                size_bytes = m.get("size", 0)
                modified = m.get("modified_at", "?")

                if size_bytes >= 1_000_000_000:
                    size_str = f"{size_bytes / 1_000_000_000:.1f} GB"
                else:
                    size_str = f"{size_bytes / 1_000_000:.0f} MB"

                # Truncate modified date to date only
                if isinstance(modified, str) and "T" in modified:
                    modified = modified.split("T")[0]

                lines.append(f"{name:<30} {size_str:<12} {modified}")

            lines.append("")
            lines.append(f"Total: {len(models)} model(s)")
            return "\n".join(lines)
        except Exception as exc:
            exc_str = str(exc)
            if "ConnectError" in type(exc).__name__ or "Connection refused" in exc_str:
                return "Ollama is not running. Start it with: ollama serve"
            return f"Error listing Ollama models: {exc}"


class OllamaInstallSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "ollama_install"

    @property
    def description(self) -> str:
        return (
            "Download and install an Ollama model locally. Examples: "
            "'qwen3:1.7b', 'softw8/nanbeige4.1-3b-tools', 'llama3.2'. "
            "Large models may take several minutes to download."
        )

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Model name (e.g., 'qwen3:4b', 'llama3.2', 'mistral')",
                },
            },
            "required": ["model"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            import httpx

            model = params["model"]
            # Stream the pull so we don't need a huge timeout waiting for
            # the entire download. We read status lines as they arrive.
            async with httpx.AsyncClient(timeout=httpx.Timeout(
                1800,  # 30 min total (large models on slow connections)
                connect=10,
            )) as client:
                resp = await client.post(
                    f"{_OLLAMA_BASE}/api/pull",
                    json={"name": model, "stream": True},
                )
                resp.raise_for_status()

                # Read streaming status lines until "success"
                last_status = ""
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        import json
                        data = json.loads(line)
                        status = data.get("status", "")
                        if status:
                            last_status = status
                        if data.get("error"):
                            return f"Error pulling '{model}': {data['error']}"
                    except Exception:
                        pass

            if "success" in last_status.lower():
                return f"Model '{model}' installed successfully. Use it with ECO local/hybrid mode."

            return f"Model '{model}' pull completed (status: {last_status})."
        except Exception as exc:
            exc_type = type(exc).__name__
            if "ConnectError" in exc_type or "Connection refused" in str(exc):
                return "Ollama is not running. Start it with: ollama serve"
            if "ReadTimeout" in exc_type or "TimeoutException" in exc_type:
                return (
                    f"Download timed out for '{params['model']}'. "
                    f"The model may be very large — try running "
                    f"'ollama pull {params['model']}' directly in terminal."
                )
            if hasattr(exc, "response") and getattr(exc.response, "status_code", 0) == 404:
                return f"Model '{params['model']}' not found. Check the name at https://ollama.com/library"
            return f"Error installing model: {exc}"


class OllamaDeleteSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "ollama_delete"

    @property
    def description(self) -> str:
        return "Delete a locally installed Ollama model to free up disk space."

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
                    "description": "Model name to delete",
                },
            },
            "required": ["model"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            import httpx

            model = params["model"]
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(
                    "DELETE",
                    f"{_OLLAMA_BASE}/api/delete",
                    json={"name": model},
                )
                resp.raise_for_status()

            return f"Model '{model}' deleted successfully."
        except Exception as exc:
            exc_type = type(exc).__name__
            if "ConnectError" in exc_type or "Connection refused" in str(exc):
                return "Ollama is not running. Start it with: ollama serve"
            if hasattr(exc, "response") and getattr(exc.response, "status_code", 0) == 404:
                return f"Model '{params['model']}' not found. Use ollama_list to see installed models."
            return f"Error deleting model: {exc}"


class OllamaShowSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "ollama_show"

    @property
    def description(self) -> str:
        return (
            "Show detailed information about an installed Ollama model "
            "including parameters, size, and family."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Model name to inspect",
                },
            },
            "required": ["model"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            import httpx

            model = params["model"]
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{_OLLAMA_BASE}/api/show",
                    json={"name": model},
                )
                resp.raise_for_status()

            data = resp.json()
            lines = [f"== Model: {model} ==", ""]

            details = data.get("details", {})
            if details:
                family = details.get("family", "unknown")
                param_size = details.get("parameter_size", "unknown")
                quant = details.get("quantization_level", "unknown")
                lines.append(f"Family: {family}")
                lines.append(f"Parameters: {param_size}")
                lines.append(f"Quantization: {quant}")

            parameters = data.get("parameters", "")
            if parameters:
                lines.append("")
                lines.append("Parameters:")
                for line in parameters.strip().split("\n")[:15]:
                    lines.append(f"  {line.strip()}")

            template = data.get("template", "")
            if template:
                # Show first few lines of template
                tmpl_lines = template.strip().split("\n")[:5]
                lines.append("")
                lines.append("Template (first 5 lines):")
                for line in tmpl_lines:
                    lines.append(f"  {line}")

            modelfile = data.get("modelfile", "")
            if modelfile:
                mf_lines = modelfile.strip().split("\n")[:10]
                lines.append("")
                lines.append("Modelfile (first 10 lines):")
                for line in mf_lines:
                    lines.append(f"  {line}")

            return "\n".join(lines)
        except Exception as exc:
            exc_type = type(exc).__name__
            if "ConnectError" in exc_type or "Connection refused" in str(exc):
                return "Ollama is not running. Start it with: ollama serve"
            if hasattr(exc, "response") and getattr(exc.response, "status_code", 0) == 404:
                return f"Model '{params['model']}' not found. Use ollama_list to see installed models."
            return f"Error showing model info: {exc}"
