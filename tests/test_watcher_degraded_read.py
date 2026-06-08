"""Tests for the degraded-read guard in ``check_watcher`` (2026-06-02).

A browser watcher polling a Cloudflare-protected Upwork messages page was
re-firing a full brain turn on EVERY heartbeat tick — an infinite
self-feeding loop. The Upwork extractor returns EMPTY on some ticks
(Cloudflare challenge / layout drift / rooms-panel empty) and populated on
others, so ``result_len`` oscillated 13463 → 20 → 814 → … across consecutive
ticks. The old code unconditionally overwrote ``last_value`` with whatever
was read (even an empty read), so each empty↔full flip produced
``changed=True``.

The fix makes change-detection MORE conservative: when the site-specific
extractor returns no real content AND a prior good baseline exists, the read
is skipped and the ORIGINAL context is returned unchanged so the last good
baseline survives. The nav sidecar (which AUGMENTS a real read) is likewise
forbidden from independently flipping the hash on a degraded read.
"""

from __future__ import annotations

import json

import pytest

from lazyclaw.browser.watcher import _is_degraded_result, check_watcher


# ── _is_degraded_result classifier ───────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "\n\t ", {}, [], (), set()],
)
def test_degraded_classifier_flags_empty(value):
    assert _is_degraded_result(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "real content",
        " x ",
        {"unread_count": 0, "text": ""},  # populated dict = legitimate read
        {"a": 1},
        ["item"],
        42,  # truthy numeric reading is real
    ],
)
def test_degraded_classifier_accepts_real_content(value):
    assert _is_degraded_result(value) is False


def test_degraded_classifier_treats_falsy_scalar_as_degraded():
    """A falsy primitive (0 / False) carries no scraped content, so it's
    degraded. The real extractors never return bare scalars, but the
    fallthrough must stay conservative — empty wins."""
    assert _is_degraded_result(0) is True
    assert _is_degraded_result(False) is True


# ── Mock CDP backend ──────────────────────────────────────────────────


class _FakeBackend:
    """Minimal CDP backend stand-in.

    ``evaluate`` returns the next queued result for the site-specific
    extractor; the nav-sidecar JS (``location.pathname``) is detected by a
    marker substring and answered from ``sidecar_value`` so it never
    consumes the primary result queue.
    """

    def __init__(self, results: list, *, url: str, sidecar=None):
        self._results = list(results)
        self._url = url
        self._sidecar = sidecar

    async def current_url(self) -> str:
        return self._url

    async def evaluate(self, js: str):
        if "location.pathname" in js:
            return self._sidecar
        return self._results.pop(0)

    async def tabs(self):  # pragma: no cover - same-host short-circuits
        return []

    async def switch_tab(self, *a, **k):  # pragma: no cover
        return None

    async def goto(self, *a, **k):  # pragma: no cover
        return None


def _base_ctx(**over) -> dict:
    ctx = {
        "url": "https://www.upwork.com/ab/messages/rooms/",
        "page_type": "auto",
        "custom_js": None,
        "check_interval": 300,
        "last_value": None,
        "last_check": None,
    }
    ctx.update(over)
    return ctx


# ── (a) Degraded read after a good baseline does NOT overwrite ────────


@pytest.mark.asyncio
async def test_degraded_read_preserves_last_good_baseline():
    """An empty read after a good baseline returns changed=False and the
    ORIGINAL context unchanged (last_value survives)."""
    baseline = "GOOD CONTENT FROM A REAL SCRAPE"
    ctx = _base_ctx(last_value=baseline, last_check="2026-06-02T00:00:00+00:00")
    backend = _FakeBackend([""], url=ctx["url"])  # extractor returns empty

    changed, notif, new_ctx = await check_watcher(backend, ctx)

    assert changed is False
    assert notif is None
    # Original context returned unchanged — baseline NOT poisoned.
    assert new_ctx["last_value"] == baseline
    assert new_ctx is ctx  # same object — last_check untouched too


@pytest.mark.asyncio
async def test_degraded_empty_dict_after_baseline_preserves_baseline():
    """Empty-dict extraction is also degraded and must not overwrite."""
    baseline = json.dumps({"text": "real", "unread_count": 1}, sort_keys=True)
    ctx = _base_ctx(
        page_type="whatsapp",
        last_value=baseline,
        last_check="2026-06-02T00:00:00+00:00",
    )
    # whatsapp path probes [role=row] count first, then the extractor.
    backend = _FakeBackend([0, 0, 0, 0, 0, {}], url="https://web.whatsapp.com/")
    ctx["url"] = "https://web.whatsapp.com/"

    changed, notif, new_ctx = await check_watcher(backend, ctx)

    assert changed is False
    assert new_ctx["last_value"] == baseline


# ── (b) empty→full→empty→full oscillation fires only on the genuine new value ──


@pytest.mark.asyncio
async def test_empty_full_oscillation_fires_once_not_every_tick():
    """The exact production bug: alternating empty/populated reads must
    NOT re-fire on every tick — only a genuinely new value fires once."""
    populated = "MESSAGE LIST v1"

    # Tick 1: empty → first check, baseline seeded (no fire).
    ctx = _base_ctx()
    b1 = _FakeBackend([""], url=ctx["url"])
    changed1, _, ctx = await check_watcher(b1, ctx)
    assert changed1 is False  # first-check baseline

    # Tick 2: populated → genuine content appears → fires ONCE.
    b2 = _FakeBackend([populated], url=ctx["url"])
    changed2, notif2, ctx = await check_watcher(b2, ctx)
    assert changed2 is True
    assert notif2 is not None

    # Tick 3: empty (Cloudflare blip) → MUST NOT fire, baseline preserved.
    b3 = _FakeBackend([""], url=ctx["url"])
    changed3, _, ctx = await check_watcher(b3, ctx)
    assert changed3 is False
    assert ctx["last_value"] == populated  # still the good value

    # Tick 4: same populated content returns → already the baseline → no fire.
    b4 = _FakeBackend([populated], url=ctx["url"])
    changed4, _, ctx = await check_watcher(b4, ctx)
    assert changed4 is False

    # Tick 5: empty again → still no fire.
    b5 = _FakeBackend([""], url=ctx["url"])
    changed5, _, ctx = await check_watcher(b5, ctx)
    assert changed5 is False
    assert ctx["last_value"] == populated


@pytest.mark.asyncio
async def test_sidecar_cannot_fire_alone_on_degraded_read():
    """A nav-badge blip must NOT trigger changed=True when the primary
    extractor came back empty (sidecar augments, never substitutes)."""
    populated = "MESSAGE LIST v1"
    ctx = _base_ctx(last_value=populated, last_check="2026-06-02T00:00:00+00:00")
    # Primary empty, but sidecar reports a changed Messages badge.
    backend = _FakeBackend(
        [""], url=ctx["url"], sidecar={"m": "9", "n": None, "p": "/ab/messages/rooms/"},
    )

    changed, notif, new_ctx = await check_watcher(backend, ctx)

    assert changed is False
    assert notif is None
    assert new_ctx["last_value"] == populated  # baseline untouched


# ── (c) Genuine content change still fires ────────────────────────────


@pytest.mark.asyncio
async def test_genuine_content_change_still_fires():
    """populated → different populated must still report changed=True."""
    ctx = _base_ctx(last_value="OLD MESSAGE LIST", last_check="2026-06-02T00:00:00+00:00")
    backend = _FakeBackend(["NEW MESSAGE LIST WITH A FRESH REPLY"], url=ctx["url"])

    changed, notif, new_ctx = await check_watcher(backend, ctx)

    assert changed is True
    assert notif is not None
    assert new_ctx["last_value"] == "NEW MESSAGE LIST WITH A FRESH REPLY"


# ── (d) WhatsApp unread_count change still fires (behaviour unchanged) ──


@pytest.mark.asyncio
async def test_whatsapp_unread_count_change_still_fires():
    """The WhatsApp dict comparison is untouched by the degraded guard."""
    old = {"unread_count": 1, "text": "Maria: hey"}
    ctx = _base_ctx(
        page_type="whatsapp",
        url="https://web.whatsapp.com/",
        last_value=json.dumps(old, sort_keys=True),
        last_check="2026-06-02T00:00:00+00:00",
    )
    new = {"unread_count": 3, "text": "Maria: hey\nPablo: yo"}
    # [role=row] count probes (>0 to break early) then the extractor dict.
    backend = _FakeBackend([5, new], url="https://web.whatsapp.com/")

    changed, notif, new_ctx = await check_watcher(backend, ctx)

    assert changed is True
    assert notif is not None


@pytest.mark.asyncio
async def test_whatsapp_no_unread_change_does_not_fire():
    """Populated WhatsApp dict with unchanged unread/text = no fire (and
    NOT treated as degraded just because counts are zero)."""
    same = {"unread_count": 0, "text": "Maria: hey"}
    ctx = _base_ctx(
        page_type="whatsapp",
        url="https://web.whatsapp.com/",
        last_value=json.dumps(same, sort_keys=True),
        last_check="2026-06-02T00:00:00+00:00",
    )
    backend = _FakeBackend([5, dict(same)], url="https://web.whatsapp.com/")

    changed, notif, _ = await check_watcher(backend, ctx)

    assert changed is False
    assert notif is None
