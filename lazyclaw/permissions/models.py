"""Immutable data models for the permissions system."""

from __future__ import annotations

from dataclasses import dataclass


# Permission levels
ALLOW = "allow"
ASK = "ask"
DENY = "deny"

VALID_LEVELS = frozenset({ALLOW, ASK, DENY})

# Default category -> permission level mapping.
# Safe read-only categories and "user's own data" categories default to ALLOW
# so the agent can act without asking "would you like me to proceed?" on
# every tool call. Sensitive categories that touch credentials, shell, or
# money still require approval.
#
# Categories listed below were promoted to ALLOW after a 2026-05-01 incident:
# the brain hit a Google-Sheets append on a beauty-salons scrape task, the
# `google` category fell through to the ASK fallback, and the user got an
# unexpected popup for an action they had already authed. The categories
# below are safe-by-default because either:
#   (a) they only touch the user's own services they explicitly authed
#       (google, communication, n8n);
#   (b) they only touch local encrypted state (lazybrain, pipeline, replay,
#       session, tasks);
#   (c) they're configuration toggles or read-only diagnostics
#       (system, ai_management, teams).
DEFAULT_CATEGORY_PERMISSIONS: dict[str, str] = {
    # ── Safe defaults (ALLOW) ────────────────────────────────────────────
    "general": ALLOW,
    "utility": ALLOW,
    "search": ALLOW,
    "research": ALLOW,
    "memory": ALLOW,
    "browser": ALLOW,
    "browser_management": ALLOW,
    "skills": ALLOW,
    "custom": ALLOW,
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
    # User's own services (already authed end-to-end at OAuth/login time):
    "google": ALLOW,         # Gmail / Sheets / Drive on the user's account
    "communication": ALLOW,  # email / whatsapp / instagram via configured MCPs
    "n8n": ALLOW,            # workflow CRUD on the user's own n8n instance
    # Local encrypted state — no external blast radius:
    "lazybrain": ALLOW,      # PKM notes / wikilinks / journal
    "pipeline": ALLOW,       # CRM-style local pipeline
    "replay": ALLOW,         # session trace recordings
    "session": ALLOW,        # session lifecycle ops
    "teams": ALLOW,          # specialist dispatch — twin of orchestration
    # Contact store — local encrypted person directory. find_contact /
    # list_contacts are read-only lookups against the user's own data;
    # save / update mutate that same local store. Without ALLOW the
    # brain hits an ASK prompt every time it tries to resolve a name
    # before whatsapp/instagram/email send — the user sees a popup
    # captioned "find_contact" and reads it as "search permission".
    # Same blast-radius profile as lazybrain / tasks (both already ALLOW).
    "contacts": ALLOW,
    # Read-only diagnostics + user-facing config toggles:
    "system": ALLOW,         # status / about / health
    "ai_management": ALLOW,  # ECO mode toggles — user-facing settings
    # Bug-bounty hunting — sandbox-only by program scope, every external
    # request is gated by the deterministic ScopeChecker + audit-logged.
    # Without ALLOW the orchestrator stalls on every probe; the in-band
    # safety is the scope guard, not the per-call approval prompt.
    "bounty": ALLOW,
    # ── Sensitive (ASK) — KEEP gated ─────────────────────────────────────
    "vault": ASK,            # credentials / API keys
    "computer": ASK,         # shell / subprocess execution
    "security": ASK,         # security operations
    "payment": ASK,          # money — always confirm
    "permissions": ASK,      # meta-op on permissions itself; gating prevents
                             # agent from silently widening its own access
    "audit": ASK,            # lazydoctor finding-review surface; the
                             # underlying user approval is in-band on
                             # Telegram, but gating the skill itself
                             # keeps the brain from spamming approvals
                             # during an unsupervised audit run.
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
