"""LazyClaw host-side AWAKE bridge — a tiny FastAPI service that runs on the
macOS host (as root) so the Dockerized agent can keep the machine awake and
schedule hardware wake-ups.

Why this exists
---------------
LazyClaw runs in a Linux container. ``caffeinate`` (holds the macOS no-sleep
power assertion) and ``pmset`` (schedules a hardware wake) are macOS-host-only
binaries — the Linux VM cannot run them. So the container POSTs to this host
service via ``host.docker.internal``, exactly like the host Brave + STT bridges.

Why root
--------
``caffeinate`` and ``pmset -g`` (status reads) do NOT need root, but
``pmset schedule`` / ``pmset repeat`` / ``pmset sleepnow`` DO. So this service
runs as a root LaunchDaemon (``/Library/LaunchDaemons/sh.lazyclaw.awake-bridge``).
A root LaunchDaemon also starts at boot before login — ideal for a headless
server box. Because it runs as root it MUST be installed to a root-owned path
(``/usr/local/lazyclaw/host-awake/``), never executed from a user-writable repo
dir (privilege-escalation vector + ~/Desktop is TCC-protected for launchd).

Security model
--------------
Binds 0.0.0.0 because the container reaches the host through
``host.docker.internal`` (a LAN-shaped IP, not 127.0.0.1). Every mutating
request must carry ``Authorization: Bearer <token>`` matching
``LAZYCLAW_HOST_AWAKE_TOKEN``. Only ``GET /health`` is unauthenticated. On
untrusted networks, ``make awake-bridge-uninstall``.

This file is intentionally standalone — no ``lazyclaw.*`` imports — so the host
venv only needs ``fastapi`` + ``uvicorn``.

NOTE: no ``from __future__ import annotations`` — keep FastAPI's runtime
type-resolution happy, matching ``stt_host_server.py``.
"""

import asyncio
import logging
import os
import re
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("lazyclaw.host-awake")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

VERSION = "1.0"
DEFAULT_PORT = 18791

# Root-owned state dir (installer creates it). PID of the live caffeinate.
STATE_DIR = Path(os.environ.get("LAZYCLAW_AWAKE_STATE_DIR", "/usr/local/lazyclaw/host-awake"))
PID_FILE = STATE_DIR / "awake.pid"

# Every-day mask for `pmset repeat`. M T W R F S U == Mon..Sun.
_PMSET_ALL_DAYS = "MTWRFSU"
_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


# ── subprocess helpers ──────────────────────────────────────────────────────

async def _run(cmd: list) -> tuple:
    """Run a command, return (returncode, stdout, stderr) as text."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return (
        proc.returncode,
        (out or b"").decode("utf-8", errors="replace"),
        (err or b"").decode("utf-8", errors="replace"),
    )


def _read_pid() -> int:
    try:
        return int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _pid_alive(pid: int) -> bool:
    """True iff `pid` is a live process owned by us (signal 0 probe)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        # PermissionError means it exists but isn't ours — treat as not-ours.
        return isinstance(sys.exc_info()[1], PermissionError)
    except OSError:
        return False


def _caffeinate_running() -> bool:
    return _pid_alive(_read_pid())


def _spawn_caffeinate(duration_minutes) -> int:
    """Spawn a detached `caffeinate -dimsu` (optionally time-boxed). Returns PID."""
    import subprocess  # local import; only needed for the long-lived detached spawn

    cmd = ["/usr/bin/caffeinate", "-dimsu"]
    if duration_minutes and int(duration_minutes) > 0:
        cmd += ["-t", str(int(duration_minutes) * 60)]
    # Detach fully so it outlives this request *and* a daemon restart.
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid))
    return proc.pid


def _kill_caffeinate() -> bool:
    pid = _read_pid()
    if not _pid_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    PID_FILE.unlink(missing_ok=True)
    return True


# ── pmset parsers (pure functions — unit-tested) ─────────────────────────────

def parse_batt(text: str) -> dict:
    """Parse `pmset -g batt`. Returns {on_ac_power, battery_percent}."""
    on_ac = "AC Power" in text
    pct = None
    m = re.search(r"(\d+)%", text)
    if m:
        pct = int(m.group(1))
    return {"on_ac_power": on_ac, "battery_percent": pct}


def parse_sched(text: str) -> dict:
    """Parse `pmset -g sched`. Returns {daily_wake, scheduled_wakes}.

    `daily_wake` is the HH:MM of a repeating wake/poweron event (or None).
    `scheduled_wakes` is a list of raw one-shot scheduled-event lines.
    """
    daily = None
    scheduled = []
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("repeating power events"):
            section = "repeat"
            continue
        if low.startswith("scheduled power events"):
            section = "sched"
            continue
        if not line:
            continue
        if section == "repeat" and ("wakeorpoweron" in low or "wakepoweron" in low or "wake" in low):
            # e.g. "wakepoweron at 7:00AM every day"
            t = _extract_clock(line)
            if t:
                daily = t
        elif section == "sched" and not low.startswith("no "):
            scheduled.append(line)
    return {"daily_wake": daily, "scheduled_wakes": scheduled}


def _extract_clock(line: str) -> str:
    """Pull a HH:MM (24h) out of a pmset schedule line like '7:00AM'."""
    m = re.search(r"(\d{1,2}):(\d{2})\s*([AaPp][Mm])?", line)
    if not m:
        return ""
    hour = int(m.group(1))
    minute = int(m.group(2))
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


# ── pmset actions (need root) ────────────────────────────────────────────────

async def _set_daily_wake(time_hhmm: str) -> tuple:
    """`pmset repeat wakeorpoweron <days> HH:MM:00`. Returns (ok, detail)."""
    if not _HHMM_RE.match(time_hhmm):
        return (False, f"bad time {time_hhmm!r}, want HH:MM")
    hhmmss = f"{time_hhmm}:00"
    code, _out, err = await _run(
        ["/usr/bin/pmset", "repeat", "wakeorpoweron", _PMSET_ALL_DAYS, hhmmss]
    )
    return (code == 0, err.strip() or "ok")


async def _cancel_daily_wake() -> tuple:
    code, _out, err = await _run(["/usr/bin/pmset", "repeat", "cancel"])
    return (code == 0, err.strip() or "ok")


async def _schedule_oneshot_wake(when: datetime) -> tuple:
    """`pmset schedule wake "MM/dd/yyyy HH:mm:ss"`. Returns (ok, detail)."""
    stamp = when.strftime("%m/%d/%Y %H:%M:%S")
    code, _out, err = await _run(["/usr/bin/pmset", "schedule", "wake", stamp])
    return (code == 0, err.strip() or stamp)


async def _sleep_now() -> None:
    await _run(["/usr/bin/pmset", "sleepnow"])


# ── FastAPI app ──────────────────────────────────────────────────────────────

def _build_app():
    from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel

    app = FastAPI(title="lazyclaw-host-awake", version=VERSION)

    expected_token = os.environ.get("LAZYCLAW_HOST_AWAKE_TOKEN", "").strip()
    if not expected_token:
        logger.error(
            "LAZYCLAW_HOST_AWAKE_TOKEN is not set — refusing to start. "
            "Reinstall via scripts/install-host-awake-bridge.sh",
        )
        sys.exit(2)

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)
        if header[7:] != expected_token:
            return JSONResponse({"error": "bad token"}, status_code=401)
        return await call_next(request)

    class OnBody(BaseModel):
        duration_minutes: int | None = None

    class NapBody(BaseModel):
        minutes: int

    class DailyWakeBody(BaseModel):
        time: str
        enabled: bool

    async def _status_payload() -> dict:
        _b, batt, _be = await _run(["/usr/bin/pmset", "-g", "batt"])
        _s, sched, _se = await _run(["/usr/bin/pmset", "-g", "sched"])
        out = {"caffeinate_running": _caffeinate_running()}
        out.update(parse_batt(batt))
        out.update(parse_sched(sched))
        return out

    @app.get("/health")
    async def health():
        return {"ok": True, "version": VERSION}

    @app.get("/awake/status")
    async def status():
        return await _status_payload()

    @app.post("/awake/on")
    async def awake_on(body: OnBody):
        # Idempotent: drop any stale process first, then (re)spawn.
        _kill_caffeinate()
        pid = _spawn_caffeinate(body.duration_minutes)
        logger.info("caffeinate started pid=%s duration=%s", pid, body.duration_minutes)
        return {"ok": True, "pid": pid, **await _status_payload()}

    @app.post("/awake/off")
    async def awake_off():
        killed = _kill_caffeinate()
        logger.info("caffeinate stopped (was_running=%s)", killed)
        return {"ok": True, "was_running": killed, **await _status_payload()}

    @app.post("/awake/nap")
    async def awake_nap(body: NapBody, background: BackgroundTasks):
        if body.minutes <= 0:
            raise HTTPException(status_code=400, detail="minutes must be > 0")
        wake_at = datetime.now() + timedelta(minutes=body.minutes)
        ok, detail = await _schedule_oneshot_wake(wake_at)
        if not ok:
            raise HTTPException(status_code=500, detail=f"pmset schedule failed: {detail}")
        _kill_caffeinate()
        # Sleep AFTER the HTTP response flushes so the caller gets confirmation.
        background.add_task(_deferred_sleep)
        logger.info("nap scheduled — wake at %s", wake_at.isoformat())
        return {"ok": True, "wake_at": wake_at.isoformat(timespec="seconds")}

    @app.put("/awake/daily-wake")
    async def daily_wake(body: DailyWakeBody):
        if body.enabled:
            ok, detail = await _set_daily_wake(body.time)
        else:
            ok, detail = await _cancel_daily_wake()
        if not ok:
            raise HTTPException(status_code=500, detail=f"pmset repeat failed: {detail}")
        logger.info("daily wake enabled=%s time=%s", body.enabled, body.time)
        return {"ok": True, "enabled": body.enabled, "time": body.time, "detail": detail}

    async def _deferred_sleep() -> None:
        # Give the response a beat to flush, then sleep the machine.
        await asyncio.sleep(1.0)
        await _sleep_now()

    return app


def main() -> None:
    import uvicorn

    port = int(os.environ.get("LAZYCLAW_HOST_AWAKE_PORT", DEFAULT_PORT))
    # Bind 0.0.0.0 so host.docker.internal can reach us. Token is the gate.
    uvicorn.run(_build_app(), host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
