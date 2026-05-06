"""Bounty hunter skills — natural-language interface over the
`claude-bug-bounty` fork (vendored at the repo root).

The agent registers a program with explicit scope, runs guarded recon, and
hands findings back for human-gated submission. Every outbound request is
gated by the upstream `ScopeChecker` (deterministic code, not LLM
judgment) and `AutopilotGuard` (HTTP-method allowlist) — both reused
unmodified from the fork to inherit their 280-test safety coverage.
"""
from __future__ import annotations

from lazyclaw.skills.builtin.bounty.list_skill import (
    BountyListFindingsSkill,
    BountyListProgramsSkill,
)
from lazyclaw.skills.builtin.bounty.recon_skill import BountyReconSkill
from lazyclaw.skills.builtin.bounty.register_skill import (
    BountyDisableProgramSkill,
    BountyRegisterProgramSkill,
)
from lazyclaw.skills.builtin.bounty.validate_skill import BountyValidateFindingSkill

__all__ = [
    "BountyRegisterProgramSkill",
    "BountyDisableProgramSkill",
    "BountyListProgramsSkill",
    "BountyListFindingsSkill",
    "BountyReconSkill",
    "BountyValidateFindingSkill",
]
