"""Unit tests for F1 confabulation detector.

Pins the two failure modes the detector closes:

1. **Failure-claim confabulation** — reply asserts a tool failed BUT
   tool_results contains real data. The 2026-05-20 14:23 incident:
   brain got real James Blue data from ``upwork_last_conversation``
   then panicked under F1 retry pressure and emitted *"No Upwork
   conversations found, make sure you're logged in"*.

2. **Made-up-quote confabulation** — reply has ``> sender (ts): …``
   quote lines whose content doesn't appear anywhere in tool_results.

Tests also pin the wiring contract:
- Truthful failure claims (when no successful read happened) do NOT trip.
- Clean replies whose quotes match tool data do NOT trip.
- Raw-data injection builder includes the actual payload and instructs
  quote-then-summarize formatting.
"""

from __future__ import annotations

import logging
import re

import pytest

from lazyclaw.runtime.f1_confabulation_detector import (
    ConfabulationVerdict,
    build_raw_data_injection,
    detect_confabulation,
)


# ─── helper: build a realistic upwork tool result payload ────────────────


_JAMES_BLUE_PAYLOAD = (
    '{"messages": ['
    '{"sender": "James", "timestamp": "10:37 PM", '
    '"content": "We need an Upwork bot to auto-accept BPO assignments"},'
    '{"sender": "James", "timestamp": "10:30 PM", '
    '"content": "Narrowed the city list to 6 — Oakland, Hayward, '
    'San Leandro, Newark, San Jose, Cupertino"}'
    ']}'
)


# ─── failure-claim confabulation ─────────────────────────────────────────


def test_failure_claim_with_successful_read_is_confabulation() -> None:
    """The exact 2026-05-20 14:23 bug: brain got real data, then lied."""
    reply = (
        "I checked your Upwork inbox but no Upwork conversations found. "
        "Make sure you're logged in to Upwork in the browser."
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = detect_confabulation(reply, history, results)

    assert verdict.is_confabulation is True
    assert verdict.kind == "failure_claim"
    assert verdict.tool_name == "upwork_last_conversation"
    assert verdict.payload_bytes == len(_JAMES_BLUE_PAYLOAD)
    assert verdict.offending_phrase  # has the matched substring


def test_failure_claim_with_no_successful_read_is_truthful() -> None:
    """When no tool succeeded, the failure claim is honest — don't flag."""
    reply = "I couldn't fetch the data — the read tool returned nothing."
    history: list[str] = []  # No tools were called this turn.
    results: list[str] = []

    verdict = detect_confabulation(reply, history, results)

    assert verdict.is_confabulation is False
    assert verdict.kind == ""


def test_failure_claim_with_tiny_empty_payload_is_truthful() -> None:
    """Tool returned ``[]`` (empty list) — "no data" is a fair claim."""
    reply = "No matching threads — make sure you're logged in."
    history = ["upwork_last_conversation"]
    results = ["[]"]  # 2 bytes after whitespace strip — below floor.

    verdict = detect_confabulation(reply, history, results)

    assert verdict.is_confabulation is False


def test_make_sure_logged_in_phrase_matches() -> None:
    """The hallmark phrase from the bug report must trip detection."""
    reply = "Make sure you are logged in to Upwork."
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]
    verdict = detect_confabulation(reply, history, results)
    assert verdict.is_confabulation is True
    assert "logged in" in verdict.offending_phrase.lower()


# ─── made-up-quote confabulation ─────────────────────────────────────────


def test_quotes_matching_tool_data_pass() -> None:
    """Real quotes that appear verbatim in tool_results — clean."""
    reply = (
        "> James (10:37 PM): We need an Upwork bot to auto-accept BPO "
        "assignments\n"
        "> James (10:30 PM): Narrowed the city list to 6 — Oakland, "
        "Hayward, San Leandro, Newark, San Jose, Cupertino\n"
        "James wants an auto-accept bot for 6 specific cities."
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = detect_confabulation(reply, history, results)

    assert verdict.is_confabulation is False, (
        f"Real quotes flagged as confab: {verdict}"
    )


def test_made_up_quotes_are_confabulation() -> None:
    """Quotes with content nowhere in tool_results — inverse confab."""
    reply = (
        "> Vato (10:37 PM): I agree to $150 for the Windows laptop "
        "project, deadline May 16\n"
        "> Vato (10:30 PM): The scope includes DoorDash and Uber "
        "integration on TaskRabbit\n"
        "Summary follows."
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]  # Talks about BPO + 6 cities, not Vato/Windows.

    verdict = detect_confabulation(reply, history, results)

    assert verdict.is_confabulation is True
    assert verdict.kind == "made_up_quote"
    assert verdict.unverified_quote_count >= 1
    assert verdict.tool_name == "upwork_last_conversation"


def test_no_tool_called_no_quote_check() -> None:
    """If no read tool ran, the quote check shouldn't fire."""
    reply = "> Someone (now): hello world that wasn't in any tool result"
    history: list[str] = []
    results: list[str] = []

    verdict = detect_confabulation(reply, history, results)

    assert verdict.is_confabulation is False


# ─── mcp_<uuid>_ prefix handling ─────────────────────────────────────────


def test_mcp_prefixed_tool_name_is_recognized() -> None:
    """MCP-bridged tools like ``mcp_abc-123_upwork_get_messages`` count."""
    reply = "No Upwork messages found — could not retrieve any data."
    history = ["mcp_aa828e97-7923-4189-b6e4-1f2ace89b115_upwork_get_messages"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = detect_confabulation(reply, history, results)

    assert verdict.is_confabulation is True
    assert verdict.kind == "failure_claim"


# ─── raw-data injection builder ─────────────────────────────────────────


def test_raw_data_injection_includes_payload_and_directive() -> None:
    """The injected message must contain the raw payload + quote directive."""
    verdict = ConfabulationVerdict(
        is_confabulation=True,
        kind="failure_claim",
        offending_phrase="no Upwork conversations found",
        unverified_quote_count=0,
        tool_name="upwork_last_conversation",
        payload_bytes=len(_JAMES_BLUE_PAYLOAD),
    )
    msg = build_raw_data_injection(
        verdict,
        ["upwork_last_conversation"],
        [_JAMES_BLUE_PAYLOAD],
        reason="confabulation",
    )

    assert "CONFABULATION DETECTED" in msg
    assert "upwork_last_conversation" in msg
    assert "Narrowed the city list to 6" in msg, "raw payload missing"
    assert "> {sender}" in msg, "quote directive missing"
    assert "Do NOT claim the tool failed" in msg


def test_raw_data_injection_truncates_huge_payload() -> None:
    """Payloads over ~4000 chars get truncated with a marker."""
    huge_payload = '{"junk":"' + ("x" * 10000) + '"}'
    verdict = ConfabulationVerdict(
        is_confabulation=True,
        kind="failure_claim",
        offending_phrase="no data",
        unverified_quote_count=0,
        tool_name="upwork_last_conversation",
        payload_bytes=len(huge_payload),
    )
    msg = build_raw_data_injection(
        verdict,
        ["upwork_last_conversation"],
        [huge_payload],
        reason="confabulation",
    )
    assert "...[truncated]" in msg
    # Message shouldn't be wildly bigger than the truncate limit + overhead.
    assert len(msg) < 6000


def test_raw_data_injection_retry_exhausted_reason() -> None:
    """When called for retry-exhaustion, the opening wording differs."""
    verdict = ConfabulationVerdict(
        is_confabulation=False,
        kind="",
        offending_phrase="",
        unverified_quote_count=0,
        tool_name="upwork_last_conversation",
        payload_bytes=0,
    )
    msg = build_raw_data_injection(
        verdict,
        ["upwork_last_conversation"],
        [_JAMES_BLUE_PAYLOAD],
        reason="retry_exhausted",
    )
    assert "F1 retries exhausted" in msg
    assert "quote-then-summarize" in msg
    assert "Narrowed the city list to 6" in msg


# ─── wiring contract: clean payload + clean reply ────────────────────────


def test_clean_short_acknowledgment_passes() -> None:
    """Short acknowledgments make no claims — don't flag them."""
    reply = "Pulled the thread — let me draft a reply."
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = detect_confabulation(reply, history, results)

    assert verdict.is_confabulation is False


def test_empty_reply_returns_clean() -> None:
    """Empty draft → clean verdict (nothing to check)."""
    verdict = detect_confabulation("", ["upwork_last_conversation"],
                                   [_JAMES_BLUE_PAYLOAD])
    assert verdict.is_confabulation is False


# ─── retry-exhaustion simulation tests (integration-shaped) ─────────────


class _FakeRetryLoop:
    """Minimal simulator of the F1 retry loop in agent.py.

    Mirrors the contract: when the brain's draft is still confabulated /
    F1-violating after ``f1_max_retries``, raw-data injection should fire
    once and then degraded-accept should ship the next draft.
    """

    def __init__(self, drafts: list[str], f1_max_retries: int = 2):
        self.drafts = list(drafts)
        self.f1_max_retries = f1_max_retries
        self.injections: list[str] = []
        self.f1_retries = 0
        self.confab_injected = False
        self.shipped_draft: str | None = None
        self.degraded_accept = False

    def step(
        self,
        tool_history: list[str],
        tool_results: list[str],
        f1_check_fn,
    ) -> None:
        """Drain the drafts queue, mimicking the agent loop."""
        for draft in self.drafts:
            # F1 phase-1 check
            violation = f1_check_fn(draft)
            if violation and self.f1_retries < self.f1_max_retries:
                self.f1_retries += 1
                continue
            # Confabulation backstop (post-F1 or post-exhaustion)
            verdict = detect_confabulation(draft, tool_history, tool_results)
            exhausted_with_violation = (
                self.f1_retries >= self.f1_max_retries
                and f1_check_fn(draft) is not None
            )
            if (
                not self.confab_injected
                and (verdict.is_confabulation or exhausted_with_violation)
            ):
                self.injections.append(
                    build_raw_data_injection(
                        verdict, tool_history, tool_results,
                        reason="confabulation"
                        if verdict.is_confabulation else "retry_exhausted",
                    )
                )
                self.confab_injected = True
                continue
            # Degraded accept: already injected once and still bad → ship.
            if (
                self.confab_injected
                and f1_check_fn(draft) is not None
            ):
                self.degraded_accept = True
            self.shipped_draft = draft
            return


def _fake_f1_violation_check(draft: str) -> str | None:
    """Minimal F1 phase-1 stand-in: substantive reply must have ``> `` line."""
    if not draft:
        return None
    if "from memory" in draft.lower():
        return "banned phrase"
    if len(draft) >= 280 and not re.search(r"^>\s+\S", draft, re.MULTILINE):
        return "missing quote block"
    return None


def test_retry_exhaustion_triggers_raw_data_injection() -> None:
    """After F1 burns through retries, the 3rd attempt receives raw data."""
    long_paraphrase = (
        "James wants a bot from memory of our earlier chat. " * 20
    )  # >280 chars, no quote block, contains banned phrase → violates F1.
    loop = _FakeRetryLoop(
        drafts=[long_paraphrase, long_paraphrase, long_paraphrase],
        f1_max_retries=2,
    )
    loop.step(
        ["upwork_last_conversation"],
        [_JAMES_BLUE_PAYLOAD],
        _fake_f1_violation_check,
    )
    # Two F1 retries consumed by the first two drafts, then the third
    # triggers the raw-data injection path.
    assert loop.f1_retries == 2
    assert len(loop.injections) == 1
    assert "Narrowed the city list to 6" in loop.injections[0]


def test_degraded_accept_when_injection_doesnt_help() -> None:
    """3rd violation after injection — ship the draft, log degraded."""
    long_paraphrase = (
        "James wants a bot from memory of our earlier chat. " * 20
    )
    loop = _FakeRetryLoop(
        drafts=[
            long_paraphrase,  # F1 retry 1 consumed
            long_paraphrase,  # F1 retry 2 consumed
            long_paraphrase,  # confab injection consumed
            long_paraphrase,  # still bad → degraded accept
        ],
        f1_max_retries=2,
    )
    loop.step(
        ["upwork_last_conversation"],
        [_JAMES_BLUE_PAYLOAD],
        _fake_f1_violation_check,
    )
    assert loop.confab_injected is True
    assert loop.degraded_accept is True
    assert loop.shipped_draft is not None


def test_confabulation_detector_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify the detector module emits no log itself (caller's job)."""
    caplog.set_level(logging.WARNING)
    reply = "No Upwork conversations found. Make sure you're logged in."
    detect_confabulation(
        reply,
        ["upwork_last_conversation"],
        [_JAMES_BLUE_PAYLOAD],
    )
    # The detector is pure — no logs from this module. Logs are emitted
    # by agent.py at the call site.
    detector_logs = [
        r for r in caplog.records
        if "f1_confabulation_detector" in r.name
    ]
    assert detector_logs == []
