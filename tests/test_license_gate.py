"""License gate — hard-fail if a copyleft/SSPL package enters the tree.

LazyClaw is MIT: AGPL/GPL/SSPL dependencies poison the license (CLAUDE.md
"License discipline"). The 2026-08-15 browser-stack research found four
attractive AGPL browser libs (nodriver, zendriver, lightpanda, Skyvern) and
one SSPL (Notte) that are each one careless ``pip install`` away — this
test makes that mistake loud at PR time instead of at relicensing time.

Weak-copyleft MPL and LGPL are allowed (matching the documented policy:
Apache/MIT/BSD/MPL) — the string checks below explicitly avoid matching
"LGPL" when looking for GPL.
"""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import distributions
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Distribution names that are outright banned regardless of what their
# metadata claims (research-verified 2026-08-15).
_BANNED_DISTRIBUTIONS = {
    "nodriver",       # AGPL-3.0
    "zendriver",      # AGPL-3.0 (nodriver fork)
    "skyvern",        # AGPL-3.0
    "notte",          # SSPL v1
    "firecrawl",      # AGPL (see ARCHITECTURE_REVIEW_2026-08-15 §5)
    "firecrawl-py",
    "pymupdf",        # AGPL (documents suite rule)
    "borb",           # AGPL
    "browser-use-core",  # license-less closed binary wheels
    "aioimaplib",     # GPL-3.0 — was declared-but-unused by mcp-email; the
                      # code has always used stdlib imaplib via to_thread
}

# License-string patterns that poison an MIT project. Word-boundary trick:
# a leading letter class exclusion keeps "LGPL" from matching the GPL rule.
_POISON_PATTERNS = [
    re.compile(r"\bAGPL", re.IGNORECASE),
    re.compile(r"GNU Affero", re.IGNORECASE),
    re.compile(r"(?<![LA])GPL[- ]?[23]", re.IGNORECASE),  # GPL-2/GPL-3, not LGPL/AGPL(dup)
    re.compile(r"\bSSPL", re.IGNORECASE),
    re.compile(r"Server Side Public License", re.IGNORECASE),
]


# Pre-existing offenders — KNOWN, decision pending, do NOT add to this list
# without a documented decision.
#
# 2026-08-22: emptied. The sole entry, aioimaplib (GPL-3.0), turned out to be
# declared by mcp-email but never imported — the server has always used stdlib
# imaplib via asyncio.to_thread. Removing the declaration cost nothing and took
# GPL-3.0 out of the shipped Docker image, so it is now simply banned.
_KNOWN_PREEXISTING: set[str] = set()


def _license_strings(dist) -> list[str]:
    meta = dist.metadata
    out = []
    for field in ("License", "License-Expression"):
        val = meta.get(field)
        # Long values are full license TEXTS (scipy bundles component
        # licenses inside this field) — pattern-matching them false-positives
        # on incidental mentions. Short values are SPDX-ish expressions.
        if val and val.lower() != "unknown" and len(val) <= 120:
            out.append(val)
    out.extend(
        c for c in meta.get_all("Classifier", [])
        if c.startswith("License ::")
    )
    return out


def test_no_banned_distributions_installed():
    installed = {
        (d.metadata.get("Name") or "").lower() for d in distributions()
    }
    hits = sorted(installed & _BANNED_DISTRIBUTIONS)
    assert not hits, (
        f"Banned (copyleft/closed) distributions installed: {hits} — "
        f"these poison the MIT license. Remove them and find a "
        f"permissive alternative (see CLAUDE.md License discipline)."
    )


def test_no_banned_distributions_declared_in_any_pyproject():
    """Catch a poisoned dep at PR time, not "whenever someone's venv has it".

    ``test_no_poison_license_metadata`` inspects INSTALLED packages, so it is
    blind on a machine that never installed the offender — and it is the
    *declaration* that ships: the Dockerfile runs ``pip install ./mcp-email``,
    which pulled GPL-3.0 ``aioimaplib`` into every production image even though
    no module ever imported it.
    """
    offenders = []
    for pyproject in sorted(_REPO_ROOT.glob("**/pyproject.toml")):
        parts = set(pyproject.parts)
        if parts & {"node_modules", ".venv", "build", ".git", "site-packages"}:
            continue
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        project = data.get("project") or {}
        declared = list(project.get("dependencies") or [])
        for group in (project.get("optional-dependencies") or {}).values():
            declared.extend(group)
        for spec in declared:
            # "aioimaplib>=1.1.0" / "pkg[extra] == 1.0" → "aioimaplib"
            name = re.split(r"[<>=!~\[\s;]", str(spec), maxsplit=1)[0].strip().lower()
            if name in _BANNED_DISTRIBUTIONS:
                rel = pyproject.relative_to(_REPO_ROOT)
                offenders.append(f"{rel}: {spec}")
    assert not offenders, (
        "Banned (copyleft/closed) packages declared as dependencies:\n  "
        + "\n  ".join(offenders)
        + "\nThese ship in the Docker image and poison the MIT license."
    )


def test_no_poison_license_metadata():
    offenders = []
    for dist in distributions():
        name = (dist.metadata.get("Name") or "?").lower()
        if name in _KNOWN_PREEXISTING:
            continue
        for lic in _license_strings(dist):
            for pat in _POISON_PATTERNS:
                if pat.search(lic):
                    offenders.append(f"{name}: {lic!r}")
                    break
    assert not offenders, (
        "Copyleft/SSPL license metadata found in installed packages:\n  "
        + "\n  ".join(sorted(set(offenders)))
        + "\nThese poison the MIT license — remove or replace them."
    )
