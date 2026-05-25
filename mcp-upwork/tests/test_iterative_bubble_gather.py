"""Tests for the iterative scroll-up bubble gatherer (2026-05-24).

The bug being closed: ``get_conversation_messages`` was single-pass —
extracted whatever bubbles happened to be mounted in Brave's chat DOM
at call time. Upwork virtualizes the chat list (only ~20 bubbles around
the current scroll position are mounted at once). If Brave's tab was
scrolled toward the TOP of history when the call landed, the OLDEST 20
were extracted and the NEWEST were silently missed.

The fix: ``_gather_all_bubbles_iter`` scrolls UP from the current
position iteratively, accumulating unique bubbles by content fingerprint,
and returns them in chronological order. These tests pin the helper's
core invariants using mock element-handles and a mock page.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from upwork_mcp.tools.messages import _gather_all_bubbles_iter


# ─── mocks ───────────────────────────────────────────────────────────────


def _mock_bubble(*, abs_y: float, header: str, body: str):
    """Build a mock Playwright element handle.

    `.evaluate(js)` returns ``[absY, fingerprint]`` when probed with the
    gatherer's probe script, mirroring real Playwright JS-eval semantics.
    """
    el = MagicMock()
    fingerprint = f"{header}|{(body or '').strip()[:200]}"
    el.evaluate = AsyncMock(return_value=[abs_y, fingerprint])
    return el


def _mock_page(bubble_snapshots: list[list], scroll_returns: list[bool]):
    """Build a mock Playwright page that returns different sets of
    bubbles per iteration AND a scripted sequence of scroll outcomes.

    Each entry in ``bubble_snapshots`` is the bubble list returned on
    iteration N (`query_selector_all` call N). Each entry in
    ``scroll_returns`` is the value the scroll-up JS evaluation returns
    on iteration N (True = scrolled, False = can't scroll further).
    """
    page = MagicMock()
    iter_idx = {"n": 0}
    scroll_idx = {"n": 0}

    async def _qsa(selector):
        # Only return non-empty for the primary story-container selector
        # so the gatherer doesn't double-iterate fallback selectors.
        n = iter_idx["n"]
        if "story-container" in selector:
            iter_idx["n"] += 1
            return bubble_snapshots[n] if n < len(bubble_snapshots) else []
        return []

    async def _eval(js, *args):
        # First arg shape ``(frac,)`` → this is the scroll-up call.
        # Otherwise it's a per-bubble probe (handled by the element's
        # own .evaluate mock — we should never hit page.evaluate for
        # that path).
        if args and isinstance(args[0], (int, float)):
            n = scroll_idx["n"]
            scroll_idx["n"] += 1
            return scroll_returns[n] if n < len(scroll_returns) else False
        return False

    page.query_selector_all = AsyncMock(side_effect=_qsa)
    page.evaluate = AsyncMock(side_effect=_eval)
    return page


# ─── tests ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_iteration_returns_all_visible_bubbles():
    """When all bubbles are mounted on the first iteration, the gatherer
    extracts them and stops on the next no-progress iteration."""
    bubbles = [
        _mock_bubble(abs_y=100, header="Alice 9:00 AM", body="hi"),
        _mock_bubble(abs_y=200, header="Bob 9:01 AM", body="hello"),
        _mock_bubble(abs_y=300, header="Alice 9:02 AM", body="how are you"),
    ]
    page = _mock_page(
        bubble_snapshots=[bubbles, bubbles],  # iter 1: 3 bubbles, iter 2: same
        scroll_returns=[True, False],  # scroll up succeeds once, then top
    )

    handles, dropped = await _gather_all_bubbles_iter(page, target_count=10)

    assert dropped == 0
    assert len(handles) == 3
    # Returned in chronological order (lowest Y = oldest first).
    assert handles == bubbles


@pytest.mark.asyncio
async def test_multi_iteration_accumulates_across_scrolls():
    """Iter 1 sees newest 3 bubbles. After scroll up, iter 2 sees the
    SAME 3 + 2 older. Result: 5 unique bubbles, oldest first."""
    new_a = _mock_bubble(abs_y=300, header="Alice 9:03 AM", body="newer")
    new_b = _mock_bubble(abs_y=400, header="Bob 9:04 AM", body="newest")
    old_x = _mock_bubble(abs_y=100, header="Alice 9:00 AM", body="oldest")
    old_y = _mock_bubble(abs_y=200, header="Bob 9:01 AM", body="older")
    middle = _mock_bubble(abs_y=250, header="Alice 9:02 AM", body="middle")

    page = _mock_page(
        bubble_snapshots=[
            [new_a, new_b, middle],  # iter 1: newest 3 (after warmup scroll-bottom)
            [old_x, old_y, middle, new_a],  # iter 2: scroll-up — older 3 + overlap
            [old_x, old_y],  # iter 3: only the oldest still mounted
        ],
        scroll_returns=[True, True, False],
    )

    handles, dropped = await _gather_all_bubbles_iter(page, target_count=20)

    assert dropped == 0
    assert len(handles) == 5
    # Chronological order: oldest (low Y) first.
    expected = [old_x, old_y, middle, new_a, new_b]
    assert handles == expected


@pytest.mark.asyncio
async def test_dedup_by_content_fingerprint():
    """Same bubble re-appearing across iterations is counted ONCE.

    Pins the dedup contract: `(header.strip() + '|' + body[:200])` is
    the key. Re-mounted bubbles match on this key even if Y position
    drifted slightly (re-render quirks).
    """
    a1 = _mock_bubble(abs_y=100, header="X 9:00 AM", body="dup")
    a2 = _mock_bubble(abs_y=110, header="X 9:00 AM", body="dup")  # same fp, Y drift
    a3 = _mock_bubble(abs_y=100, header="X 9:00 AM", body="dup")  # same fp again
    b = _mock_bubble(abs_y=200, header="Y 9:01 AM", body="other")

    page = _mock_page(
        bubble_snapshots=[[a1, b], [a2, b, a3]],
        scroll_returns=[True, False],
    )

    handles, _ = await _gather_all_bubbles_iter(page, target_count=20)

    # Only TWO unique bubbles (a's dedupe to whichever was inserted first).
    assert len(handles) == 2
    # The first-inserted version of `a` is preserved; the second
    # mounting at Y=110 / Y=100 is ignored.
    assert handles[0] is a1
    assert handles[1] is b


@pytest.mark.asyncio
async def test_terminates_on_two_no_progress_iterations():
    """Two consecutive iterations with zero new bubbles → stop.

    Prevents infinite scrolling when we've actually reached the top
    of the conversation (or scroll is wedged and Brave isn't loading
    more history).
    """
    bubbles = [
        _mock_bubble(abs_y=100, header="A 1:00", body="x"),
    ]
    page = _mock_page(
        bubble_snapshots=[bubbles, bubbles, bubbles, bubbles, bubbles],
        scroll_returns=[True, True, True, True, True],
    )

    handles, _ = await _gather_all_bubbles_iter(
        page, target_count=20, no_progress_limit=2,
    )

    # Should stop after no-progress threshold, not keep iterating to max.
    assert len(handles) == 1
    # query_selector_all called: iter 1 added 1, iter 2 added 0
    # (no_progress=1), iter 3 added 0 (no_progress=2 → break). So at
    # most 3 iterations.
    assert page.query_selector_all.await_count <= 3


@pytest.mark.asyncio
async def test_terminates_when_scroll_cannot_go_higher():
    """`scroll_up` JS returning False (top of chat or no scroller found)
    breaks out of the loop immediately, even if we haven't filled
    `target_count`."""
    b1 = _mock_bubble(abs_y=100, header="A 1:00", body="one")
    b2 = _mock_bubble(abs_y=200, header="B 1:01", body="two")
    page = _mock_page(
        bubble_snapshots=[[b1, b2]],
        scroll_returns=[False],  # cannot scroll up at all
    )

    handles, _ = await _gather_all_bubbles_iter(page, target_count=50)

    assert len(handles) == 2
    # Should only have iterated once (extracted then tried to scroll
    # and immediately broke).
    assert page.query_selector_all.await_count == 1


@pytest.mark.asyncio
async def test_terminates_when_target_count_reached_with_buffer():
    """When unique count exceeds `target_count + 5`, the gatherer stops
    iterating to avoid burning more JS round-trips than needed."""
    bubbles_iter1 = [
        _mock_bubble(abs_y=float(i * 10), header=f"S{i}", body=f"b{i}")
        for i in range(30)
    ]
    page = _mock_page(
        bubble_snapshots=[bubbles_iter1, []],
        scroll_returns=[True, True],
    )

    handles, _ = await _gather_all_bubbles_iter(page, target_count=20)

    # target=20 + 5 buffer → break at first iter (30 >= 25)
    assert len(handles) == 30
    assert page.query_selector_all.await_count == 1


@pytest.mark.asyncio
async def test_skips_empty_fingerprint_bubbles():
    """Skeleton rows / virtualization placeholders with empty header
    AND empty body must be silently skipped, not counted."""
    real = _mock_bubble(abs_y=100, header="Real 9:00", body="real msg")
    skel = MagicMock()
    skel.evaluate = AsyncMock(return_value=[150.0, "|"])  # empty fp
    page = _mock_page(
        bubble_snapshots=[[real, skel]],
        scroll_returns=[False],
    )

    handles, dropped = await _gather_all_bubbles_iter(page, target_count=20)

    assert len(handles) == 1
    assert handles[0] is real
    # Skeleton wasn't a JS failure — just empty content; not counted as
    # "dropped" either.
    assert dropped == 0


@pytest.mark.asyncio
async def test_counts_js_eval_failures_as_dropped():
    """When the per-bubble JS probe raises, that bubble is counted
    as `dropped` so the diagnostic log can surface the failure."""
    good = _mock_bubble(abs_y=100, header="A", body="ok")
    broken = MagicMock()
    broken.evaluate = AsyncMock(side_effect=RuntimeError("eval failed"))
    page = _mock_page(
        bubble_snapshots=[[good, broken]],
        scroll_returns=[False],
    )

    handles, dropped = await _gather_all_bubbles_iter(page, target_count=20)

    assert len(handles) == 1
    assert handles[0] is good
    assert dropped == 1


@pytest.mark.asyncio
async def test_no_containers_returns_empty_immediately():
    """If `query_selector_all` returns no bubbles at all, the gatherer
    returns an empty list without trying to scroll."""
    page = _mock_page(bubble_snapshots=[[]], scroll_returns=[])

    handles, dropped = await _gather_all_bubbles_iter(page, target_count=20)

    assert handles == []
    assert dropped == 0
    # Should NOT have attempted a scroll.
    assert page.evaluate.await_count == 0


@pytest.mark.asyncio
async def test_respects_max_iterations_cap():
    """Even if scroll always succeeds and new bubbles always appear,
    `max_iterations` is a hard cap."""
    counter = {"n": 0}

    def make_snapshot(_n):
        counter["n"] += 1
        return [_mock_bubble(
            abs_y=float(counter["n"]),
            header=f"S{counter['n']}",
            body=f"unique-{counter['n']}",
        )]

    page = MagicMock()
    page.query_selector_all = AsyncMock(
        side_effect=lambda sel: make_snapshot(sel) if "story-container" in sel else []
    )
    page.evaluate = AsyncMock(return_value=True)  # scroll always succeeds

    handles, _ = await _gather_all_bubbles_iter(
        page, target_count=10_000, max_iterations=3,
    )

    # 3 iters × 1 new bubble each = 3 total
    assert len(handles) == 3
