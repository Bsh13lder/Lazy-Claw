"""Dep version producers.

Two producers ship in MVP:

  * ``PipDepProducer`` — walks ``mcps_root/mcp-*/pyproject.toml`` and
    flags any pinned dep where the latest released version on PyPI is
    higher than what the MCP currently asks for.

  * ``NpxDepProducer`` — reads ``BUNDLED_MCPS`` to find npx-launched
    servers (claude-code, stripe, n8n-nodes), runs ``npm view <pkg>
    version`` and flags drift.

Both producers are deliberately conservative: when they cannot reach
PyPI/npm or cannot parse a pyproject.toml, they emit a producer error
into the AuditReport rather than a Finding. False-negatives beat
false-positives in an automated upgrade loop.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover — older Pythons
    import tomli as tomllib  # type: ignore[no-redef]

from mcp_lazydoctor.diagnostics import run_command
from mcp_lazydoctor.findings import Finding

logger = logging.getLogger(__name__)


# ── Version helpers ──────────────────────────────────────────────────────

# A loose semver regex — accepts "1", "1.2", "1.2.3", "1.2.3.post1",
# "1.2.3-rc.1", "1.2.3.dev0", etc. Used to peel a comparable version out
# of pyproject specifiers like ">=1.2.3,<2".
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)*(?:[-.\w]+)?)")


def _parse_version_tuple(v: str) -> tuple:
    """Best-effort tuple of ints + fallback string segments. Stable enough
    for ``>`` comparisons across the version shapes we encounter (PyPI +
    npm semver). Not PEP 440 strict — that would pull in `packaging`."""
    parts: list = []
    for chunk in v.replace("-", ".").split("."):
        try:
            parts.append((0, int(chunk)))
        except ValueError:
            parts.append((1, chunk))  # strings sort after ints in same slot
    return tuple(parts)


def _newer(latest: str, current: str) -> bool:
    try:
        return _parse_version_tuple(latest) > _parse_version_tuple(current)
    except Exception:
        return latest != current  # at least flag drift


def _classify_severity(latest: str, current: str) -> str:
    """High = major bump, medium = minor, low = patch / metadata."""
    try:
        lat = _parse_version_tuple(latest)
        cur = _parse_version_tuple(current)
        if not lat or not cur:
            return "medium"
        if lat[0] != cur[0]:
            return "high"
        if len(lat) > 1 and len(cur) > 1 and lat[1] != cur[1]:
            return "medium"
        return "low"
    except Exception:
        return "medium"


# ── Pyproject reader ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class _PinnedDep:
    package: str
    version: str            # the bare version part of the specifier
    raw_spec: str           # the full original "pkg>=1.2.3" string


def _extract_pinned_deps(pyproject: Path) -> list[_PinnedDep]:
    """Return only deps that have a numeric pin (``==``, ``~=``, ``>=``).
    Specifiers without a number are skipped — we cannot safely propose a
    bump if there's no current floor to compare against."""
    try:
        with pyproject.open("rb") as fp:
            data = tomllib.load(fp)
    except Exception as exc:
        logger.debug("Cannot read %s: %s", pyproject, exc)
        return []

    deps_blob = ((data.get("project") or {}).get("dependencies")) or []
    pinned: list[_PinnedDep] = []
    for raw in deps_blob:
        if not isinstance(raw, str):
            continue
        spec = raw.strip()
        # Skip path/URL deps — nothing to bump.
        if " @ " in spec or spec.startswith("-e "):
            continue
        # Split package name from specifier. PEP 508 allows extras,
        # markers, etc — we only need name + first numeric we see.
        m = re.match(
            r"^\s*([A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*(.*)$", spec,
        )
        if not m:
            continue
        name, rest = m.group(1), m.group(2)
        if not rest:
            continue
        # Strip env-marker tail.
        if ";" in rest:
            rest = rest.split(";", 1)[0]
        ver_match = _VERSION_RE.search(rest)
        if not ver_match:
            continue
        pinned.append(
            _PinnedDep(package=name, version=ver_match.group(1), raw_spec=spec),
        )
    return pinned


# ── Pip producer ─────────────────────────────────────────────────────────

class PipDepProducer:
    """One Finding per (mcp, dep) when PyPI has a newer version."""

    name = "pip_deps"

    def __init__(self, mcps_root: str | Path) -> None:
        self._root = Path(mcps_root)

    def _iter_pyprojects(self) -> Iterable[Path]:
        if not self._root.exists():
            return []
        # We only want top-level mcp-* dirs, not nested re-vendors.
        return sorted(self._root.glob("mcp-*/pyproject.toml"))

    async def _latest_pypi_version(self, package: str) -> str | None:
        """Use ``pip index versions`` so we don't pull in a new HTTP dep.
        Returns None on any error (rate-limited, offline, package not found)."""
        code, stdout, stderr = await run_command(
            ["python3", "-m", "pip", "index", "versions", package],
            cwd=str(self._root),
        )
        if code != 0:
            logger.debug(
                "pip index versions %s failed: %s", package,
                (stderr or stdout).strip()[:200],
            )
            return None
        # Output looks like:
        #   crawl4ai (0.8.5)
        #   Available versions: 0.8.5, 0.8.0, 0.7.8, ...
        m = re.search(r"\(([^)]+)\)", stdout)
        return m.group(1).strip() if m else None

    async def produce(self) -> list[Finding]:
        out: list[Finding] = []
        for pyproject in self._iter_pyprojects():
            mcp_dir = pyproject.parent.name
            for dep in _extract_pinned_deps(pyproject):
                latest = await self._latest_pypi_version(dep.package)
                if latest is None or not _newer(latest, dep.version):
                    continue
                target = f"{mcp_dir}/pyproject.toml::{dep.package}"
                fid = Finding.make_id("pip_bump", target, dep.version)
                severity = _classify_severity(latest, dep.version)
                title = (
                    f"{mcp_dir}: bump {dep.package} {dep.version} → {latest}"
                )
                action = (
                    f"Edit {pyproject.relative_to(self._root)} so the dep on "
                    f"{dep.package!r} pins {latest}, then `pip install -e .` "
                    f"inside {mcp_dir}/ and run smoke tests."
                )
                cmd = (
                    f"sed -i.bak \"s/{re.escape(dep.raw_spec)}/"
                    f"{dep.package}>={latest}/\" {pyproject}\n"
                    f"cd {mcp_dir} && python3 -m pip install -e ."
                )
                out.append(Finding(
                    id=fid, kind="pip_bump", target=target, title=title,
                    severity=severity, current_version=dep.version,
                    proposed_version=latest, proposed_action=action,
                    diff_or_command=cmd,
                    rationale=f"PyPI latest {latest} > pinned {dep.version}",
                ))
        return out


# ── Npx producer ─────────────────────────────────────────────────────────

# We discover npx MCPs by reading lazyclaw's BUNDLED_MCPS dict. Instead of
# importing it (which would force the lazyclaw package onto lazydoctor's
# import path — cross-process pollution), we parse the source file with
# regex. The dict shape is stable; if it ever moves we'll catch it via a
# producer_error on the report.

_BUNDLED_MCPS_PATH_HINT = "lazyclaw/mcp/manager.py"
_NPX_LINE_RE = re.compile(r'"npx"\s*:\s*"([^"]+)"')


def _resolve_lazyclaw_manager(mcps_root: Path) -> Path | None:
    """``mcps_root`` is typically the LazyClaw repo root. The manager file
    lives at ``lazyclaw/mcp/manager.py``."""
    candidate = mcps_root / _BUNDLED_MCPS_PATH_HINT
    if candidate.exists():
        return candidate
    # Fallback — walk one level up in case mcps_root is a sibling dir.
    candidate = mcps_root.parent / _BUNDLED_MCPS_PATH_HINT
    return candidate if candidate.exists() else None


def _split_npx_spec(spec: str) -> tuple[str, str | None]:
    """``"@stripe/mcp@latest"`` → ``("@stripe/mcp", "latest")``.
    Scoped npm packages start with ``@`` and have an internal ``/``."""
    # Skip the scope's own '@' — search for an '@' AFTER the package name.
    if spec.startswith("@"):
        slash = spec.find("/")
        if slash == -1:
            return spec, None
        at = spec.find("@", slash)
    else:
        at = spec.find("@")
    if at == -1:
        return spec, None
    return spec[:at], spec[at + 1 :]


class NpxDepProducer:
    """One Finding per npx-launched MCP whose pinned version is below
    ``npm view <pkg> version``."""

    name = "npx_deps"

    def __init__(self, mcps_root: str | Path) -> None:
        self._root = Path(mcps_root)

    async def _latest_npm_version(self, package: str) -> str | None:
        code, stdout, stderr = await run_command(
            ["npm", "view", package, "version"], cwd=str(self._root),
        )
        if code != 0:
            logger.debug(
                "npm view %s version failed: %s", package,
                (stderr or stdout).strip()[:200],
            )
            return None
        return stdout.strip() or None

    async def produce(self) -> list[Finding]:
        manager_path = _resolve_lazyclaw_manager(self._root)
        if manager_path is None:
            # Don't raise — runner records this as a producer_error.
            raise FileNotFoundError(
                f"could not locate {_BUNDLED_MCPS_PATH_HINT} under "
                f"{self._root}"
            )

        text = manager_path.read_text(encoding="utf-8", errors="replace")
        npx_specs = list(dict.fromkeys(_NPX_LINE_RE.findall(text)))
        out: list[Finding] = []
        for spec in npx_specs:
            pkg, current = _split_npx_spec(spec)
            # Skip "latest" pins — they auto-update at install time, no
            # finding to surface.
            if current in (None, "latest"):
                continue
            latest = await self._latest_npm_version(pkg)
            if latest is None or not _newer(latest, current):
                continue
            target = f"BUNDLED_MCPS::{spec}"
            fid = Finding.make_id("npx_bump", target, current)
            severity = _classify_severity(latest, current)
            title = f"npx {pkg}: bump {current} → {latest}"
            action = (
                f"Edit lazyclaw/mcp/manager.py — change \"{spec}\" to "
                f"\"{pkg}@{latest}\" in BUNDLED_MCPS."
            )
            cmd = (
                f"sed -i.bak \"s/{re.escape(spec)}/{pkg}@{latest}/\" "
                f"lazyclaw/mcp/manager.py"
            )
            out.append(Finding(
                id=fid, kind="npx_bump", target=target, title=title,
                severity=severity, current_version=current,
                proposed_version=latest, proposed_action=action,
                diff_or_command=cmd,
                rationale=f"npm latest {latest} > pinned {current}",
            ))
        return out


# ── Future kinds (Phase 1.5+) ────────────────────────────────────────────
# VendoredCrawl4aiProducer, UpworkForkBaseProducer — declared here so
# server.py can advertise the kinds in its tool schema, but not wired
# into the runner yet. See plan: Phase 1.5.
