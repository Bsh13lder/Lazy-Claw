"""Model catalog and per-user feature assignments."""

from __future__ import annotations

import logging

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature constants
# ---------------------------------------------------------------------------

FEATURE_CHAT = "chat"
FEATURE_BROWSER = "browser"
FEATURE_SKILL_WRITER = "skill_writer"
FEATURE_SUMMARY = "summary"

# ---------------------------------------------------------------------------
# Default model catalog
# ---------------------------------------------------------------------------

DEFAULT_MODELS = [
    {"model_id": "gpt-4o-mini", "display_name": "GPT-4o Mini", "provider": "openai", "is_default": 1},
    {"model_id": "gpt-4o", "display_name": "GPT-4o", "provider": "openai", "is_default": 0},
    {"model_id": "gpt-4.1-mini", "display_name": "GPT-4.1 Mini", "provider": "openai", "is_default": 0},
    {"model_id": "gpt-4.1", "display_name": "GPT-4.1", "provider": "openai", "is_default": 0},
    {"model_id": "claude-sonnet-4-20250514", "display_name": "Claude Sonnet 4", "provider": "anthropic", "is_default": 0},
    {"model_id": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5", "provider": "anthropic", "is_default": 0},
]


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

async def seed_default_models(config: Config) -> int:
    """Insert default models if ai_models table is empty. Returns count inserted."""
    async with db_session(config) as db:
        row = await db.execute("SELECT COUNT(*) FROM ai_models")
        count = (await row.fetchone())[0]

        if count > 0:
            return 0

        inserted = 0
        for model in DEFAULT_MODELS:
            await db.execute(
                "INSERT INTO ai_models (model_id, display_name, provider, is_default) "
                "VALUES (?, ?, ?, ?)",
                (model["model_id"], model["display_name"], model["provider"], model["is_default"]),
            )
            inserted += 1
        await db.commit()

    logger.info("Seeded %d default models", inserted)
    return inserted


# ---------------------------------------------------------------------------
# Model queries
# ---------------------------------------------------------------------------

async def list_models(config: Config) -> list[dict]:
    """List all available models."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT model_id, display_name, provider, is_default FROM ai_models ORDER BY provider, display_name"
        )
        results = await rows.fetchall()

    return [
        {
            "model_id": r[0],
            "display_name": r[1],
            "provider": r[2],
            "is_default": bool(r[3]),
        }
        for r in results
    ]


# ---------------------------------------------------------------------------
# Per-user assignments
# ---------------------------------------------------------------------------

async def get_user_model(config: Config, user_id: str, feature: str) -> str:
    """Get user's assigned model for a feature. Falls back to config.default_model."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT model_id FROM user_model_assignments WHERE user_id = ? AND feature = ?",
            (user_id, feature),
        )
        result = await row.fetchone()

    if result:
        return result[0]
    return config.default_model


async def set_user_model(config: Config, user_id: str, feature: str, model_id: str) -> None:
    """Assign a model to a user for a specific feature (upsert)."""
    async with db_session(config) as db:
        # Try update first
        cursor = await db.execute(
            "UPDATE user_model_assignments SET model_id = ? WHERE user_id = ? AND feature = ?",
            (model_id, user_id, feature),
        )
        if cursor.rowcount == 0:
            await db.execute(
                "INSERT INTO user_model_assignments (user_id, feature, model_id) VALUES (?, ?, ?)",
                (user_id, feature, model_id),
            )
        await db.commit()

    logger.info("Set model %s for user %s feature %s", model_id, user_id, feature)


async def get_user_assignments(config: Config, user_id: str) -> dict[str, str]:
    """Get all feature -> model_id assignments for a user."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT feature, model_id FROM user_model_assignments WHERE user_id = ?",
            (user_id,),
        )
        results = await rows.fetchall()

    return {r[0]: r[1] for r in results}
