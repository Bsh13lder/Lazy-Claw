"""Mid-turn pivot detector — pins the architectural rule "brain dispatches,
workers execute" by firing a system nudge after the brain emits N
same-shape tool calls in a single foreground TAOR turn.

Production log 2026-04-28 21:09–21:12 captured the failure mode: brain
ran 22 inline ``mcp_scraper_extract_entities`` / ``web_search`` calls
itself instead of dispatching workers. These tests pin the detection
function so that path can never go silent again.
"""

from __future__ import annotations

from lazyclaw.runtime.stuck_detector import (
    PivotSignal,
    detect_inline_pivot,
)


def test_pivot_fires_at_threshold():
    sigs = [("mcp_scraper_extract_entities", ("entity_types", "url"))] * 5
    out = detect_inline_pivot(sigs, threshold=5)
    assert isinstance(out, PivotSignal)
    assert out.tool_name == "mcp_scraper_extract_entities"
    assert out.arg_keys == ("entity_types", "url")
    assert out.count == 5
    assert out.reason == "same_shape_inline_loop"


def test_pivot_does_not_fire_below_threshold():
    sigs = [("mcp_scraper_extract_entities", ("url",))] * 4
    assert detect_inline_pivot(sigs, threshold=5) is None


def test_pivot_signal_carries_count_for_long_run():
    sigs = [("web_search", ("query",))] * 7
    out = detect_inline_pivot(sigs, threshold=5)
    assert out is not None
    assert out.count == 7


def test_pivot_signature_includes_arg_key_set_not_just_name():
    """Same tool but different arg-key sets → not same shape."""
    sigs = [
        ("browser", ("action", "ref")),
        ("browser", ("action", "ref")),
        ("browser", ("action", "ref")),
        ("browser", ("action", "url")),  # different shape
        ("browser", ("action", "url")),
    ]
    # Trailing run is only 2 of ("action","url") — below threshold 5.
    assert detect_inline_pivot(sigs, threshold=5) is None


def test_pivot_signature_distinguishes_tools_with_same_arg_keys():
    """``extract_entities(url=…)`` and ``crawl_url(url=…)`` share arg keys
    but are different tools — should NOT collapse into the same shape."""
    sigs = [
        ("mcp_scraper_extract_entities", ("url",)),
        ("mcp_scraper_extract_entities", ("url",)),
        ("mcp_scraper_crawl_url", ("url",)),
        ("mcp_scraper_crawl_url", ("url",)),
        ("mcp_scraper_crawl_url", ("url",)),
    ]
    # Trailing run is 3 of crawl_url — below threshold 5.
    assert detect_inline_pivot(sigs, threshold=5) is None


def test_pivot_ignores_memory_tools():
    """recall_memories / save_memory / search_tools at the tail must NOT
    trip the detector — those are dispatcher-role tools by design."""
    sigs = [("recall_memories", ("query",))] * 7
    assert detect_inline_pivot(sigs, threshold=5) is None
    sigs = [("save_memory", ("content", "category"))] * 7
    assert detect_inline_pivot(sigs, threshold=5) is None
    sigs = [("search_tools", ("query",))] * 7
    assert detect_inline_pivot(sigs, threshold=5) is None


def test_pivot_only_counts_trailing_run():
    """A switch in shape resets the trailing count — only the most
    recent run matters for the pivot decision."""
    sigs = [
        ("mcp_scraper_extract_entities", ("url",)),
        ("mcp_scraper_extract_entities", ("url",)),
        ("mcp_scraper_extract_entities", ("url",)),
        ("mcp_scraper_extract_entities", ("url",)),
        ("mcp_scraper_extract_entities", ("url",)),
        # Brain pivoted to a different shape — old run shouldn't count.
        ("modify_sheet_values", ("range", "values")),
        ("modify_sheet_values", ("range", "values")),
    ]
    assert detect_inline_pivot(sigs, threshold=5) is None


def test_pivot_empty_history_returns_none():
    assert detect_inline_pivot([], threshold=5) is None


def test_pivot_threshold_below_2_returns_none():
    """Defensive: bogus threshold of 0 or 1 must not always-fire."""
    sigs = [("x", ())] * 10
    assert detect_inline_pivot(sigs, threshold=1) is None
    assert detect_inline_pivot(sigs, threshold=0) is None


def test_pivot_signal_is_immutable():
    """PivotSignal is frozen — caller can pass it around safely."""
    import dataclasses
    sigs = [("x", ("a",))] * 5
    sig = detect_inline_pivot(sigs, threshold=5)
    assert sig is not None
    assert getattr(dataclasses.fields(sig)[0], "name", None) == "reason"
    # Frozen → assignment raises.
    try:
        sig.count = 999  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:  # pragma: no cover
        raise AssertionError("PivotSignal is supposed to be frozen")
