"""Computer control skills — run commands, read/write files, list dirs, screenshots."""

from __future__ import annotations

import json

from lazyclaw.skills.base import BaseSkill


class RunCommandSkill(BaseSkill):
    """Execute a shell command on the user's computer."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command on the user's computer. "
            "Returns stdout, stderr, and return code. "
            "Dangerous commands (rm -rf /, fork bombs, etc.) are blocked."
        )

    @property
    def category(self) -> str:
        return "computer"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": "Shell command to execute (e.g., 'ls -la', 'python script.py')",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max execution time in seconds (default: 30)",
                },
            },
            "required": ["cmd"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.computer.manager import ComputerManager
        from lazyclaw.gateway.routes.connector import get_connector_server

        if not self._config:
            return "Error: config not available"

        cmd = params.get("cmd", "")
        if not cmd:
            return "Error: cmd is required"

        timeout = params.get("timeout", 30)
        connector = get_connector_server()
        manager = ComputerManager(self._config, connector)
        result = await manager.exec_command(user_id, cmd, timeout=timeout)

        if not result.get("success"):
            return f"Error: {result.get('error', 'Unknown error')}"

        data = result["data"]
        parts = []
        if data.get("stdout"):
            parts.append(data["stdout"])
        if data.get("stderr"):
            parts.append(f"STDERR: {data['stderr']}")
        parts.append(f"(exit code: {data.get('return_code', '?')})")
        return "\n".join(parts)


class ReadFileSkill(BaseSkill):
    """Read a file from the user's computer."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file from the user's computer. "
            "Max 100KB. Sensitive paths (SSH keys, etc.) are blocked."
        )

    @property
    def category(self) -> str:
        return "computer"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to read (e.g., '/tmp/data.txt', '~/notes.md')",
                },
            },
            "required": ["path"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.computer.manager import ComputerManager
        from lazyclaw.gateway.routes.connector import get_connector_server

        if not self._config:
            return "Error: config not available"

        path = params.get("path", "")
        if not path:
            return "Error: path is required"

        connector = get_connector_server()
        manager = ComputerManager(self._config, connector)
        result = await manager.read_file(user_id, path)

        if not result.get("success"):
            return f"Error: {result.get('error', 'Unknown error')}"

        data = result["data"]
        if data.get("encoding") == "base64":
            return f"Binary file ({data.get('size', 0)} bytes) at {data.get('path')}"
        return data.get("content", "")


class WriteFileSkill(BaseSkill):
    """Write content to a file on the user's computer."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Write text content to a file on the user's computer. "
            "Creates parent directories if needed. "
            "System paths and sensitive locations are blocked."
        )

    @property
    def category(self) -> str:
        return "computer"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to write to (e.g., '/tmp/output.txt')",
                },
                "content": {
                    "type": "string",
                    "description": "Text content to write to the file",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.computer.manager import ComputerManager
        from lazyclaw.gateway.routes.connector import get_connector_server

        if not self._config:
            return "Error: config not available"

        path = params.get("path", "")
        content = params.get("content", "")
        if not path:
            return "Error: path is required"

        connector = get_connector_server()
        manager = ComputerManager(self._config, connector)
        result = await manager.write_file(user_id, path, content)

        if not result.get("success"):
            return f"Error: {result.get('error', 'Unknown error')}"

        data = result["data"]
        return f"Written {data.get('bytes_written', 0)} bytes to {data.get('path')}"


class ListDirectorySkill(BaseSkill):
    """List files and folders in a directory."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_directory"

    @property
    def description(self) -> str:
        return (
            "List files and directories in a given path. "
            "Defaults to home directory if no path specified. "
            "Shows name, type, size, and last modified date."
        )

    @property
    def category(self) -> str:
        return "computer"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list (defaults to home directory)",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.computer.manager import ComputerManager
        from lazyclaw.gateway.routes.connector import get_connector_server

        if not self._config:
            return "Error: config not available"

        path = params.get("path")
        connector = get_connector_server()
        manager = ComputerManager(self._config, connector)
        result = await manager.list_dir(user_id, path)

        if not result.get("success"):
            return f"Error: {result.get('error', 'Unknown error')}"

        data = result["data"]
        lines = [f"Directory: {data.get('path')} ({data.get('total', 0)} entries)\n"]
        for entry in data.get("entries", []):
            icon = "/" if entry["type"] == "dir" else " "
            size = f"{entry['size']:>8}" if entry["type"] == "file" else "     <DIR>"
            lines.append(f"  {size}  {entry['modified']}  {entry['name']}{icon}")
        return "\n".join(lines)


class TakeScreenshotSkill(BaseSkill):
    """Capture a screenshot of the user's screen."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "take_screenshot"

    @property
    def description(self) -> str:
        return (
            "Capture a screenshot of the user's screen. "
            "Returns the screenshot dimensions. "
            "Requires mss and Pillow packages."
        )

    @property
    def category(self) -> str:
        return "computer"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.computer.manager import ComputerManager
        from lazyclaw.gateway.routes.connector import get_connector_server

        if not self._config:
            return "Error: config not available"

        connector = get_connector_server()
        manager = ComputerManager(self._config, connector)
        result = await manager.screenshot(user_id)

        if not result.get("success"):
            return f"Error: {result.get('error', 'Unknown error')}"

        data = result["data"]
        return (
            f"Screenshot captured: {data.get('width')}x{data.get('height')} "
            f"({data.get('format', 'jpeg')})"
        )
