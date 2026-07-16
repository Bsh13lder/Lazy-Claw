"""CDP browser backend — controls user's real Chrome/Brave browser.

Uses Chrome DevTools Protocol via ws://localhost:9222 (configurable port).
Standalone module — no ABC or Playwright dependency.

Utility functions (restart_browser_with_cdp, js_str, is_same_origin_nav)
live in cdp_utils.py and are re-exported here for backwards compatibility.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lazyclaw.browser.cdp import (
    CDPConnection,
    CDPTab,
    find_chrome_cdp,
    list_chrome_tabs,
)
from lazyclaw.browser.cdp_utils import (
    is_same_origin_nav as _is_same_origin_nav,
    js_str as _js_str,
    restart_browser_with_cdp,
)

logger = logging.getLogger(__name__)

# Idle timeout for CDP connections (5 minutes)
CDP_IDLE_TIMEOUT = 300


def _log_host(url: str | None) -> str:
    """Bare hostname for safe logging — NEVER logs path/query (may carry tokens).

    Diagnostic-only helper. Returns ``"?"`` on empty/parse failure so a log
    line always has a stable placeholder. This is the ONLY shape of a URL we
    are allowed to emit to logs (an OAuth callback / signed-download URL can
    smuggle secrets in the query string).
    """
    if not url:
        return "?"
    try:
        from urllib.parse import urlparse

        return (urlparse(url).hostname or "?").lower()
    except Exception:
        return "?"


async def _target_needs_host_browser(
    target_url: str | None, user_id: str | None,
) -> bool:
    """True when navigating to ``target_url`` MUST go through the user's
    signed-in host browser (Cloudflare-protected hosts like upwork.com /
    linkedin.com + the user's own ``live_hosts`` extras).

    Reuses the single canonical predicate ``_needs_live_browser`` and the
    builtin frozenset from ``heartbeat.daemon`` — there is exactly one
    CF-sensitive host list in the codebase and we do NOT fork a second.
    A fresh/headless container browser fails Cloudflare's fingerprint
    SILENTLY (empty results, no error), so for these hosts we must refuse
    to drive the wrong-identity browser rather than fall through to it.

    Lazy imports keep the ``browser → heartbeat`` edge off module load
    (heartbeat already imports browser; a top-level import here would
    risk a cycle).
    """
    if not target_url:
        return False
    try:
        from urllib.parse import urlparse

        host = (urlparse(target_url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False

    from lazyclaw.heartbeat.daemon import _needs_live_browser

    user_extras: frozenset[str] = frozenset()
    if user_id:
        try:
            from lazyclaw.browser.browser_settings import get_live_hosts
            from lazyclaw.config import load_config

            user_extras = frozenset(
                await get_live_hosts(load_config(), user_id)
            )
        except Exception:
            logger.debug(
                "Could not load user live_hosts for CF guard (user=%s)",
                user_id, exc_info=True,
            )
            user_extras = frozenset()

    return _needs_live_browser(host, user_extras)


@dataclass(frozen=True)
class TabInfo:
    """Immutable info about a browser tab."""

    id: str
    title: str
    url: str
    active: bool = False


class CDPBackend:
    """Real Chrome browser control via Chrome DevTools Protocol.

    On-demand: connects lazily when first used, auto-disconnects on idle.
    Does NOT close the user's browser — only disconnects the WebSocket.
    """

    def __init__(
        self,
        port: int = 9222,
        profile_dir: str | None = None,
        user_id: str | None = None,
        account_slug: str | None = None,
    ) -> None:
        self._port = port
        self._profile_dir = profile_dir  # Shared with Playwright when set
        self._user_id = user_id           # When set, action events are published
        self._account_slug = account_slug  # Multi-account browser identity (Phase A)
        self._conn: CDPConnection | None = None
        self._current_tab: CDPTab | None = None
        self._last_activity: float = 0.0
        self._connect_lock = asyncio.Lock()
        self._last_thumb_url: str | None = None  # Throttle thumb captures per URL change
        # Source of the active CDP connection: "host" (user's real Brave on the
        # host via host.docker.internal), "local" (container-launched Brave),
        # or None until the first connect. Gates apply_stealth and drives the
        # UI badge so the user knows which browser identity the agent is using.
        self._cdp_source: str | None = None
        # Phase A — per-domain cadence: derived from the active tab's URL on
        # every action via :meth:`_active_cadence`. ``_cadence_overrides`` is
        # the user's persisted per-domain factor map (lazy-loaded from
        # browser_settings on first use; agent runtime can refresh via
        # :meth:`set_cadence_overrides`).
        self._current_domain: str | None = None
        self._cadence_overrides: dict[str, dict[str, float]] = {}
        # Target ids this backend CREATED via ``_create_tab`` (cold-connect with
        # zero open page tabs). Consumed once by ``_pick_preferred_tab`` to mark
        # the pinned agent tab as agent-CREATED (→ eligible for turn-end
        # auto-close) rather than BORROWED. A fresh empty-Brave tab is the
        # agent's own — closing it at turn-end is safe. (2026-06-09)
        self._just_created_ids: set[str] = set()

    def set_user_id(self, user_id: str | None) -> None:
        """Late-bind user_id (used by the shared singleton when user switches)."""
        self._user_id = user_id

    def set_account_slug(self, account_slug: str | None) -> None:
        """Late-bind the multi-account browser identity."""
        self._account_slug = account_slug

    def set_cadence_overrides(
        self, overrides: dict[str, dict[str, float]] | None,
    ) -> None:
        """Push the user's per-domain cadence factor map.

        Agent runtime calls this after loading the user's browser
        settings; subsequent click/type/scroll calls sample from the
        adjusted ranges. Pass ``None`` or empty dict to clear.
        """
        self._cadence_overrides = dict(overrides) if overrides else {}

    def _set_current_domain_from_url(self, url: str | None) -> None:
        """Update ``_current_domain`` from a CDP-reported URL.

        Called internally on tab activation / navigation. Failures are
        non-fatal — leaves the previous domain in place rather than
        silently switching to DEFAULT cadence on a malformed URL.
        """
        if not url:
            return
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            host = host.lower()
            if host.startswith("www."):
                host = host[4:]
            if host:
                self._current_domain = host
        except Exception:
            logger.debug("cdp_backend._set_current_domain_from_url failed", exc_info=True)

    def _active_cadence(self):
        """Resolve the cadence profile for the current domain.

        Returns a :class:`CadenceProfile`. Lookup chain:
        user-override (per-domain factors) → DOMAIN_OVERRIDES → DEFAULT.
        """
        from lazyclaw.browser.cadence import get_cadence
        return get_cadence(self._current_domain, self._cadence_overrides or None)

    def _emit(
        self,
        kind: str,
        action: str | None = None,
        target: str | None = None,
        url: str | None = None,
        title: str | None = None,
        detail: str | None = None,
        extra: dict | None = None,
    ) -> None:
        """Publish a browser event. No-op if no user_id is bound."""
        if not self._user_id:
            return
        try:
            from lazyclaw.browser.event_bus import BrowserEvent, is_live_mode, publish
            publish(BrowserEvent(
                user_id=self._user_id,
                kind=kind,
                action=action,
                target=target,
                url=url,
                title=title,
                detail=detail,
                extra=extra,
            ))
            # In live mode, schedule a fresh thumbnail after every action so
            # the canvas reflects what the agent actually sees, not a stale frame.
            if kind == "action" and is_live_mode(self._user_id):
                try:
                    asyncio.get_running_loop().create_task(self._maybe_live_capture())
                except RuntimeError:
                    # No running loop (sync context) — safe to skip
                    pass
        except Exception:
            logger.debug("Browser event publish failed (non-fatal)", exc_info=True)

    async def _capture_thumbnail(self, url: str | None, force: bool = False) -> None:
        """Capture a small WebP thumbnail and store it in the event bus.

        Throttled: only captures when URL changes (or when force=True / live mode).
        Uses Pillow if available, else stores the raw PNG.
        """
        if not self._user_id or not self._conn:
            return
        if not force and url and url == self._last_thumb_url:
            return
        try:
            # Use the inline path (don't call self.screenshot to avoid emitting
            # a fake "screenshot" action event for the agent log).
            params: dict = {"format": "png", "quality": 80}
            result = await self._conn.send("Page.captureScreenshot", params)
            png_bytes = base64.b64decode(result.get("data", ""))
            if not png_bytes:
                return
            thumb_bytes = png_bytes
            try:
                import io
                from PIL import Image
                img = Image.open(io.BytesIO(png_bytes))
                if img.width > 640:
                    ratio = 640.0 / img.width
                    img = img.resize((640, int(img.height * ratio)))
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=70)
                thumb_bytes = buf.getvalue()
            except Exception:
                logger.debug("Pillow downscale unavailable, storing PNG as-is", exc_info=True)
            from lazyclaw.browser.event_bus import set_thumbnail
            set_thumbnail(self._user_id, thumb_bytes, url=url)
            self._last_thumb_url = url
        except Exception:
            logger.debug("Thumbnail capture failed", exc_info=True)

    async def _maybe_live_capture(self) -> None:
        """If live mode is on, capture a thumbnail right now.

        Called after every user-visible action when the user is actively
        watching the canvas. No-op when live mode is off (zero overhead).
        """
        if not self._user_id:
            return
        try:
            from lazyclaw.browser.event_bus import is_live_mode
            if not is_live_mode(self._user_id):
                return
        except Exception:
            return
        try:
            url = await self.current_url()
        except Exception:
            url = None
        # Force capture even if URL is unchanged — that's the whole point of live mode.
        asyncio.create_task(self._capture_thumbnail(url, force=True))

    async def _grab_frame_b64(self, max_width: int = 480) -> str | None:
        """Capture the current viewport as a base64-encoded WebP.

        Used for per-action before/after flipbook frames. Returns None on
        any failure — the caller should continue the action regardless, since
        frames are a UX nicety, never a correctness requirement.
        """
        if not self._conn:
            return None
        try:
            result = await self._conn.send(
                "Page.captureScreenshot", {"format": "png", "quality": 75},
            )
            raw = result.get("data", "")
            if not raw:
                return None
            try:
                import io as _io
                from PIL import Image
                img = Image.open(_io.BytesIO(base64.b64decode(raw)))
                if img.width > max_width:
                    ratio = max_width / img.width
                    img = img.resize((max_width, int(img.height * ratio)))
                buf = _io.BytesIO()
                img.save(buf, format="WEBP", quality=65)
                return base64.b64encode(buf.getvalue()).decode("ascii")
            except Exception:
                # Fall back to the original PNG if Pillow isn't available /
                # chokes on the buffer — smaller is nice but correctness wins.
                return raw
        except Exception:
            logger.debug("frame grab failed (non-fatal)", exc_info=True)
            return None

    async def _emit_action_with_frames(
        self,
        *,
        action: str,
        target: str | None,
        detail: str | None,
        coro,
        url: str | None = None,
        title: str | None = None,
    ):
        """Wrap a CDP input coroutine with pre/post frame capture.

        Frames are captured only when the user is in live mode — no cost
        to background sessions. The existing ``kind="action"`` event is
        still emitted after the action finishes so downstream consumers
        that only care about action tracking don't regress.
        """
        capture_frames = False
        if self._user_id:
            try:
                from lazyclaw.browser.event_bus import is_live_mode
                capture_frames = is_live_mode(self._user_id)
            except Exception:
                capture_frames = False

        if capture_frames:
            pre = await self._grab_frame_b64()
            if pre:
                self._emit(
                    kind="frame", action=action, target=target,
                    extra={"phase": "pre", "frame_b64": pre},
                )

        # Redact single-char press_key targets — those are literal keystroke
        # content (never log typed text). Named keys (Enter/Tab/…) + CSS
        # selectors are safe to trace.
        safe_target = target
        if action == "press_key" and target and len(target) == 1:
            safe_target = "<char>"
        tab_id = self._current_tab.id[:24] if self._current_tab else "?"
        _t0 = time.monotonic()
        try:
            result = await coro
        except Exception as exc:
            logger.warning(
                "[browser] action=%s target=%s tab=%s status=error err=%s dur_ms=%d",
                action, safe_target, tab_id, type(exc).__name__,
                (time.monotonic() - _t0) * 1000.0,
            )
            raise
        logger.debug(
            "[browser] action=%s target=%s tab=%s status=ok dur_ms=%d",
            action, safe_target, tab_id, (time.monotonic() - _t0) * 1000.0,
        )

        if capture_frames:
            post = await self._grab_frame_b64()
            if post:
                self._emit(
                    kind="frame", action=action, target=target,
                    extra={"phase": "post", "frame_b64": post},
                )

        self._emit(
            kind="action", action=action, target=target,
            url=url, title=title, detail=detail,
        )
        return result

    def _pick_preferred_tab(self, page_tabs: list["CDPTab"]) -> "CDPTab":
        """Pick the foreground agent's tab, EXCLUDING tabs a background lane owns.

        Resolution order (VISIBLE/foreground lane):
          1. REUSE the pinned ``"agent"`` tab if it's still open AND a
             watcher/background lane hasn't since claimed it — so the agent
             always returns to ITS tab turn-to-turn and a stray tab can't
             shuffle it onto the wrong page (the reported collision).
          2. Otherwise pick the MRU page tab, excluding any tab a background
             watcher/cron lane owns (those are parked tabs — the foreground
             must never clobber one). This freshly-picked tab is a BORROWED
             user tab; the caller pins it as the ``"agent"`` tab WITHOUT marking
             it created, so the turn-end auto-close never closes a tab the agent
             didn't create.
          3. Fall back to the raw MRU tab when EVERY open tab is owned
             (correctness over isolation — the foreground still works).

        A background backend transiently runs this too, but it immediately
        re-pins to its own tab via ``switch_tab`` after connecting, so the
        transient pick is harmless — the agent-pin path below is gated to the
        visible lane only.
        """
        from lazyclaw.browser import owned_tabs

        # Tabs a watcher/background lane owns (EXCLUDING the agent key). The
        # foreground must never land on one of these — even if it was once the
        # agent tab and a watcher has since claimed the same id.
        bg_anchored = owned_tabs.anchored_target_ids_excluding_agent(
            self._user_id
        )

        # 1. Reuse the pinned agent tab when it's still open and NOT now
        # background/watcher-owned (visible lane only).
        if self._is_visible_lane():
            agent_id = owned_tabs.get_owned(self._user_id, owned_tabs.AGENT_KEY)
            if agent_id and agent_id not in bg_anchored:
                for t in page_tabs:
                    if t.id == agent_id:
                        return t

        anchored = owned_tabs.all_owned_target_ids(self._user_id)
        picked: "CDPTab" | None = None
        if anchored:
            preferred = [t for t in page_tabs if t.id not in anchored]
            if preferred:
                picked = preferred[0]
        if picked is None:
            picked = page_tabs[0]

        # 2. Pin the freshly-picked tab as the agent's tab (visible lane only,
        # and only when it isn't a background-owned tab — the all-owned
        # fallback above can land on one). A tab this backend just CREATED
        # (cold-connect on an empty Brave) is agent-created → closeable; any
        # pre-existing tab is BORROWED → never auto-closed.
        if self._is_visible_lane() and picked.id not in anchored:
            created = picked.id in self._just_created_ids
            self._just_created_ids.discard(picked.id)
            self._pin_agent_tab(picked.id, created=created)
        return picked

    def _is_visible_lane(self) -> bool:
        """True when this backend is serving the VISIBLE/foreground lane.

        The pinning logic must apply ONLY to the foreground agent — never to a
        background watcher/cron backend (which owns its own parked tab). We're
        permissive on import failure (treat as visible) so a missing import
        can never break tab selection; the background backend re-pins itself
        anyway via ``switch_tab``.
        """
        try:
            from lazyclaw.runtime.browser_turn_lock import (
                BACKGROUND_ROLE,
                current_browser_role,
            )
        except Exception:
            return True
        return current_browser_role() != BACKGROUND_ROLE

    def _pin_agent_tab(self, target_id: str, *, created: bool) -> None:
        """Register *target_id* as the foreground agent's owned tab.

        ``created=True`` records it as agent-CREATED (eligible for turn-end
        auto-close); ``created=False`` marks it BORROWED (never auto-closed).
        Best-effort — a registry hiccup must never break tab selection.
        """
        try:
            from lazyclaw.browser import owned_tabs

            owned_tabs.set_owned(self._user_id, owned_tabs.AGENT_KEY, target_id)
            if created:
                owned_tabs.mark_agent_created(self._user_id, target_id)
        except Exception:
            logger.debug("pin agent tab failed (non-fatal)", exc_info=True)

    # Cosmetic prefix so ``tabs()`` (and the user's tab strip) shows which tab
    # the foreground agent is driving. Idempotent — never double-prefixes.
    _AGENT_TITLE_MARKER = "lazyclaw-agent"

    async def _mark_agent_tab_title(self) -> None:
        """Best-effort: prefix the visible agent tab's title with a marker.

        Purely cosmetic so ``tabs()`` surfaces the agent's tab. ALWAYS wrapped
        — a title-set failure must NEVER fail the action. No-op off the visible
        lane or when no connection is live.
        """
        if not self._is_visible_lane() or self._conn is None:
            return
        try:
            marker = _js_str(self._AGENT_TITLE_MARKER)
            expr = (
                "(() => { const m = " + marker + "; "
                "const t = document.title || ''; "
                "if (!t.startsWith('[' + m + '] ')) "
                "{ document.title = '[' + m + '] ' + t; } "
                "return document.title; })()"
            )
            await self._conn.send(
                "Runtime.evaluate",
                {"expression": expr, "returnByValue": True},
            )
        except Exception:
            logger.debug("agent tab title marker failed (non-fatal)", exc_info=True)

    async def _ensure_connected(
        self, target_url: str | None = None,
    ) -> CDPConnection:
        """Lazy connect: discover Chrome and connect to active tab.

        ``target_url`` (when known by the caller, e.g. ``goto``) lets us
        guard Cloudflare-protected navigations: if the host browser can't
        be reached and the upcoming nav target is CF-sensitive
        (upwork.com / linkedin.com / user ``live_hosts``), we raise
        instead of silently falling through to the wrong-identity
        container browser (which fails CF's fingerprint with empty
        results and no error).

        Resolution order (see host_bridge.find_cdp_with_preference):
          1. If the user opted in to host-browser mode, probe
             ``host.docker.internal:{port}`` — that's their real Brave with
             their cookies, saved logins, extensions.
          2. Probe ``localhost:{port}`` for an existing CDP endpoint.
          3. Auto-launch a headless Chromium inside the container.

        When connected via the host, we also skip ``apply_stealth`` (their
        real browser should look like themselves, not a fake mobile UA) and
        send the Origin header matching ``--remote-allow-origins``.

        Uses asyncio.Lock to prevent duplicate Chrome launches from
        concurrent coroutines.
        """
        # Fast path: already connected (no lock needed)
        if self._conn and self._conn.is_connected:
            self._last_activity = time.monotonic()
            return self._conn

        async with self._connect_lock:
            # Re-check after acquiring lock (another coroutine may have connected)
            if self._conn and self._conn.is_connected:
                self._last_activity = time.monotonic()
                return self._conn

            prefer_host, host_token = await self._resolve_host_preference()
            ws_url, source = await self._find_cdp_endpoint(prefer_host, host_token)

            # Self-heal: bridge is armed but the host probe just failed. Write
            # a repair flag (so a host-side watcher can react) and retry once
            # with a short backoff — covers the "launchd just kicked Brave but
            # the port isn't bound yet" race on cold macOS starts.
            if prefer_host and source != "host":
                try:
                    from lazyclaw.config import load_config

                    cfg = load_config()
                    flag_path = cfg.database_dir / ".host_bridge_repair_needed"
                    flag_path.write_text(
                        f"requested_at={int(time.time())}\nreason=cdp_unreachable_port_{self._port}\n",
                        encoding="utf-8",
                    )
                except Exception:
                    logger.debug("Could not write host-bridge repair flag", exc_info=True)

                for delay in (2.0, 3.0):
                    await asyncio.sleep(delay)
                    ws_url, source = await self._find_cdp_endpoint(prefer_host, host_token)
                    if source == "host":
                        break

                # Bridge came back up — clear the flag.
                if source == "host":
                    try:
                        from lazyclaw.config import load_config as _lc

                        flag_path = _lc().database_dir / ".host_bridge_repair_needed"
                        if flag_path.exists():
                            flag_path.unlink()
                    except Exception:
                        pass

            # Cloudflare guard: the host browser is still unreachable after
            # retries. For CF-sensitive targets (upwork.com / linkedin.com /
            # user live_hosts) we MUST NOT fall through to the container
            # Brave — it fails Cloudflare's fingerprint silently (empty
            # extractor results, no error) and the caller can never tell.
            # Raise the standard "Cannot reach Brave" RuntimeError the
            # host-bridge heal endpoint keys on so recovery can kick in.
            # Non-CF hosts keep the existing fallback behavior unchanged.
            if (
                prefer_host
                and source != "host"
                and await _target_needs_host_browser(target_url, self._user_id)
            ):
                from urllib.parse import urlparse as _urlparse

                _host = (_urlparse(target_url or "").hostname or "").lower()
                from lazyclaw.browser.host_bridge import HOST_GATEWAY_HOSTNAME

                logger.error(
                    "CF guard: host Brave unreachable on %s:%s — refusing to "
                    "drive container browser to Cloudflare-protected host %s",
                    HOST_GATEWAY_HOSTNAME, self._port, _host,
                )
                raise RuntimeError(
                    f"Cannot reach Brave: the Cloudflare-protected host "
                    f"'{_host}' must be driven through your signed-in Brave on "
                    f"port {self._port}, but the host browser is unreachable. "
                    "A fresh container browser would be blocked by Cloudflare "
                    "and silently return empty results. Start/repair the host "
                    "Brave bridge (e.g. `make host-bridge`) and retry."
                )

            origin: str | None = None
            if source == "host" and host_token:
                # Chromium 147+ rejects WS handshakes that send a custom
                # Origin header even when --remote-allow-origins is set to
                # match — server returns HTTP 403 ("origin not allowed").
                # When the connection arrives over loopback (we go through
                # the host-side TCP forwarder so Brave sees 127.0.0.1),
                # Chromium auto-allows requests WITHOUT an Origin header.
                # So leave origin=None for host-bridge — the forwarder is
                # the access-control layer (bound to a Docker-only iface
                # so LAN traffic can't reach it).
                #
                # Empirically verified 2026-05-04 with Brave 147.0.7727.137:
                #   WITHOUT Origin: handshake succeeds
                #   WITH    Origin: HTTP 403 InvalidStatus

                # ``ws_url`` from probe_host_cdp is the BROWSER-level WS
                # (``/devtools/browser/{id}``), which only exposes
                # ``Browser.*``, ``Target.*`` and ``Tracing.*`` domains.
                # ``Page.enable`` and ``Runtime.evaluate`` only exist on
                # PAGE-level WS endpoints (``/devtools/page/{id}``). List
                # the host's open tabs and switch to a page-level WS so
                # downstream skills (action_open, screenshot, etc.) work.
                from lazyclaw.browser.host_bridge import HOST_GATEWAY_HOSTNAME

                page_tabs = await list_chrome_tabs(
                    port=self._port, host=HOST_GATEWAY_HOSTNAME,
                )
                page_tabs = [t for t in page_tabs if t.tab_type == "page"]
                if not page_tabs:
                    raise ConnectionError(
                        f"Host Brave reachable on {HOST_GATEWAY_HOSTNAME}:{self._port} "
                        "but reports zero page tabs. Open a tab in your Brave window."
                    )
                # Use the most recently focused tab (Chromium returns tabs
                # in MRU order in /json, with the active one first).
                self._current_tab = self._pick_preferred_tab(page_tabs)
                ws_url = self._current_tab.ws_url
                logger.info(
                    "Host bridge: switched to page-level WS for tab %r (%s)",
                    self._current_tab.title[:40] or "(no title)",
                    self._current_tab.url[:60] or "(no url)",
                )
            elif source == "local":
                # Connect to the first (active) page tab using the regular
                # discovery endpoint so CDPTab metadata comes through.
                tabs = await list_chrome_tabs(self._port)
                page_tabs = [t for t in tabs if t.tab_type == "page"]

                if not page_tabs:
                    logger.info("Chrome has no tabs, creating a new one")
                    page_tabs = await self._create_tab()
                    if not page_tabs:
                        raise ConnectionError("Chrome has no open tabs and failed to create one.")

                self._current_tab = self._pick_preferred_tab(page_tabs)
                ws_url = self._current_tab.ws_url
            else:
                # Nothing reachable — auto-launch the container's own Brave.
                ws_url = await self._auto_launch_chrome()
                if not ws_url:
                    raise ConnectionError(
                        f"Could not launch Chrome with debugging port {self._port}."
                    )
                source = "local"
                tabs = await list_chrome_tabs(self._port)
                page_tabs = [t for t in tabs if t.tab_type == "page"]
                if not page_tabs:
                    page_tabs = await self._create_tab()
                    if not page_tabs:
                        raise ConnectionError("Chrome has no open tabs and failed to create one.")
                self._current_tab = self._pick_preferred_tab(page_tabs)
                ws_url = self._current_tab.ws_url

            self._conn = CDPConnection()
            # Timeout — a hung WS handshake (bad port, firewall, zombie
            # Chrome) used to strand the whole agent because ``connect``
            # had no deadline. 15 s is well above any legitimate local
            # connect (sub-second in practice).
            try:
                await asyncio.wait_for(
                    self._conn.connect(ws_url, origin=origin),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "CDP connect timed out after 15s (ws=%s source=%s)",
                    ws_url, source,
                )
                self._conn = None
                raise ConnectionError(
                    f"CDP connect timeout after 15s (source={source})"
                )
            self._cdp_source = source

            # Enable required CDP domains
            await self._conn.send("Page.enable")
            await self._conn.send("Runtime.enable")

            # Subscribe to Network events for the inspector ring buffer
            await self._subscribe_network_events()

            # Apply stealth only on the container Brave. On the user's real
            # browser stealth would trip anomaly detection (Gmail, banks) and
            # persist weird navigator overrides for the debug session.
            if source == "local":
                try:
                    from lazyclaw.browser.stealth import apply_stealth
                    await apply_stealth(self._conn)
                except Exception as exc:
                    logger.warning("Stealth injection failed (non-fatal): %s", exc)
            else:
                logger.info("Connected to host browser — skipping stealth injection")

            # Record the resolved source so status endpoints can show it.
            await self._persist_last_host_source(source)

            # Let the UI know which browser identity we're driving.
            self._emit(
                kind="host_cdp",
                detail=f"CDP source: {source}",
                extra={"source": source},
            )

            self._last_activity = time.monotonic()
            logger.info(
                "CDP connected (source=%s) to tab: %s (%s)",
                source, self._current_tab.title, self._current_tab.url,
            )
            return self._conn

    async def _resolve_host_preference(self) -> tuple[bool, str | None]:
        """Read the user's host-browser setting + token.

        Token resolution: shared env var (LAZYCLAW_HOST_CDP_TOKEN, set by
        the launchd installer) wins over per-user DB token. The env-var
        path is the "single Mac, single user" common case after running
        ``scripts/install-host-brave-bridge.sh`` — the same token sits in
        the launchd plist's ``--remote-allow-origins`` flag and in .env,
        so the handshake matches without any per-user setup. The DB
        token still works for legacy / multi-user setups.

        Best-effort — any error means we default to container behaviour so
        the browser skill never fails for settings-lookup reasons.
        """
        from lazyclaw.browser.host_bridge import shared_host_token

        env_token = shared_host_token()

        if not self._user_id:
            # No user_id (CLI / startup probe). Use the shared env token
            # if present so the bridge still works for non-user contexts.
            return (bool(env_token), env_token)
        try:
            from lazyclaw.browser.browser_settings import get_browser_settings
            from lazyclaw.config import load_config

            settings = await get_browser_settings(load_config(), self._user_id)
            mode = settings.get("use_host_browser", "off")
            db_token = settings.get("host_cdp_token")
            # Env wins. Fall back to DB token for legacy installs.
            token = env_token or db_token
            # When the env token is set, treat the bridge as armed —
            # the user installed the launchd helper, the intent is clear.
            prefer_host = bool(env_token) or (
                mode in ("auto", "ask") and bool(db_token)
            )
            return prefer_host, token
        except Exception:
            logger.debug("host-browser settings lookup failed (falling back to local)", exc_info=True)
            # Even on settings lookup failure, env token is enough to use the bridge.
            return (bool(env_token), env_token)

    async def _find_cdp_endpoint(
        self, prefer_host: bool, host_token: str | None,
    ) -> tuple[str | None, str]:
        """Delegate to host_bridge.find_cdp_with_preference.

        Kept as a thin method wrapper so tests can patch it without pulling
        in httpx network calls.
        """
        from lazyclaw.browser.host_bridge import find_cdp_with_preference

        return await find_cdp_with_preference(
            port=self._port, prefer_host=prefer_host, token=host_token,
        )

    async def _persist_last_host_source(self, source: str) -> None:
        """Record the last-resolved CDP source for diagnostics in /status."""
        if not self._user_id:
            return
        try:
            from lazyclaw.browser.browser_settings import update_browser_settings
            from lazyclaw.config import load_config

            await update_browser_settings(
                load_config(), self._user_id, {"last_host_cdp_source": source},
            )
        except Exception:
            logger.debug("persist last_host_cdp_source failed (non-fatal)", exc_info=True)

    async def _auto_launch_chrome(self) -> str | None:
        """Launch headless browser with remote debugging. Returns CDP ws_url or None.

        Auto-detects Brave > Chrome > Chromium. Uses the shared profile
        directory (same as Playwright) so cookies persist between both engines.
        Kills stale processes on the debugging port before launching.
        """
        import os

        from lazyclaw.config import load_config

        config = load_config()
        chrome_bin = config.browser_executable

        if not chrome_bin:
            logger.warning("No browser found (Brave/Chrome/Chromium), cannot auto-launch")
            return None

        # Kill any stale headless browser hogging the debugging port. pkill is
        # only present in fat container images / hosts — slim Python images
        # don't ship procps. Skip cleanly when missing rather than logging a
        # full traceback every launch.
        import shutil

        pkill_bin = shutil.which("pkill")
        if pkill_bin:
            try:
                kill_proc = await asyncio.create_subprocess_exec(
                    pkill_bin, "-f", f"--remote-debugging-port={self._port}",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await kill_proc.wait()
                await asyncio.sleep(0.5)
            except Exception:
                logger.warning("Failed to kill existing browser process on port %d", self._port, exc_info=True)
        else:
            logger.debug("pkill not on PATH — skipping stale-browser cleanup (container image)")

        # Use shared profile dir (same as Playwright) for cookie sharing,
        # or fall back to a persistent temp dir
        if self._profile_dir:
            profile_dir = self._profile_dir
            os.makedirs(profile_dir, exist_ok=True)
        else:
            import tempfile
            profile_dir = os.path.join(tempfile.gettempdir(), "lazyclaw-cdp-profile")

        # Clean stale profile locks (from crashed/killed containers or processes)
        for lock_file in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            lock_path = os.path.join(profile_dir, lock_file)
            try:
                if os.path.exists(lock_path) or os.path.islink(lock_path):
                    os.unlink(lock_path)
            except OSError:
                logger.debug("Failed to remove stale browser lock file: %s", lock_path, exc_info=True)

        # Auto-load LazyClaw ref engine extension (silent, no user prompt)
        ext_path = str(Path(__file__).parent / "extension")

        from lazyclaw.browser.stealth import STEALTH_LAUNCH_ARGS

        cmd = [
            chrome_bin,
            "--headless=new",
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            *STEALTH_LAUNCH_ARGS,
            f"--load-extension={ext_path}",
            f"--disable-extensions-except={ext_path}",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info(
                "Launched headless Chrome (pid=%d, port=%d, profile=%s)",
                proc.pid, self._port, profile_dir,
            )
            # Wait for Chrome to start accepting connections
            for i in range(6):
                await asyncio.sleep(0.5)
                # Check if process died (zombie reaping)
                if proc.returncode is not None:
                    stderr_bytes = await proc.stderr.read() if proc.stderr else b""
                    stderr_tail = stderr_bytes[-500:].decode("utf-8", errors="replace")
                    logger.error(
                        "Chrome exited with code %d after %.1fs. stderr: %s",
                        proc.returncode, (i + 1) * 0.5, stderr_tail,
                    )
                    break
                ws_url = await find_chrome_cdp(self._port)
                if ws_url:
                    return ws_url
            else:
                logger.warning("Chrome launched but CDP not responding after 3s")
        except Exception as exc:
            logger.error("Failed to launch Chrome: %s", exc)

        return None

    async def _create_tab(self, url: str = "about:blank") -> list[CDPTab]:
        """Create a new tab via Chrome's /json/new endpoint."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.put(
                    f"http://localhost:{self._port}/json/new?{url}"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tab = CDPTab(
                        id=data.get("id", ""),
                        title=data.get("title", ""),
                        url=data.get("url", url),
                        ws_url=data.get("webSocketDebuggerUrl", ""),
                        tab_type=data.get("type", "page"),
                    )
                    if tab.ws_url:
                        logger.info("Created new tab: %s", tab.url)
                        if tab.id:
                            # Mark as agent-created so the visible-lane pin in
                            # _pick_preferred_tab records it created (closeable).
                            self._just_created_ids.add(tab.id)
                        return [tab]
        except Exception as exc:
            logger.warning(
                "[browser] action=create_tab host=%s status=error err=%s: %s",
                _log_host(url), type(exc).__name__, exc,
            )
        return []

    async def goto(self, url: str) -> None:
        _nav_host = _log_host(url)
        _nav_t0 = time.monotonic()
        logger.debug("[browser] action=goto host=%s status=start", _nav_host)
        conn = await self._ensure_connected(target_url=url)

        self._emit(
            kind="navigate", action="goto", url=url,
            detail=f"Going to {url}",
        )

        # SPA hash navigation: if same origin but different hash/path,
        # Page.navigate won't work (Gmail, Twitter, etc. ignore it).
        # Use JS window.location.href instead, which SPAs do handle.
        current = await self.current_url()
        if _is_same_origin_nav(current, url):
            logger.info("SPA navigation detected: %s → %s", current[:60], url[:60])
            await conn.send(
                "Runtime.evaluate",
                {
                    "expression": f"window.location.href = {_js_str(url)};",
                    "awaitPromise": False,
                },
            )
            # SPAs need time to render after hash change (Gmail: 1-2s)
            await asyncio.sleep(random.uniform(1.5, 2.5))
            await self._post_nav_emit(url)
            logger.debug(
                "[browser] action=goto host=%s status=ok nav=spa dur_ms=%d",
                _nav_host, (time.monotonic() - _nav_t0) * 1000.0,
            )
            return

        await conn.send("Page.navigate", {"url": url})
        # Human-like wait for page load (0.8-1.5s). Page.waitForNavigation isn't
        # a real CDP method (Playwright invented it on top of frame events) —
        # wait_for_page_ready below listens for the proper Page.lifecycleEvent /
        # Page.frameStoppedLoading signals instead.
        await asyncio.sleep(random.uniform(0.8, 1.5))

        # Wait for DOM to settle + auto-solve Cloudflare if detected
        try:
            from lazyclaw.browser.stealth import detect_and_solve_cloudflare, wait_for_page_ready

            await wait_for_page_ready(conn, timeout=5.0)
            await detect_and_solve_cloudflare(conn, timeout=20.0)
        except Exception:
            logger.warning(
                "[browser] page-ready/Cloudflare handling failed after nav host=%s",
                _nav_host, exc_info=True,
            )

        await self._post_nav_emit(url)
        logger.debug(
            "[browser] action=goto host=%s status=ok nav=full dur_ms=%d",
            _nav_host, (time.monotonic() - _nav_t0) * 1000.0,
        )

    async def _post_nav_emit(self, requested_url: str) -> None:
        """Emit navigate event with final URL+title and capture a thumbnail."""
        try:
            final_url = await self.current_url()
        except Exception:
            final_url = requested_url
        # Update the active domain so subsequent click/type/scroll calls
        # sample from the right per-domain cadence profile.
        self._set_current_domain_from_url(final_url)
        # Cosmetic agent-tab marker (visible lane only, fully best-effort).
        await self._mark_agent_tab_title()
        if not self._user_id:
            return
        try:
            page_title = await self.title()
        except Exception:
            page_title = None
        self._emit(
            kind="navigate", action="goto", url=final_url, title=page_title,
            detail=f"Loaded {page_title or final_url}",
        )
        # Fire-and-forget thumbnail (don't block the agent loop)
        asyncio.create_task(self._capture_thumbnail(final_url))

    async def current_url(self) -> str:
        conn = await self._ensure_connected()
        result = await conn.send(
            "Runtime.evaluate",
            {"expression": "window.location.href", "returnByValue": True},
        )
        return result.get("result", {}).get("value", "")

    async def title(self) -> str:
        conn = await self._ensure_connected()
        result = await conn.send(
            "Runtime.evaluate",
            {"expression": "document.title", "returnByValue": True},
        )
        return result.get("result", {}).get("value", "")

    async def content(self) -> str:
        conn = await self._ensure_connected()
        result = await conn.send(
            "Runtime.evaluate",
            {
                "expression": "document.documentElement.outerHTML",
                "returnByValue": True,
            },
        )
        return result.get("result", {}).get("value", "")

    async def evaluate(self, js: str) -> Any:
        conn = await self._ensure_connected()
        result = await conn.send(
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True, "awaitPromise": True},
        )
        inner = result.get("result", {})
        if inner.get("type") == "undefined":
            return None
        return inner.get("value", inner.get("description", ""))

    async def screenshot(self, full_page: bool = False) -> bytes:
        _shot_t0 = time.monotonic()
        conn = await self._ensure_connected()
        params: dict = {"format": "png", "quality": 80}
        if full_page:
            # Get full page dimensions
            metrics = await conn.send("Page.getLayoutMetrics")
            content_size = metrics.get("contentSize", {})
            if content_size:
                params["clip"] = {
                    "x": 0, "y": 0,
                    "width": content_size.get("width", 1280),
                    "height": content_size.get("height", 720),
                    "scale": 1,
                }
        result = await conn.send("Page.captureScreenshot", params)
        b64_data = result.get("data", "")
        png_bytes = base64.b64decode(b64_data)
        # Update thumbnail cache opportunistically. Don't recurse — the
        # thumbnail helper calls screenshot() too, but with throttling.
        if self._user_id and not full_page:
            try:
                from lazyclaw.browser.event_bus import set_thumbnail
                try:
                    cur_url = await self.current_url()
                except Exception:
                    cur_url = None
                # Cheap downscale path inline to avoid circular call
                try:
                    import io
                    from PIL import Image
                    img = Image.open(io.BytesIO(png_bytes))
                    if img.width > 640:
                        ratio = 640.0 / img.width
                        img = img.resize((640, int(img.height * ratio)))
                    buf = io.BytesIO()
                    img.save(buf, format="WEBP", quality=70)
                    set_thumbnail(self._user_id, buf.getvalue(), url=cur_url)
                except Exception:
                    set_thumbnail(self._user_id, png_bytes, url=cur_url)
            except Exception:
                logger.debug("Thumbnail cache update failed", exc_info=True)
        self._emit(
            kind="action", action="screenshot",
            detail="Captured screenshot",
        )
        # Log byte-count only (never the image bytes / page content).
        logger.debug(
            "[browser] action=screenshot tab=%s full_page=%s bytes=%d dur_ms=%d",
            self._current_tab.id[:24] if self._current_tab else "?",
            full_page, len(png_bytes), (time.monotonic() - _shot_t0) * 1000.0,
        )
        return png_bytes

    async def click(self, selector: str) -> None:
        conn = await self._ensure_connected()
        # Find element center coordinates and size via JS
        js = f"""
        (() => {{
            const el = document.querySelector({_js_str(selector)});
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            return {{
                x: rect.x + rect.width / 2,
                y: rect.y + rect.height / 2,
                w: rect.width, h: rect.height
            }};
        }})()
        """
        result = await conn.send(
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True},
        )
        coords = result.get("result", {}).get("value")
        if not coords:
            raise ValueError(f"Element not found: {selector}")

        x, y = coords["x"], coords["y"]
        target_size = min(coords.get("w", 20), coords.get("h", 20))

        # Human-like click: Bezier movement + complete event chain
        from lazyclaw.browser.human_input import human_click
        await self._emit_action_with_frames(
            action="click", target=selector[:80],
            detail=f"Clicked {selector[:60]}",
            coro=human_click(
                conn, x, y, target_size=target_size,
                cadence=self._active_cadence(),
            ),
        )

    async def type_text(self, selector: str, text: str) -> None:
        conn = await self._ensure_connected()
        # Find element coordinates and click to focus (human-like)
        js = f"""
        (() => {{
            const el = document.querySelector({_js_str(selector)});
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            return {{x: rect.x + rect.width / 2, y: rect.y + rect.height / 2}};
        }})()
        """
        result = await conn.send(
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True},
        )
        coords = result.get("result", {}).get("value")

        # Type with human-like keystroke timing
        from lazyclaw.browser.human_input import human_type

        # Mask passwords/secrets in the visible detail line (build before the
        # emit wrapper so the pre-frame and action event share the same label)
        masked = text if len(text) <= 40 else text[:37] + "…"
        if any(s in selector.lower() for s in ("password", "passwd", "pin", "secret")):
            masked = "•" * min(len(text), 8)

        await self._emit_action_with_frames(
            action="type", target=selector[:80],
            detail=f"Typed '{masked}' into {selector[:40]}",
            coro=human_type(
                conn, text,
                field_x=coords["x"] if coords else None,
                field_y=coords["y"] if coords else None,
                cadence=self._active_cadence(),
            ),
        )

    async def press_key(self, key: str) -> None:
        """Press a keyboard key (Enter, Escape, Tab, Backspace, ArrowDown, etc)."""
        conn = await self._ensure_connected()
        # Map common names to CDP key identifiers
        key_map = {
            "enter": ("Enter", "\r", 13),
            "return": ("Enter", "\r", 13),
            "escape": ("Escape", "", 27),
            "esc": ("Escape", "", 27),
            "tab": ("Tab", "\t", 9),
            "backspace": ("Backspace", "", 8),
            "delete": ("Delete", "", 46),
            "arrowup": ("ArrowUp", "", 38),
            "arrowdown": ("ArrowDown", "", 40),
            "arrowleft": ("ArrowLeft", "", 37),
            "arrowright": ("ArrowRight", "", 39),
            "space": (" ", " ", 32),
        }
        lookup = key_map.get(key.lower().replace(" ", ""))
        if lookup:
            key_name, text, code = lookup
        else:
            # Single printable char → send as text; multi-char names (e.g.
            # "F5", "PageDown") are key identifiers, not literal text.
            key_name = key
            text = key if len(key) == 1 else ""
            code = ord(key.upper()) if len(key) == 1 else 0

        key_down = {
            "type": "keyDown",
            "key": key_name,
            "windowsVirtualKeyCode": code,
            "nativeVirtualKeyCode": code,
        }
        # CDP rejects text="" — only include when non-empty
        if text:
            key_down["text"] = text

        async def _do_press() -> None:
            await conn.send("Input.dispatchKeyEvent", key_down)
            await asyncio.sleep(random.uniform(0.03, 0.08))
            await conn.send("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": key_name,
                "windowsVirtualKeyCode": code,
                "nativeVirtualKeyCode": code,
            })

        await self._emit_action_with_frames(
            action="press_key", target=key,
            detail=f"Pressed {key}",
            coro=_do_press(),
        )

    async def scroll(self, direction: str = "down", amount: int = 300) -> None:
        conn = await self._ensure_connected()
        # Human-like scroll with momentum deceleration
        from lazyclaw.browser.human_input import human_scroll
        await self._emit_action_with_frames(
            action="scroll", target=direction,
            detail=f"Scrolled {direction} {amount}px",
            coro=human_scroll(
                conn, direction=direction, amount=amount,
                cadence=self._active_cadence(),
            ),
        )

    async def wait_for_selector(
        self, selector: str, timeout_ms: int = 5000,
    ) -> bool:
        conn = await self._ensure_connected()
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            result = await conn.send(
                "Runtime.evaluate",
                {
                    "expression": (
                        f"!!document.querySelector({_js_str(selector)})"
                    ),
                    "returnByValue": True,
                },
            )
            if result.get("result", {}).get("value"):
                return True
            await asyncio.sleep(0.2)
        return False

    def _tab_list_host(self) -> str:
        """Host the CDP ``/json`` tab-list endpoint lives on.

        In host-bridge mode (``_cdp_source == "host"``) the user's real Brave —
        and therefore its tab list — is reachable only via the Docker host
        gateway, NOT ``localhost`` (which inside the container resolves to the
        container itself and reports zero tabs). The connect path already uses
        the gateway (see :meth:`_ensure_connected`); ``tabs()`` and
        ``switch_tab()`` MUST match it, or every anchored-tab lookup silently
        misses and passive watchers re-create a parked tab on every poll.
        """
        if self._cdp_source == "host":
            from lazyclaw.browser.host_bridge import HOST_GATEWAY_HOSTNAME

            return HOST_GATEWAY_HOSTNAME
        return "localhost"

    async def tabs(self) -> list[TabInfo]:
        chrome_tabs = await list_chrome_tabs(self._port, host=self._tab_list_host())
        current_id = self._current_tab.id if self._current_tab else ""
        logger.debug(
            "[browser] action=tabs source=%s count=%d",
            self._cdp_source or "?", len(chrome_tabs),
        )
        return [
            TabInfo(
                id=t.id,
                title=t.title,
                url=t.url,
                active=(t.id == current_id),
            )
            for t in chrome_tabs
        ]

    async def switch_tab(self, tab_id: str, *, focus: bool = True) -> None:
        chrome_tabs = await list_chrome_tabs(self._port, host=self._tab_list_host())
        target = next((t for t in chrome_tabs if t.id == tab_id), None)
        if not target:
            logger.warning(
                "[browser] action=switch_tab tab=%s status=not_found (of %d tabs)",
                tab_id[:24], len(chrome_tabs),
            )
            raise ValueError(f"Tab not found: {tab_id}")
        logger.debug(
            "[browser] action=switch_tab tab=%s host=%s focus=%s",
            tab_id[:24], _log_host(target.url), focus,
        )

        # Close old connection, connect to new tab
        if self._conn:
            await self._conn.close()

        self._current_tab = target
        self._conn = CDPConnection()
        await self._conn.connect(target.ws_url)
        await self._conn.send("Page.enable")
        await self._conn.send("Runtime.enable")

        # Re-subscribe to Network events on the new connection — without this,
        # the inspector ring buffer silently stops after any tab switch.
        await self._subscribe_network_events()

        # Bring tab to front only when caller wants visible focus. Watcher
        # polls pass focus=False so the user's active tab isn't stolen
        # every 2 minutes.
        if focus:
            await self._conn.send("Page.bringToFront")

        logger.info(
            "%s to tab: %s (%s)",
            "Switched" if focus else "Attached",
            target.title, target.url,
        )

    async def _subscribe_network_events(self) -> None:
        """Enable CDP Network domain and wire handlers into the inspector.

        Called from both _ensure_connected and switch_tab — the connection
        object (and its _event_handlers dict) is fresh in both cases, so we
        never double-register.
        """
        if not self._conn:
            return
        try:
            await self._conn.send("Network.enable", {})
        except Exception:
            logger.debug("Network.enable failed (non-fatal)", exc_info=True)
            return

        from lazyclaw.browser import network_inspector

        user_id = self._user_id

        def on_request(params: dict) -> None:
            req = params.get("request", {}) or {}
            network_inspector.record_request(
                user_id,
                params.get("requestId", ""),
                req.get("url", ""),
                req.get("method", "GET"),
            )

        def on_response(params: dict) -> None:
            resp = params.get("response", {}) or {}
            network_inspector.record_response(
                user_id,
                params.get("requestId", ""),
                resp.get("status", 0),
                resp.get("mimeType"),
                resp.get("encodedDataLength"),
                resp.get("fromDiskCache", False) or resp.get("fromServiceWorker", False),
            )

        def on_finished(params: dict) -> None:
            network_inspector.record_finished(
                user_id,
                params.get("requestId", ""),
                params.get("encodedDataLength"),
            )

        def on_failed(params: dict) -> None:
            network_inspector.record_failed(
                user_id,
                params.get("requestId", ""),
                params.get("errorText"),
            )

        self._conn.register_event_handler("Network.requestWillBeSent", on_request)
        self._conn.register_event_handler("Network.responseReceived", on_response)
        self._conn.register_event_handler("Network.loadingFinished", on_finished)
        self._conn.register_event_handler("Network.loadingFailed", on_failed)

    # ── Accessibility tree ────────────────────────────────────────────

    async def accessibility_tree(self, max_depth: int = 8) -> str:
        """Get the page's accessibility tree as compact text.

        Returns semantic roles, names, and states — works on ANY site
        without custom JS extractors. Typically 2-5KB vs 50KB raw text.
        """
        conn = await self._ensure_connected()
        await conn.send("Accessibility.enable")
        result = await conn.send("Accessibility.getFullAXTree", {"depth": max_depth})
        nodes = result.get("nodes", [])

        lines: list[str] = []
        node_map = {n["nodeId"]: n for n in nodes}

        def _format_node(node: dict, depth: int = 0) -> None:
            role = node.get("role", {}).get("value", "")
            name = node.get("name", {}).get("value", "")
            props = node.get("properties", [])

            # Skip ignored/invisible nodes
            if role in ("none", "generic", "InlineTextBox", "LineBreak"):
                for cid in node.get("childIds", []):
                    child = node_map.get(cid)
                    if child:
                        _format_node(child, depth)
                return

            # Build compact line
            parts = [role]
            if name:
                parts.append(f'"{name}"')
            for prop in props:
                pname = prop.get("name", "")
                pval = prop.get("value", {}).get("value", "")
                if pname in ("checked", "selected", "disabled", "expanded",
                             "pressed", "required") and pval:
                    parts.append(f"{pname}={pval}")
                elif pname == "value" and pval:
                    parts.append(f'value="{str(pval)[:60]}"')

            line = "  " * depth + " ".join(parts)
            if line.strip():
                lines.append(line)

            for cid in node.get("childIds", []):
                child = node_map.get(cid)
                if child:
                    _format_node(child, depth + 1)

        # Start from root
        if nodes:
            _format_node(nodes[0], 0)

        tree = "\n".join(lines)
        # Log the READ action: node count + output size ONLY, never the tree
        # text (page content must not enter logs).
        logger.debug(
            "[browser] action=read tab=%s nodes=%d chars=%d",
            self._current_tab.id[:24] if self._current_tab else "?",
            len(nodes), len(tree),
        )
        return tree

    async def find_element_by_role(self, description: str) -> dict | None:
        """Find an element by role/label description and return its coordinates.

        Accepts natural descriptions like "Search input", "Submit button",
        "Send message". Returns {"x": float, "y": float, "role": str, "name": str} or None.
        """
        conn = await self._ensure_connected()
        await conn.send("Accessibility.enable")
        result = await conn.send("Accessibility.getFullAXTree", {"depth": 6})
        nodes = result.get("nodes", [])

        desc_lower = description.lower()
        best_match = None
        best_score = 0

        for node in nodes:
            role = node.get("role", {}).get("value", "").lower()
            name = node.get("name", {}).get("value", "").lower()
            backend_id = node.get("backendDOMNodeId")
            if not backend_id:
                continue

            # Score how well this node matches the description
            score = 0
            for word in desc_lower.split():
                if word in role:
                    score += 2
                if word in name:
                    score += 3

            if score > best_score:
                best_score = score
                best_match = (node, backend_id)

        if not best_match or best_score < 2:
            return None

        node, backend_id = best_match

        # Use JS to resolve backendNodeId to coordinates (more reliable than DOM.getBoxModel)
        try:
            resolve_result = await conn.send(
                "DOM.resolveNode", {"backendNodeId": backend_id}
            )
            object_id = resolve_result.get("object", {}).get("objectId")
            if not object_id:
                return None

            # Call getBoundingClientRect() on the resolved object
            box_result = await conn.send("Runtime.callFunctionOn", {
                "objectId": object_id,
                "functionDeclaration": """function() {
                    const rect = this.getBoundingClientRect();
                    return {x: rect.x + rect.width/2, y: rect.y + rect.height/2,
                            w: rect.width, h: rect.height};
                }""",
                "returnByValue": True,
            })
            coords = box_result.get("result", {}).get("value")
            if coords and coords.get("w", 0) > 0:
                return {
                    "x": coords["x"], "y": coords["y"],
                    "role": node.get("role", {}).get("value", ""),
                    "name": node.get("name", {}).get("value", ""),
                }
        except Exception as exc:
            logger.debug("find_element_by_role coordinate lookup failed: %s", exc)

        return None

    async def click_by_role(self, description: str) -> dict | None:
        """Find element by role/label and click it via DOM click().

        Uses the same accessibility tree search as find_element_by_role,
        but dispatches mousedown + mouseup + click on the actual DOM element.
        Returns {"role": str, "name": str} if clicked, None if not found.
        """
        # Emit will fire after success below — record intent here too so
        # the user sees the agent attempting it even if it fails.
        self._emit(
            kind="action", action="click_by_role", target=description[:80],
            detail=f"Looking for {description!r}",
        )
        conn = await self._ensure_connected()
        await conn.send("Accessibility.enable")
        result = await conn.send("Accessibility.getFullAXTree", {"depth": 6})
        nodes = result.get("nodes", [])

        desc_lower = description.lower()
        best_match = None
        best_score = 0

        for node in nodes:
            role = node.get("role", {}).get("value", "").lower()
            name = node.get("name", {}).get("value", "").lower()
            backend_id = node.get("backendDOMNodeId")
            if not backend_id:
                continue

            score = 0
            for word in desc_lower.split():
                if word in role:
                    score += 2
                if word in name:
                    score += 3

            if score > best_score:
                best_score = score
                best_match = (node, backend_id)

        if not best_match or best_score < 2:
            return None

        node, backend_id = best_match
        try:
            resolve_result = await conn.send(
                "DOM.resolveNode", {"backendNodeId": backend_id}
            )
            object_id = resolve_result.get("object", {}).get("objectId")
            if not object_id:
                return None

            await conn.send("Runtime.callFunctionOn", {
                "objectId": object_id,
                "functionDeclaration": """function() {
                    this.scrollIntoView({block: 'center', behavior: 'instant'});
                    this.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                    this.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                    this.click();
                }""",
            })
            role_name = node.get("role", {}).get("value", "")
            label = node.get("name", {}).get("value", "")
            self._emit(
                kind="action", action="click", target=label or role_name,
                detail=f"Clicked {role_name} '{label}'" if label else f"Clicked {role_name}",
            )
            return {"role": role_name, "name": label}
        except Exception as exc:
            logger.warning(
                "[browser] action=click_by_role tab=%s status=error err=%s: %s",
                self._current_tab.id[:24] if self._current_tab else "?",
                type(exc).__name__, exc,
            )

        return None

    # ── Console logs ────────────────────────────────────────────────

    async def enable_console(self) -> None:
        """Enable console log collection."""
        conn = await self._ensure_connected()
        self._console_logs: list[dict] = getattr(self, "_console_logs", [])
        await conn.send("Console.enable")

    async def get_console_logs(self, clear: bool = True) -> list[dict]:
        """Get collected console logs. Returns list of {level, text, url, line}."""
        conn = await self._ensure_connected()

        # Fetch via Runtime.evaluate since Console events need listener setup
        result = await conn.send("Runtime.evaluate", {
            "expression": """(() => {
                const logs = window.__lazyclaw_console_logs || [];
                return JSON.stringify(logs.slice(-50));
            })()""",
            "returnByValue": True,
        })
        raw = result.get("result", {}).get("value", "[]")

        import json
        try:
            logs = json.loads(raw) if isinstance(raw, str) else []
        except Exception:
            logger.debug("Failed to parse console logs JSON", exc_info=True)
            logs = []

        if clear:
            await conn.send("Runtime.evaluate", {
                "expression": "window.__lazyclaw_console_logs = [];",
            })

        return logs

    async def inject_console_capture(self) -> None:
        """Inject JS to capture console.log/warn/error into a buffer."""
        conn = await self._ensure_connected()
        await conn.send("Runtime.evaluate", {
            "expression": """(() => {
                if (window.__lazyclaw_console_hooked) return;
                window.__lazyclaw_console_logs = [];
                const orig = {log: console.log, warn: console.warn, error: console.error, info: console.info};
                for (const [level, fn] of Object.entries(orig)) {
                    console[level] = function(...args) {
                        window.__lazyclaw_console_logs.push({
                            level, text: args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' '),
                            time: Date.now()
                        });
                        if (window.__lazyclaw_console_logs.length > 100)
                            window.__lazyclaw_console_logs.shift();
                        fn.apply(console, args);
                    };
                }
                window.__lazyclaw_console_hooked = true;
            })()""",
        })

    # ── Hover and drag ──────────────────────────────────────────────

    async def hover(self, selector: str) -> None:
        """Hover over an element matching the CSS selector."""
        conn = await self._ensure_connected()
        js = f"""
        (() => {{
            const el = document.querySelector({_js_str(selector)});
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            return {{x: rect.x + rect.width / 2, y: rect.y + rect.height / 2}};
        }})()
        """
        result = await conn.send(
            "Runtime.evaluate", {"expression": js, "returnByValue": True}
        )
        coords = result.get("result", {}).get("value")
        if not coords:
            raise ValueError(f"Element not found: {selector}")

        # Human-like Bezier movement to hover position
        from lazyclaw.browser.human_input import human_move_to

        async def _do_hover() -> None:
            await human_move_to(conn, coords["x"], coords["y"])
            await asyncio.sleep(random.uniform(0.3, 0.8))

        await self._emit_action_with_frames(
            action="hover", target=selector[:80],
            detail=f"Hovered {selector[:60]}",
            coro=_do_hover(),
        )

    async def drag_and_drop(
        self, source_selector: str, target_selector: str
    ) -> None:
        """Drag element from source to target with Bezier path."""
        conn = await self._ensure_connected()
        js = f"""
        (() => {{
            const src = document.querySelector({_js_str(source_selector)});
            const tgt = document.querySelector({_js_str(target_selector)});
            if (!src || !tgt) return null;
            const sr = src.getBoundingClientRect();
            const tr = tgt.getBoundingClientRect();
            return {{
                sx: sr.x + sr.width / 2, sy: sr.y + sr.height / 2,
                tx: tr.x + tr.width / 2, ty: tr.y + tr.height / 2
            }};
        }})()
        """
        result = await conn.send(
            "Runtime.evaluate", {"expression": js, "returnByValue": True}
        )
        coords = result.get("result", {}).get("value")
        if not coords:
            raise ValueError("Source or target element not found")

        sx, sy, tx, ty = coords["sx"], coords["sy"], coords["tx"], coords["ty"]

        # Move to source with Bezier curve
        from lazyclaw.browser.human_input import human_move_to, _generate_bezier_path

        async def _do_drag() -> None:
            await human_move_to(conn, sx, sy)
            await conn.send("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": round(sx), "y": round(sy),
                "button": "left", "clickCount": 1,
            })
            await asyncio.sleep(random.uniform(0.1, 0.2))

            path = _generate_bezier_path(sx, sy, tx, ty)
            for point in path:
                await conn.send("Input.dispatchMouseEvent", {
                    "type": "mouseMoved",
                    "x": round(point.x),
                    "y": round(point.y),
                })
                await asyncio.sleep(random.uniform(0.02, 0.06))

            await conn.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": round(tx), "y": round(ty),
                "button": "left", "clickCount": 1,
            })
            await asyncio.sleep(random.uniform(0.2, 0.5))

        await self._emit_action_with_frames(
            action="drag", target=f"{source_selector[:40]}→{target_selector[:40]}",
            detail=f"Dragged {source_selector[:30]} to {target_selector[:30]}",
            coro=_do_drag(),
        )

    async def is_connected(self) -> bool:
        if not self._conn:
            return False
        return self._conn.is_connected

    async def close(self) -> None:
        """Disconnect CDP — does NOT close the user's browser."""
        if self._conn:
            await self._conn.close()
            self._conn = None
        self._current_tab = None
        logger.info("CDP backend disconnected (browser still running)")

    # ------------------------------------------------------------------
    # Tab management via CDP Target domain (for TabManager)
    # ------------------------------------------------------------------

    async def new_tab(
        self, url: str = "about:blank", *, background: bool = False
    ) -> str:
        """Create a new browser tab via CDP Target domain. Returns targetId.

        ``background=True`` sets Chromium's ``Target.createTarget`` background
        flag so the new tab opens WITHOUT being brought to the foreground —
        passive watchers use this for their dedicated parked tab so it never
        steals the user's visible screen ("jumping on screen").
        """
        conn = await self._ensure_connected()
        params: dict = {"url": url}
        if background:
            params["background"] = True
        result = await conn.send("Target.createTarget", params)
        target_id = result.get("targetId", "")
        if not target_id:
            logger.warning(
                "[browser] action=new_tab host=%s background=%s status=no_target_id",
                _log_host(url), background,
            )
            raise RuntimeError("Target.createTarget returned no targetId")
        logger.debug(
            "[browser] action=new_tab host=%s background=%s tab=%s",
            _log_host(url), background, target_id[:24],
        )
        return target_id

    async def attach_to_target(self, target_id: str) -> str:
        """Attach to a target with flat session mode. Returns sessionId.

        Flat mode multiplexes all tab sessions over the single WebSocket
        connection, using sessionId to route commands to the correct tab.
        """
        conn = await self._ensure_connected()
        result = await conn.send(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        session_id = result.get("sessionId", "")
        if not session_id:
            raise RuntimeError("Target.attachToTarget returned no sessionId")
        # Enable required CDP domains in the new session
        await conn.send("Page.enable", session_id=session_id)
        await conn.send("Runtime.enable", session_id=session_id)
        return session_id

    async def send_to_target(
        self, session_id: str, method: str, params: dict | None = None,
    ) -> dict:
        """Send a CDP command scoped to a specific session (tab)."""
        conn = await self._ensure_connected()
        return await conn.send(method, params, session_id=session_id)

    async def close_tab(self, target_id: str) -> None:
        """Close a specific browser tab via CDP."""
        try:
            conn = await self._ensure_connected()
            await conn.send("Target.closeTarget", {"targetId": target_id})
            self._emit(
                kind="action", action="close_tab", target=target_id[:24],
                detail="Closed tab",
            )
            logger.debug("[browser] action=close_tab tab=%s status=ok", target_id[:24])
        except Exception as exc:
            logger.warning(
                "[browser] action=close_tab tab=%s status=error err=%s",
                target_id[:24], type(exc).__name__,
            )

    @property
    def backend_type(self) -> str:
        return "cdp"


# restart_browser_with_cdp, _is_same_origin_nav, _js_str are imported
# from cdp_utils.py at the top of this file for backwards compatibility.
