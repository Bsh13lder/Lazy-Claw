"""register_browser_account — add a browser identity to the user.

Creates the on-disk Chromium profile dir and persists an ``AccountInfo``
in browser_settings. Subsequent ``switch_browser_account`` or goals
that pass ``account_slug`` will route through this profile, isolating
cookies + storage from the user's other accounts on the same domain.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class RegisterBrowserAccountSkill(BaseSkill):
    """Register a new browser-profile identity (e.g. second Reddit account)."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "register_browser_account"

    @property
    def display_name(self) -> str:
        return "Register Browser Account"

    @property
    def description(self) -> str:
        return (
            "Register a new browser-profile identity. Use when the user has "
            "more than one account on the same site (e.g. two Reddit accounts "
            "for two businesses). Creates an isolated Chromium profile so "
            "cookies and login state stay separate. Slug is the on-disk key; "
            "friendly_name is what humans use ('Hirossa main', 'side-hustle')."
        )

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": (
                        "Lowercase identifier 1-32 chars, [a-z0-9_-]. "
                        "Becomes the directory name on disk."
                    ),
                },
                "friendly_name": {
                    "type": "string",
                    "description": (
                        "Human label ('Hirossa main', 'marketing'). Shown "
                        "in lists + used by the agent in conversation."
                    ),
                },
                "primary_domain": {
                    "type": "string",
                    "description": (
                        "Site this identity is for (e.g. 'reddit.com'). "
                        "Used to pick the active account when no domain "
                        "hint is given."
                    ),
                },
            },
            "required": ["slug", "friendly_name", "primary_domain"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        slug = (params.get("slug") or "").strip().lower()
        friendly_name = (params.get("friendly_name") or "").strip()
        primary_domain = (params.get("primary_domain") or "").strip().lower()
        if not slug or not friendly_name or not primary_domain:
            return (
                "Missing required field. Provide slug, friendly_name, and "
                "primary_domain."
            )

        from lazyclaw.browser.browser_settings import register_account
        from lazyclaw.browser.profile_resolver import (
            ensure_profile_dir, InvalidAccountSlugError,
        )

        try:
            account = await register_account(
                self._config, user_id, slug, friendly_name, primary_domain,
            )
        except InvalidAccountSlugError as exc:
            return f"Invalid slug: {exc}"
        except ValueError as exc:
            return f"Cannot register: {exc}"

        path = ensure_profile_dir(self._config, user_id, account.slug)
        return (
            f"Registered browser account `{account.slug}` "
            f"('{account.friendly_name}') for `{account.primary_domain}`.\n"
            f"Profile path: `{path}`\n\n"
            "Next: open the site once and log in — cookies persist in this "
            "profile only. To make this account the default for "
            f"`{account.primary_domain}`, call `switch_browser_account`."
        )
