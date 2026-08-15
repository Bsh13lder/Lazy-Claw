"""Test-scope adapter: minimal ``browser_session`` surface for the vendored
``DomService`` (``lazyclaw/_vendor/browser_use/dom/service.py``).

MEASUREMENT-ONLY. Nothing here is wired into any production code path.
Production browser control goes through
``lazyclaw/browser/browser_use_backend.py::BrowserUseBackend`` /
``_ActorSession``, which deliberately keeps a much smaller duck-typed
surface (``.cdp_client`` + ``.id``) because production never calls
``DomService`` — it uses ``lazyclaw/browser/snapshot.py::SnapshotManager``
instead. This adapter exists solely so ``scripts/browser-use-snapshot-ab.py``
can run ``DomService`` head-to-head against ``SnapshotManager`` for a
cost/benefit measurement.

``DomService.__init__`` type-hints ``browser_session: 'BrowserSession'``
(the full upstream browser-use session, which we deliberately did NOT
vendor) but at runtime — with ``cross_origin_iframes=False``, the only mode
this adapter supports — it only ever touches:

- ``get_or_create_cdp_session(target_id, focus=False)`` -> an object with
  ``.cdp_client`` (anything shaped like ``cdp_use.CDPClient``, i.e.
  exposing ``.send.<Domain>.<method>(params=..., session_id=...)``) and
  ``.session_id`` (str). Called once per constructed DOM node, so it MUST
  be cheap on repeat — this adapter caches the attached session per
  ``target_id``.
- ``.agent_focus_target_id`` -> str, the tab DomService should read.
- ``.id`` -> str, an opaque id threaded into ``DOMTreeSerializer`` purely
  to key a "seen in a previous serialization" cache — NOT a CDP session id.
- ``.logger`` -> only read when ``DomService(...)`` is constructed without
  an explicit ``logger=`` kwarg (the A/B script always passes one).

``get_all_frames()`` and ``.session_manager`` are ONLY reached by
``DomService``'s cross-origin-iframe recursion (see
``dom/service.py::get_dom_tree``, the ``self.cross_origin_iframes and
node['nodeName'].upper() == 'IFRAME'...`` branch), which is unreachable
here because the adapter is only ever used with ``cross_origin_iframes=
False``. They raise ``NotImplementedError`` rather than fake data, so a
future switch to ``cross_origin_iframes=True`` fails loudly instead of
silently returning wrong results.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Domains DomService issues session-scoped CDP commands against
# (DOM.getDocument, Page.getLayoutMetrics/getFrameTree, Runtime.evaluate/
# getProperties/releaseObject, Accessibility.getFullAXTree,
# DOMSnapshot.captureSnapshot — see dom/service.py::_get_all_trees /
# _get_viewport_ratio / _get_ax_tree_for_all_frames). Each CDP session must
# have every domain it uses explicitly enabled first. This mirrors what the
# vendored actor's own ``Page._ensure_session()`` already does for
# Page/DOM/Runtime (``lazyclaw/_vendor/browser_use/actor/page.py``), plus
# Accessibility and DOMSnapshot which DomService additionally needs.
_REQUIRED_DOMAINS = ("DOM", "Page", "Runtime", "Accessibility", "DOMSnapshot")


@dataclass(frozen=True)
class _CDPSession:
    """Shape DomService expects back from ``get_or_create_cdp_session()``."""

    cdp_client: Any
    session_id: str


class TestScopeBrowserSession:
    """Duck-typed stand-in for browser-use's ``BrowserSession`` — A/B measurement only.

    Wraps an already-connected ``cdp_use.CDPClient`` and a single target
    (tab). Attaches one flat CDP session to that target on first use
    (``Target.attachToTarget(flatten=True)``), enables the domains
    ``DomService`` needs, and caches the session for reuse — attachment is
    idempotent and cheap to repeat but there is no reason to pay for it on
    every one of the (potentially thousands of) DOM nodes DomService walks.
    """

    def __init__(self, client: Any, target_id: str) -> None:
        self._client = client
        self.agent_focus_target_id = target_id
        self.id = f"testscope-{uuid.uuid4().hex[:12]}"
        self.logger = logger
        self._sessions: dict[str, _CDPSession] = {}
        self._lock = asyncio.Lock()

    async def get_or_create_cdp_session(
        self, target_id: str, focus: bool = False
    ) -> _CDPSession:
        async with self._lock:
            cached = self._sessions.get(target_id)
            if cached is not None:
                return cached

            attached = await self._client.send.Target.attachToTarget(
                params={"targetId": target_id, "flatten": True}
            )
            session_id = attached["sessionId"]

            for domain in _REQUIRED_DOMAINS:
                await getattr(self._client.send, domain).enable(session_id=session_id)

            session = _CDPSession(cdp_client=self._client, session_id=session_id)
            self._sessions[target_id] = session
            return session

    async def get_all_frames(self) -> tuple[dict, None]:
        """Unimplemented — cross-origin-iframe path only, disabled here.

        Real signature per ``dom/service.py``: ``all_frames, _ =
        await self.browser_session.get_all_frames()``. This adapter is only
        exercised with ``cross_origin_iframes=False``, so DomService never
        calls this. If that ever changes, this needs a real implementation
        (walk ``Page.getFrameTree`` + ``Target.getTargets`` to build a
        frameId -> {frameTargetId, url, title} map) before it can be trusted.
        """
        raise NotImplementedError(
            "TestScopeBrowserSession.get_all_frames: not implemented — only "
            "reached by DomService's cross-origin-iframe traversal, which "
            "this adapter disables via cross_origin_iframes=False"
        )

    @property
    def session_manager(self) -> Any:
        """Unimplemented — same cross-origin-iframe-only path as ``get_all_frames``."""
        raise NotImplementedError(
            "TestScopeBrowserSession.session_manager: not implemented — only "
            "reached by DomService's cross-origin-iframe traversal, which "
            "this adapter disables via cross_origin_iframes=False"
        )
