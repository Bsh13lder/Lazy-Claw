"""Immutable data models for the permissions system."""

from __future__ import annotations

from dataclasses import dataclass


# Permission levels
ALLOW = "allow"
ASK = "ask"
DENY = "deny"

VALID_LEVELS = frozenset({ALLOW, ASK, DENY})

# Default category -> permission level mapping
# Safe read-only categories default to ALLOW so the agent can act without
# asking "would you like me to proceed?" on every tool call.
# Only truly sensitive categories (vault, computer) require approval.
DEFAULT_CATEGORY_PERMISSIONS: dict[str, str] = {
    "general": ALLOW,
    "utility": ALLOW,
    "search": ALLOW,
    "research": ALLOW,
    "memory": ALLOW,
    "vault": ASK,
    "browser": ALLOW,
    "browser_management": ALLOW,
    "computer": ASK,
    "skills": ALLOW,
    "custom": ALLOW,
    "security": ASK,
    "mcp": ALLOW,
    "mcp_management": ALLOW,
    "survival": ALLOW,
    "tasks": ALLOW,
    # Meta-tools the brain MUST be able to call without an approval loop:
    # search_tools is how the brain discovers anything beyond the 16 base
    # tools; orchestration covers dispatch_subagents / delegate /
    # run_background — gating these on ASK locks the brain in an
    # approval-deny-retry loop and the user sees no progress. Production
    # log on 2026-04-28 10:29 showed the brain stuck calling search_tools
    # → 'requires approval' → next iteration → repeat.
    "core": ALLOW,
    "orchestration": ALLOW,
}


@dataclass(frozen=True)
class ApprovalRequest:
    """A pending, approved, denied, or expired approval request."""

    id: str
    user_id: str
    skill_name: str
    arguments: str
    status: str  # pending | approved | denied | expired
    source: str  # agent | heartbeat | channel
    decided_by: str | None
    decided_at: str | None
    expires_at: str
    created_at: str


@dataclass(frozen=True)
class ResolvedPermission:
    """The resolved permission level for a single skill."""

    skill_name: str
    level: str  # allow | ask | deny
    source: str  # category_default | skill_override


@dataclass(frozen=True)
class AuditEntry:
    """A single audit log entry."""

    id: str
    user_id: str
    action: str
    skill_name: str | None
    arguments_hash: str | None
    result_summary: str | None
    approval_id: str | None
    source: str
    ip_address: str | None
    created_at: str
