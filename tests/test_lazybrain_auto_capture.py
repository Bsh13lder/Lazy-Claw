"""Auto-capture regex detectors — the cheap tier that works without an LLM.

These tests pin the detection behaviour so the threshold for "important"
can't silently drift. Low confidence = nothing saved, protects the PKM
from junk.
"""
from __future__ import annotations

from lazyclaw.lazybrain import auto_capture


def _kinds(caps: list) -> list[str]:
    return [c.kind for c in caps]


def test_detects_til() -> None:
    caps = auto_capture.extract("TIL that Redis uses LRU eviction by default.")
    assert "til" in _kinds(caps)
    til = [c for c in caps if c.kind == "til"][0]
    assert til.confidence >= 0.8


def test_detects_decision() -> None:
    caps = auto_capture.extract("We decided to use Postgres instead of Mongo.")
    assert "decision" in _kinds(caps)


def test_detects_price() -> None:
    caps = auto_capture.extract("Coffee costs $4.50 per cup here.")
    price = [c for c in caps if c.kind == "price"]
    assert len(price) == 1
    assert "$4.50" in price[0].content


def test_detects_deadline() -> None:
    caps = auto_capture.extract("Deadline tomorrow: ship the release.")
    assert "deadline" in _kinds(caps)


def test_detects_command() -> None:
    caps = auto_capture.extract("run: `git rebase -i HEAD~5` to clean up")
    cmd = [c for c in caps if c.kind == "command"]
    assert len(cmd) == 1
    assert "git rebase" in cmd[0].content


def test_detects_contact() -> None:
    caps = auto_capture.extract("Maria's phone is +34 600 111 222 in Madrid")
    assert "contact" in _kinds(caps)


def test_no_match_short_text() -> None:
    assert auto_capture.extract("hi") == []


def test_dedupe_same_kind_content() -> None:
    # Two identical TILs back-to-back should collapse into one
    text = "TIL: Foo uses LRU. TIL: Foo uses LRU."
    caps = auto_capture.extract(text)
    tils = [c for c in caps if c.kind == "til"]
    assert len(tils) == 1


def test_multi_kind_single_message() -> None:
    text = (
        "TIL Redis uses LRU eviction. Deadline tomorrow: launch. "
        "run: `redis-cli flushall`"
    )
    kinds = set(_kinds(auto_capture.extract(text)))
    assert {"til", "deadline", "command"} <= kinds


def test_confidence_threshold_filter() -> None:
    caps = auto_capture.extract("Maybe I should explore [[some idea]] later")
    idea = [c for c in caps if c.kind == "idea"]
    if idea:
        # The idea detector is intentionally low-confidence so it gets
        # filtered out unless the caller lowers the threshold.
        assert idea[0].confidence < 0.7


def test_capture_is_frozen_dataclass() -> None:
    from dataclasses import FrozenInstanceError

    cap = auto_capture.Capture(
        kind="til",
        content="x",
        title="y",
        tags=("auto", "til"),
        importance=5,
        confidence=0.8,
    )
    import pytest

    with pytest.raises(FrozenInstanceError):
        cap.kind = "other"  # type: ignore[misc]
