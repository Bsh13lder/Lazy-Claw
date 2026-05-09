"""Browser persistence settings — stored in users.settings JSON.

Three modes:
  - "off"  — browser launches on-demand, dies after task
  - "auto" — browser stays alive after use, closes after idle timeout
  - "on"   — browser always running, admin keeps it on
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

VALID_MODES = {"off", "auto", "on"}
VALID_BACKENDS = {"cdp", "browser_use"}
# Tri-state: "off" keeps agent on the container Brave; "auto" prefers host
# Brave when it's reachable (and silently falls back to container when not);
# "ask" raises a checkpoint-style prompt on every connect when host Brave is
# reachable. ``"ask"`` is reserved for a later UI affordance — today it
# behaves like ``"auto"`` from the backend's perspective.
VALID_USE_HOST_BROWSER = {"off", "auto", "ask"}

DEFAULT_BROWSER = {
    "persistent": "auto",      # "off" | "auto" | "on"
    "idle_timeout": 3600,      # seconds before auto-close (auto mode), 1 hour
    "cdp_approved": False,     # user approved auto-restart Brave with CDP
    "backend": "cdp",          # "cdp" (raw CDP) | "browser_use" (Playwright via browser-use)
    # Host-browser bridge — see lazyclaw/browser/host_bridge.py
    "use_host_browser": "off",       # VALID_USE_HOST_BROWSER
    "host_cdp_token": None,          # Origin-handshake token for --remote-allow-origins
    "host_switch_confirmed": 0,      # counter; >=3 silences per-switch confirmations
    "last_host_cdp_source": None,    # "host" | "local" | None — diagnostic only
    # Auto-close idle tabs once the open-tab count exceeds this cap. Active
    # tab + system tabs (chrome://, devtools://) are always preserved.
    "max_open_tabs": 8,
    # Multi-account browser identities (Phase A — Goal Executor).
    # Maps slug → AccountInfo-shaped dict. Populated by register_browser_account
    # skill; read by profile_resolver consumers via get_account() / list_accounts().
    "accounts": {},
    # Active account per domain — when present, browser actions on that domain
    # use browser_profiles/<user_id>/accounts/<slug>/ instead of the legacy
    # single-profile path. Missing domain = legacy path, back-compat preserved.
    "active_account_by_domain": {},
    # Per-domain cadence tweaks: domain → {field_name: float_factor}.
    # Read by lazyclaw.browser.cadence.get_cadence(). Empty = use defaults.
    "cadence_overrides": {},
}


@dataclass(frozen=True)
class AccountInfo:
    """Browser identity registered to a user.

    Slug is the on-disk profile directory name (validated by
    profile_resolver.validate_slug). friendly_name is what the LLM and the
    user use when referring to the account ("Hirossa main",
    "side-hustle"). primary_domain is the site this identity was
    registered against; the agent uses it to pick the active account when
    no domain hint is given.
    """

    slug: str
    friendly_name: str
    primary_domain: str
    last_used_at: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AccountInfo":
        return cls(
            slug=str(data["slug"]),
            friendly_name=str(data.get("friendly_name") or data["slug"]),
            primary_domain=str(data.get("primary_domain") or ""),
            last_used_at=data.get("last_used_at"),
            created_at=data.get("created_at"),
        )

# In-memory activity tracker (no DB overhead)
_last_browser_activity: float = 0.0


def touch_browser_activity() -> None:
    """Mark browser as recently used. Called by browser skills."""
    global _last_browser_activity
    _last_browser_activity = time.monotonic()


def browser_idle_seconds() -> float:
    """Seconds since last browser activity."""
    if _last_browser_activity == 0.0:
        return float("inf")
    return time.monotonic() - _last_browser_activity


async def get_browser_settings(config: Config, user_id: str) -> dict:
    """Get user's browser persistence settings."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()

    if not result or not result[0]:
        return dict(DEFAULT_BROWSER)

    try:
        settings = json.loads(result[0])
    except (json.JSONDecodeError, TypeError):
        logger.debug("Failed to parse user settings JSON, using browser defaults", exc_info=True)
        return dict(DEFAULT_BROWSER)

    browser = settings.get("browser", {})
    if not isinstance(browser, dict):
        return dict(DEFAULT_BROWSER)

    merged = dict(DEFAULT_BROWSER)
    merged.update(browser)

    # Backwards compat: old boolean persistent → new mode string
    if isinstance(merged["persistent"], bool):
        merged["persistent"] = "on" if merged["persistent"] else "off"

    return merged


async def update_browser_settings(
    config: Config, user_id: str, updates: dict,
) -> dict:
    """Update user's browser settings. Returns the new settings."""
    if "persistent" in updates:
        mode = updates["persistent"]
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid browser mode: {mode}. Must be one of: {VALID_MODES}"
            )
    if "backend" in updates and updates["backend"] not in VALID_BACKENDS:
        raise ValueError(
            f"Invalid backend: {updates['backend']}. Must be one of: {VALID_BACKENDS}"
        )
    if "use_host_browser" in updates and updates["use_host_browser"] not in VALID_USE_HOST_BROWSER:
        raise ValueError(
            f"Invalid use_host_browser: {updates['use_host_browser']}. "
            f"Must be one of: {VALID_USE_HOST_BROWSER}"
        )

    # Load current settings
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()

    current_settings: dict = {}
    if result and result[0]:
        try:
            current_settings = json.loads(result[0])
        except (json.JSONDecodeError, TypeError):
            logger.debug("Failed to parse existing settings JSON during update", exc_info=True)
            current_settings = {}

    # Update browser section
    browser = current_settings.get("browser", dict(DEFAULT_BROWSER))
    if not isinstance(browser, dict):
        browser = dict(DEFAULT_BROWSER)

    for key, value in updates.items():
        if key in DEFAULT_BROWSER:
            browser[key] = value

    # Write back (immutable pattern)
    new_settings = dict(current_settings)
    new_settings["browser"] = browser
    settings_json = json.dumps(new_settings)

    async with db_session(config) as db:
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (settings_json, user_id),
        )
        await db.commit()

    return browser


# ── Multi-account helpers (Phase A — Goal Executor) ──────────────────


def normalize_domain(domain: str) -> str:
    """Canonical lowercase form, ``www.`` stripped.

    Matches the same convention used by ``cadence.get_cadence`` so a
    user's tuning override on ``reddit.com`` applies to ``www.reddit.com``
    too. Returns empty string when input is falsy — callers should treat
    that as "no domain hint, use the legacy single-profile path".
    """
    if not domain:
        return ""
    canonical = domain.strip().lower()
    if canonical.startswith("www."):
        canonical = canonical[4:]
    return canonical


async def register_account(
    config: Config,
    user_id: str,
    slug: str,
    friendly_name: str,
    primary_domain: str,
) -> AccountInfo:
    """Add an AccountInfo to the user's browser settings.

    Idempotent on slug: re-registering the same slug updates
    friendly_name + primary_domain and refreshes ``last_used_at`` —
    callers that care about "is this new" should check
    :func:`get_account` first.
    """
    from lazyclaw.browser.profile_resolver import validate_slug
    validate_slug(slug)
    domain = normalize_domain(primary_domain)
    if not domain:
        raise ValueError("primary_domain is required and must be non-empty")
    if not friendly_name.strip():
        raise ValueError("friendly_name is required and must be non-empty")

    settings = await get_browser_settings(config, user_id)
    accounts = dict(settings.get("accounts") or {})
    now = _utc_now_iso()
    existing = accounts.get(slug)
    info = AccountInfo(
        slug=slug,
        friendly_name=friendly_name.strip(),
        primary_domain=domain,
        last_used_at=now,
        created_at=(existing or {}).get("created_at") or now,
    )
    accounts[slug] = info.to_dict()
    await update_browser_settings(config, user_id, {"accounts": accounts})
    logger.info(
        "browser_settings.register_account user=%s slug=%s domain=%s",
        user_id, slug, domain,
    )
    return info


async def get_account(
    config: Config, user_id: str, slug: str,
) -> AccountInfo | None:
    """Look up an account by slug. Returns ``None`` if missing."""
    settings = await get_browser_settings(config, user_id)
    accounts = settings.get("accounts") or {}
    raw = accounts.get(slug)
    if not raw:
        return None
    try:
        return AccountInfo.from_dict(raw)
    except (KeyError, TypeError, ValueError):
        logger.warning(
            "browser_settings.get_account malformed entry user=%s slug=%s",
            user_id, slug,
        )
        return None


async def list_accounts(
    config: Config, user_id: str, domain: str | None = None,
) -> list[AccountInfo]:
    """List registered accounts, optionally filtered to a single domain."""
    settings = await get_browser_settings(config, user_id)
    accounts = settings.get("accounts") or {}
    domain_norm = normalize_domain(domain) if domain else ""
    result: list[AccountInfo] = []
    for raw in accounts.values():
        try:
            info = AccountInfo.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            continue
        if domain_norm and info.primary_domain != domain_norm:
            continue
        result.append(info)
    result.sort(key=lambda a: a.slug)
    return result


async def set_active_account(
    config: Config, user_id: str, domain: str, slug: str | None,
) -> dict[str, str]:
    """Pin a domain to a slug, or unpin when ``slug`` is ``None``.

    Returns the updated ``active_account_by_domain`` mapping. When
    ``slug`` is provided and missing from ``accounts`` the call raises —
    avoids silently persisting a pointer to nothing.
    """
    domain_norm = normalize_domain(domain)
    if not domain_norm:
        raise ValueError("domain is required")

    settings = await get_browser_settings(config, user_id)
    active = dict(settings.get("active_account_by_domain") or {})

    if slug is None:
        active.pop(domain_norm, None)
    else:
        accounts = settings.get("accounts") or {}
        if slug not in accounts:
            raise ValueError(
                f"unknown account slug {slug!r} — register it first"
            )
        active[domain_norm] = slug
        # Touch last_used_at on the chosen slug so the next list_accounts
        # call surfaces it as the most recently used.
        accounts_mut = dict(accounts)
        entry = dict(accounts_mut[slug])
        entry["last_used_at"] = _utc_now_iso()
        accounts_mut[slug] = entry
        await update_browser_settings(
            config, user_id, {"accounts": accounts_mut, "active_account_by_domain": active},
        )
        return active

    await update_browser_settings(
        config, user_id, {"active_account_by_domain": active},
    )
    return active


async def get_active_account_slug(
    config: Config, user_id: str, domain: str,
) -> str | None:
    """Return the active slug for ``domain`` or ``None`` if unpinned."""
    domain_norm = normalize_domain(domain)
    if not domain_norm:
        return None
    settings = await get_browser_settings(config, user_id)
    active = settings.get("active_account_by_domain") or {}
    slug = active.get(domain_norm)
    return slug if isinstance(slug, str) and slug else None


# ── Cadence override helpers ─────────────────────────────────────────


async def get_cadence_overrides(
    config: Config, user_id: str,
) -> dict[str, dict[str, float]]:
    """Return per-domain cadence overrides (deep-copied)."""
    settings = await get_browser_settings(config, user_id)
    raw = settings.get("cadence_overrides") or {}
    result: dict[str, dict[str, float]] = {}
    for domain, overrides in raw.items():
        if not isinstance(overrides, dict):
            continue
        domain_norm = normalize_domain(domain)
        if not domain_norm:
            continue
        result[domain_norm] = {
            str(k): float(v)
            for k, v in overrides.items()
            if isinstance(v, (int, float))
        }
    return result


async def set_cadence_override(
    config: Config,
    user_id: str,
    domain: str,
    factors: dict[str, float],
) -> dict[str, dict[str, float]]:
    """Replace the override map for ``domain`` with ``factors``.

    Each factor is a multiplier on the default cadence range for that
    field (e.g. ``{"click_pause_ms": 1.3}`` slows clicks 30%). Empty
    ``factors`` clears the domain entry.
    """
    domain_norm = normalize_domain(domain)
    if not domain_norm:
        raise ValueError("domain is required")

    overrides = await get_cadence_overrides(config, user_id)
    if factors:
        overrides[domain_norm] = {
            str(k): float(v) for k, v in factors.items()
            if isinstance(v, (int, float))
        }
    else:
        overrides.pop(domain_norm, None)

    await update_browser_settings(
        config, user_id, {"cadence_overrides": overrides},
    )
    return overrides


def _utc_now_iso() -> str:
    """Stable UTC ISO timestamp for serialized fields."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
