"""Snapshot system — ref-ID page snapshots with landmark pruning.

Replaces raw accessibility tree dumps with compact, ref-ID-based
snapshots that reduce tokens by ~93%. Each interactive element gets
a ref like [e1], [e2] that maps directly to a live DOM element in
the Chrome extension.

Usage:
    manager = SnapshotManager()
    snapshot = await manager.take_snapshot(backend)
    text = manager.format_snapshot(snapshot, task_hint="delete emails")
    coords = await manager.resolve_ref(backend, "e5")
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from types import MappingProxyType

logger = logging.getLogger(__name__)

# ── Immutable data models ─────────────────────────────────────────────


@dataclass(frozen=True)
class ElementRef:
    """Single interactive element with a ref ID."""

    ref_id: str        # "e1", "f1_e3"
    role: str          # "button", "textbox", "link", "tab"
    name: str          # accessible name / text content
    tag: str           # "button", "input", "a"
    landmark: str      # "navigation", "main", "other"
    properties: tuple[tuple[str, str], ...]  # (("selected","true"), ...)


@dataclass(frozen=True)
class Landmark:
    """Page section (ARIA landmark) with its element refs."""

    name: str          # "main", "navigation", "other"
    ref_ids: tuple[str, ...]


@dataclass(frozen=True)
class PageSnapshot:
    """Immutable snapshot of a page's interactive elements."""

    version: int
    url: str
    title: str
    landmarks: tuple[Landmark, ...]
    elements: MappingProxyType  # ref_id → ElementRef (read-only dict)
    element_count: int
    timestamp: float
    context: tuple[tuple[str, str], ...] = ()  # e.g. (("main_rows", "25 total (showing first 5)"),)


# ── Pruning heuristics ────────────────────────────────────────────────

# Keyword → landmark relevance mapping (no LLM call needed)
_LANDMARK_KEYWORDS: dict[str, set[str]] = {
    "navigation": {"search", "find", "tab", "navigate", "filter", "menu", "go to"},
    "main": set(),  # always included
    "complementary": {"filter", "sort", "category", "sidebar", "settings", "options"},
    "banner": set(),  # almost never relevant
    "contentinfo": set(),
    "search": {"search", "find", "look", "query"},
    "form": {"fill", "enter", "submit", "form", "type", "input", "sign", "login", "register"},
    "region": set(),
    "other": set(),
}

# Action-oriented keywords → relevant landmark types
_ACTION_KEYWORDS: dict[str, list[str]] = {
    "delete": ["main", "actions", "navigation"],
    "archive": ["main", "actions"],
    "move": ["main", "actions"],
    "mark": ["main", "actions"],
    "send": ["main", "form", "actions"],
    "submit": ["main", "form"],
    "reply": ["main", "form"],
    "compose": ["main", "form", "navigation"],
    "select": ["main"],
    "check": ["main"],
    "uncheck": ["main"],
    "click": ["main", "navigation"],
    "open": ["main", "navigation"],
    "close": ["main"],
    "scroll": ["main"],
    "read": ["main"],
    "download": ["main", "actions"],
    "upload": ["main", "form"],
    "buy": ["main", "form"],
    "add": ["main", "form"],
}


def _score_landmarks(
    task_hint: str,
    landmark_names: list[str],
) -> dict[str, float]:
    """Score each landmark's relevance to the task (0.0-1.0).

    Uses keyword heuristics — no LLM call.
    """
    scores: dict[str, float] = {}
    hint_lower = task_hint.lower()
    hint_words = set(hint_lower.split())

    for lm_name in landmark_names:
        # main always gets full score
        if lm_name == "main":
            scores[lm_name] = 1.0
            continue

        score = 0.3  # base visibility

        # Check landmark-specific keywords
        keywords = _LANDMARK_KEYWORDS.get(lm_name, set())
        for kw in keywords:
            if kw in hint_lower:
                score = max(score, 0.8)
                break

        # Check action keywords
        for word in hint_words:
            relevant_landmarks = _ACTION_KEYWORDS.get(word, [])
            if lm_name in relevant_landmarks:
                score = max(score, 0.9)
                break

        # "other" gets moderate score (might contain unlabeled controls)
        if lm_name == "other":
            score = max(score, 0.5)

        scores[lm_name] = score

    return scores


# ── Snapshot Manager ──────────────────────────────────────────────────

# JS fallback — injected when extension is not loaded.
# Same logic as content.js but as a single evaluate() call.
_FALLBACK_VERSION = 4  # Bump when changing fallback JS to force re-injection

_JS_INJECT_FALLBACK = """
(() => {
  const EXPECTED_VERSION = 4;
  if (typeof window.__lazyclaw !== 'undefined' && window.__lazyclaw_version === EXPECTED_VERSION) return 'already_loaded';
  // Force re-inject if version mismatch (code updated)

  const INTERACTIVE = 'input, button, select, textarea, a[href], ' +
    '[role="button"], [role="link"], [role="tab"], [role="menuitem"], ' +
    '[role="checkbox"], [role="radio"], [role="switch"], [role="option"], ' +
    '[role="combobox"], [role="searchbox"], ' +
    '[role="row"], [role="listitem"], [role="treeitem"], [role="gridcell"], ' +
    '[onclick], [jsaction], [data-action], ' +
    '[role="alert"], [role="status"], [role="banner"] span[role="link"], ' +
    '[contenteditable="true"], [tabindex]:not([tabindex=\\"-1\\"]), summary';

  const LANDMARK_TAGS = {NAV:'navigation',MAIN:'main',ASIDE:'complementary',
    HEADER:'banner',FOOTER:'contentinfo',FORM:'form',SECTION:'region'};
  const LANDMARK_ROLES = new Set(Object.values(LANDMARK_TAGS).concat(['search']));
  const SKIP_ROLES = new Set(['none','presentation','generic']);

  let _v = 0, _dirty = false;
  const _refs = new Map(), _meta = new Map();

  function vis(el) {
    if (!el.offsetWidth && !el.offsetHeight) return false;
    const s = getComputedStyle(el);
    return s.display!=='none' && s.visibility!=='hidden' && s.opacity!=='0';
  }
  function lm(el) {
    let n = el;
    while (n && n !== document.body) {
      const r = n.getAttribute('role');
      if (r && LANDMARK_ROLES.has(r)) return r;
      const t = LANDMARK_TAGS[n.tagName];
      if (t) return t;
      n = n.parentElement;
    }
    return 'other';
  }
  function role(el) {
    const r = el.getAttribute('role');
    if (r && !SKIP_ROLES.has(r)) return r;
    const t = el.tagName.toLowerCase(), tp = el.getAttribute('type');
    if (t==='input') return tp==='checkbox'?'checkbox':tp==='radio'?'radio':
      (tp==='submit'||tp==='button')?'button':'textbox';
    if (t==='button') return 'button';
    if (t==='a') return 'link';
    if (t==='select') return 'combobox';
    if (t==='textarea') return 'textbox';
    return t;
  }
  function nm(el) {
    return el.getAttribute('aria-label') || el.getAttribute('data-tooltip') ||
      el.getAttribute('title') || el.getAttribute('alt') ||
      el.getAttribute('placeholder') ||
      (el.textContent||'').trim().slice(0,80) || '';
  }
  function props(el) {
    const p = {};
    if (el.checked) p.checked='true';
    if (el.selected||el.getAttribute('aria-selected')==='true') p.selected='true';
    if (el.disabled) p.disabled='true';
    if (el.getAttribute('aria-expanded')) p.expanded=el.getAttribute('aria-expanded');
    if (el.placeholder) p.placeholder=el.placeholder;
    if (el.type&&el.type!=='text') p.type=el.type;
    if (el.href) p.href=el.href.slice(0,120);
    return p;
  }

  const api = {
    snapshot() {
      _refs.clear(); _meta.clear(); _v++; _dirty=false;
      const els = document.querySelectorAll(INTERACTIVE);
      const landmarks={}, elements={}, rowCt={}, rowShown={}; let c=0;
      const MAX_ROWS=5;
      const ROW_ROLES=new Set(['row','listitem','treeitem','gridcell']);
      for (const el of els) {
        if (!vis(el)) continue;
        const r=role(el), l=lm(el);
        if (ROW_ROLES.has(r)) {
          rowCt[l]=(rowCt[l]||0)+1;
          rowShown[l]=rowShown[l]||0;
          if (rowShown[l]>=MAX_ROWS) {
            c++; const id='e'+c;
            _refs.set(id,el); _meta.set(id,{role:r,name:nm(el),tag:el.tagName.toLowerCase(),landmark:l,props:props(el)});
            continue;
          }
          rowShown[l]++;
        }
        c++;
        const id='e'+c, n=nm(el), p=props(el);
        const m={role:r,name:n,tag:el.tagName.toLowerCase(),landmark:l,props:p};
        _refs.set(id,el); _meta.set(id,m); elements[id]=m;
        if (!landmarks[l]) landmarks[l]=[];
        landmarks[l].push(id);
      }
      const context={};
      for (const [l,total] of Object.entries(rowCt)) {
        if (total>(rowShown[l]||0)) context[l+'_rows']=total+' total (showing first '+(rowShown[l]||0)+')';
      }
      try {
        const m=document.querySelector('[role="main"],main,#content');
        if (m) { const t=m.innerText.trim().slice(0,1000); if(t) context.main_text=t; }
      } catch(_){}
      return {version:_v,url:location.href,title:document.title||'',
        landmarks,elements,elementCount:c,context,dirty:false};
    },
    resolve(id) {
      const el=_refs.get(id);
      if (!el||!el.isConnected) return null;
      const r=el.getBoundingClientRect();
      if (!r.width&&!r.height) return null;
      return {x:r.x+r.width/2,y:r.y+r.height/2,width:r.width,height:r.height,visible:vis(el)};
    },
    click(id) {
      const el=_refs.get(id);
      if (!el||!el.isConnected) return null;
      el.scrollIntoView({block:'center',behavior:'instant'});
      const r=el.getBoundingClientRect();
      if (!r.width&&!r.height) return null;
      return {x:r.x+r.width/2,y:r.y+r.height/2};
    },
    performClick(id) {
      const el=_refs.get(id);
      if (!el||!el.isConnected) return false;
      el.scrollIntoView({block:'center',behavior:'instant'});
      el.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));
      el.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true}));
      el.click();
      return true;
    },
    focus(id) {
      const el=_refs.get(id);
      if (!el||!el.isConnected) return false;
      el.scrollIntoView({block:'center',behavior:'instant'});
      el.focus();
      if (el.select) el.select();
      else if (el.setSelectionRange&&el.value!==undefined) el.setSelectionRange(0,el.value.length);
      return document.activeElement===el;
    },
    getVersion() { return _v; },
    isDirty() { return _dirty; },
    getMeta(id) { return _meta.get(id)||null; },
  };

  try {
    new MutationObserver(()=>{_dirty=true;}).observe(
      document.body||document.documentElement,
      {childList:true,subtree:true,attributes:true,
       attributeFilter:['disabled','aria-hidden','hidden','style','class']});
  } catch(_){}

  // Use regular property (not defineProperty) so version upgrades can overwrite
  window.__lazyclaw = Object.freeze(api);
  window.__lazyclaw_version = EXPECTED_VERSION;
  return 'injected';
})()
"""


class SnapshotManager:
    """Manages ref-ID snapshots via the Chrome extension or JS fallback."""

    def __init__(self) -> None:
        self._current: PageSnapshot | None = None

    @property
    def current(self) -> PageSnapshot | None:
        return self._current

    async def _ensure_engine(self, backend) -> None:
        """Ensure __lazyclaw is available — inject fallback if needed."""
        check = await backend.evaluate(
            "(typeof window.__lazyclaw !== 'undefined')"
        )
        if not check:
            logger.info("Extension not found, injecting JS fallback")
            await backend.evaluate(_JS_INJECT_FALLBACK)

    async def take_snapshot(self, backend) -> PageSnapshot:
        """Take a fresh snapshot via the extension/fallback.

        Returns an immutable PageSnapshot with all interactive elements
        grouped by landmark.
        """
        await self._ensure_engine(backend)
        raw = await backend.evaluate("window.__lazyclaw.snapshot()")

        if not raw or not isinstance(raw, dict):
            logger.warning("Snapshot returned empty/invalid: %s", raw)
            return PageSnapshot(
                version=0, url="", title="", landmarks=(),
                elements=MappingProxyType({}), element_count=0,
                timestamp=time.time(),
            )

        # Parse elements into immutable ElementRef objects
        elements: dict[str, ElementRef] = {}
        raw_elements = raw.get("elements", {})
        for ref_id, meta in raw_elements.items():
            props_dict = meta.get("props", {})
            props_tuple = tuple(sorted(props_dict.items()))
            elements[ref_id] = ElementRef(
                ref_id=ref_id,
                role=meta.get("role", ""),
                name=meta.get("name", ""),
                tag=meta.get("tag", ""),
                landmark=meta.get("landmark", "other"),
                properties=props_tuple,
            )

        # Parse landmarks
        landmarks: list[Landmark] = []
        raw_landmarks = raw.get("landmarks", {})
        for lm_name, ref_ids in raw_landmarks.items():
            landmarks.append(Landmark(
                name=lm_name,
                ref_ids=tuple(ref_ids),
            ))

        # Parse context (row counts, etc.)
        raw_context = raw.get("context", {})
        context_tuple = tuple(sorted(raw_context.items())) if raw_context else ()

        snapshot = PageSnapshot(
            version=raw.get("version", 0),
            url=raw.get("url", ""),
            title=raw.get("title", ""),
            landmarks=tuple(landmarks),
            elements=MappingProxyType(elements),
            element_count=raw.get("elementCount", len(elements)),
            timestamp=time.time(),
            context=context_tuple,
        )

        self._current = snapshot
        logger.info(
            "Snapshot v%d: %d elements, %d landmarks on %s",
            snapshot.version, snapshot.element_count,
            len(snapshot.landmarks), snapshot.url[:60],
        )
        return snapshot

    async def resolve_ref(
        self, backend, ref_id: str,
    ) -> tuple[float, float] | None:
        """Resolve a ref ID to (x, y) coordinates.

        Returns center point or None if element is gone/invisible.
        """
        await self._ensure_engine(backend)
        coords = await backend.evaluate(
            f"window.__lazyclaw.click('{_safe_ref(ref_id)}')"
        )
        if not coords or not isinstance(coords, dict):
            return None
        x, y = coords.get("x"), coords.get("y")
        if x is None or y is None:
            return None
        return (float(x), float(y))

    async def perform_click(self, backend, ref_id: str) -> bool:
        """Click an element by ref ID using DOM click().

        Dispatches mousedown + mouseup + click on the actual DOM element.
        Works on all sites including Gmail's jsaction event system.
        """
        await self._ensure_engine(backend)
        result = await backend.evaluate(
            f"window.__lazyclaw.performClick('{_safe_ref(ref_id)}')"
        )
        return bool(result)

    async def focus_ref(self, backend, ref_id: str) -> bool:
        """Focus an element by ref ID (for typing)."""
        await self._ensure_engine(backend)
        result = await backend.evaluate(
            f"window.__lazyclaw.focus('{_safe_ref(ref_id)}')"
        )
        return bool(result)

    async def is_stale(self, backend) -> bool:
        """Check if the current snapshot is outdated."""
        if self._current is None:
            return True
        await self._ensure_engine(backend)
        dirty = await backend.evaluate("window.__lazyclaw.isDirty()")
        return bool(dirty)

    async def get_ref_meta(self, backend, ref_id: str) -> dict | None:
        """Get metadata for a ref without resolving coordinates."""
        await self._ensure_engine(backend)
        return await backend.evaluate(
            f"window.__lazyclaw.getMeta('{_safe_ref(ref_id)}')"
        )

    def format_snapshot(
        self,
        snapshot: PageSnapshot,
        task_hint: str | None = None,
        landmark_filter: str | None = None,
        max_elements: int = 50,
    ) -> str:
        """Format a snapshot as compact text for the LLM.

        With task_hint: prune low-relevance landmarks to counts only.
        With landmark_filter: show only that landmark's elements.
        """
        if not snapshot.landmarks:
            return f"Page: {snapshot.title} | {snapshot.url}\n\nNo interactive elements found."

        lines = [f"Page: {snapshot.title} | {_short_url(snapshot.url)}"]

        # Show page context (row counts, etc.) — helps LLM know what's on page
        if snapshot.context:
            for key, val in snapshot.context:
                label = key.replace("_rows", " items").replace("_", " ")
                lines.append(f"  {label}: {val}")

        lines.append("")

        # Compute landmark scores if task hint provided
        lm_names = [lm.name for lm in snapshot.landmarks]
        scores = (
            _score_landmarks(task_hint, lm_names)
            if task_hint else {name: 1.0 for name in lm_names}
        )

        total_shown = 0
        for lm in snapshot.landmarks:
            # If filtering to a specific landmark
            if landmark_filter and lm.name != landmark_filter:
                continue

            score = scores.get(lm.name, 0.3)
            count = len(lm.ref_ids)

            # Low relevance: show count only
            if score < 0.5 and not landmark_filter:
                lines.append(f"[{lm.name}] ({count} elements)")
                continue

            lines.append(f"[{lm.name}] ({count} elements)")

            # Show elements (up to remaining budget)
            budget = max_elements - total_shown
            shown = 0
            for ref_id in lm.ref_ids:
                if shown >= budget:
                    remaining = count - shown
                    if remaining > 0:
                        lines.append(f"  ... +{remaining} more")
                    break
                el = snapshot.elements.get(ref_id)
                if el:
                    lines.append(_format_element(el))
                    shown += 1
            total_shown += shown
            lines.append("")

        return "\n".join(lines).rstrip()


# ── Formatting helpers ────────────────────────────────────────────────


def _format_element(el: ElementRef) -> str:
    """Format a single element as a compact line.

    Output: "  [e1] button \"Search\" selected"
    """
    parts = [f"  [{el.ref_id}]", el.role]
    if el.name:
        parts.append(f'"{el.name}"')
    for key, val in el.properties:
        if key in ("checked", "selected", "disabled", "expanded", "pressed", "required"):
            parts.append(f"{key}={val}")
        elif key == "placeholder" and not el.name:
            parts.append(f'placeholder="{val}"')
        elif key == "type" and val not in ("text", "submit"):
            parts.append(f"type={val}")
    return " ".join(parts)


def _short_url(url: str) -> str:
    """Shorten URL for display: https://mail.google.com/mail/u/0/#inbox → mail.google.com"""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.hostname or url[:60]
    except Exception:
        return url[:60]


def _safe_ref(ref_id: str) -> str:
    """Sanitize ref ID to prevent JS injection."""
    return re.sub(r"[^a-zA-Z0-9_]", "", ref_id)
