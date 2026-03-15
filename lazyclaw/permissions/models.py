"""Immutable data models for the permissions system."""

from __future__ import annotations

from dataclasses import dataclass


# Permission levels
ALLOW = "allow"
ASK = "ask"
DENY = "deny"

VALID_LEVELS = frozenset({ALLOW, ASK, DENY})

# Default category -> permission level mapping
DEFAULT_CATEGORY_PERMISSIONS: dict[str, str] = {
    "general": ALLOW,
    "search": ALLOW,
    "memory": ALLOW,
    "vault": ASK,
    "browser": ASK,
    "computer": ASK,
    "skills": ALLOW,
    "custom": ASK,
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
