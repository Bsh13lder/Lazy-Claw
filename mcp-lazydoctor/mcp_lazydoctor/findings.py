"""Finding + AuditReport data model + SQLite-backed pending-finding store.

Lazydoctor runs in its own process and cannot reach lazyclaw's main DB. Its
audit results live here in a self-contained sqlite file so:
  - lazydoctor stays process-isolated (no cross-process schema coupling)
  - rolling back a runaway audit is `rm -rf .lazydoctor/`
  - findings persist across server restarts so a Telegram approval that
    arrives an hour after the audit ran can still resolve to a real row
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

logger = logging.getLogger(__name__)


Severity = Literal["low", "medium", "high"]
FindingKind = Literal[
    "pip_bump", "npx_bump",
    # Phase 1.5 / Phase 2 kinds — declared up front so SQLite columns are
    # forward-compatible. Producers for these don't ship in MVP.
    "vendored_bump", "fork_base_bump",
    "broken_tool", "stale_memory_ref", "stale_skill_default",
]
FindingStatus = Literal[
    "pending", "approved", "rejected", "applied", "failed", "needs_human",
]

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


def severity_at_or_above(severity: str, threshold: str) -> bool:
    return _SEVERITY_RANK.get(severity, -1) >= _SEVERITY_RANK.get(threshold, 99)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Finding:
    """A single audit finding. Immutable — apply produces a new row,
    state changes happen in the store, not on the dataclass."""

    id: str
    kind: FindingKind
    target: str
    title: str
    severity: Severity
    proposed_action: str
    diff_or_command: str
    current_version: str | None = None
    proposed_version: str | None = None
    rationale: str = ""
    created_at: str = field(default_factory=_now_iso)

    @staticmethod
    def make_id(kind: str, target: str, current_version: str | None) -> str:
        """Stable id across reruns — same finding never produces two rows."""
        h = hashlib.sha256()
        h.update(kind.encode())
        h.update(b"\x00")
        h.update(target.encode())
        h.update(b"\x00")
        h.update((current_version or "").encode())
        return h.hexdigest()[:12]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AuditReport:
    audit_id: str
    started_at: str
    finished_at: str
    scope: str
    findings: tuple[Finding, ...] = ()
    producer_errors: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "audit_id": self.audit_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "scope": self.scope,
            "findings": [f.to_dict() for f in self.findings],
            "producer_errors": list(self.producer_errors),
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS lazydoctor_findings (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  target TEXT NOT NULL,
  title TEXT NOT NULL,
  severity TEXT NOT NULL,
  current_version TEXT,
  proposed_version TEXT,
  proposed_action TEXT NOT NULL,
  diff_or_command TEXT,
  rationale TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  applied_commit TEXT,
  decided_at TEXT,
  decided_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_status
  ON lazydoctor_findings(status, created_at);
"""


class FindingStore:
    """Synchronous sqlite store. Lazydoctor's tools are async, but each tool
    call is short-lived enough that the GIL + sqlite WAL are fine here. Avoids
    pulling in aiosqlite as a new dep just for this MCP."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            logger.debug("FindingStore close failed", exc_info=True)

    def upsert_many(self, findings: Iterable[Finding]) -> int:
        """Insert findings; if id collides keep the existing row's status
        but refresh title/severity/proposed_version so an audit's view of
        'what's currently outdated' is up-to-date even if a finding was
        previously rejected (still pending re-review next cycle)."""
        rows = list(findings)
        if not rows:
            return 0
        now = _now_iso()
        with self._conn:
            for f in rows:
                existing = self._conn.execute(
                    "SELECT status FROM lazydoctor_findings WHERE id = ?",
                    (f.id,),
                ).fetchone()
                if existing is None:
                    self._conn.execute(
                        "INSERT INTO lazydoctor_findings "
                        "(id, kind, target, title, severity, current_version, "
                        "proposed_version, proposed_action, diff_or_command, "
                        "rationale, status, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                        (
                            f.id, f.kind, f.target, f.title, f.severity,
                            f.current_version, f.proposed_version,
                            f.proposed_action, f.diff_or_command, f.rationale,
                            f.created_at, now,
                        ),
                    )
                else:
                    # Refresh mutable fields, leave status alone so a previous
                    # 'applied'/'rejected' decision sticks.
                    self._conn.execute(
                        "UPDATE lazydoctor_findings SET "
                        "title = ?, severity = ?, proposed_version = ?, "
                        "proposed_action = ?, diff_or_command = ?, "
                        "rationale = ?, updated_at = ? WHERE id = ?",
                        (
                            f.title, f.severity, f.proposed_version,
                            f.proposed_action, f.diff_or_command, f.rationale,
                            now, f.id,
                        ),
                    )
        return len(rows)

    def get(self, finding_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM lazydoctor_findings WHERE id = ?", (finding_id,),
        ).fetchone()
        return dict(row) if row else None

    def list(
        self, status: FindingStatus | None = None, limit: int = 50,
    ) -> list[dict]:
        if status is not None:
            cur = self._conn.execute(
                "SELECT * FROM lazydoctor_findings WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM lazydoctor_findings "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in cur.fetchall()]

    def update_status(
        self,
        finding_id: str,
        status: FindingStatus,
        reason: str | None = None,
        applied_commit: str | None = None,
    ) -> bool:
        now = _now_iso()
        with self._conn:
            cur = self._conn.execute(
                "UPDATE lazydoctor_findings SET status = ?, decided_at = ?, "
                "decided_reason = COALESCE(?, decided_reason), "
                "applied_commit = COALESCE(?, applied_commit), "
                "updated_at = ? WHERE id = ?",
                (status, now, reason, applied_commit, now, finding_id),
            )
            return cur.rowcount > 0


def make_audit_id() -> str:
    """Date-prefixed for human readability + nanosecond suffix for uniqueness."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
