"""Adaptive selectors that survive DOM redesigns.

The core insight Scrapling teaches us: a CSS selector like ``.product-price``
is a fragile pointer. The day the site renames it to ``.priceTag`` your
extractor returns ``None`` — silently. You don't know it's broken until
a user complains. For LazyClaw this is invisible breakage in survival /
business-research / price-watch flows that revisit the same site.

The fix: store an **element fingerprint** (sha256 of tag + sorted attrs +
normalized text snippet) keyed by (domain, selector_id). Next visit:

  1. Try the saved CSS path. If it returns an element with the same
     fingerprint → done. Cheap and exact.
  2. If the saved path misses or returns a wrong-fingerprint element
     (DOM redesign), walk the entire DOM and score every element against
     the saved fingerprint by Jaccard(attrs) + text_similarity. Pick
     the best match above threshold.
  3. **Update the stored selector** with the new CSS path so future
     calls hit the fast path again.
  4. If no match ≥ threshold, return ``None`` AND emit a structured
     warning. Today we silently lose data — this surfaces breakage.

Storage is a single-file SQLite database at ``~/.lazyclaw/scraper_selectors.db``
(WAL mode, atomic writes, thread-safe). Schema is one table, ~10 cols,
PRIMARY KEY (domain, selector_id).

Approach inspired by Scrapling's adaptive selector engine (BSD-3) but
implemented independently — fingerprint-based element relocation is a
standard pattern not unique to Scrapling. Uses lxml (already transitive
via crawl4ai) and stdlib sqlite3. Zero new dependencies.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Similarity threshold above which we accept a relocation match. Below
# this we return None and log a warning so the breakage is visible.
# 0.7 is a strong match — usually the same element with a renamed class
# or a small attribute tweak. Tune higher for stricter, lower for looser.
_RELOCATION_THRESHOLD = 0.7

# Weight balance between attribute Jaccard similarity and text similarity.
# Attributes are usually more stable than rendered text (text changes when
# data updates; attrs change on DOM redesigns). Bias toward attrs.
_ATTR_WEIGHT = 0.7
_TEXT_WEIGHT = 0.3


# ── Storage ────────────────────────────────────────────────────────────

_DEFAULT_DB_PATH = Path.home() / ".lazyclaw" / "scraper_selectors.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS selectors (
    domain TEXT NOT NULL,
    selector_id TEXT NOT NULL,
    fingerprint_hash TEXT NOT NULL,
    css_path TEXT NOT NULL,
    attrs_json TEXT NOT NULL,
    text_snippet TEXT NOT NULL,
    last_seen REAL NOT NULL,
    last_relocated REAL NOT NULL,
    relocate_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (domain, selector_id)
)
"""


@dataclass
class _StoredSelector:
    domain: str
    selector_id: str
    fingerprint_hash: str
    css_path: str
    attrs: dict[str, str]
    text_snippet: str
    last_seen: float
    last_relocated: float
    relocate_count: int


# ── Fingerprinting ─────────────────────────────────────────────────────

def _normalize_text(text: str | None) -> str:
    """Whitespace-collapse + first 200 chars. Stable across re-renders."""
    if not text:
        return ""
    return " ".join(text.split())[:200]


def _fingerprint(tag: str, attrs: dict[str, str], text: str) -> str:
    """sha256 over (tag, sorted attrs, normalized text). Stable identifier
    across small DOM tweaks but changes on real content updates."""
    payload = json.dumps(
        {
            "tag": tag.lower(),
            "attrs": dict(sorted(attrs.items())),
            "text": _normalize_text(text),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _tokenize_attrs(attrs: dict[str, str]) -> set[tuple[str, str]]:
    """Split multi-value attrs (class="a b c", rel="nofollow noopener")
    into per-token (key, token) pairs. Single-value attrs pass through
    as-is. Critical for relocation — sites typically rename ONE class
    in a list while keeping the others stable, so tokenized Jaccard
    credits the partial match instead of treating the whole class
    string as a single token that either matches exactly or not at all."""
    pairs: set[tuple[str, str]] = set()
    for k, v in attrs.items():
        if not v:
            pairs.add((k, ""))
        elif " " in v:
            for tok in v.split():
                if tok:
                    pairs.add((k, tok))
        else:
            pairs.add((k, v))
    return pairs


def _attr_jaccard(a: dict[str, str], b: dict[str, str]) -> float:
    """Tokenized Jaccard on (key, token) pairs. See _tokenize_attrs for
    why we tokenize."""
    set_a = _tokenize_attrs(a)
    set_b = _tokenize_attrs(b)
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0


def _text_similarity(a: str, b: str) -> float:
    """Jaccard on word sets — same content phrased identically gives 1.0,
    same content with a typo or whitespace change gives near-1.0."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0
    inter = len(words_a & words_b)
    union = len(words_a | words_b)
    return inter / union if union else 0.0


def _score(stored: _StoredSelector, candidate_attrs: dict[str, str], candidate_text: str) -> float:
    """Combined attr + text similarity. Returns 0.0–1.0.

    Special case: when the candidate's normalized text matches the stored
    text exactly (and is non-empty), we boost the floor to 0.85. Identical
    content surviving across versions is one of the strongest relocation
    signals — it routinely beats class renames + minor attr drift, which
    would otherwise drag the Jaccard score below threshold."""
    base = (
        _ATTR_WEIGHT * _attr_jaccard(stored.attrs, candidate_attrs)
        + _TEXT_WEIGHT * _text_similarity(stored.text_snippet, candidate_text)
    )
    candidate_text_norm = _normalize_text(candidate_text)
    if stored.text_snippet and candidate_text_norm == stored.text_snippet:
        return max(base, 0.85)
    return base


# ── DOM utilities (lxml) ───────────────────────────────────────────────

def _parse(html: str):
    """Parse HTML into an lxml tree. Forgiving — never raises on bad markup."""
    from lxml import html as lxml_html
    try:
        return lxml_html.fromstring(html)
    except Exception:
        # Empty doc fallback so callers get a graceful None
        return lxml_html.fromstring("<html></html>")


def _build_css_path(elem) -> str:
    """Generate a CSS selector that uniquely targets this element.

    Walks up to the document root, picking the most specific stable
    identifier at each level: id > tag.class.class > tag:nth-of-type(n).
    Same approach Chrome DevTools' "Copy selector" uses.
    """
    if elem is None:
        return ""

    parts: list[str] = []
    while elem is not None and elem.tag and elem.tag != "html":
        if not isinstance(elem.tag, str):
            elem = elem.getparent()
            continue
        token = elem.tag
        elem_id = elem.get("id")
        if elem_id and re.match(r"^[A-Za-z_][\w-]*$", elem_id):
            token = f"#{elem_id}"
            parts.insert(0, token)
            break  # ID is unique enough — stop walking up
        classes = (elem.get("class") or "").split()
        # Skip classes that look auto-generated (random hashes) — those
        # are notoriously unstable across redeploys
        stable_classes = [
            c for c in classes
            if re.match(r"^[A-Za-z_][\w-]*$", c) and not re.search(r"[0-9]{5,}", c)
        ]
        if stable_classes:
            token = elem.tag + "".join("." + c for c in stable_classes[:3])
        else:
            # No stable classes → use nth-of-type for deterministic path
            siblings = [s for s in elem.itersiblings(preceding=True) if s.tag == elem.tag]
            token = f"{elem.tag}:nth-of-type({len(siblings) + 1})"
        parts.insert(0, token)
        elem = elem.getparent()

    return " > ".join(parts) if parts else ""


def _get_elem_attrs(elem) -> dict[str, str]:
    """Element attributes as a plain dict[str,str]."""
    return {k: v for k, v in elem.attrib.items()}


def _get_elem_text(elem) -> str:
    """Concatenated visible text content, normalized. Includes children."""
    return _normalize_text(elem.text_content() if hasattr(elem, "text_content") else "")


def _select_with_css(tree, css: str):
    """Run a CSS selector via lxml + cssselect. Returns first match or None."""
    if not css:
        return None
    try:
        from lxml.cssselect import CSSSelector
        sel = CSSSelector(css)
        matches = sel(tree)
        return matches[0] if matches else None
    except Exception as exc:
        logger.debug("CSS select failed for %r: %s", css, exc)
        return None


_STRUCTURAL_TAGS = frozenset({"html", "body", "head"})


def _walk_all_elements(tree):
    """Yield candidate element nodes in document order. Skips:
      * comments / processing instructions (non-string tag)
      * structural container tags (html / body / head) — these have
        ``text_content()`` that concatenates everything and therefore
        false-positive on the perfect-text-match boost; they're never
        valid extraction targets anyway."""
    for elem in tree.iter():
        if not isinstance(elem.tag, str):
            continue
        if elem.tag in _STRUCTURAL_TAGS:
            continue
        yield elem


# ── Storage class ──────────────────────────────────────────────────────

class _SelectorStore:
    """Thread-safe SQLite wrapper. Single connection per process; sqlite3
    is already serializable when isolation_level=None + WAL mode."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self._db_path), isolation_level="DEFERRED")
        try:
            yield conn
        finally:
            conn.close()

    def get(self, domain: str, selector_id: str) -> _StoredSelector | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT domain, selector_id, fingerprint_hash, css_path, "
                "attrs_json, text_snippet, last_seen, last_relocated, "
                "relocate_count FROM selectors WHERE domain=? AND selector_id=?",
                (domain, selector_id),
            ).fetchone()
        if not row:
            return None
        return _StoredSelector(
            domain=row[0],
            selector_id=row[1],
            fingerprint_hash=row[2],
            css_path=row[3],
            attrs=json.loads(row[4]),
            text_snippet=row[5],
            last_seen=row[6],
            last_relocated=row[7],
            relocate_count=row[8],
        )

    def upsert(
        self,
        *,
        domain: str,
        selector_id: str,
        fingerprint_hash: str,
        css_path: str,
        attrs: dict[str, str],
        text_snippet: str,
        bump_relocate: bool = False,
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT relocate_count FROM selectors WHERE domain=? AND selector_id=?",
                (domain, selector_id),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO selectors (domain, selector_id, fingerprint_hash, "
                    "css_path, attrs_json, text_snippet, last_seen, last_relocated, "
                    "relocate_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        domain, selector_id, fingerprint_hash, css_path,
                        json.dumps(attrs), text_snippet, now, now, 0,
                    ),
                )
            else:
                relocate_count = existing[0] + (1 if bump_relocate else 0)
                conn.execute(
                    "UPDATE selectors SET fingerprint_hash=?, css_path=?, "
                    "attrs_json=?, text_snippet=?, last_seen=?, "
                    "last_relocated=?, relocate_count=? "
                    "WHERE domain=? AND selector_id=?",
                    (
                        fingerprint_hash, css_path, json.dumps(attrs),
                        text_snippet, now,
                        now if bump_relocate else (
                            conn.execute(
                                "SELECT last_relocated FROM selectors WHERE "
                                "domain=? AND selector_id=?",
                                (domain, selector_id),
                            ).fetchone()[0]
                        ),
                        relocate_count, domain, selector_id,
                    ),
                )
            conn.commit()

    def all_stats(self) -> dict:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT domain, selector_id, last_seen, last_relocated, "
                "relocate_count FROM selectors"
            ).fetchall()
        return {
            "total": len(rows),
            "rows": [
                {
                    "domain": r[0], "selector_id": r[1],
                    "last_seen": r[2], "last_relocated": r[3],
                    "relocate_count": r[4],
                }
                for r in rows
            ],
        }


# ── Public API ─────────────────────────────────────────────────────────

@dataclass
class FindResult:
    """Result of an adaptive find. ``element`` is None when nothing
    matched above threshold; check ``status`` to distinguish.

    Statuses:
      * ``hit`` — saved CSS path returned the right fingerprint
      * ``relocated`` — DOM changed; we found the element by similarity
        and updated the stored CSS path
      * ``cold`` — first time for this (domain, selector_id); stored
        the fingerprint for next visit
      * ``broken`` — no match above threshold; element is genuinely gone
        (or so different it might as well be). Caller should treat this
        as "extractor broken, please report."
    """

    element: Any  # lxml element or None
    text: str
    status: str  # "hit" | "relocated" | "cold" | "broken"
    score: float = 0.0
    css_path: str = ""


class AdaptiveSelector:
    """SQLite-backed element fingerprint store + relocator.

    Usage:
        sel = AdaptiveSelector()
        result = sel.find(
            html, domain="example.com",
            selector_id="price", initial_css=".product-price",
        )
        if result.status == "broken":
            log.warning("price selector for example.com no longer matches anything")
        else:
            print(result.text)
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._store = _SelectorStore(Path(db_path) if db_path else _DEFAULT_DB_PATH)

    def find(
        self,
        html: str,
        *,
        domain: str,
        selector_id: str,
        initial_css: str,
    ) -> FindResult:
        """Find the element identified by (domain, selector_id) in ``html``.

        On the first call, ``initial_css`` is used as the seed selector.
        On subsequent calls, the stored fingerprint is consulted; if the
        saved CSS path no longer points to a matching element, walk the
        DOM and relocate.
        """
        if not html:
            return FindResult(element=None, text="", status="broken")

        tree = _parse(html)
        stored = self._store.get(domain, selector_id)

        # Cold path — first time we've seen this (domain, selector_id)
        if stored is None:
            elem = _select_with_css(tree, initial_css)
            if elem is None:
                # Even on the first visit, the seed selector misses.
                # Don't store anything — let the caller fix the seed.
                return FindResult(element=None, text="", status="broken")
            self._save(domain, selector_id, elem, css_path=initial_css)
            return FindResult(
                element=elem,
                text=_get_elem_text(elem),
                status="cold",
                score=1.0,
                css_path=initial_css,
            )

        # Try the saved CSS path first — fast path
        elem = _select_with_css(tree, stored.css_path)
        if elem is not None:
            current_fp = _fingerprint(
                elem.tag, _get_elem_attrs(elem), _get_elem_text(elem),
            )
            if current_fp == stored.fingerprint_hash:
                # Exact match — bump last_seen, return.
                self._save(domain, selector_id, elem, css_path=stored.css_path)
                return FindResult(
                    element=elem,
                    text=_get_elem_text(elem),
                    status="hit",
                    score=1.0,
                    css_path=stored.css_path,
                )

        # Saved selector missed OR returned wrong-fingerprint element.
        # Walk the DOM, score every candidate, pick the best.
        best_elem = None
        best_score = 0.0
        for cand in _walk_all_elements(tree):
            cand_attrs = _get_elem_attrs(cand)
            cand_text = _get_elem_text(cand)
            s = _score(stored, cand_attrs, cand_text)
            if s > best_score:
                best_score = s
                best_elem = cand

        if best_elem is None or best_score < _RELOCATION_THRESHOLD:
            logger.warning(
                "adaptive_selector broken: domain=%s selector_id=%s "
                "best_score=%.2f threshold=%.2f — element appears gone",
                domain, selector_id, best_score, _RELOCATION_THRESHOLD,
            )
            return FindResult(
                element=None, text="", status="broken", score=best_score,
            )

        # Relocated — update the stored CSS path so future calls hit fast
        new_css = _build_css_path(best_elem)
        self._save(domain, selector_id, best_elem, css_path=new_css, relocated=True)
        logger.info(
            "adaptive_selector relocated: domain=%s selector_id=%s "
            "old_css=%s new_css=%s score=%.2f",
            domain, selector_id, stored.css_path, new_css, best_score,
        )
        return FindResult(
            element=best_elem,
            text=_get_elem_text(best_elem),
            status="relocated",
            score=best_score,
            css_path=new_css,
        )

    def find_text(
        self,
        html: str,
        *,
        domain: str,
        selector_id: str,
        initial_css: str,
    ) -> str | None:
        """Convenience wrapper — returns just the element text, or None
        on ``broken`` status."""
        result = self.find(
            html, domain=domain, selector_id=selector_id, initial_css=initial_css,
        )
        return None if result.status == "broken" else result.text

    def stats(self) -> dict:
        """Snapshot of all stored selectors — for debugging / Web UI."""
        return self._store.all_stats()

    # ── Internal ───────────────────────────────────────────────────────

    def _save(
        self,
        domain: str,
        selector_id: str,
        elem,
        *,
        css_path: str,
        relocated: bool = False,
    ) -> None:
        attrs = _get_elem_attrs(elem)
        text = _get_elem_text(elem)
        fp = _fingerprint(elem.tag, attrs, text)
        self._store.upsert(
            domain=domain,
            selector_id=selector_id,
            fingerprint_hash=fp,
            css_path=css_path,
            attrs=attrs,
            text_snippet=_normalize_text(text),
            bump_relocate=relocated,
        )
