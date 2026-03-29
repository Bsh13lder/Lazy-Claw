"""ECO mode settings — CRUD for user eco preferences.

Settings stored in the existing users.settings JSON column
under the "eco" key. No new DB table needed.

Two modes (3 roles: Brain, Worker, Fallback):
  hybrid  — Haiku brain + Nanbeige local worker ($0) + Haiku fallback (auto)
  full    — User-configurable brain/worker/fallback (paid, auto)
"""

from __future__ import annotations

import json
import logging

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session
from lazyclaw.llm.eco_router import (
    VALID_MODES,
    _DISABLED_MODES,
    DISABLED_MODE_MESSAGE,
    normalize_mode,
)
from lazyclaw.llm.free_providers import PROVIDER_DEFS, discover_providers

logger = logging.getLogger(__name__)

# Base set of known providers
_BASE_PROVIDERS = set(PROVIDER_DEFS.keys()) | {"ollama", "mlx"}


def _get_valid_providers() -> set[str]:
    """Return valid provider names."""
    return set(_BASE_PROVIDERS)


# Default eco settings
DEFAULT_ECO = {
    "mode": "hybrid",
    "show_badges": True,
    "monthly_paid_budget": 0,
    "auto_fallback": True,
    "max_workers": 10,
    "brain_model": None,       # None = use default from model_registry
    "worker_model": None,      # None = use default from model_registry
    "fallback_model": None,    # None = use default from model_registry
    # FULL mode user-settable overrides (take priority over generic overrides)
    "full_brain_model": None,
    "full_worker_model": None,
    "full_fallback_model": None,
    "locked_provider": None,
    "allowed_providers": None,
    "free_providers": None,    # None = auto-detect from env
    "preferred_free_model": None,
}


async def get_eco_settings(config: Config, user_id: str) -> dict:
    """Get user's ECO mode settings."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()

    if not result or not result[0]:
        return dict(DEFAULT_ECO)

    try:
        settings = json.loads(result[0])
    except (json.JSONDecodeError, TypeError):
        return dict(DEFAULT_ECO)

    eco = settings.get("eco", {})
    if not isinstance(eco, dict):
        return dict(DEFAULT_ECO)

    # Merge with defaults for any missing keys
    merged = dict(DEFAULT_ECO)
    merged.update(eco)

    # Normalize mode (backward compat: full→full, off→full, etc.)
    raw_mode = merged.get("mode", "hybrid")
    normalized = normalize_mode(raw_mode)
    # Reject disabled modes — fall back to hybrid
    if normalized in _DISABLED_MODES:
        normalized = "hybrid"
    merged["mode"] = normalized

    # Migrate specialist_model → worker_model (legacy)
    if merged.get("specialist_model") and not merged.get("worker_model"):
        merged["worker_model"] = merged["specialist_model"]

    return merged


async def update_eco_settings(config: Config, user_id: str, updates: dict) -> dict:
    """Update user's ECO mode settings. Returns the new settings."""
    # Normalize mode if provided
    if "mode" in updates:
        raw = updates["mode"].lower().strip()
        if raw in _DISABLED_MODES:
            raise ValueError(DISABLED_MODE_MESSAGE)
        normalized = normalize_mode(raw)
        if normalized not in VALID_MODES:
            raise ValueError(
                f"Invalid eco mode: {updates['mode']}. "
                f"Use: hybrid, full, claude"
            )
        updates["mode"] = normalized

    # Validate locked_provider
    valid_providers = _get_valid_providers()
    if "locked_provider" in updates and updates["locked_provider"] is not None:
        if updates["locked_provider"] not in valid_providers:
            raise ValueError(f"Invalid provider: {updates['locked_provider']}")

    # Validate allowed_providers
    if "allowed_providers" in updates and updates["allowed_providers"] is not None:
        if not isinstance(updates["allowed_providers"], list):
            raise ValueError("allowed_providers must be a list")
        invalid = set(updates["allowed_providers"]) - valid_providers
        if invalid:
            raise ValueError(f"Invalid providers: {invalid}")

    # Validate free_providers
    if "free_providers" in updates and updates["free_providers"] is not None:
        if not isinstance(updates["free_providers"], list):
            raise ValueError("free_providers must be a list")
        invalid = set(updates["free_providers"]) - valid_providers
        if invalid:
            raise ValueError(f"Invalid free providers: {invalid}")

    # Validate max_workers
    if "max_workers" in updates:
        val = int(updates["max_workers"])
        if not 1 <= val <= 20:
            raise ValueError("max_workers must be 1-20")
        updates["max_workers"] = val

    # Validate monthly_paid_budget
    if "monthly_paid_budget" in updates:
        val = float(updates["monthly_paid_budget"])
        if val < 0:
            raise ValueError("monthly_paid_budget must be >= 0")
        updates["monthly_paid_budget"] = val

    # Load current settings
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()

    current_settings = {}
    if result and result[0]:
        try:
            current_settings = json.loads(result[0])
        except (json.JSONDecodeError, TypeError):
            current_settings = {}

    # Update eco section (immutable pattern)
    eco = current_settings.get("eco", dict(DEFAULT_ECO))
    if not isinstance(eco, dict):
        eco = dict(DEFAULT_ECO)

    for key, value in updates.items():
        if key in DEFAULT_ECO:
            eco[key] = value

    # Mark as explicit if user changes mode
    if "mode" in updates:
        eco["explicit"] = True

    # Write back
    new_settings = dict(current_settings)
    new_settings["eco"] = eco
    settings_json = json.dumps(new_settings)

    async with db_session(config) as db:
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (settings_json, user_id),
        )
        await db.commit()

    return eco


async def auto_detect_eco_mode(config: Config, user_id: str) -> str | None:
    """Auto-detect best ECO mode based on available providers.

    Only activates if user hasn't explicitly set a mode.
    """
    current = await get_eco_settings(config, user_id)
    if current.get("explicit"):
        return None

    providers = discover_providers()
    if providers:
        provider_names = list(providers.keys())
        await update_eco_settings(
            config, user_id,
            {"mode": "hybrid", "free_providers": provider_names},
        )
        logger.info(
            "Auto-enabled hybrid ECO (%d free providers: %s)",
            len(providers), ", ".join(provider_names),
        )
        return "hybrid"

    return None
