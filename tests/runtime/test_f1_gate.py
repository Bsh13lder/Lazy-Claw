"""Unit tests for the specialist-path F1 grounding gate (``f1_gate``).

The specialist runner (``teams/runner.py``) historically ran ZERO
mechanical anti-confabulation checks — it shipped the worker's reply raw.
All mechanical F1 enforcement lived only in the brain (``agent.py``).
Channel-reading specialists (freelance/messaging/email) could therefore
confabulate quotes with no deterministic backstop.

``f1_gate.evaluate_grounding`` closes that gap by COMPOSING the existing
detectors (``detect_confabulation`` + ``phase2_enforcement_verdict`` +
``build_raw_data_injection``) into one entry point the runner can call at
its final-reply site. This module does NOT reimplement detection — it
reuses the same battle-tested functions the brain uses.

Contract pinned here:
  (a) non-channel-read turn → ok=True (observe-only, never block)
  (b) clean verbatim quotes after a channel read → ok=True
  (c) made-up quote / wikilink-in-quote after a channel read → ok=False
      with a corrective_injection carrying the raw tool data
  (d) a detector exception → fail-OPEN ok=True (a crashing detector must
      never break the specialist)
"""

from __future__ import annotations

import logging

import pytest

from lazyclaw.runtime.f1_gate import GroundingVerdict, evaluate_grounding


# ─── shared fixture: a realistic channel-read tool payload ───────────────
# Mirrors tests/runtime/test_f1_confabulation_detector.py so the gate is
# exercised against the same data shape the detector was validated on.

_JAMES_BLUE_PAYLOAD = (
    '{"messages": ['
    '{"sender": "James", "timestamp": "10:37 PM", '
    '"content": "We need an Upwork bot to auto-accept BPO assignments"},'
    '{"sender": "James", "timestamp": "10:30 PM", '
    '"content": "Narrowed the city list to 6 — Oakland, Hayward, '
    'San Leandro, Newark, San Jose, Cupertino"}'
    ']}'
)


# ─── (a) non-channel-read turn → ok=True (observe-only) ──────────────────


def test_non_channel_read_turn_is_ok() -> None:
    """No channel-read tool ran → gate must not block, even with quotes.

    Non-channel turns (research/code/web specialists) are observe-only:
    their "quotes" are often paraphrases of prior context, not lifts from
    a fresh channel read. Blocking them would be a massive false-positive.
    """
    reply = (
        "> Someone (now): a line that appears in no tool result at all\n"
        "Here is my summary of the work."
    )
    history = ["web_search", "extract_entities"]
    results = ["some web result text", "extracted entity data"]

    verdict = evaluate_grounding(reply, history, results)

    assert isinstance(verdict, GroundingVerdict)
    assert verdict.ok is True
    assert verdict.reason is None
    assert verdict.corrective_injection is None


def test_empty_history_is_ok() -> None:
    """No tools at all → trivially ok (nothing to ground against)."""
    verdict = evaluate_grounding("any reply text", [], [])
    assert verdict.ok is True
    assert verdict.corrective_injection is None


def test_empty_reply_on_channel_read_is_ok() -> None:
    """An empty draft makes no claims → clean."""
    verdict = evaluate_grounding("", ["upwork_last_conversation"],
                                 [_JAMES_BLUE_PAYLOAD])
    assert verdict.ok is True


# ─── (b) clean verbatim quotes after a channel read → ok=True ────────────


def test_clean_verbatim_quotes_after_channel_read_is_ok() -> None:
    """Quotes that appear verbatim in the tool result must pass."""
    reply = (
        "> James (10:37 PM): We need an Upwork bot to auto-accept BPO "
        "assignments\n"
        "> James (10:30 PM): Narrowed the city list to 6 — Oakland, "
        "Hayward, San Leandro, Newark, San Jose, Cupertino\n"
        "James wants an auto-accept bot for 6 specific cities."
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = evaluate_grounding(reply, history, results)

    assert verdict.ok is True, f"clean reply flagged: {verdict}"
    assert verdict.corrective_injection is None


def test_clean_short_acknowledgment_after_channel_read_is_ok() -> None:
    """Short acks make no factual claims — never block them."""
    reply = "Pulled the thread — let me draft a reply."
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = evaluate_grounding(reply, history, results)

    assert verdict.ok is True


# ─── (c) confabulation after a channel read → ok=False + injection ───────


def test_made_up_quote_after_channel_read_blocks() -> None:
    """Quotes whose content is nowhere in the tool result → block."""
    reply = (
        "> Vato (10:37 PM): I agree to $150 for the Windows laptop "
        "project, deadline May 16\n"
        "> Vato (10:30 PM): The scope includes DoorDash and Uber "
        "integration on TaskRabbit\n"
        "Summary follows."
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = evaluate_grounding(reply, history, results)

    assert verdict.ok is False
    assert verdict.reason is not None
    assert verdict.corrective_injection is not None
    # The corrective injection must spoon-feed the raw tool data verbatim.
    assert "Narrowed the city list to 6" in verdict.corrective_injection
    assert "MANDATORY REWRITE" in verdict.corrective_injection


def test_wikilink_in_quote_after_channel_read_blocks() -> None:
    """`[[X]]` inside a quote block is a hard memory-leak signal → block."""
    reply = (
        "Here's the latest from James Blue:\n\n"
        "> James Blue (9:12 PM): I have a Mac [[computer]] and iPad for "
        "the auto-accept bot project.\n\n"
        "He wants alerts on those devices."
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = evaluate_grounding(reply, history, results)

    assert verdict.ok is False
    assert verdict.reason is not None
    assert "wikilink" in verdict.reason.lower()
    assert verdict.corrective_injection is not None
    assert "[[computer]]" in verdict.corrective_injection
    assert "Narrowed the city list to 6" in verdict.corrective_injection


def test_failure_claim_after_successful_read_blocks() -> None:
    """Brain claims the tool failed but real data was returned → block."""
    reply = (
        "I checked your Upwork inbox but no Upwork conversations found. "
        "Make sure you're logged in to Upwork in the browser."
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = evaluate_grounding(reply, history, results)

    assert verdict.ok is False
    assert verdict.corrective_injection is not None
    assert "Narrowed the city list to 6" in verdict.corrective_injection


def test_phase2_unverified_quote_after_channel_read_blocks() -> None:
    """A single unverified quote on a channel-read turn (phase-2 path).

    ``upwork_get_conversation`` is a channel-read pattern recognized by
    ``phase2_enforcement_verdict``; a quote whose content is absent from
    the tool data must block even when the made-up-quote majority rule
    in ``detect_confabulation`` wouldn't fire.
    """
    reply = (
        "> James Blue (9:12 PM): I want a totally fabricated thing "
        "that doesn't appear in the tool data at all.\n"
    )
    history = ["upwork_get_conversation"]
    results = ['{"messages":[{"sender":"James","content":"Different content."}]}']

    verdict = evaluate_grounding(reply, history, results)

    assert verdict.ok is False
    assert verdict.reason is not None


def test_mcp_prefixed_channel_read_tool_is_recognized() -> None:
    """MCP-bridged channel reads (``mcp_<uuid>_upwork_get_messages``) gate."""
    reply = "No Upwork messages found — could not retrieve any data."
    history = ["mcp_aa828e97-7923-4189-b6e4-1f2ace89b115_upwork_get_messages"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = evaluate_grounding(reply, history, results)

    assert verdict.ok is False
    assert verdict.corrective_injection is not None


# ─── (d) detector exception → fail-OPEN ok=True ──────────────────────────


def test_detector_exception_fails_open(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A crashing detector must NOT break the specialist — fail open.

    We monkeypatch the SOURCE ``detect_confabulation`` (the gate imports it
    lazily from ``f1_confabulation_detector``) to raise. The gate must
    swallow it, log a warning, and return ok=True so the specialist still
    ships its reply.
    """
    import lazyclaw.runtime.f1_confabulation_detector as detector_mod

    def _boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise RuntimeError("simulated detector crash")

    monkeypatch.setattr(detector_mod, "detect_confabulation", _boom)

    caplog.set_level(logging.WARNING)
    reply = (
        "> Vato (10:37 PM): a fabricated quote that would normally block\n"
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = evaluate_grounding(reply, history, results)

    assert verdict.ok is True, "gate must fail OPEN on detector crash"
    assert verdict.corrective_injection is None
    # A warning should have been emitted so the crash isn't silent.
    assert any(
        "f1_gate" in r.name or "grounding" in r.message.lower()
        for r in caplog.records
    )


# ─── GroundingVerdict shape ──────────────────────────────────────────────


def test_grounding_verdict_is_immutable() -> None:
    """The verdict dataclass must be frozen (safe to log/forward)."""
    verdict = evaluate_grounding("hi", [], [])
    with pytest.raises(Exception):
        verdict.ok = False  # type: ignore[misc]
