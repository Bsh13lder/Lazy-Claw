"""Remote takeover via noVNC — works on both Linux servers and macOS.

When the agent needs human help (login, CAPTCHA, 2FA), this module starts
a VNC + WebSocket bridge and returns a URL the user can open on their phone.

Linux flow:  Xvfb → Brave (visible) → x11vnc → websockify/noVNC → URL
macOS flow:  Built-in Screen Sharing (port 5900) → websockify/noVNC → URL

Also provides desktop screenshot capture (full screen, not just browser).
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import signal
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

_DISPLAY_START = 99
_DISPLAY_RETRIES = 5
_WS_PORT_START = 6080
_WS_PORT_END = 6099
_AUTO_TIMEOUT_SECONDS = 300  # 5 minutes
_NOVNC_DEFAULT_PATHS = (
    # Project-local (cross-platform)
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "novnc"),
    # Linux system packages
    "/usr/share/novnc",
    "/usr/share/noVNC",
    "/opt/novnc",
    "/snap/novnc/current/usr/share/novnc",
)

_IS_MACOS = sys.platform == "darwin"


# ── Data ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RemoteSession:
    """Immutable snapshot of an active noVNC takeover session."""

    user_id: str
    display_num: int
    vnc_port: int
    ws_port: int
    token: str
    url: str
    xvfb_pid: int
    browser_pid: int
    vnc_pid: int
    ws_pid: int
    created_at: float


# Module-level registry of active sessions (one per user)
_active_sessions: dict[str, RemoteSession] = {}
# Timeout tasks keyed by user_id
_timeout_tasks: dict[str, asyncio.Task] = {}
# Guard against concurrent session start/stop for same user
_session_lock = asyncio.Lock()


# ── Public API ────────────────────────────────────────────────────────

def is_server_mode() -> bool:
    """Check if we're running in headless server mode (Linux + env flag)."""
    return (
        sys.platform == "linux"
        and os.getenv("LAZYCLAW_SERVER_MODE", "").lower() in ("true", "1", "yes")
    )


def is_remote_capable() -> bool:
    """Check if remote takeover is available (Linux server OR macOS)."""
    if is_server_mode():
        return True
    if _IS_MACOS:
        return bool(shutil.which("websockify"))
    return False


def get_active_session(user_id: str) -> RemoteSession | None:
    """Return the active remote session for a user, or None."""
    return _active_sessions.get(user_id)


async def start_remote_session(
    user_id: str,
    cdp_port: int,
    profile_dir: str,
    browser_bin: str,
    stuck_url: str | None = None,
) -> RemoteSession:
    """Start a full noVNC remote takeover session.

    Launches: Xvfb → Brave (visible) → x11vnc → websockify/noVNC.
    Returns a frozen RemoteSession with the URL to share.

    Raises RuntimeError if required system packages are missing.
    """
    async with _session_lock:
        return await _start_remote_session_locked(
            user_id, cdp_port, profile_dir, browser_bin, stuck_url,
        )


async def _start_remote_session_locked(
    user_id: str,
    cdp_port: int,
    profile_dir: str,
    browser_bin: str,
    stuck_url: str | None,
) -> RemoteSession:
    """Inner start logic — must be called under _session_lock."""
    # Stop any existing session for this user first
    await stop_remote_session(user_id)

    _check_dependencies()

    display_num = await _find_free_display()
    ws_port = _find_free_port()
    vnc_port = 5900 + display_num
    token = secrets.token_urlsafe(32)
    novnc_path = _find_novnc_path()

    pids: list[int] = []
    try:
        # 1. Xvfb — virtual display
        xvfb_proc = await _start_xvfb(display_num)
        pids.append(xvfb_proc.pid)

        # 2. Brave — visible on virtual display (NOT headless)
        browser_proc = await _start_browser(
            display_num, cdp_port, profile_dir, browser_bin,
        )
        pids.append(browser_proc.pid)

        # Wait for CDP to become available
        from lazyclaw.browser.cdp import find_chrome_cdp

        for _ in range(20):
            await asyncio.sleep(0.5)
            if await find_chrome_cdp(cdp_port):
                break

        # Navigate to stuck URL if provided
        if stuck_url:
            await _navigate_to_url(cdp_port, profile_dir, stuck_url)

        # 3. x11vnc — VNC server on the virtual display
        vnc_proc = await _start_x11vnc(display_num, vnc_port)
        pids.append(vnc_proc.pid)

        # 4. websockify — WebSocket bridge with noVNC web client
        ws_proc = await _start_websockify(ws_port, vnc_port, novnc_path)
        pids.append(ws_proc.pid)

    except Exception:
        # Cleanup whatever started on failure
        for pid in reversed(pids):
            await _kill_process(pid)
        raise

    url = _build_novnc_url(ws_port, token)
    session = RemoteSession(
        user_id=user_id,
        display_num=display_num,
        vnc_port=vnc_port,
        ws_port=ws_port,
        token=token,
        url=url,
        xvfb_pid=xvfb_proc.pid,
        browser_pid=browser_proc.pid,
        vnc_pid=vnc_proc.pid,
        ws_pid=ws_proc.pid,
        created_at=time.monotonic(),
    )
    _active_sessions[user_id] = session

    # Auto-timeout if user never connects
    task = asyncio.create_task(_auto_timeout(user_id))
    _timeout_tasks[user_id] = task

    logger.info(
        "Remote session started for %s — display=:%d, ws_port=%d, url=%s",
        user_id, display_num, ws_port, url.split("?")[0] + "?password=<redacted>",
    )
    return session


async def stop_remote_session(user_id: str) -> None:
    """Stop and clean up a remote takeover session. Idempotent."""
    # Cancel timeout task
    timeout_task = _timeout_tasks.pop(user_id, None)
    if timeout_task and not timeout_task.done():
        timeout_task.cancel()

    session = _active_sessions.pop(user_id, None)
    if session is None:
        return

    # Kill in reverse order: websockify → x11vnc → browser → Xvfb
    # On macOS some PIDs are 0 (not started) — _kill_process handles gracefully
    for pid in (session.ws_pid, session.vnc_pid, session.browser_pid, session.xvfb_pid):
        if pid:
            await _kill_process(pid)

    logger.info("Remote session stopped for %s", user_id)


# ── macOS support ────────────────────────────────────────────────────

async def take_desktop_screenshot() -> bytes | None:
    """Capture full desktop screenshot. Returns PNG bytes or None on failure."""
    import tempfile

    tmp = os.path.join(tempfile.gettempdir(), f"lazyclaw_screen_{int(time.time())}.png")
    try:
        if _IS_MACOS:
            proc = await asyncio.create_subprocess_exec(
                "screencapture", "-x", tmp,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        else:
            # Linux: use import (ImageMagick) or scrot
            tool = shutil.which("import") or shutil.which("scrot")
            if not tool:
                return None
            if "scrot" in tool:
                proc = await asyncio.create_subprocess_exec(
                    tool, tmp,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    tool, "-window", "root", tmp,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )

        await asyncio.wait_for(proc.wait(), timeout=10)
        if os.path.exists(tmp):
            with open(tmp, "rb") as f:
                data = f.read()
            return data if data else None
    except Exception as exc:
        logger.warning("Desktop screenshot failed: %s", exc)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return None


async def start_macos_remote_session(user_id: str) -> RemoteSession:
    """Start a noVNC session on macOS using built-in Screen Sharing.

    macOS flow: Screen Sharing (port 5900) → websockify → noVNC web URL.
    No Xvfb or x11vnc needed — macOS has a real display and built-in VNC.
    """
    async with _session_lock:
        await stop_remote_session(user_id)

        # Check websockify is available
        if not shutil.which("websockify"):
            raise RuntimeError(
                "websockify not found. Install: pip install websockify"
            )

        novnc_path = _find_novnc_path()
        if not novnc_path:
            raise RuntimeError(
                "noVNC not found. Run: git clone https://github.com/novnc/noVNC data/novnc"
            )

        # Check if Screen Sharing is active (VNC on port 5900)
        vnc_port = 5900
        vnc_ok = False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(("localhost", vnc_port))
                vnc_ok = True
        except (OSError, ConnectionRefusedError):
            pass

        if not vnc_ok:
            raise RuntimeError(
                "macOS Screen Sharing not enabled.\n"
                "Enable: System Settings → General → Sharing → Screen Sharing → ON"
            )

        ws_port = _find_free_port()
        token = secrets.token_urlsafe(32)

        # Start websockify bridging to macOS VNC
        ws_proc = await _start_websockify(ws_port, vnc_port, novnc_path)

        url = _build_novnc_url(ws_port, token)
        session = RemoteSession(
            user_id=user_id,
            display_num=0,       # not applicable on macOS
            vnc_port=vnc_port,
            ws_port=ws_port,
            token=token,
            url=url,
            xvfb_pid=0,         # not applicable on macOS
            browser_pid=0,       # not applicable on macOS
            vnc_pid=0,           # built-in, not managed by us
            ws_pid=ws_proc.pid,
            created_at=time.monotonic(),
        )
        _active_sessions[user_id] = session

        task = asyncio.create_task(_auto_timeout(user_id))
        _timeout_tasks[user_id] = task

        logger.info("macOS remote session started for %s — ws_port=%d", user_id, ws_port)
        return session


# ── Internal helpers ──────────────────────────────────────────────────

def _check_dependencies() -> None:
    """Verify required system packages are installed."""
    deps = {
        "Xvfb": "apt install xvfb",
        "x11vnc": "apt install x11vnc",
        "websockify": "apt install websockify",
    }
    for binary, install_cmd in deps.items():
        if not shutil.which(binary):
            raise RuntimeError(
                f"{binary} not found. Install: {install_cmd}"
            )

    if not _find_novnc_path():
        raise RuntimeError(
            "noVNC web files not found. Install: apt install novnc"
        )


def _find_novnc_path() -> str:
    """Find noVNC web directory, checking env var then common paths."""
    env_path = os.getenv("NOVNC_PATH", "")
    if env_path and Path(env_path).is_dir():
        return env_path

    for candidate in _NOVNC_DEFAULT_PATHS:
        if Path(candidate).is_dir():
            return candidate

    return ""


async def _find_free_display() -> int:
    """Find a free X display number starting from _DISPLAY_START."""
    for offset in range(_DISPLAY_RETRIES):
        display = _DISPLAY_START + offset
        lock_file = Path(f"/tmp/.X{display}-lock")
        sock_file = Path(f"/tmp/.X11-unix/X{display}")
        if not lock_file.exists() and not sock_file.exists():
            return display

    raise RuntimeError(
        f"No free X display found (tried :{_DISPLAY_START}-:{_DISPLAY_START + _DISPLAY_RETRIES - 1})"
    )


def _find_free_port() -> int:
    """Find a free TCP port in the websockify range."""
    for port in range(_WS_PORT_START, _WS_PORT_END + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
                return port
        except OSError:
            continue

    raise RuntimeError(
        f"No free port found in range {_WS_PORT_START}-{_WS_PORT_END}"
    )


async def _start_xvfb(display_num: int) -> asyncio.subprocess.Process:
    """Start Xvfb virtual display. Verifies it actually bound the display."""
    proc = await asyncio.create_subprocess_exec(
        "Xvfb", f":{display_num}",
        "-screen", "0", "1280x720x24",
        "-ac",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    # Give Xvfb time to initialize and verify it bound the display
    await asyncio.sleep(0.5)
    if not Path(f"/tmp/.X{display_num}-lock").exists():
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise RuntimeError(f"Xvfb failed to bind display :{display_num}")
    logger.debug("Xvfb started on :%d (pid=%d)", display_num, proc.pid)
    return proc


async def _start_browser(
    display_num: int,
    cdp_port: int,
    profile_dir: str,
    browser_bin: str,
) -> asyncio.subprocess.Process:
    """Start Brave/Chrome visible on the virtual display (NOT headless)."""
    os.makedirs(profile_dir, exist_ok=True)
    ext_path = str(Path(__file__).parent / "extension")

    env = {**os.environ, "DISPLAY": f":{display_num}"}

    proc = await asyncio.create_subprocess_exec(
        browser_bin,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-blink-features=AutomationControlled",
        f"--load-extension={ext_path}",
        f"--disable-extensions-except={ext_path}",
        "--window-size=1280,720",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )
    logger.debug(
        "Browser started on :%d (pid=%d, cdp_port=%d)", display_num, proc.pid, cdp_port,
    )
    return proc


async def _start_x11vnc(display_num: int, vnc_port: int) -> asyncio.subprocess.Process:
    """Start x11vnc serving the virtual display."""
    proc = await asyncio.create_subprocess_exec(
        "x11vnc",
        "-display", f":{display_num}",
        "-nopw",
        "-forever",
        "-shared",
        "-rfbport", str(vnc_port),
        "-noxdamage",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.sleep(0.5)
    logger.debug("x11vnc started on port %d (pid=%d)", vnc_port, proc.pid)
    return proc


async def _start_websockify(
    ws_port: int,
    vnc_port: int,
    novnc_path: str,
) -> asyncio.subprocess.Process:
    """Start websockify bridging WebSocket to VNC with noVNC web client."""
    proc = await asyncio.create_subprocess_exec(
        "websockify",
        "--web", novnc_path,
        str(ws_port),
        f"localhost:{vnc_port}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.sleep(0.5)
    logger.debug("websockify started on port %d (pid=%d)", ws_port, proc.pid)
    return proc


async def _navigate_to_url(cdp_port: int, profile_dir: str, url: str) -> None:
    """Navigate the browser to the stuck URL via CDP."""
    try:
        from lazyclaw.browser.cdp_backend import CDPBackend

        backend = CDPBackend(port=cdp_port, profile_dir=profile_dir)
        await backend.goto(url)
    except Exception as exc:
        logger.warning("Failed to navigate to stuck URL %s: %s", url, exc)


def _build_novnc_url(ws_port: int, token: str) -> str:
    """Build the noVNC URL with auth token."""
    host = os.getenv("LAZYCLAW_PUBLIC_HOST", "") or _get_hostname()
    scheme = "https" if os.getenv("LAZYCLAW_SSL", "") else "http"
    return f"{scheme}://{host}:{ws_port}/vnc.html?password={token}&autoconnect=true"


def _get_hostname() -> str:
    """Get the public hostname or IP for noVNC URLs."""
    hostname = socket.gethostname()
    try:
        return socket.gethostbyname(hostname)
    except socket.gaierror:
        return hostname


async def _kill_process(pid: int) -> None:
    """Kill a process by PID. Handles already-dead processes gracefully."""
    try:
        os.kill(pid, signal.SIGTERM)
        await asyncio.sleep(0.3)
        # Check if it exited gracefully before escalating
        try:
            result, _ = os.waitpid(pid, os.WNOHANG)
            if result == 0:  # Still running
                os.kill(pid, signal.SIGKILL)
        except ChildProcessError:
            pass  # Not our child or already reaped
    except ProcessLookupError:
        pass  # Already dead
    except Exception as exc:
        logger.debug("Failed to kill pid %d: %s", pid, exc)


async def _auto_timeout(user_id: str) -> None:
    """Auto-cancel session if user doesn't interact within timeout."""
    try:
        await asyncio.sleep(_AUTO_TIMEOUT_SECONDS)
        logger.warning(
            "Remote session for %s timed out after %ds",
            user_id, _AUTO_TIMEOUT_SECONDS,
        )
        # stop_remote_session is idempotent — safe even if already stopped
        await stop_remote_session(user_id)
    except asyncio.CancelledError:
        pass
