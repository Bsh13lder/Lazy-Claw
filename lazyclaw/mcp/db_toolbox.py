"""Config generation for the bundled Google MCP Toolbox for Databases.

The `toolbox` binary (Apache-2.0, github.com/googleapis/mcp-toolbox) reads a
`tools.yaml` describing database `sources` and named `tools` (parameterized
SQL). We generate that file per-user so the agent can run *pre-defined, safe*
queries against a business database for monitoring and alerting.

SECURITY POSTURE — defense in depth:
  1. Every generated tool's SQL is validated read-only here (SELECT/WITH/… only;
     any write verb or multi-statement is rejected).
  2. The DB credential in the source SHOULD be a read-only role — this module
     defaults ``read_only=True`` and callers are told to use a read-only DSN.
Writes to a business system go through the app / apihunter `panel_call`, never
raw SQL from the agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# A tool statement must START with one of these (first meaningful keyword).
_READ_ONLY_HEADS = frozenset({"select", "with", "show", "explain", "pragma"})

# Tokens that make a statement a write / DDL / side-effect — rejected anywhere.
_WRITE_TOKENS = frozenset({
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "replace", "merge", "grant", "revoke", "call", "exec", "execute", "into",
    "copy", "vacuum", "attach", "detach", "reindex", "lock", "commit",
    "rollback", "set", "load", "handler",
})

# source kind -> the tool `kind` the toolbox expects (e.g. postgres -> postgres-sql).
_SQL_TOOL_KIND = {
    "postgres": "postgres-sql",
    "postgresql": "postgres-sql",
    "alloydb-postgres": "alloydb-postgres-sql",
    "cloud-sql-postgres": "cloud-sql-postgres-sql",
    "mysql": "mysql-sql",
    "cloud-sql-mysql": "cloud-sql-mysql-sql",
    "sqlite": "sqlite-sql",
    "mssql": "mssql-sql",
    "cloud-sql-mssql": "cloud-sql-mssql-sql",
    "bigquery": "bigquery-sql",
    "spanner": "spanner-sql",
}

_COMMENT_RE = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)
_WORD_RE = re.compile(r"[a-zA-Z_][a-zA-Z_0-9]*")


class UnsafeStatement(ValueError):
    """Raised when a tool statement is not provably read-only."""


def is_read_only_sql(statement: str) -> bool:
    """True only if ``statement`` is a single, read-only SQL statement.

    Conservative by design — a full parser is overkill; this rejects anything
    that isn't obviously a read. It is one layer; a read-only DB role is the
    other.
    """
    if not statement or not statement.strip():
        return False
    cleaned = _COMMENT_RE.sub(" ", statement).strip().rstrip(";").strip()
    if not cleaned:
        return False
    if ";" in cleaned:
        return False  # multiple statements
    words = [w.lower() for w in _WORD_RE.findall(cleaned)]
    if not words or words[0] not in _READ_ONLY_HEADS:
        return False
    return not any(w in _WRITE_TOKENS for w in words)


@dataclass(frozen=True)
class DbSource:
    """A database connection the toolbox can query."""

    name: str
    kind: str
    config: dict[str, Any] = field(default_factory=dict)

    def to_yaml_obj(self) -> dict[str, Any]:
        return {"kind": self.kind, **self.config}


@dataclass(frozen=True)
class DbTool:
    """A named, parameterized, read-only query exposed as an MCP tool."""

    name: str
    source: str
    statement: str
    description: str = ""
    parameters: list[dict[str, Any]] = field(default_factory=list)

    def to_yaml_obj(self, source_kind: str) -> dict[str, Any]:
        kind = _SQL_TOOL_KIND.get(source_kind, f"{source_kind}-sql")
        obj: dict[str, Any] = {
            "kind": kind,
            "source": self.source,
            "description": self.description,
            "statement": self.statement,
        }
        if self.parameters:
            obj["parameters"] = self.parameters
        return obj


def generate_tools_yaml(
    sources: list[DbSource],
    tools: list[DbTool],
    *,
    read_only: bool = True,
) -> str:
    """Render a toolbox ``tools.yaml`` string from sources + tools.

    Validates every tool statement is read-only when ``read_only`` (the
    default). Raises :class:`UnsafeStatement` on the first violation.
    """
    source_kinds = {s.name: s.kind for s in sources}
    for tool in tools:
        if tool.source not in source_kinds:
            raise ValueError(
                f"tool '{tool.name}' references unknown source '{tool.source}'"
            )
        if read_only and not is_read_only_sql(tool.statement):
            raise UnsafeStatement(
                f"tool '{tool.name}' statement is not read-only — refusing to "
                f"generate it: {tool.statement!r}"
            )
    doc = {
        "sources": {s.name: s.to_yaml_obj() for s in sources},
        "tools": {t.name: t.to_yaml_obj(source_kinds[t.source]) for t in tools},
    }
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def default_tools_yaml() -> str:
    """A valid but empty config — lets the MCP start before any DB is set up."""
    return yaml.safe_dump({"sources": {}, "tools": {}}, sort_keys=False)


def resolve_tools_file(profile_dir: Path) -> Path:
    """Per-user tools.yaml path under the user's private profile dir."""
    return Path(profile_dir) / "db-toolbox" / "tools.yaml"


def write_tools_file(path: Path, content: str) -> None:
    """Atomically write the tools.yaml (write-then-rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def ensure_tools_file(profile_dir: Path) -> Path:
    """Ensure a per-user tools.yaml exists (empty default if absent). Returns it."""
    path = resolve_tools_file(profile_dir)
    if not path.is_file():
        write_tools_file(path, default_tools_yaml())
    return path


def configure_db_toolbox(
    profile_dir: Path,
    sources: list[DbSource],
    tools: list[DbTool],
    *,
    read_only: bool = True,
) -> Path:
    """Generate + write a user's tools.yaml from their DB config. Returns path."""
    content = generate_tools_yaml(sources, tools, read_only=read_only)
    path = resolve_tools_file(profile_dir)
    write_tools_file(path, content)
    return path
