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

from lazyclaw.runtime.agent import _MAX_TOOL_RESULT_CHARS_CHANNEL_READ
from lazyclaw.runtime.f1_confabulation_detector import (
    ConfabulationVerdict,
    _RAW_INJECTION_TRUNCATE_CHARS,
    _find_wikilink_in_quote_block,
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

    assert "MANDATORY REWRITE" in msg
    assert "CONFABULATION DETECTED" in msg
    assert "upwork_last_conversation" in msg
    assert "Narrowed the city list to 6" in msg, "raw payload missing"
    assert "> {sender}" in msg, "quote directive missing"
    assert "Do NOT claim the tool failed" in msg
    assert "character-for-character" in msg, "raw-data fidelity rule missing"
    assert "FORBIDDEN" in msg, "wikilink prohibition missing"


def test_raw_data_injection_truncates_huge_payload() -> None:
    """Payloads over the 50K channel-read ceiling still get truncated."""
    huge_payload = '{"junk":"' + ("x" * 60000) + '"}'
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
    assert len(msg) < _RAW_INJECTION_TRUNCATE_CHARS + 2000


def test_raw_injection_cap_matches_channel_read_cap() -> None:
    """Regression: the F1 repair re-injection cap must equal the 50K
    channel-read result cap. A 4 KB cap here silently re-introduced the
    2026-05-24 truncation incident (10.9 KB Upwork JSON chopped to 4 KB,
    F1 retry saw only the first 2-3 messages, brain re-confabulated
    "no new messages"). See agent._MAX_TOOL_RESULT_CHARS_CHANNEL_READ.
    """
    assert _RAW_INJECTION_TRUNCATE_CHARS == 50000
    assert (
        _RAW_INJECTION_TRUNCATE_CHARS
        == _MAX_TOOL_RESULT_CHARS_CHANNEL_READ
    )


def test_raw_injection_preserves_payload_over_4kb() -> None:
    """A >4 KB read payload must survive un-truncated into the repair
    prompt (the old 4000-char cap would have chopped it).
    """
    # 8 KB of distinct, quotable conversation text — well over the old
    # 4 KB cap, well under the new 50 KB ceiling.
    sentinel_tail = "FINAL_MESSAGE_SENTINEL_QUOTE_ME"
    big_payload = (
        '{"messages":["'
        + ("padding message number with lots of words. " * 180)
        + sentinel_tail
        + '"]}'
    )
    assert len(big_payload) > 4000
    assert len(big_payload) < 50000
    verdict = ConfabulationVerdict(
        is_confabulation=True,
        kind="failure_claim",
        offending_phrase="no data",
        unverified_quote_count=0,
        tool_name="upwork_last_conversation",
        payload_bytes=len(big_payload),
    )
    msg = build_raw_data_injection(
        verdict,
        ["upwork_last_conversation"],
        [big_payload],
        reason="confabulation",
    )
    # Whole payload preserved — no truncation marker, and the LAST
    # message (which a 4 KB cap would have dropped) is present.
    assert "...[truncated]" not in msg
    assert sentinel_tail in msg


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
    assert "MANDATORY REWRITE" in msg
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


# ─── wikilink-in-quote confabulation (memory leak from lazybrain) ────────


def test_wikilink_in_quote_detected() -> None:
    """`[[X]]` inside a `> sender (ts): ...` block is a hard memory-leak signal.

    The 2026-05-21 22:13 incident: brain quoted James Blue's "Mac
    [[computer]] iPad" message — the [[computer]] proves the content came
    from `reference_james_blue_contract.md`, not the live Upwork chat.
    Real contacts never send wikilink syntax.
    """
    reply = (
        "Here's the latest from James Blue:\n\n"
        "> James Blue (9:12 PM): I have a Mac [[computer]] and iPad for "
        "the auto-accept bot project.\n\n"
        "He wants alerts on those devices."
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = detect_confabulation(reply, history, results)

    assert verdict.is_confabulation is True
    assert verdict.kind == "wikilink_in_quote"
    assert verdict.offending_phrase == "[[computer]]"
    assert verdict.tool_name == "upwork_last_conversation"


def test_wikilink_in_non_quote_section_ignored() -> None:
    """Wikilinks in headers / free prose are fine — only quote-block matters."""
    reply = (
        "## See note: [[james_blue_contract]]\n\n"
        "The thread covers: project scope, [[computer]] devices, deadline.\n\n"
        "> James (10:30 PM): Narrowed the city list to 6 — Oakland, Hayward, "
        "San Leandro, Newark, San Jose, Cupertino"
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = detect_confabulation(reply, history, results)

    # The wikilinks are in headers / free prose, NOT in the quote block.
    # The quote block content IS in the payload. → clean verdict.
    assert verdict.is_confabulation is False
    assert verdict.kind == ""


def test_retry_prompt_includes_mandatory_rewrite_directive() -> None:
    """The new retry prompt must be imperative + character-anchored."""
    verdict = ConfabulationVerdict(
        is_confabulation=True,
        kind="wikilink_in_quote",
        offending_phrase="[[computer]]",
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

    assert "MANDATORY REWRITE" in msg
    assert "MEMORY LEAK DETECTED" in msg
    assert "[[computer]]" in msg, "offending wikilink must be cited"
    assert "character-for-character" in msg
    assert "FORBIDDEN" in msg
    assert "RAW DATA" in msg
    assert "Narrowed the city list to 6" in msg, "raw payload missing"


# ─── widened wikilink detector: bold + plain sender shapes (2026-05-22) ──


def test_wikilink_in_bold_sender_format_detected() -> None:
    """`**Sender (ts):** ... [[X]] ...` is the 2026-05-22 10:09 incident shape.

    The brain switched from Markdown blockquotes (`> Sender (ts):`) to
    bold-sender prefix (`**Sender (ts):**`). Yesterday's detector only
    scanned `>`-prefixed lines, so `[[computer]]` slipped through and
    only the slower content-mismatch verifier caught it. The widened
    detector must flag this shape directly.
    """
    reply = (
        "Here's what James said:\n\n"
        "**James Blue (9:12 PM):** Mac [[computer]] iPad for the bot.\n\n"
        "He wants alerts on those devices."
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = detect_confabulation(reply, history, results)

    assert verdict.is_confabulation is True
    assert verdict.kind == "wikilink_in_quote"
    assert verdict.offending_phrase == "[[computer]]"
    assert verdict.tool_name == "upwork_last_conversation"


def test_wikilink_in_plain_sender_format_detected() -> None:
    """`Sender Name (HH:MM): ... [[X]] ...` — third quote shape.

    Plain-text sender prefix with no Markdown decoration. Common when
    the brain forgets formatting entirely but still attributes the line.
    """
    reply = (
        "Latest from the thread:\n\n"
        "James Blue (9:12 PM): I have a Mac [[computer]] and iPad for "
        "the auto-accept bot project.\n\n"
        "He wants alerts on those devices."
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = detect_confabulation(reply, history, results)

    assert verdict.is_confabulation is True
    assert verdict.kind == "wikilink_in_quote"
    assert verdict.offending_phrase == "[[computer]]"


def test_wikilink_in_blockquote_still_detected() -> None:
    """Regression guard: the original `>` blockquote shape must still trip."""
    reply = (
        "From James:\n\n"
        "> James Blue (9:12 PM): I have a Mac [[computer]] and iPad.\n\n"
        "Alerts requested."
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = detect_confabulation(reply, history, results)

    assert verdict.is_confabulation is True
    assert verdict.kind == "wikilink_in_quote"
    assert verdict.offending_phrase == "[[computer]]"


def test_wikilink_in_free_prose_not_flagged() -> None:
    """Wikilinks in headers / free prose / non-sender lines must NOT trip.

    Three subtleties matter:
      - Header line with wikilink (`## See [[X]]`) is fine.
      - Free prose with wikilink and no sender prefix is fine.
      - Quote block whose content matches tool data should pass cleanly.
    """
    reply = (
        "## See note: [[james_blue_contract]] for full context\n\n"
        "The thread discusses the [[computer]] devices the user owns. "
        "Notes also reference [[upwork_contracts]] in passing.\n\n"
        "> James (10:30 PM): Narrowed the city list to 6 — Oakland, "
        "Hayward, San Leandro, Newark, San Jose, Cupertino"
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = detect_confabulation(reply, history, results)

    assert verdict.is_confabulation is False, (
        f"Free-prose wikilinks flagged as confab: {verdict}"
    )
    assert verdict.kind == ""


def test_wikilink_in_inline_markdown_link_not_flagged() -> None:
    """`[text](url)` is markdown link syntax — single brackets, not `[[X]]`.

    Defensive: ensure the detector doesn't confuse inline links with
    wikilinks. Single-bracket inline links must pass cleanly even when
    they sit next to sender-prefixed lines.
    """
    reply = (
        "Reference doc: [contract spec](https://example.com/spec)\n\n"
        "**James Blue (9:12 PM):** Narrowed the city list to 6 — Oakland, "
        "Hayward, San Leandro, Newark, San Jose, Cupertino\n\n"
        "See [the brief](https://x.com/brief) for next steps."
    )
    history = ["upwork_last_conversation"]
    results = [_JAMES_BLUE_PAYLOAD]

    verdict = detect_confabulation(reply, history, results)

    # No `[[X]]` syntax anywhere → wikilink detector must not trip. The
    # bold-sender line's content IS in the payload, so the made-up-quote
    # check also passes. Verdict should be clean.
    assert verdict.is_confabulation is False, (
        f"Inline markdown link confused with wikilink: {verdict}"
    )


# ─── Continuation-line wikilink scan (2026-05-23 fix) ────────────────────


def test_find_wikilink_catches_multiline_blockquote_continuation() -> None:
    """Multi-line `>` blockquote where wikilink is on a continuation line.

    The brain often splits a long quote across multiple `>` lines. The
    per-line scan already iterates them; this test pins the existing
    behavior so a future refactor can't regress it.
    """
    reply = (
        "Quoting James verbatim:\n"
        "> James Blue (9:12 PM): Which platform am I monitoring?\n"
        "> Mac [[computer]] Once I have these I can start the same day.\n"
    )
    assert _find_wikilink_in_quote_block(reply) == "[[computer]]"


def test_find_wikilink_catches_bold_sender_with_naked_continuation() -> None:
    """Bold-sender + naked continuation line — the bug fixed today.

    Before the fix, only the sender-prefix line itself was scanned for
    wikilinks. The brain emits multi-paragraph quotes where the bold
    `**Sender:**` opens the span and subsequent paragraphs are NAKED
    continuation lines (no `>`, no `**`). `[[computer]]` survived the
    detector here.
    """
    reply = (
        "**James Blue (9:12 PM):** Which platform am I monitoring?\n"
        "Property scraper question?\n"
        "Mac [[computer]] Once I have these I can start the same day.\n"
    )
    assert _find_wikilink_in_quote_block(reply) == "[[computer]]"


def test_find_wikilink_catches_plain_sender_with_naked_continuation() -> None:
    reply = (
        "James Blue (9:12 PM): Which platform am I monitoring?\n"
        "Mac [[computer]] Once I have these I can start the same day.\n"
    )
    assert _find_wikilink_in_quote_block(reply) == "[[computer]]"


def test_find_wikilink_resets_on_blank_line() -> None:
    """Blank line between sender and wikilink → wikilink is in fresh prose.

    Distinguishes "wikilink inside a contact's multi-paragraph quote"
    (bad — memory leak) from "wikilink in the brain's own commentary
    after the quote ends" (legitimate — user-facing note reference).
    """
    reply = (
        "**James Blue (9:12 PM):** Which platform am I monitoring?\n"
        "\n"  # blank line ends the quote span
        "I'll save this to [[contact_James]] for follow-up.\n"
    )
    assert _find_wikilink_in_quote_block(reply) is None


def test_find_wikilink_resets_on_horizontal_rule() -> None:
    """`---` horizontal rule also terminates the open quote span."""
    reply = (
        "**James (9:12 PM):** First message\n"
        "---\n"
        "Saved to [[contact_James]].\n"
    )
    assert _find_wikilink_in_quote_block(reply) is None


# ─── Phase-2 enforcement helper ──────────────────────────────────────────


def test_phase2_enforcement_blocks_on_channel_read_turn_with_unverified() -> None:
    from lazyclaw.runtime.f1_content_verifier import phase2_enforcement_verdict

    reply = (
        "> James Blue (9:12 PM): I want a totally fabricated thing "
        "that doesn't appear in the tool data at all.\n"
    )
    history = ["upwork_get_conversation"]
    results = ['{"messages":[{"sender":"James","content":"Different content."}]}']

    should_block, reason = phase2_enforcement_verdict(reply, history, results)

    assert should_block is True
    assert "unverified" in reason


def test_phase2_enforcement_passes_clean_quotes() -> None:
    from lazyclaw.runtime.f1_content_verifier import phase2_enforcement_verdict

    quoted = "I want X."
    reply = f"> James Blue (9:12 PM): {quoted}\n"
    history = ["upwork_get_conversation"]
    results = [f'{{"messages":[{{"sender":"James","content":"{quoted}"}}]}}']

    should_block, reason = phase2_enforcement_verdict(reply, history, results)

    assert should_block is False, reason


def test_phase2_enforcement_skips_non_channel_turn() -> None:
    """Even with unverified quotes, non-channel turns stay observation-only.

    Phase-2 is too noisy on internal chat / planning replies where
    "quotes" are paraphrases of earlier conversation, not lifts from
    a fresh channel-read tool result.
    """
    from lazyclaw.runtime.f1_content_verifier import phase2_enforcement_verdict

    reply = "> James Blue (9:12 PM): garbage content\n"
    history = ["lazybrain_recall"]  # not a channel-read tool
    results = ["no match"]

    should_block, _ = phase2_enforcement_verdict(reply, history, results)
    assert should_block is False


def test_phase2_enforcement_handles_empty_inputs() -> None:
    from lazyclaw.runtime.f1_content_verifier import phase2_enforcement_verdict

    should_block, _ = phase2_enforcement_verdict("", [], [])
    assert should_block is False
    should_block, _ = phase2_enforcement_verdict("no quotes here", ["upwork_get_conversation"], [])
    assert should_block is False
