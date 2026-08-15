"""Shared bridge from ref-based browser actions to the anti-detect input layer.

Every click and keystroke the agent performs must execute through
:mod:`lazyclaw.browser.human_input` (Bezier mouse paths + per-domain
cadence from :mod:`lazyclaw.browser.cadence`). Before this module the
CSS-selector path (``cdp_backend.click`` / ``type_text``) was the only
one that did; the ref-ID path (``interact.py``) and the chain path
(``navigation.py``) each dispatched raw CDP input with their own inline
timing, which is exactly the fingerprint bot-detection looks for.

This module is a THIN adapter — it resolves a connection, a cadence and
(for clicks) viewport coordinates, then delegates. It deliberately
contains no event-dispatch logic of its own; ``human_input`` stays the
single implementation.

Fallback policy for clicks — the injected JS ``el.click()`` survives, but
only where a synthetic mouse click provably cannot work:

* **iframe refs** (``f1_e3``) — the engine reports
  ``getBoundingClientRect`` from INSIDE the frame, so the coordinates are
  frame-relative while ``Input.dispatchMouseEvent`` is viewport-absolute.
  A bezier click would land on whatever sits at those top-level pixels.
* **unresolvable geometry** — element gone, or zero-sized.
* **invisible / clipped** — ``display:none``-style elements and centers
  that scrolled outside the viewport (sticky headers, overlays).

Which path ran is logged at INFO so a "the click did nothing" report can
be diagnosed without a repro.
"""

from __future__ import annotations

import logging
import re

from lazyclaw.browser.human_input import cadence_for, human_click, human_type
from lazyclaw.browser.snapshot import RefBox

logger = logging.getLogger(__name__)

__all__ = [
    "RefBox",
    "cadence_for",
    "connection_for",
    "human_click_ref",
    "human_type_text",
]

# Refs minted inside a same-origin iframe carry an ``f<n>_`` prefix
# (possibly nested: ``f1_f2_e7``). See browser/extension/content.js.
_FRAME_REF_RE = re.compile(r"^f\d+_")


class _TargetConn:
    """``conn.send()`` adapter for a session-scoped :class:`TabContext`.

    ``TabContext`` has no ``_ensure_connected`` — it routes every CDP call
    through ``backend.send_to_target(session_id, ...)``. Wrapping that here
    keeps the ref/chain paths working (and human-like) on a leased tab
    instead of raising ``AttributeError``.
    """

    __slots__ = ("_backend", "_session_id")

    def __init__(self, backend, session_id: str) -> None:
        self._backend = backend
        self._session_id = session_id

    async def send(self, method: str, params: dict | None = None) -> dict:
        return await self._backend.send_to_target(
            self._session_id, method, params or {},
        )


async def connection_for(backend):
    """Return an object exposing ``await send(method, params)`` for *backend*.

    Covers all three shapes the action modules can receive:
    ``CDPBackend`` (CDPConnection), ``BrowserUseBackend`` (``_SessionConn``)
    and ``TabContext`` (wrapped in :class:`_TargetConn`).
    """
    ensure = getattr(backend, "_ensure_connected", None)
    if ensure is not None:
        return await ensure()

    session_id = getattr(backend, "session_id", None)
    inner = getattr(backend, "backend", None)
    if session_id and inner is not None and hasattr(inner, "send_to_target"):
        return _TargetConn(inner, session_id)

    raise AttributeError(
        f"{type(backend).__name__} exposes no CDP connection for human input"
    )


def _js_fallback_reason(ref_id: str, box: RefBox | None) -> str | None:
    """Why a ref must use the DOM click, or ``None`` to go human."""
    if _FRAME_REF_RE.match(ref_id):
        return "iframe_ref"
    if box is None:
        return "no_coords"
    if not box.visible:
        return "invisible"
    if not box.in_viewport():
        return "offscreen"
    return None


async def human_click_ref(backend, snapshot_mgr, ref_id: str) -> str:
    """Click a snapshot ref, preferring the human (bezier) mouse path.

    Returns which path executed: ``"human"``, ``"js"``, or ``""`` when the
    element is gone. Callers treat the empty string as failure — the same
    falsy contract ``snapshot_mgr.perform_click`` had.
    """
    box: RefBox | None = None
    if not _FRAME_REF_RE.match(ref_id):
        try:
            box = await snapshot_mgr.resolve_ref_box(backend, ref_id)
        except Exception:
            logger.debug("resolve_ref_box failed for %s", ref_id, exc_info=True)

    reason = _js_fallback_reason(ref_id, box)
    if reason is None and box is not None:
        conn = await connection_for(backend)
        await human_click(
            conn, box.x, box.y,
            target_size=box.target_size,
            cadence=cadence_for(backend),
        )
        logger.info(
            "[browser] click ref=%s path=human x=%.0f y=%.0f size=%.0f",
            ref_id, box.x, box.y, box.target_size,
        )
        return "human"

    clicked = await snapshot_mgr.perform_click(backend, ref_id)
    logger.info(
        "[browser] click ref=%s path=js reason=%s ok=%s",
        ref_id, reason, bool(clicked),
    )
    return "js" if clicked else ""


async def human_type_text(
    backend,
    text: str,
    field_x: float | None = None,
    field_y: float | None = None,
) -> None:
    """Type *text* through ``human_type`` with the backend's cadence.

    The element is expected to already hold focus (``snapshot_mgr.focus_ref``
    on the ref path). Pass ``field_x``/``field_y`` to have ``human_type``
    click the field first — used by the natural-description path, which
    locates the element by role rather than by ref.
    """
    conn = await connection_for(backend)
    await human_type(
        conn, text,
        field_x=field_x,
        field_y=field_y,
        cadence=cadence_for(backend),
    )
