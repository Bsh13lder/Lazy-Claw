"""Awake mode skill — keep the macOS host awake with the lid closed.

NL control over ``lazyclaw.host.awake_client``. Lets the user say:
  "stay awake", "let it sleep", "sleep for 2 hours", "wake me at 7am every day",
  "are you awake?" — all routed from Telegram NL and the Web UI chat without any
  extra wiring (brain auto-discovers via skill registry).

Works via the host awake bridge (a root launchd FastAPI on port 18791).
If the bridge isn't installed the skill replies with a clear install instruction
rather than failing silently.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class AwakeModeSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "awake_mode"

    @property
    def description(self) -> str:
        return (
            "Control whether the macOS host stays awake with the laptop lid closed. "
            "Use for: 'stay awake', 'let it sleep', 'sleep for 2 hours then come back', "
            "'wake me at 7am every day', 'turn off daily wake', 'are you awake?'. "
            "action='on' → hold the no-sleep assertion (keeps running lid-closed). "
            "action='off' → allow sleep. "
            "action='nap' → sleep now and auto-wake after duration_minutes. "
            "action='daily_wake' → set/cancel a daily repeating wake alarm. "
            "action='status' → show current state."
        )

    @property
    def category(self) -> str:
        return "core"

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["on", "off", "nap", "daily_wake", "status"],
                    "description": "What to do: on/off/nap/daily_wake/status",
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "For 'on': time-box the awake assertion (optional, no value = indefinite). "
                                   "For 'nap': how many minutes to sleep before auto-waking (required).",
                },
                "daily_wake_time": {
                    "type": "string",
                    "description": "For 'daily_wake': time in HH:MM 24h format, e.g. '07:00'.",
                },
                "daily_wake_enabled": {
                    "type": "boolean",
                    "description": "For 'daily_wake': true to enable the alarm, false to cancel it.",
                },
            },
            "required": ["action"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.host import awake_client
        from lazyclaw.host.awake_client import AwakeBridgeUnavailable
        from lazyclaw.settings.general import get_general_settings, update_general_settings

        action = (params.get("action") or "status").lower().strip()

        if action == "status":
            return await self._status(user_id, awake_client, get_general_settings)

        if action == "on":
            return await self._on(user_id, params, awake_client, update_general_settings)

        if action == "off":
            return await self._off(user_id, awake_client, update_general_settings)

        if action == "nap":
            return await self._nap(user_id, params, awake_client, update_general_settings)

        if action == "daily_wake":
            return await self._daily_wake(user_id, params, awake_client, update_general_settings)

        return f"Unknown action '{action}'. Use: on, off, nap, daily_wake, status."

    # ── action handlers ──────────────────────────────────────────────────────

    async def _status(self, user_id, client, get_settings) -> str:
        settings = await get_settings(self._config, user_id)
        awake_cfg = settings.get("awake", {})

        if not client.is_configured():
            return (
                "Awake bridge not installed — the host can't be prevented from sleeping yet.\n"
                "To install it run: `make awake-bridge` (needs one-time sudo)."
            )
        try:
            st = await client.status()
        except client.AwakeBridgeUnavailable as exc:
            return f"Could not reach awake bridge: {exc}"

        lines = []
        if st.get("caffeinate_running"):
            lines.append("🟢 **Awake mode: ON** — lid-closed sleep is blocked.")
        else:
            lines.append("🌙 **Awake mode: OFF** — machine can sleep normally.")

        if st.get("on_ac_power"):
            pct = st.get("battery_percent")
            pct_str = f" ({pct}%)" if pct is not None else ""
            lines.append(f"🔌 Power: AC{pct_str}")
        else:
            pct = st.get("battery_percent")
            lines.append(f"🔋 Power: Battery ({pct}%)" if pct is not None else "🔋 Power: Battery")
            if awake_cfg.get("enabled"):
                lines.append("⚠️ Awake mode is ON but you're on battery — closing the lid still sleeps. Plug in.")

        if st.get("daily_wake"):
            lines.append(f"⏰ Daily wake: {st['daily_wake']} every day")
        elif awake_cfg.get("daily_wake_enabled"):
            lines.append(f"⏰ Daily wake: {awake_cfg.get('daily_wake_time', '07:00')} (will apply on next reconcile)")

        suppressed = awake_cfg.get("suppressed_until")
        if suppressed:
            try:
                until = datetime.fromisoformat(suppressed.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if until > now:
                    remaining_m = int((until - now).total_seconds() // 60)
                    lines.append(f"😴 Napping — wakes in ~{remaining_m} min")
            except (ValueError, TypeError):
                pass

        return "\n".join(lines)

    async def _on(self, user_id, params, client, update_settings) -> str:
        duration = params.get("duration_minutes")
        if not client.is_configured():
            return _not_installed_msg()
        try:
            st = await client.turn_on(duration_minutes=duration)
        except client.AwakeBridgeUnavailable as exc:
            return f"Awake bridge error: {exc}"

        await update_settings(self._config, user_id, {"awake": {"enabled": True, "suppressed_until": None}})

        dur_str = f" for {duration} min" if duration else " indefinitely"
        msg = f"🟢 Awake mode ON{dur_str} — lid-closed sleep is blocked."
        if not st.get("on_ac_power"):
            msg += "\n⚠️ You're on battery — closing the lid still sleeps. Plug in to keep running lid-closed."
        return msg

    async def _off(self, user_id, client, update_settings) -> str:
        if not client.is_configured():
            return _not_installed_msg()
        try:
            await client.turn_off()
        except client.AwakeBridgeUnavailable as exc:
            return f"Awake bridge error: {exc}"
        await update_settings(self._config, user_id, {"awake": {"enabled": False}})
        return "🌙 Awake mode OFF — the machine can sleep normally."

    async def _nap(self, user_id, params, client, update_settings) -> str:
        minutes = params.get("duration_minutes")
        if not minutes or int(minutes) <= 0:
            return "Please specify duration_minutes > 0 for a nap. E.g. 'sleep for 120 minutes'."
        minutes = int(minutes)
        if not client.is_configured():
            return _not_installed_msg()
        try:
            result = await client.nap(minutes)
        except client.AwakeBridgeUnavailable as exc:
            return f"Awake bridge error: {exc}"

        wake_at = result.get("wake_at", "")
        suppressed_until = wake_at or (
            datetime.now(timezone.utc).isoformat()
        )
        await update_settings(self._config, user_id, {
            "awake": {"suppressed_until": suppressed_until}
        })

        h, m = divmod(minutes, 60)
        dur_str = f"{h}h {m}m" if h else f"{m}m"
        wake_str = f" Scheduled wake at {wake_at}." if wake_at else ""
        return f"😴 Napping for {dur_str}.{wake_str} I'll come back automatically."

    async def _daily_wake(self, user_id, params, client, update_settings) -> str:
        enabled = params.get("daily_wake_enabled")
        time_str = (params.get("daily_wake_time") or "07:00").strip()

        if enabled is None:
            return "Please specify daily_wake_enabled (true/false) and optionally daily_wake_time (HH:MM)."

        if not client.is_configured():
            return _not_installed_msg()

        try:
            await client.set_daily_wake(time_str, bool(enabled))
        except client.AwakeBridgeUnavailable as exc:
            return f"Awake bridge error: {exc}"

        await update_settings(self._config, user_id, {
            "awake": {
                "daily_wake_enabled": bool(enabled),
                "daily_wake_time": time_str,
            }
        })

        if enabled:
            return f"⏰ Daily wake set for {time_str} every day. The machine will wake itself even from deep sleep."
        return "⏰ Daily wake alarm cancelled."


def _not_installed_msg() -> str:
    return (
        "Awake bridge not installed yet.\n"
        "Run `make awake-bridge` on your Mac host (one-time, needs sudo) then restart the container.\n"
        "This installs a small launchd daemon that controls caffeinate + pmset."
    )
