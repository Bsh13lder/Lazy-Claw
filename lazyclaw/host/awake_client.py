"""Container-side client for the host AWAKE bridge.

Mirrors the host-STT-bridge client pattern (``lazyclaw/audio/stt.py``): the
bridge is usable only when its marker file exists AND a bearer token is set, and
a process-local circuit breaker keeps a missing/down bridge from adding latency
to every call.

``caffeinate`` / ``pmset`` live on the macOS host; the Linux container reaches
them through this HTTP shim at ``host.docker.internal:18791`` (Docker) or
``127.0.0.1:18791`` (native). See ``scripts/awake_bridge_server.py``.

Every method is a thin, well-typed wrapper that returns a structured dict or
raises :class:`AwakeBridgeUnavailable` with an actionable message — never a
silent failure (per the project's error-handling rules).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MARKER = Path("/app/data/.host_awake_bridge_installed")
_PORT = int(os.environ.get("LAZYCLAW_HOST_AWAKE_PORT", "18791"))
_TOKEN_ENV = "LAZYCLAW_HOST_AWAKE_TOKEN"

# Circuit breaker: after a connection failure, stop hitting the bridge for this
# long so a missing/down bridge never adds dead-air to reconcile ticks / skills.
_BACKOFF_S = 60.0
_disabled_until: float = 0.0

_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


class AwakeBridgeUnavailable(RuntimeError):
    """Raised when the host awake bridge is not installed or unreachable."""


def _host() -> str:
    """Docker reaches the host via host.docker.internal; native via loopback."""
    if Path("/.dockerenv").exists() or _MARKER.exists():
        return "host.docker.internal"
    return "127.0.0.1"


def _base_url() -> str:
    return f"http://{_host()}:{_PORT}"


def is_configured() -> bool:
    """True when the marker exists and a token is set (cheap pre-check)."""
    return _MARKER.exists() and bool(os.environ.get(_TOKEN_ENV, "").strip())


def _token() -> str:
    token = os.environ.get(_TOKEN_ENV, "").strip()
    if not token:
        raise AwakeBridgeUnavailable(
            "Awake bridge token missing. Install it with `make awake-bridge`, "
            "then restart the container so it picks up LAZYCLAW_HOST_AWAKE_TOKEN."
        )
    return token


def _ensure_available() -> None:
    global _disabled_until
    if not _MARKER.exists():
        raise AwakeBridgeUnavailable(
            "Awake bridge not installed. Run `make awake-bridge` on the host "
            "(macOS) to enable lid-closed operation."
        )
    if time.monotonic() < _disabled_until:
        raise AwakeBridgeUnavailable(
            "Awake bridge is temporarily unreachable (recent failure). It will "
            "be retried shortly — check it with `make awake-bridge-status`."
        )


def _trip_breaker() -> None:
    global _disabled_until
    _disabled_until = time.monotonic() + _BACKOFF_S


async def _request(method: str, path: str, json: dict | None = None) -> dict[str, Any]:
    _ensure_available()
    headers = {"Authorization": f"Bearer {_token()}"}
    url = f"{_base_url()}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(method, url, json=json, headers=headers)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        _trip_breaker()
        raise AwakeBridgeUnavailable(
            f"Could not reach the awake bridge at {url}. Is it running? "
            f"`make awake-bridge-status`. ({exc.__class__.__name__})"
        ) from exc
    except httpx.HTTPError as exc:  # pragma: no cover — defensive
        _trip_breaker()
        raise AwakeBridgeUnavailable(f"Awake bridge request failed: {exc}") from exc

    if resp.status_code == 401:
        raise AwakeBridgeUnavailable(
            "Awake bridge rejected the token (401). Reinstall with "
            "`make awake-bridge` and restart the container to re-sync the token."
        )
    if resp.status_code >= 400:
        detail = _safe_detail(resp)
        raise AwakeBridgeUnavailable(f"Awake bridge error {resp.status_code}: {detail}")
    try:
        return resp.json()
    except ValueError as exc:  # pragma: no cover — defensive
        raise AwakeBridgeUnavailable("Awake bridge returned non-JSON.") from exc


def _safe_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("detail") or body.get("error") or body)
        return str(body)
    except ValueError:
        return resp.text[:200]


# ── public API ───────────────────────────────────────────────────────────────

async def status() -> dict[str, Any]:
    """Return {caffeinate_running, on_ac_power, battery_percent, daily_wake,
    scheduled_wakes}."""
    return await _request("GET", "/awake/status")


async def turn_on(duration_minutes: int | None = None) -> dict[str, Any]:
    """Hold the no-sleep assertion (optionally time-boxed)."""
    return await _request("POST", "/awake/on", {"duration_minutes": duration_minutes})


async def turn_off() -> dict[str, Any]:
    """Release the no-sleep assertion (allow sleep)."""
    return await _request("POST", "/awake/off")


async def nap(minutes: int) -> dict[str, Any]:
    """Sleep now and auto-wake after `minutes` (one-shot pmset wake)."""
    return await _request("POST", "/awake/nap", {"minutes": int(minutes)})


async def set_daily_wake(time_hhmm: str, enabled: bool) -> dict[str, Any]:
    """Apply or cancel a daily repeating wake at HH:MM."""
    return await _request(
        "PUT", "/awake/daily-wake", {"time": time_hhmm, "enabled": bool(enabled)}
    )
