"""Audit subsystem — finding producers + the runner that fans them out.

A producer is anything with an ``async produce()`` method that returns a
list of Findings. The runner calls every enabled producer in parallel and
hands the merged list to the FindingStore.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from mcp_lazydoctor.findings import Finding


@runtime_checkable
class FindingProducer(Protocol):
    """Producers must be cheap to instantiate and idempotent — running an
    audit twice in the same minute should yield the same finding ids so
    the FindingStore's upsert dedupes them."""

    name: str

    async def produce(self) -> list[Finding]: ...
