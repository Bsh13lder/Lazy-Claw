"""tune_browser_cadence — slow down or speed up browser actions per domain.

Wraps :func:`lazyclaw.browser.cadence.apply_user_tuning`. Persists a
multiplicative factor against the resolved domain profile (DEFAULT or a
DOMAIN_OVERRIDES baseline). The next click / type / scroll on that
domain samples from the widened range automatically.
"""

from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


_VALID_FIELDS = (
    "click_pause_ms",
    "type_speed_ms",
    "word_boundary_ms",
    "micro_pause_ms",
    "scroll_step_ms",
    "post_scroll_dwell_ms",
    "dwell_after_load_ms",
)


class TuneBrowserCadenceSkill(BaseSkill):
    """Adjust per-domain timing of clicks/typing/scrolls."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "tune_browser_cadence"

    @property
    def display_name(self) -> str:
        return "Tune Browser Cadence"

    @property
    def description(self) -> str:
        return (
            "Slow down or speed up browser-action timing on a domain. "
            "factor>1 slows; factor<1 speeds. Optional `fields` restricts "
            "scope ('click_pause_ms', 'type_speed_ms', 'dwell_after_load_ms', …)."
            " Without fields, every cadence axis is multiplied uniformly. "
            "Persisted; takes effect on next browser action."
        )

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Domain to tune (e.g. 'reddit.com', 'instagram.com').",
                },
                "factor": {
                    "type": "number",
                    "description": (
                        "Multiplier. 1.3 = 30% slower; 0.8 = 20% faster. Must be > 0."
                    ),
                },
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": list(_VALID_FIELDS),
                    },
                    "description": (
                        "Optional. Restrict tuning to specific cadence axes. "
                        "Omit to apply across the board."
                    ),
                },
            },
            "required": ["domain", "factor"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        domain = (params.get("domain") or "").strip().lower()
        if not domain:
            return "Missing required field: domain."
        try:
            factor = float(params.get("factor"))
        except (TypeError, ValueError):
            return "Missing/invalid field: factor (must be a positive number)."
        if factor <= 0:
            return "factor must be > 0."

        fields = params.get("fields") or None
        if fields is not None and not isinstance(fields, list):
            return "fields must be a list of cadence-field names."

        from lazyclaw.browser.cadence import apply_user_tuning

        try:
            overrides = await apply_user_tuning(
                self._config, user_id, domain, factor,
                field_names=fields,
            )
        except ValueError as exc:
            return f"Cannot tune: {exc}"

        applied = overrides.get(_normalize(domain), {})
        return (
            f"Cadence factor for `{_normalize(domain)}` set: "
            + ", ".join(f"{k}×{v}" for k, v in applied.items())
            + "\nTakes effect on the next browser action."
        )


def _normalize(domain: str) -> str:
    s = domain.strip().lower()
    if s.startswith("www."):
        s = s[4:]
    return s
