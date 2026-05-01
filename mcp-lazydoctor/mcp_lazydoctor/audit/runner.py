"""Audit runner — fan out producers, dedupe, persist."""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable, Sequence

from mcp_lazydoctor.audit import FindingProducer
from mcp_lazydoctor.config import LazyDoctorConfig
from mcp_lazydoctor.findings import (
    AuditReport,
    Finding,
    FindingStore,
    _now_iso,
    make_audit_id,
    severity_at_or_above,
)

logger = logging.getLogger(__name__)


def _select_producers(
    config: LazyDoctorConfig, scope: str,
) -> tuple[Sequence[FindingProducer], list[str]]:
    """Pick the producer set for a given scope.

    MVP supports ``scope="deps"``. ``scope="all"`` aliases to ``"deps"``
    until Phase 2 adds broken-tool and stale-config producers.
    """
    notes: list[str] = []
    if scope not in ("deps", "all"):
        notes.append(f"unknown scope {scope!r} — falling back to 'deps'")
        scope = "deps"

    # Local import to avoid a cycle: deps.py imports from findings, runner
    # imports deps. Same package — fine — but a top-level import here would
    # break if we later introduce a producer that needs the runner module.
    from mcp_lazydoctor.audit.deps import PipDepProducer, NpxDepProducer

    producers: list[FindingProducer] = [
        PipDepProducer(mcps_root=config.mcps_root),
        NpxDepProducer(mcps_root=config.mcps_root),
    ]
    return producers, notes


def _dedupe(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Last finding with a given id wins (producers are deterministic so
    this only matters when two producers happen to emit the same id)."""
    seen: dict[str, Finding] = {}
    for f in findings:
        seen[f.id] = f
    return tuple(seen.values())


async def run_audit(
    config: LazyDoctorConfig,
    *,
    scope: str = "deps",
    severity_min: str = "medium",
    store: FindingStore | None = None,
) -> AuditReport:
    """Run every enabled producer, persist findings, return an AuditReport.

    Failures inside a single producer are caught and surfaced as
    ``producer_errors`` on the report — one bad producer does not abort
    the audit.
    """
    audit_id = make_audit_id()
    started = _now_iso()
    producers, notes = _select_producers(config, scope)

    coros = [p.produce() for p in producers]
    raw = await asyncio.gather(*coros, return_exceptions=True)

    findings: list[Finding] = []
    errors: list[str] = list(notes)
    for producer, result in zip(producers, raw):
        if isinstance(result, BaseException):
            errors.append(f"{producer.name}: {result!r}")
            logger.warning(
                "lazydoctor producer %s failed: %s", producer.name, result,
            )
            continue
        findings.extend(result)

    deduped = _dedupe(findings)
    filtered = tuple(
        f for f in deduped
        if severity_at_or_above(f.severity, severity_min)
    )

    if store is not None and filtered:
        try:
            store.upsert_many(filtered)
        except Exception as exc:
            errors.append(f"store.upsert_many: {exc!r}")
            logger.exception("FindingStore upsert failed")

    return AuditReport(
        audit_id=audit_id,
        started_at=started,
        finished_at=_now_iso(),
        scope=scope,
        findings=filtered,
        producer_errors=tuple(errors),
    )
