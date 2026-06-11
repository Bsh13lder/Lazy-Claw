"""vault_get skill (2026-06-10 audit, Phase 4).

The vault had set/list/delete skills but NO gated retrieval path — code
elsewhere (memory_recall.py) even told the brain to "call vault_get"
while the skill didn't exist, so credentials could only re-enter a turn
by the user pasting them in plaintext. vault_get closes that gap in the
``security`` category (default ASK) so retrieval stays approval-gated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.crypto.vault import set_credential
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.skills.builtin.vault import VaultGetSkill

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def config(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo!!")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    await create_user_dek(c, "u1", "salt-a")
    try:
        yield c
    finally:
        await close_pool()


def test_vault_get_is_in_ask_gated_security_category():
    skill = VaultGetSkill()
    assert skill.name == "vault_get"
    assert skill.category == "security"


async def test_vault_get_returns_stored_secret(config):
    await set_credential(config, "u1", "openai_api_key", "sk-test-123")
    skill = VaultGetSkill(config=config)
    result = await skill.execute("u1", {"key": "openai_api_key"})
    assert "sk-test-123" in result


async def test_vault_get_missing_key_lists_nothing_sensitive(config):
    skill = VaultGetSkill(config=config)
    result = await skill.execute("u1", {"key": "nope"})
    assert "not found" in result.lower()


async def test_vault_get_requires_key_param(config):
    skill = VaultGetSkill(config=config)
    result = await skill.execute("u1", {})
    assert "error" in result.lower()


def test_vault_get_is_registered():
    from lazyclaw.skills.registry import SkillRegistry

    registry = SkillRegistry()
    registry.register_defaults(config=None)
    assert registry.get("vault_get") is not None
