"""LazyClaw Computer Connector — WebSocket client.

Connects to the LazyClaw server, receives commands, executes them locally,
and returns results. Auto-reconnects on disconnect.
"""
import asyncio
import base64
import io
import json
import logging
import os
import platform
import subprocess
import time

import websockets

from security import SecurityManager

logger = logging.getLogger("connector")

MAX_FILE_READ = 100 * 1024      # 100KB
MAX_OUTPUT = 10 * 1024           # 10KB
COMMAND_TIMEOUT = 30             # seconds
HEARTBEAT_INTERVAL = 30          # seconds
MAX_DIR_ENTRIES = 200
RECONNECT_BASE = 5               # seconds
RECONNECT_MAX = 60               # seconds


class Connector:
    """WebSocket client that connects to LazyClaw server."""

    def __init__(self, config: dict):
        self.server_url = config['server_url'].rstrip('/')
        self.token = config['connector_token']
        self.security = SecurityManager(
            require_approval=config.get('require_approval', True)
        )
        self._ws = None
        self._running = False

    async def run(self):
        """Main loop: connect, listen, auto-reconnect."""
        self._running = True
        backoff = RECONNECT_BASE

        while self._running:
            try:
                await self._connect()
                backoff = RECONNECT_BASE
            except (websockets.exceptions.ConnectionClosed,
                    websockets.exceptions.InvalidStatusCode,
                    OSError) as e:
                logger.warning(f"Connection lost: {e}")
            except Exception as e:
                logger.error(f"Unexpected error: {e}")

            if not self._running:
                break

            logger.info(f"Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    def stop(self):
        """Stop the connector gracefully."""
        self._running = False
        if self._ws:
            asyncio.ensure_future(self._ws.close())

    async def _connect(self):
        """Establish WebSocket connection and start listening."""
        ws_base = self.server_url.replace('http://', 'ws://').replace('https://', 'wss://')
        ws_url = f"{ws_base}/ws/connector"

        logger.info(f"Connecting to {ws_url}...")

        async with websockets.connect(
            ws_url,
            extra_headers={"Authorization": f"Bearer {self.token}"},
            ping_interval=60,
            ping_timeout=30,
            max_size=10 * 1024 * 1024,
        ) as ws:
            self._ws = ws
            logger.info("Connected!")

            await self._register(ws)

            await asyncio.gather(
                self._heartbeat_loop(ws),
                self._listen(ws),
            )

    async def _register(self, ws):
        """Send registration info to server."""
        info = {
            "type": "register",
            "platform": platform.system().lower(),
            "hostname": platform.node(),
            "username": os.getenv("USER") or os.getenv("USERNAME") or "unknown",
            "python_version": platform.python_version(),
        }
        await ws.send(json.dumps(info))
        logger.info(f"Registered: {info['platform']} / {info['hostname']}")

    async def _heartbeat_loop(self, ws):
        """Send periodic heartbeats."""
        while self._running:
            try:
                await ws.send(json.dumps({"type": "heartbeat"}))
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            except websockets.exceptions.ConnectionClosed:
                return

    async def _listen(self, ws):
        """Listen for incoming commands from server."""
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received: {raw[:100]}")
                continue

            if msg.get("type") == "command":
                asyncio.create_task(self._handle_command(ws, msg))

    async def _handle_command(self, ws, msg: dict):
        """Dispatch command to appropriate handler."""
        cmd_id = msg.get("id", "")
        command = msg.get("command", "")
        args = msg.get("args", {})

        logger.info(f"Command [{cmd_id}]: {command}")

        try:
            handler = {
                "exec": self._exec_command,
                "read_file": self._read_file,
                "write_file": self._write_file,
                "list_dir": self._list_dir,
                "screenshot": self._screenshot,
                "clipboard": self._clipboard,
            }.get(command)

            if not handler:
                result = {"success": False, "error": f"Unknown command: {command}"}
            else:
                result = await handler(args)
        except Exception as e:
            logger.error(f"Handler error for {command}: {e}")
            result = {"success": False, "error": str(e)}

        response = {
            "type": "result",
            "id": cmd_id,
            **result,
        }
        try:
            await ws.send(json.dumps(response))
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"Cannot send result for {cmd_id} — connection closed")

    # ==================== Command Handlers ====================

    async def _exec_command(self, args: dict) -> dict:
        """Execute a shell command."""
        cmd = args.get("cmd", "")
        if not cmd:
            return {"success": False, "error": "No command provided"}

        allowed, reason = self.security.is_command_allowed(cmd)
        if not allowed:
            return {"success": False, "error": reason}

        if not self.security.prompt_approval(f"$ {cmd}"):
            return {"success": False, "error": "User denied execution"}

        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=5,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=COMMAND_TIMEOUT,
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
            return {"success": False, "error": f"Command timed out after {COMMAND_TIMEOUT}s"}

    async def _read_file(self, args: dict) -> dict:
        """Read a file from the local filesystem."""
        path = args.get("path", "")
        if not path:
            return {"success": False, "error": "No path provided"}

        abs_path = os.path.abspath(os.path.expanduser(path))

        allowed, reason = self.security.is_path_allowed(abs_path, write=False)
        if not allowed:
            return {"success": False, "error": reason}

        if not os.path.exists(abs_path):
            return {"success": False, "error": f"File not found: {path}"}

        if not os.path.isfile(abs_path):
            return {"success": False, "error": f"Not a file: {path}"}

        size = os.path.getsize(abs_path)
        if size > MAX_FILE_READ:
            return {"success": False, "error": f"File too large ({size} bytes, max {MAX_FILE_READ})"}

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

    async def _write_file(self, args: dict) -> dict:
        """Write content to a file."""
        path = args.get("path", "")
        content = args.get("content", "")
        if not path:
            return {"success": False, "error": "No path provided"}

        abs_path = os.path.abspath(os.path.expanduser(path))

        allowed, reason = self.security.is_path_allowed(abs_path, write=True)
        if not allowed:
            return {"success": False, "error": reason}

        preview = content[:80] + ('...' if len(content) > 80 else '')
        if not self.security.prompt_approval(f"Write to {abs_path}\n  Content: {preview}"):
            return {"success": False, "error": "User denied write operation"}

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

    async def _list_dir(self, args: dict) -> dict:
        """List directory contents."""
        path = args.get("path", "")
        if not path:
            path = os.path.expanduser("~")

        abs_path = os.path.abspath(os.path.expanduser(path))

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
                        "modified": time.strftime('%Y-%m-%d %H:%M', time.localtime(stat.st_mtime)),
                    })
                except OSError:
                    entries.append({"name": name, "type": "unknown", "size": 0, "modified": ""})

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

    async def _screenshot(self, args: dict) -> dict:
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
            return {"success": False, "error": "Screenshot requires mss and Pillow packages"}
        except Exception as e:
            return {"success": False, "error": f"Screenshot failed: {e}"}

    async def _clipboard(self, args: dict) -> dict:
        """Read clipboard contents."""
        system = platform.system()
        try:
            if system == 'Darwin':
                result = subprocess.run(['pbpaste'], capture_output=True, text=True, timeout=5)
                content = result.stdout
            elif system == 'Linux':
                result = subprocess.run(
                    ['xclip', '-selection', 'clipboard', '-o'],
                    capture_output=True, text=True, timeout=5,
                )
                content = result.stdout
            elif system == 'Windows':
                result = subprocess.run(
                    ['powershell', '-command', 'Get-Clipboard'],
                    capture_output=True, text=True, timeout=5,
                )
                content = result.stdout
            else:
                return {"success": False, "error": f"Clipboard not supported on {system}"}

            return {
                "success": True,
                "data": {"content": content[:2000]},
            }
        except FileNotFoundError:
            return {"success": False, "error": "Clipboard tool not found (install xclip on Linux)"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Clipboard read timed out"}
