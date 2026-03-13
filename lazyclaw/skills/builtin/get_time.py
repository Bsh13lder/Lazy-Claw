from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from lazyclaw.skills.base import BaseSkill


class GetTimeSkill(BaseSkill):
    @property
    def category(self) -> str:
        return "utility"

    @property
    def name(self) -> str:
        return "get_current_time"

    @property
    def description(self) -> str:
        return "Get the current date and time in a specified timezone."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone name (e.g., 'America/New_York', 'Asia/Tokyo', 'UTC')",
                    "default": "UTC",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        tz_name = params.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except (KeyError, Exception):
            return f"Unknown timezone: {tz_name}. Use IANA format like 'America/New_York'."

        now = datetime.now(tz)
        return f"Current time in {tz_name}: {now.strftime('%A, %B %d, %Y %I:%M:%S %p %Z')}"
