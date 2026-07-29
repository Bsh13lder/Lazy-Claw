"""Blank-page recovery must fire on a NULL document, not skip it.

2026-07-26 incident tail: `browser(action="open")` navigated the user's
signed-in Upwork tab, the navigation committed, and the tab was left
with `document.documentElement === null`. The tool reported
`status=ok nav=full dur_ms=3934` and the specialist consumed the result.

Live CDP capture of that tab (2026-07-28)::

    readyState      : complete
    documentElement : NULL
    DOM elements    : 0

`read_open.action_open` already had blank detection with a reload, but
its probe was `document.body.innerText` — on a null document
`document.body` is ALSO null, so the expression THROWS, and the handler
was `except Exception: break`, which left the retry loop *without*
reloading. The one state that needed recovery was the one state that
skipped it.

A single reload recovered the page in ~3s (0 -> 1003 -> 2830 elements),
so recovery is both cheap and effective — it just never ran.
"""

from __future__ import annotations

import pytest

from lazyclaw.skills.builtin.browser_actions.read_open import (
    ensure_page_not_blank,
    probe_page_alive,
)


class FakeBackend:
    """Minimal backend double. `script` drives successive evaluate calls.

    An entry may be a bool (probe answer) or an Exception (raised) —
    mirroring a null-document page where the probe throws.
    """

    def __init__(self, script, alive_after_reload=None):
        self._script = list(script)
        self._alive_after_reload = alive_after_reload
        self.reloads = 0
        self.evaluates = 0

    async def evaluate(self, expr):
        self.evaluates += 1
        if "location.reload" in expr:
            self.reloads += 1
            if self._alive_after_reload is not None:
                self._script = list(self._alive_after_reload)
            return None
        if not self._script:
            return True
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Keep the polling loop instant."""
    async def _fast(_seconds):
        return None
    monkeypatch.setattr(
        "lazyclaw.skills.builtin.browser_actions.read_open.asyncio.sleep",
        _fast,
    )


class TestProbe:
    @pytest.mark.asyncio
    async def test_alive_page_probes_true(self):
        assert await probe_page_alive(FakeBackend([True])) is True

    @pytest.mark.asyncio
    async def test_blank_page_probes_false(self):
        assert await probe_page_alive(FakeBackend([False])) is False

    @pytest.mark.asyncio
    async def test_throwing_probe_counts_as_blank_not_alive(self):
        """THE BUG: a null document throws; that means blank, not 'unknown'."""
        be = FakeBackend([TypeError("Cannot read properties of null")])
        assert await probe_page_alive(be) is False


class TestRecovery:
    @pytest.mark.asyncio
    async def test_null_document_triggers_reload(self):
        """THE FIX: throwing probe must still reach the reload."""
        be = FakeBackend(
            [TypeError("null")] * 6, alive_after_reload=[True] * 4,
        )
        alive = await ensure_page_not_blank(be)
        assert be.reloads == 1, "a null document must trigger exactly one reload"
        assert alive is True

    @pytest.mark.asyncio
    async def test_healthy_page_never_reloads(self):
        be = FakeBackend([True])
        assert await ensure_page_not_blank(be) is True
        assert be.reloads == 0, "must not reload a working page"

    @pytest.mark.asyncio
    async def test_slow_page_that_settles_does_not_reload(self):
        """Hydration lag is not a failure — settle before reloading."""
        be = FakeBackend([False, False, True])
        assert await ensure_page_not_blank(be) is True
        assert be.reloads == 0

    @pytest.mark.asyncio
    async def test_persistently_blank_reloads_once_and_reports_blank(self):
        be = FakeBackend([False] * 12, alive_after_reload=[False] * 6)
        alive = await ensure_page_not_blank(be)
        assert be.reloads == 1, "reload once, never loop"
        assert alive is False, "must report honestly when recovery failed"

    @pytest.mark.asyncio
    async def test_reload_failure_is_not_fatal(self):
        class Exploding(FakeBackend):
            async def evaluate(self, expr):
                if "location.reload" in expr:
                    raise RuntimeError("detached frame")
                return await super().evaluate(expr)

        alive = await ensure_page_not_blank(Exploding([False] * 12))
        assert alive is False  # degraded, but no exception escaped
