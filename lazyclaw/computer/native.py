"""Native executor — local subprocess execution for computer control.

Runs commands, reads/writes files, lists directories, and captures screenshots
directly on the server machine. All operations validated through SecurityManager.
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import shlex
import time

from lazyclaw.computer.security import SecurityManager

MAX_FILE_READ = 100 * 1024   # 100KB
MAX_OUTPUT = 10 * 1024        # 10KB
COMMAND_TIMEOUT = 30          # seconds
MAX_DIR_ENTRIES = 200


class NativeExecutor:
    """Execute computer commands locally via subprocess."""

    def __init__(self, security: SecurityManager) -> None:
        self._security = security

    async def exec_command(self, cmd: str, timeout: int = COMMAND_TIMEOUT) -> dict:
        """Execute a shell command and return stdout/stderr."""
        if not cmd:
            return {"success": False, "error": "No command provided"}

        allowed, reason = self._security.is_command_allowed(cmd)
        if not allowed:
            return {"success": False, "error": reason}

        try:
            args = shlex.split(cmd)
        except ValueError as e:
            return {"success": False, "error": f"Invalid command syntax: {e}"}

        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=5,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

            return {
                "success": True,
                "data": {
                    "stdout": stdout.decode('utf-8', errors='replace')[:MAX_OUTPUT],
                    "stderr": stderr.decode('utf-8', errors='replace')[:MAX_OUTPUT],
                    "return_code": proc.returncode,
                },
            }
        except asyncio.TimeoutError:
            return {"success": False, "error": f"Command timed out after {timeout}s"}
        except OSError as e:
            return {"success": False, "error": f"Execution failed: {e}"}

    async def read_file(self, path: str) -> dict:
        """Read a file from the local filesystem."""
        if not path:
            return {"success": False, "error": "No path provided"}

        abs_path = os.path.abspath(os.path.expanduser(path))

        allowed, reason = self._security.is_path_allowed(abs_path, write=False)
        if not allowed:
            return {"success": False, "error": reason}

        if not os.path.exists(abs_path):
            return {"success": False, "error": f"File not found: {path}"}

        if not os.path.isfile(abs_path):
            return {"success": False, "error": f"Not a file: {path}"}

        size = os.path.getsize(abs_path)
        if size > MAX_FILE_READ:
            return {
                "success": False,
                "error": f"File too large ({size} bytes, max {MAX_FILE_READ})",
            }

        try:
            with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            return {
                "success": True,
                "data": {
                    "content": content,
                    "size": size,
                    "encoding": "utf-8",
                    "path": abs_path,
                },
            }
        except UnicodeDecodeError:
            with open(abs_path, 'rb') as f:
                raw = f.read()
            return {
                "success": True,
                "data": {
                    "content": base64.b64encode(raw).decode('ascii'),
                    "size": size,
                    "encoding": "base64",
                    "path": abs_path,
                },
            }

    async def write_file(self, path: str, content: str) -> dict:
        """Write content to a file."""
        if not path:
            return {"success": False, "error": "No path provided"}

        abs_path = os.path.abspath(os.path.expanduser(path))

        allowed, reason = self._security.is_path_allowed(abs_path, write=True)
        if not allowed:
            return {"success": False, "error": reason}

        try:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, 'w', encoding='utf-8') as f:
                bytes_written = f.write(content)
            return {
                "success": True,
                "data": {
                    "path": abs_path,
                    "bytes_written": bytes_written,
                },
            }
        except OSError as e:
            return {"success": False, "error": str(e)}

    async def list_dir(self, path: str | None = None) -> dict:
        """List directory contents."""
        if not path:
            path = os.path.expanduser("~")

        abs_path = os.path.abspath(os.path.expanduser(path))

        allowed, reason = self._security.is_path_allowed(abs_path)
        if not allowed:
            return {"success": False, "error": reason}

        if not os.path.isdir(abs_path):
            return {"success": False, "error": f"Not a directory: {path}"}

        try:
            entries = []
            for name in sorted(os.listdir(abs_path))[:MAX_DIR_ENTRIES]:
                full = os.path.join(abs_path, name)
                try:
                    stat = os.stat(full)
                    entries.append({
                        "name": name,
                        "type": "dir" if os.path.isdir(full) else "file",
                        "size": stat.st_size,
                        "modified": time.strftime(
                            '%Y-%m-%d %H:%M', time.localtime(stat.st_mtime)
                        ),
                    })
                except OSError:
                    entries.append({
                        "name": name,
                        "type": "unknown",
                        "size": 0,
                        "modified": "",
                    })

            return {
                "success": True,
                "data": {
                    "path": abs_path,
                    "entries": entries,
                    "total": len(entries),
                },
            }
        except PermissionError:
            return {"success": False, "error": f"Permission denied: {path}"}

    async def screenshot(self) -> dict:
        """Capture a screenshot of the screen."""
        try:
            import mss
            from PIL import Image

            with mss.mss() as sct:
                monitor = sct.monitors[0]
                img = sct.grab(monitor)

                pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")

                buf = io.BytesIO()
                pil_img.save(buf, format="JPEG", quality=60)
                b64 = base64.b64encode(buf.getvalue()).decode('ascii')

                return {
                    "success": True,
                    "data": {
                        "image_base64": b64,
                        "width": img.size.width,
                        "height": img.size.height,
                        "format": "jpeg",
                    },
                }
        except ImportError:
            return {
                "success": False,
                "error": "Screenshot requires mss and Pillow packages",
            }
        except Exception as e:
            return {"success": False, "error": f"Screenshot failed: {e}"}
