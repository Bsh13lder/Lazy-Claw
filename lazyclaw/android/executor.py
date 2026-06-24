"""Android executor — drive a physical Android device over ADB (no root).

Sibling of `computer/native.py`. Where NativeExecutor runs subprocesses on the
host, AndroidExecutor runs them on a USB/Wi-Fi-connected Android phone through
`adb`. This is enough for an LLM to operate the phone like a human:

    screenshot() + ui_dump()   →  the agent SEES the screen (pixels + element tree)
    tap()/swipe()/input_text() →  the agent ACTS
    launch_app()/current_app() →  the agent NAVIGATES

All methods return the project's standard envelope: {"success", "data"|"error"}.
No root and no bootloader unlock required — `adb` already grants input
injection, screen capture, and the UI hierarchy as the `shell` user.
"""
from __future__ import annotations

import asyncio
import base64

from lazyclaw.android.security import AndroidSecurity

DEFAULT_TIMEOUT = 20          # seconds
MAX_TEXT_OUTPUT = 200 * 1024  # UI dumps can be large
UI_DUMP_REMOTE = "/sdcard/window_dump.xml"


class AndroidExecutor:
    """Execute Android control actions via adb. Stateless except for config."""

    def __init__(
        self,
        security: AndroidSecurity | None = None,
        adb_path: str = "adb",
        serial: str | None = None,
    ) -> None:
        self._security = security or AndroidSecurity()
        self._adb = adb_path
        # When more than one device is attached, pin to this serial.
        self._serial = serial

    # ----- internal subprocess helpers ------------------------------------

    def _base_args(self) -> list[str]:
        args = [self._adb]
        if self._serial:
            args += ["-s", self._serial]
        return args

    async def _run_bytes(
        self, args: list[str], timeout: int = DEFAULT_TIMEOUT
    ) -> tuple[int, bytes, str]:
        """Run an adb invocation, returning (returncode, stdout_bytes, stderr_text)."""
        proc = await asyncio.create_subprocess_exec(
            *self._base_args(), *args,
            stdin=asyncio.subprocess.DEVNULL,   # never let adb eat our stdin
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout, stderr.decode("utf-8", errors="replace")

    async def _run_text(
        self, args: list[str], timeout: int = DEFAULT_TIMEOUT
    ) -> dict:
        """Run an adb invocation and return a text envelope."""
        try:
            code, out, err = await self._run_bytes(args, timeout)
        except asyncio.TimeoutError:
            return {"success": False, "error": f"adb timed out after {timeout}s"}
        except FileNotFoundError:
            return {"success": False, "error": f"adb not found at '{self._adb}'"}
        except OSError as e:
            return {"success": False, "error": f"adb failed: {e}"}

        text = out.decode("utf-8", errors="replace")
        if code != 0:
            return {"success": False, "error": err.strip() or text.strip() or "adb error"}
        return {"success": True, "data": {"stdout": text[:MAX_TEXT_OUTPUT]}}

    # ----- observation -----------------------------------------------------

    async def devices(self) -> dict:
        """List attached devices."""
        res = await self._run_text(["devices", "-l"])
        if not res["success"]:
            return res
        lines = [
            ln for ln in res["data"]["stdout"].splitlines()[1:] if ln.strip()
        ]
        return {"success": True, "data": {"devices": lines, "count": len(lines)}}

    async def screenshot(self) -> dict:
        """Capture the screen as a base64 PNG (via `exec-out screencap -p`)."""
        try:
            code, out, err = await self._run_bytes(["exec-out", "screencap", "-p"])
        except asyncio.TimeoutError:
            return {"success": False, "error": "Screenshot timed out"}
        except OSError as e:
            return {"success": False, "error": f"Screenshot failed: {e}"}
        if code != 0 or not out:
            return {"success": False, "error": err.strip() or "Empty screenshot"}
        return {
            "success": True,
            "data": {
                "image_base64": base64.b64encode(out).decode("ascii"),
                "format": "png",
                "bytes": len(out),
            },
        }

    async def ui_dump(self) -> dict:
        """Return the UI hierarchy XML — the agent's structured view of the screen.

        Two steps: tell uiautomator to dump to the device, then read it back.
        The XML lists every visible node with its text, resource-id, and
        clickable bounds, e.g. `bounds="[42,180][1038,300]"`.
        """
        dump = await self._run_text(["shell", "uiautomator", "dump", UI_DUMP_REMOTE])
        if not dump["success"]:
            return dump
        read = await self._run_text(["exec-out", "cat", UI_DUMP_REMOTE])
        if not read["success"]:
            return read
        return {"success": True, "data": {"xml": read["data"]["stdout"]}}

    async def current_app(self) -> dict:
        """Best-effort foreground package via dumpsys."""
        res = await self._run_text(
            ["shell", "dumpsys", "activity", "activities"]
        )
        if not res["success"]:
            return res
        for line in res["data"]["stdout"].splitlines():
            if "mResumedActivity" in line or "ResumedActivity" in line:
                return {"success": True, "data": {"resumed": line.strip()}}
        return {"success": True, "data": {"resumed": ""}}

    async def list_packages(self, third_party_only: bool = False) -> dict:
        """List installed packages for the current user."""
        args = ["shell", "pm", "list", "packages", "--user", "0"]
        if third_party_only:
            args.append("-3")
        res = await self._run_text(args)
        if not res["success"]:
            return res
        pkgs = sorted(
            ln.replace("package:", "").strip()
            for ln in res["data"]["stdout"].splitlines()
            if ln.startswith("package:")
        )
        return {"success": True, "data": {"packages": pkgs, "count": len(pkgs)}}

    # ----- action ----------------------------------------------------------

    async def tap(self, x: int, y: int) -> dict:
        """Tap at (x, y) in screen pixels."""
        return await self._run_text(["shell", "input", "tap", str(int(x)), str(int(y))])

    async def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> dict:
        """Swipe from (x1, y1) to (x2, y2) over duration_ms."""
        return await self._run_text([
            "shell", "input", "swipe",
            str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)),
            str(int(duration_ms)),
        ])

    async def input_text(self, text: str) -> dict:
        """Type ASCII text into the focused field.

        `input text` treats spaces as argument separators, so we send them as
        `%s`. Rich unicode/emoji need ADBKeyboard — out of scope for this stub.
        """
        if not text:
            return {"success": False, "error": "No text provided"}
        safe = text.replace(" ", "%s")
        return await self._run_text(["shell", "input", "text", safe])

    async def key_event(self, key: str | int) -> dict:
        """Send a key event. Accepts a numeric code or a name like KEYCODE_BACK.

        Common: KEYCODE_BACK, KEYCODE_HOME, KEYCODE_ENTER, KEYCODE_APP_SWITCH.
        """
        return await self._run_text(["shell", "input", "keyevent", str(key)])

    async def launch_app(self, package: str) -> dict:
        """Launch an app by package name via its launcher intent."""
        if not package:
            return {"success": False, "error": "No package provided"}
        return await self._run_text([
            "shell", "monkey", "-p", package,
            "-c", "android.intent.category.LAUNCHER", "1",
        ])

    # ----- guarded mutations ----------------------------------------------

    async def shell(self, cmd: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
        """Run an arbitrary adb shell command, blocked-pattern checked first."""
        allowed, reason = self._security.is_shell_allowed(cmd)
        if not allowed:
            return {"success": False, "error": reason}
        return await self._run_text(["shell", cmd], timeout)

    async def uninstall_user0(self, package: str) -> dict:
        """Reversibly remove a package for user 0 (protected list enforced)."""
        allowed, reason = self._security.is_uninstall_allowed(package)
        if not allowed:
            return {"success": False, "error": reason}
        res = await self._run_text(["shell", "pm", "uninstall", "--user", "0", package])
        if res["success"] and "Success" in res["data"]["stdout"]:
            return {"success": True, "data": {"package": package, "removed": True}}
        return {"success": False, "error": res.get("error") or "pm uninstall did not report Success"}
