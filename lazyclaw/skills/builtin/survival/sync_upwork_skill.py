"""Pull the freelancer's name + title + bio from their actual Upwork
profile via mcp-upwork and write them to SkillsProfile.

Closes the manual-typo gap on display_name: today the user has to NL-set
``set my display name to Vato``. With this skill, lazyclaw fetches the
real name from `https://www.upwork.com/freelancers/settings/profile`
(via the existing ``upwork_get_my_profile`` MCP tool) and saves it
verbatim — guaranteed to match what the client sees.

Never overwrites fields the user has set manually. Empty fields get
populated; non-empty fields are left alone unless ``force=true``.

Auto-fired by ``survival_mode enabled=true`` when display_name is
empty, so the first-time user flow is:
  1. log in to Upwork in Brave
  2. enable survival mode
  3. lazyclaw silently pulls the name and uses it on every proposal
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class SyncUpworkProfileSkill(BaseSkill):
    """Pull display_name + title + bio from the user's Upwork profile."""

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "sync_upwork_profile"

    @property
    def description(self) -> str:
        return (
            "Fetch your name, title, and bio from your actual Upwork "
            "freelancer profile and save them to your SkillsProfile so "
            "every proposal uses the EXACT name your client sees on "
            "Upwork (no typos, no mismatches). Empty fields get filled; "
            "non-empty fields are left alone unless you pass force=true. "
            "Run once at setup, or whenever you change your Upwork "
            "profile. NL: 'sync my upwork profile' / 'pull my name "
            "from upwork'."
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "force": {
                    "type": "boolean",
                    "description": (
                        "When true, overwrite display_name / title / bio "
                        "even if they're already set. Default false."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.survival.profile import get_profile, update_profile

        force = bool(params.get("force") or False)
        if self._registry is None:
            return "Error: skill registry not available."

        # Locate upwork_get_my_profile through the MCP bridge naming
        tool = None
        for tool_info in self._registry.list_mcp_tools():
            func = tool_info.get("function", {})
            tname = (func.get("name") or "").lower()
            if "upwork_get_my_profile" in tname:
                tool = self._registry.get(func.get("name", ""))
                if tool is not None:
                    break

        if tool is None:
            return (
                "Cannot sync — `upwork_get_my_profile` not available. "
                "Check that the upwork MCP is running and you're logged "
                "in to Upwork in your Brave."
            )

        try:
            raw = await tool.execute(user_id, {})
        except Exception as exc:
            return f"Failed to fetch Upwork profile: {exc}"

        # Tool result may be a dict OR a JSON-string envelope.
        if isinstance(raw, str):
            try:
                import json as _json
                upwork_profile = _json.loads(raw)
            except Exception:
                return f"Could not parse Upwork profile response: {raw[:200]}"
        else:
            upwork_profile = raw or {}

        upwork_name = (upwork_profile.get("name") or "").strip()
        upwork_title = (upwork_profile.get("title") or "").strip()
        upwork_bio = (upwork_profile.get("overview") or upwork_profile.get("bio") or "").strip()

        if not upwork_name and not upwork_title and not upwork_bio:
            return (
                "Upwork profile fetched but no name / title / bio fields "
                "could be parsed. The Upwork DOM may have changed — "
                "report this so the selectors can be updated."
            )

        current = await get_profile(self._config, user_id)
        updates: dict = {}
        applied: list[str] = []
        skipped: list[str] = []

        # Lazyclaw-set values always win over Upwork — but surface the
        # Upwork value side-by-side when they differ, so the user can
        # spot a mismatch and decide whether to re-sync with force=true.
        def _diff_note(label: str, lazy_val: str, upwork_val: str) -> str:
            if lazy_val.strip().lower() == upwork_val.strip().lower():
                return f"{label} (matches Upwork: '{lazy_val}')"
            return (
                f"{label} — lazyclaw='{lazy_val}', upwork='{upwork_val}' "
                f"(MISMATCH — pass force=true to overwrite with Upwork)"
            )

        if upwork_name:
            if force or not current.display_name:
                updates["display_name"] = upwork_name
                applied.append(f"display_name → {upwork_name}")
            else:
                skipped.append(_diff_note(
                    "display_name", current.display_name, upwork_name
                ))
        if upwork_title:
            if force or not current.title:
                updates["title"] = upwork_title
                applied.append(f"title → {upwork_title}")
            else:
                skipped.append(_diff_note(
                    "title", current.title, upwork_title
                ))
        if upwork_bio:
            if force or not current.bio:
                updates["bio"] = upwork_bio[:500]
                applied.append("bio → (filled from Upwork overview)")
            else:
                skipped.append(
                    "bio (lazyclaw value kept; Upwork has its own — "
                    "pass force=true to overwrite)"
                )

        if not updates:
            return (
                "Upwork profile fetched. Nothing to update — all "
                "matching fields already set. Pass force=true to "
                "overwrite."
            )

        await update_profile(self._config, user_id, updates)

        lines = ["Upwork profile synced:"] + [f"  ✓ {a}" for a in applied]
        if skipped:
            lines.append("Skipped (already set):")
            lines.extend([f"  · {s}" for s in skipped])
        return "\n".join(lines)
