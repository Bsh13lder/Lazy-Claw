"""Per-domain human-input cadence — tunable from natural language.

The numbers in :data:`DEFAULT` are the same hardcoded ranges that lived
inline in :mod:`lazyclaw.browser.human_input` before Phase A. Pulling
them out lets us:

1. Slow down clicks / typing on bot-sensitive domains (Reddit, X,
   Instagram) without touching the action loop.
2. Let the user re-tune via NL ("slow down reddit by 30%") — the skill
   calls :func:`apply_user_tuning` and the next click samples from the
   widened range.

Lookup order in :func:`get_cadence`:

    user override (per-domain factors)  →  DOMAIN_OVERRIDES  →  DEFAULT

Sub-domain match is best-effort: ``old.reddit.com`` resolves to the
``reddit.com`` profile so the user only has to tune the registrable
domain once. We do NOT do real eTLD+1 parsing — overkill for the half-
dozen domains we care about; a suffix check is enough and avoids
pulling in ``tldextract``.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, fields, replace
from typing import Iterable

logger = logging.getLogger(__name__)


# ── Profile shape ────────────────────────────────────────────────────


@dataclass(frozen=True)
class CadenceProfile:
    """Sampling ranges for the four human-input cadence axes.

    All ``*_ms`` fields are ``(min_ms, max_ms)`` tuples passed straight
    to ``random.uniform`` (after dividing by 1000.0 to get seconds).
    ``batch_action_throttle_s`` is the minimum gap between back-to-back
    queued actions when the agent fires a long sequence in one turn.
    """

    click_pause_ms: tuple[int, int]
    type_speed_ms: tuple[int, int]
    word_boundary_ms: tuple[int, int]
    micro_pause_ms: tuple[int, int]
    scroll_step_ms: tuple[int, int]
    post_scroll_dwell_ms: tuple[int, int]
    dwell_after_load_ms: tuple[int, int]
    batch_action_throttle_s: float

    def with_factors(self, factors: dict[str, float]) -> "CadenceProfile":
        """Return a new profile with each named field multiplied by its factor.

        Unknown field names are ignored (logged at debug). Factor must be
        positive; non-positive values fall back to 1.0 to keep the
        sampler well-defined.
        """
        if not factors:
            return self
        updates: dict[str, object] = {}
        valid_field_names = {f.name for f in fields(self)}
        for name, raw_factor in factors.items():
            if name not in valid_field_names:
                logger.debug("cadence.with_factors unknown field %s", name)
                continue
            try:
                factor = float(raw_factor)
            except (TypeError, ValueError):
                continue
            if factor <= 0:
                factor = 1.0
            current = getattr(self, name)
            if isinstance(current, tuple) and len(current) == 2:
                lo, hi = current
                updates[name] = (int(round(lo * factor)), int(round(hi * factor)))
            elif isinstance(current, (int, float)):
                updates[name] = type(current)(current * factor)
        if not updates:
            return self
        return replace(self, **updates)


# ── Defaults ─────────────────────────────────────────────────────────


# These exactly mirror the values that lived inline in human_input.py
# before Phase A — moving them here is a no-op at runtime for any
# domain that isn't in DOMAIN_OVERRIDES or doesn't have a user override.
DEFAULT = CadenceProfile(
    click_pause_ms=(100, 400),
    type_speed_ms=(30, 100),
    word_boundary_ms=(80, 180),
    micro_pause_ms=(150, 350),
    scroll_step_ms=(30, 80),
    post_scroll_dwell_ms=(200, 500),
    dwell_after_load_ms=(800, 2200),
    batch_action_throttle_s=0.6,
)


def _slow(profile: CadenceProfile, factor: float = 1.5) -> CadenceProfile:
    """Helper for DOMAIN_OVERRIDES — slows the four most fingerprint-relevant fields."""
    return profile.with_factors({
        "click_pause_ms": factor,
        "type_speed_ms": factor,
        "word_boundary_ms": factor,
        "dwell_after_load_ms": factor,
        "post_scroll_dwell_ms": factor,
    })


# Bot-sensitive domains where we want a slower default cadence. The user
# can still override these per-domain via apply_user_tuning.
DOMAIN_OVERRIDES: dict[str, CadenceProfile] = {
    "reddit.com":     _slow(DEFAULT, 1.5),
    "x.com":          _slow(DEFAULT, 1.5),
    "twitter.com":    _slow(DEFAULT, 1.5),
    "instagram.com":  _slow(DEFAULT, 1.6),
    "facebook.com":   _slow(DEFAULT, 1.5),
    "linkedin.com":   _slow(DEFAULT, 1.5),
}


# ── Lookup ───────────────────────────────────────────────────────────


def _normalize_domain(domain: str | None) -> str:
    """Lowercase + strip ``www.``. Empty string when missing."""
    if not domain:
        return ""
    canonical = domain.strip().lower()
    if canonical.startswith("www."):
        canonical = canonical[4:]
    return canonical


def _resolve_base_domain(domain: str) -> str | None:
    """Find the ``DOMAIN_OVERRIDES`` key that matches ``domain``, or None.

    Exact match wins; otherwise we walk the dotted suffix
    (``a.b.reddit.com`` → ``b.reddit.com`` → ``reddit.com``) and return
    the first hit.
    """
    if not domain:
        return None
    if domain in DOMAIN_OVERRIDES:
        return domain
    parts = domain.split(".")
    for i in range(1, len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in DOMAIN_OVERRIDES:
            return candidate
    return None


def get_cadence(
    domain: str | None,
    user_overrides: dict[str, dict[str, float]] | None = None,
) -> CadenceProfile:
    """Resolve the effective cadence profile for ``domain``.

    Resolution order:

    1. If ``user_overrides`` has an entry whose key matches ``domain``
       (exact or suffix), apply its factors on top of the base profile.
    2. If ``DOMAIN_OVERRIDES`` has a matching entry, use it as the base.
    3. Otherwise use :data:`DEFAULT`.

    User overrides are factors (e.g. ``{"click_pause_ms": 1.3}``)
    applied to the resolved base, NOT absolute (min, max) tuples — this
    way "slow reddit by 30%" composes cleanly with the bot-sensitive
    base profile already in DOMAIN_OVERRIDES.
    """
    domain_norm = _normalize_domain(domain)
    base_key = _resolve_base_domain(domain_norm)
    base = DOMAIN_OVERRIDES.get(base_key, DEFAULT) if base_key else DEFAULT

    if not user_overrides:
        logger.debug(
            "[cadence] domain=%s base=%s user_override=no",
            domain_norm or "?", base_key or "default",
        )
        return base

    # Exact-then-suffix lookup over the user's keys, normalizing as we go.
    user_keys_norm = {_normalize_domain(k): v for k, v in user_overrides.items()}
    factors: dict[str, float] | None = user_keys_norm.get(domain_norm)
    if factors is None and base_key and base_key in user_keys_norm:
        factors = user_keys_norm[base_key]
    if factors is None:
        # Try walking the suffix in user_overrides too (mirror of base resolution).
        for key in sorted(user_keys_norm.keys(), key=len, reverse=True):
            if key and (domain_norm == key or domain_norm.endswith("." + key)):
                factors = user_keys_norm[key]
                break
    if not factors:
        logger.debug(
            "[cadence] domain=%s base=%s user_override=none-matched",
            domain_norm or "?", base_key or "default",
        )
        return base
    logger.debug(
        "[cadence] domain=%s base=%s user_override=applied fields=%d",
        domain_norm or "?", base_key or "default", len(factors),
    )
    return base.with_factors(factors)


# ── Sampling helpers ─────────────────────────────────────────────────


def sample_ms(rng: tuple[int, int]) -> float:
    """Draw a delay in seconds from a (min_ms, max_ms) tuple."""
    lo, hi = rng
    if hi < lo:
        lo, hi = hi, lo
    return random.uniform(lo, hi) / 1000.0


# ── Persisted user tuning ────────────────────────────────────────────


async def apply_user_tuning(
    config,
    user_id: str,
    domain: str,
    factor: float,
    field_names: Iterable[str] | None = None,
) -> dict[str, dict[str, float]]:
    """Persist a user-level cadence multiplier for ``domain``.

    ``factor=1.3`` with ``field_names=None`` slows every cadence axis
    by 30%. Pass an explicit ``field_names`` iterable to restrict the
    scope (e.g. ``["click_pause_ms", "type_speed_ms"]``).

    Stored under ``users.settings.browser.cadence_overrides`` via
    :func:`browser_settings.set_cadence_override`. Returns the new
    full overrides map (all domains, not just this one) so callers
    can echo back what's now in place.
    """
    from lazyclaw.browser.browser_settings import (
        get_cadence_overrides, set_cadence_override,
    )

    if factor <= 0:
        raise ValueError(f"factor must be positive, got {factor!r}")

    if field_names is None:
        names = [
            "click_pause_ms",
            "type_speed_ms",
            "word_boundary_ms",
            "micro_pause_ms",
            "scroll_step_ms",
            "post_scroll_dwell_ms",
            "dwell_after_load_ms",
        ]
    else:
        valid = {f.name for f in fields(CadenceProfile)}
        names = [n for n in field_names if n in valid]
        if not names:
            raise ValueError("no valid cadence field names in field_names")

    existing = await get_cadence_overrides(config, user_id)
    domain_norm = _normalize_domain(domain)
    if not domain_norm:
        raise ValueError("domain is required")

    domain_factors = dict(existing.get(domain_norm, {}))
    for name in names:
        domain_factors[name] = float(factor)

    return await set_cadence_override(config, user_id, domain_norm, domain_factors)
