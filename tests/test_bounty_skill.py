"""DB-backed tests for the bounty skill bundle.

Coverage:
  - register_program: happy path, duplicate rejection, scope encryption
  - list_programs: returns decrypted scope assets correctly
  - recon (include_target path): refuses out-of-scope, accepts in-scope,
    writes audit rows, prefix-attack rejection (`*.x.com` ≠ `evil-x.com`)
  - validate_finding: gate passes, gate fails, force override
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.skills.builtin.bounty import store
from lazyclaw.skills.builtin.bounty.recon_skill import BountyReconSkill
from lazyclaw.skills.builtin.bounty.register_skill import (
    BountyRegisterProgramSkill,
)
from lazyclaw.skills.builtin.bounty.validate_skill import (
    BountyValidateFindingSkill,
)


@pytest.fixture
async def tmp_config(tmp_path: Path):
    """Fresh DB + registered user with derivable DEK."""
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u-bounty", "bouncy", "x", "salt-bounty-test"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


# ─── register_program ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_program_happy_path(tmp_config: Config) -> None:
    skill = BountyRegisterProgramSkill(config=tmp_config)
    out = await skill.execute("u-bounty", {
        "name": "acronis",
        "platform": "hackerone",
        "scope_assets": ["*.acronis.com", "5nine.com"],
        "rate_limit_rps": 8,
    })
    assert "Registered bounty program" in out
    assert "acronis" in out
    assert "8 rps" in out

    # Verify it's persisted + decrypts
    programs = await store.list_programs(tmp_config, "u-bounty")
    assert len(programs) == 1
    assert programs[0]["scope_assets"] == ["*.acronis.com", "5nine.com"]
    assert programs[0]["rate_limit_rps"] == 8


@pytest.mark.asyncio
async def test_register_program_rejects_empty_scope(tmp_config: Config) -> None:
    skill = BountyRegisterProgramSkill(config=tmp_config)
    out = await skill.execute("u-bounty", {
        "name": "blank",
        "platform": "intigriti",
        "scope_assets": [],
    })
    assert out.startswith("❌")
    assert "scope_assets" in out


@pytest.mark.asyncio
async def test_register_program_rejects_duplicate_name(tmp_config: Config) -> None:
    skill = BountyRegisterProgramSkill(config=tmp_config)
    await skill.execute("u-bounty", {
        "name": "dup",
        "platform": "intigriti",
        "scope_assets": ["*.dup.com"],
    })
    out = await skill.execute("u-bounty", {
        "name": "dup",
        "platform": "intigriti",
        "scope_assets": ["*.dup.com"],
    })
    assert out.startswith("❌")
    assert "already exists" in out


@pytest.mark.asyncio
async def test_register_program_rejects_unknown_platform(tmp_config: Config) -> None:
    skill = BountyRegisterProgramSkill(config=tmp_config)
    out = await skill.execute("u-bounty", {
        "name": "weird",
        "platform": "yahoo-bbp",  # not in allowlist
        "scope_assets": ["*.x.com"],
    })
    assert out.startswith("❌")
    assert "platform" in out


@pytest.mark.asyncio
async def test_scope_assets_actually_encrypted(tmp_config: Config) -> None:
    """Sanity: row in DB must be ciphertext, not plaintext JSON."""
    skill = BountyRegisterProgramSkill(config=tmp_config)
    await skill.execute("u-bounty", {
        "name": "enc-test",
        "platform": "yeswehack",
        "scope_assets": ["*.secretfun.com"],
    })
    async with db_session(tmp_config) as db:
        async with db.execute(
            "SELECT scope_assets FROM bounty_programs WHERE name = ?",
            ("enc-test",),
        ) as cur:
            row = await cur.fetchone()
    raw = row[0]
    assert "secretfun" not in raw, "scope must be encrypted at rest"
    # Envelope tag is one of LazyClaw's stable encryption versions.
    assert raw.startswith("enc:"), "must use the LazyClaw enc envelope"


# ─── recon: include_target path (scope guard) ───────────────────────────


@pytest.mark.asyncio
async def test_recon_refuses_out_of_scope_target(tmp_config: Config) -> None:
    reg = BountyRegisterProgramSkill(config=tmp_config)
    await reg.execute("u-bounty", {
        "name": "scoped",
        "platform": "intigriti",
        "scope_assets": ["*.acronis.com"],
    })

    recon = BountyReconSkill(config=tmp_config)
    out = await recon.execute("u-bounty", {
        "program_name": "scoped",
        "include_target": "https://google.com/search",
    })
    assert "scope_refused" in out
    assert "google.com" in out

    # Audit row written
    program = await store.get_program(tmp_config, "u-bounty", "scoped")
    assert program is not None
    async with db_session(tmp_config) as db:
        async with db.execute(
            "SELECT tool, decision FROM bounty_audit "
            "WHERE program_id = ? AND user_id = ?",
            (program["id"], "u-bounty"),
        ) as cur:
            rows = await cur.fetchall()
    assert any(r[0] == "scope_checker" and r[1] == "refuse" for r in rows)


@pytest.mark.asyncio
async def test_recon_accepts_in_scope_target(tmp_config: Config) -> None:
    reg = BountyRegisterProgramSkill(config=tmp_config)
    await reg.execute("u-bounty", {
        "name": "scoped2",
        "platform": "intigriti",
        "scope_assets": ["*.acronis.com", "5nine.com"],
    })

    recon = BountyReconSkill(config=tmp_config)
    out = await recon.execute("u-bounty", {
        "program_name": "scoped2",
        "include_target": "https://api.acronis.com/v1/users",
    })
    assert "in scope" in out
    assert "api.acronis.com" in out


@pytest.mark.asyncio
async def test_recon_blocks_evil_prefix_attack(tmp_config: Config) -> None:
    """The prefix-confusion edge case the upstream tests cover.
    *.acronis.com must NOT match evil-acronis.com.
    """
    reg = BountyRegisterProgramSkill(config=tmp_config)
    await reg.execute("u-bounty", {
        "name": "prefix",
        "platform": "intigriti",
        "scope_assets": ["*.acronis.com"],
    })

    recon = BountyReconSkill(config=tmp_config)
    out = await recon.execute("u-bounty", {
        "program_name": "prefix",
        "include_target": "https://evil-acronis.com/login",
    })
    assert "scope_refused" in out, "prefix attack must be refused"


@pytest.mark.asyncio
async def test_recon_refuses_unknown_program(tmp_config: Config) -> None:
    recon = BountyReconSkill(config=tmp_config)
    out = await recon.execute("u-bounty", {
        "program_name": "does-not-exist",
        "include_target": "https://acronis.com",
    })
    assert "❌" in out
    assert "does-not-exist" in out


@pytest.mark.asyncio
async def test_recon_refuses_disabled_program(tmp_config: Config) -> None:
    reg = BountyRegisterProgramSkill(config=tmp_config)
    await reg.execute("u-bounty", {
        "name": "off",
        "platform": "intigriti",
        "scope_assets": ["*.x.com"],
    })
    await store.set_enabled(tmp_config, "u-bounty", "off", False)

    recon = BountyReconSkill(config=tmp_config)
    out = await recon.execute("u-bounty", {
        "program_name": "off",
        "include_target": "https://x.com",
    })
    assert "disabled" in out


# ─── validate_finding ────────────────────────────────────────────────────


async def _seed_finding(cfg: Config, *, severity: str = "medium",
                        cvss_vector: str = "CVSS:4.0/AV:N/AC:L",
                        cvss_score: float = 6.5,
                        poc_extra: str = "") -> str:
    reg = BountyRegisterProgramSkill(config=cfg)
    await reg.execute("u-bounty", {
        "name": "vprog",
        "platform": "intigriti",
        "scope_assets": ["*.acronis.com"],
    })
    program = await store.get_program(cfg, "u-bounty", "vprog")
    assert program is not None

    poc = (
        "Subdomain admin.acronis.com has a CNAME to old-app.herokudns.com. "
        "Visiting https://admin.acronis.com returns the Heroku 'no such "
        "app' error page, indicating the underlying Heroku app has been "
        "deleted. Steps: (1) dig CNAME admin.acronis.com → old-app... "
        "(2) curl -I https://admin.acronis.com → 404 from herokuapp. "
        + poc_extra
    )
    return await store.create_finding(
        cfg, "u-bounty",
        program_id=program["id"],
        title="Subdomain takeover on admin.acronis.com",
        vuln_class="subdomain_takeover",
        severity=severity,
        target_url="https://admin.acronis.com",
        poc=poc,
        cvss_vector=cvss_vector,
        cvss_score=cvss_score,
    )


@pytest.mark.asyncio
async def test_validate_passes_clean_finding(tmp_config: Config) -> None:
    fid = await _seed_finding(tmp_config)
    skill = BountyValidateFindingSkill(config=tmp_config)
    out = await skill.execute("u-bounty", {"finding_id": fid})

    assert "VALIDATED" in out, out
    finding = await store.get_finding(tmp_config, "u-bounty", fid)
    assert finding is not None
    assert finding["status"] == "validated"


@pytest.mark.asyncio
async def test_validate_fails_thin_poc(tmp_config: Config) -> None:
    """POC under 80 chars and no host reference should fail the gate."""
    reg = BountyRegisterProgramSkill(config=tmp_config)
    await reg.execute("u-bounty", {
        "name": "thin",
        "platform": "intigriti",
        "scope_assets": ["*.acronis.com"],
    })
    program = await store.get_program(tmp_config, "u-bounty", "thin")
    fid = await store.create_finding(
        tmp_config, "u-bounty",
        program_id=program["id"],
        title="thin",  # too short title also
        vuln_class="subdomain_takeover",
        severity="medium",
        target_url="https://admin.acronis.com",
        poc="found a bug",  # under 80 chars, no host mention
    )
    skill = BountyValidateFindingSkill(config=tmp_config)
    out = await skill.execute("u-bounty", {"finding_id": fid})

    assert "FAILED" in out
    finding = await store.get_finding(tmp_config, "u-bounty", fid)
    assert finding["status"] == "proposed", "must stay proposed on fail"


@pytest.mark.asyncio
async def test_validate_force_override(tmp_config: Config) -> None:
    """force=true marks validated even when gate flags issues."""
    reg = BountyRegisterProgramSkill(config=tmp_config)
    await reg.execute("u-bounty", {
        "name": "forced",
        "platform": "intigriti",
        "scope_assets": ["*.acronis.com"],
    })
    program = await store.get_program(tmp_config, "u-bounty", "forced")
    fid = await store.create_finding(
        tmp_config, "u-bounty",
        program_id=program["id"],
        title="thin-force",
        vuln_class="subdomain_takeover",
        severity="high",  # critical/high requires CVSS — we leave it off
        target_url="https://admin.acronis.com",
        poc="x" * 200,  # long enough but doesn't mention host
    )
    skill = BountyValidateFindingSkill(config=tmp_config)
    out = await skill.execute("u-bounty", {"finding_id": fid, "force": True})

    assert "VALIDATED" in out
    assert "(forced)" in out
    finding = await store.get_finding(tmp_config, "u-bounty", fid)
    assert finding["status"] == "validated"


@pytest.mark.asyncio
async def test_validate_unknown_finding(tmp_config: Config) -> None:
    skill = BountyValidateFindingSkill(config=tmp_config)
    out = await skill.execute("u-bounty", {"finding_id": "nope-not-real"})
    assert "❌" in out


# ─── ScopeChecker reuse sanity (defence in depth) ───────────────────────


def test_upstream_scope_checker_blocks_prefix_attack() -> None:
    """Defence in depth — verify the upstream guard we lean on still
    refuses the prefix-confusion edge case. If this regresses upstream,
    we hear about it here before any live recon."""
    from claude_bug_bounty import ScopeChecker

    sc = ScopeChecker(domains=["*.target.com"])
    assert sc.is_in_scope("https://sub.target.com") is True
    assert sc.is_in_scope("https://evil-target.com") is False
    assert sc.is_in_scope("https://target.com") is False  # apex needs explicit
    assert sc.is_in_scope("https://192.168.1.1/admin") is False  # IPs refused
