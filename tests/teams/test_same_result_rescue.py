"""same_result one-shot rescue — trim semantics.

Incident 2026-08-17 (himap blog): the browser specialist ran its
duplicate-check search, the results page was stable (likely empty), and it
re-read the identical page 3× — detect_same_result correctly tripped, but the
runner had NO rescue for that reason and hard-failed one step short of the
create form. runner.py now injects a one-shot "a stable page IS the answer —
act on it" hint and trims the identical tail from ``_tool_results`` /
``_tool_history`` before continuing.

These tests pin the part with real regression risk: after the runner's trim
expression, the detector must NOT re-trip on the very next iteration (an
instant re-trip would make the rescue a no-op and reintroduce the hard-fail).
"""

from __future__ import annotations

from lazyclaw.runtime.stuck_detector import detect_same_result, detect_stuck

_PAGE = "Tab: Select blog post to change | HiMap URL: .../blogpost/?q=madrid" * 10


def _trim(seq: list) -> list:
    """The exact trim expression runner.py applies on rescue."""
    return seq[:-3] if len(seq) >= 3 else []


class TestSameResultRescueTrim:
    def test_three_identical_results_trip(self):
        results = ["ok A", "ok B", _PAGE, _PAGE, _PAGE]
        signal = detect_same_result(results)
        assert signal is not None
        assert signal.reason == "same_result"

    def test_after_trim_next_differing_result_does_not_retrip(self):
        results = ["ok A", "ok B", _PAGE, _PAGE, _PAGE]
        trimmed = _trim(results)
        # Post-rescue iteration: the worker acts on the hint and gets a NEW
        # page (the add form). The detector must stay quiet.
        trimmed.append("Tab: Add blog post | HiMap — title/excerpt/content form")
        assert detect_same_result(trimmed) is None

    def test_after_trim_re_reading_same_page_trips_again(self):
        # The rescue is ONE-shot by flag, but if the worker ignores the hint
        # and re-reads the same page 3 more times the detector must trip
        # again so the hard-fail path still ends the loop.
        results = _trim([_PAGE, _PAGE, _PAGE])
        results += [_PAGE, _PAGE, _PAGE]
        assert detect_same_result(results) is not None

    def test_detect_stuck_routes_same_result(self):
        history = ["browser", "browser", "browser"]
        results = [_PAGE, _PAGE, _PAGE]
        signal = detect_stuck(history, results, results[-1])
        assert signal is not None
        assert signal.reason == "same_result"

    def test_short_histories_never_trip(self):
        assert detect_same_result([]) is None
        assert detect_same_result([_PAGE, _PAGE]) is None
        assert detect_same_result(_trim([_PAGE, _PAGE])) is None
