"""Compose recurring low-stock alert checks.

The industrial use case: "watch my stock and tell me when something runs low."
There's no new daemon machinery needed — a low-stock check is just a recurring
cron job whose instruction tells the agent to (1) read current stock via a
recorded panel endpoint (`panel_call`) or a read-only db-toolbox query, (2)
compare against a threshold, and (3) alert the user *only* when something is
low. This module builds that instruction and provides a deterministic threshold
evaluator so the "is it low?" logic is testable and unambiguous.

Restock is deliberately gated: an alert never places an order on its own. Any
reorder goes through `panel_call` with explicit user approval (confirm=true).
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Any, Callable

# Comparison operators the threshold check understands.
_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
}


@dataclass(frozen=True)
class ThresholdResult:
    tripped: bool
    breaching: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""


def evaluate_threshold(
    rows: list[dict[str, Any]],
    field_name: str,
    op: str,
    threshold: float,
) -> ThresholdResult:
    """Return which rows breach ``row[field] <op> threshold``.

    Rows missing the field or holding a non-numeric value are skipped (not
    breaching) — a malformed row must never trigger a false alert. Raises on an
    unknown operator so a typo fails loudly at setup, not silently at runtime.
    """
    compare = _OPS.get(op)
    if compare is None:
        raise ValueError(f"unknown operator {op!r}; use one of {sorted(_OPS)}")

    breaching: list[dict[str, Any]] = []
    for row in rows:
        if field_name not in row:
            continue
        value = row[field_name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if compare(value, threshold):
            breaching.append(row)

    if breaching:
        summary = (
            f"{len(breaching)} item(s) with {field_name} {op} {threshold}"
        )
    else:
        summary = f"all items have {field_name} not {op} {threshold}"
    return ThresholdResult(tripped=bool(breaching), breaching=breaching, summary=summary)


def build_stock_watch_instruction(
    *,
    how_to_check: str,
    low_condition: str,
    restock: str | None = None,
) -> str:
    """Compose the recurring instruction the brain runs each cron tick.

    ``how_to_check`` — the concrete read to run (e.g. "call panel_call with
    site='shop', name='low_stock'" or "run the db-toolbox 'low_stock' query").
    ``low_condition`` — what counts as low (e.g. "quantity below 5").
    ``restock`` — optional reorder path; used ONLY with the user's approval.
    """
    lines = [
        "Low-stock check. Do this quietly and only speak up if action is needed:",
        f"1. Read current stock: {how_to_check}.",
        f"2. A row is LOW when: {low_condition}.",
        "3. If NO rows are low, do nothing and stay silent — do not message the user.",
        "4. If one or more rows are low, send the user ONE alert listing each low "
        "item (name/SKU + current level).",
    ]
    if restock:
        lines.append(
            "5. Restock is allowed ONLY with the user's explicit approval: "
            f"{restock}. Propose it in the alert and wait for a yes; when "
            "approved, place it via panel_call with confirm=true. Never reorder "
            "automatically."
        )
    else:
        lines.append(
            "5. Do NOT place any order — this is alert-only. If the user wants a "
            "reorder, they will ask."
        )
    return "\n".join(lines)


async def create_stock_watch_job(
    config: Any,
    user_id: str,
    *,
    name: str,
    cron_expression: str,
    how_to_check: str,
    low_condition: str,
    restock: str | None = None,
) -> str:
    """Schedule the recurring low-stock check. Returns the job id."""
    from lazyclaw.heartbeat.orchestrator import create_job

    instruction = build_stock_watch_instruction(
        how_to_check=how_to_check,
        low_condition=low_condition,
        restock=restock,
    )
    return await create_job(
        config,
        user_id,
        name=name,
        instruction=instruction,
        job_type="cron",
        cron_expression=cron_expression,
    )
