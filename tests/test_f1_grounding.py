"""Regression tests for F1 grounding enforcement.

Locks in the mechanical layers that fix the 2026-05-19 16:19 hallucination
(brain called upwork tools 3x, got real data, then fabricated $150 /
Windows laptop / May 16 / "Vato agreed" from training-data bias). The full
agent loop is not exercised here — that needs fixture-heavy setup. These
tests pin the module-level helpers so regressions are caught fast.

Three guards under test:
1. ``_is_channel_read_tool`` — classifies channel-read tools (incl. MCP
   ``mcp_<uuid>_`` prefixes) so the enforcer knows when to engage.
2. ``_check_f1_violation`` — pure validator that rejects drafts with
   banned 'from memory' phrases or substantive replies lacking a verbatim
   quote block.
3. ``_is_channel_thread_paraphrase`` — drops daily_log entries that are
   paraphrased dumps of channel conversations from the system prompt.
"""

from __future__ import annotations

import pytest

from lazyclaw.runtime import agent as agent_mod
from lazyclaw.runtime import context_builder as ctx_mod


# ─── _is_channel_read_tool ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "upwork_last_conversation",
        "upwork_inbox_check",
        "upwork_contract_poll",
        # MCP-bridged tools — uuid prefix must not block detection.
        "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_get_messages",
        "mcp_ce2f19a7-bd2a-4259-a669-89b316165a46_upwork_get_conversation",
        "mcp_aa828e97-7923-4189-b6e4-1f2ace89b115_upwork_get_unread_count",
        # Bare suffixes — wired via NL skill names.
        "whatsapp_read",
        "instagram_read_dms",
        "email_list_chats",
        "telegram_read_feed",
        # Mixed case — classifier is case-insensitive.
        "Upwork_Last_Conversation",
        "MCP_489C8963-CDC0-4937-8470-15E6BA9B6E4C_UPWORK_GET_MESSAGES",
    ],
)
def test_is_channel_read_tool_recognizes_reads(name: str) -> None:
    assert agent_mod._is_channel_read_tool(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "",
        # Action tools — sending / submitting are NOT reads.
        "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_send_message",
        "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_submit_proposal",
        "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_accept_offer",
        # Discovery tools — these aren't channel reads, they're meta.
        "search_tools",
        "recall_memories",
        "save_memory",
        "run_background",
        # Browser / web — also not channel reads.
        "browser",
        "web_search",
        # Random plausible name that ends with a non-channel suffix.
        "list_tasks",
    ],
)
def test_is_channel_read_tool_rejects_non_reads(name: str) -> None:
    assert agent_mod._is_channel_read_tool(name) is False


def test_is_channel_read_tool_handles_none_safely() -> None:
    # The agent loop occasionally calls into this with names that are None
    # when a malformed tool_call slipped past the registry — must not raise.
    assert agent_mod._is_channel_read_tool(None) is False  # type: ignore[arg-type]


# ─── _check_f1_violation ──────────────────────────────────────────────────


# The 2026-05-19 16:19 actual draft head — the exact phrase that should
# have been caught. Kept verbatim as the canonical regression fixture.
_REAL_INCIDENT_DRAFT_HEAD = (
    "Already pulled the thread above — here's the summary:\n\n"
    "Thread: 6 total messages (2 from James, 4 from you)\n\n"
    "James Blue's job: BPO scraper, $150 budget, 3-day deadline, Windows "
    "laptop required, due May 16. Vato agreed $150 / 3 days."
)


def test_check_f1_violation_catches_real_incident_draft() -> None:
    """The exact 2026-05-19 16:19 draft must be rejected."""
    tool_results = [
        "James Blue: I need someone to write an auto-accept bot for eStreet "
        "AMC BPO assignments. $120 budget, due Tuesday."
    ]
    reason = agent_mod._check_f1_violation(
        _REAL_INCIDENT_DRAFT_HEAD, tool_results,
    )
    assert reason is not None
    assert "already pulled" in reason.lower()


@pytest.mark.parametrize(
    "phrase",
    [
        "Already pulled the thread above — here's a summary.",
        "I already have the messages from earlier.",
        "I already got the conversation details from our chat.",
        "From our chat earlier, James wants a BPO scraper.",
        "From the conversation yesterday, the scope is 6 cities.",
        "Based on what he said, $120 is the budget.",
        "Based on what they wrote, they're using an iPhone.",
        "Based on what she told you, the deadline is Tuesday.",
        "From memory, I recall James mentioned the credentials.",
        "I recall from a prior message that the rate is $20/h.",
        "I remember that James is on an iPad.",
        "I remember from earlier that he wants 6 cities.",
        "As we discussed earlier, the deliverable is a CLI.",
        "As I saw earlier, James needs the bot by Tuesday.",
        "Per our earlier exchange, $120 is locked in.",
    ],
)
def test_check_f1_violation_catches_banned_phrases(phrase: str) -> None:
    reason = agent_mod._check_f1_violation(
        phrase + " " * 300,  # pad to substantive length
        ["James Blue: real tool data here that the draft ignored."],
    )
    assert reason is not None, f"Expected banned-phrase match for: {phrase!r}"
    assert "banned phrase" in reason


def test_check_f1_violation_passes_short_acknowledgment() -> None:
    # Below the 280-char threshold AND no banned phrase — ship it.
    draft = "Let me re-read the thread first — pulling now."
    reason = agent_mod._check_f1_violation(
        draft, ["any tool result"],
    )
    assert reason is None


def test_check_f1_violation_passes_grounded_reply_with_quote_block() -> None:
    draft = (
        "> James Blue (2026-05-19 16:13): I need an auto-accept bot for "
        "eStreet AMC BPO assignments. $120 budget, due Tuesday.\n"
        "> James Blue (2026-05-19 16:14): broker510@gmail.com / Jb042244\n\n"
        "Scope per James's most recent message: $120 budget, BPO auto-accept "
        "bot, due Tuesday. Credentials provided. I'll start scaffolding now."
    )
    reason = agent_mod._check_f1_violation(
        draft, ["James: I need a bot. $120 budget."],
    )
    assert reason is None, reason


def test_check_f1_violation_flags_substantive_reply_without_quotes() -> None:
    # No banned phrase, but the brain wrote 400+ chars of summary without
    # quoting the source. This is the silent-fabrication path.
    draft = (
        "James needs an auto-accept bot for BPO assignments. Budget is "
        "$120 due Tuesday. He'll provide credentials separately. The "
        "deliverable is a CLI that polls the eStreet portal and accepts "
        "matching jobs. I'll set up a Playwright scaffold and wire up the "
        "appraisal-ID base64 decoder once I have the login URL. Should be "
        "doable in a day or two of focused work given the scope.\n"
    )
    reason = agent_mod._check_f1_violation(
        draft, ["James: I need a bot. $120. Due Tuesday."],
    )
    assert reason is not None
    assert "quote block" in reason


def test_check_f1_violation_passes_when_no_tool_results() -> None:
    # If the tool returned nothing, we cannot demand quotes — the brain
    # has nothing to quote. F1 only fires against tool data that exists.
    draft = (
        "I checked your Upwork inbox. No new messages from James in the "
        "last 24 hours. Want me to ping him with a status update? Happy "
        "to draft something — or you can paste the latest exchange and "
        "I'll fold it in. Either way works for me."
    )
    reason = agent_mod._check_f1_violation(draft, ["", "   ", ""])
    assert reason is None


def test_check_f1_violation_passes_empty_draft() -> None:
    assert agent_mod._check_f1_violation("", ["anything"]) is None


# ─── _is_channel_thread_paraphrase ────────────────────────────────────────


@pytest.mark.parametrize(
    "summary_head",
    [
        # The exact 2026-05-16 daily_log head that contaminated 16:19.
        "## Last Upwork Conversation\n**Contact:** James Blue\n"
        "**Thread Time:** 10:34 PM → 12:21 AM\n### Messages:",
        "## Last WhatsApp Conversation\n**Contact:** Buchvardi",
        "## Last Instagram Conversation\n**Contact:** someone",
        "## Last Email Thread\n**From:** james@example.com",
        # Lowercase / mixed case still matches.
        "## last telegram conversation\n**contact:** the user",
    ],
)
def test_is_channel_thread_paraphrase_flags_dump_headers(
    summary_head: str,
) -> None:
    assert ctx_mod._is_channel_thread_paraphrase(summary_head) is True


@pytest.mark.parametrize(
    "summary",
    [
        # Normal diary entries — must NOT be filtered.
        "Spent 2 hours debugging the agent runtime. Found a race in lane "
        "queue cleanup. Fixed by moving the cleanup into a lock guard.",
        "Pushed the F2 grounding fix to feat/claude-agent-sdk. Tests "
        "passing. Code goal session persistence still wedged on resume.",
        "User asked about James Blue's contract status — replied with the "
        "current Upwork queue summary.",  # mentions James but isn't a dump
        "",
        "   ",
    ],
)
def test_is_channel_thread_paraphrase_passes_normal_diary(summary: str) -> None:
    assert ctx_mod._is_channel_thread_paraphrase(summary) is False


# ─── End-to-end sanity: the three layers compose ──────────────────────────


def test_f1_layers_compose_on_real_incident_pattern() -> None:
    """All three layers must agree on the canonical incident pattern.

    Layer 0 (context filter): the contaminating daily_log header is
        dropped from the prompt.
    Layer 1 (read-tool classifier): the actual tool that ran
        (``upwork_last_conversation``) is correctly identified as a
        channel read, triggering enforcement.
    Layer 2 (draft validator): the bad draft is rejected with a clear
        reason that the correction prompt can quote back to the brain.
    """
    contaminating_log = (
        "## Last Upwork Conversation\n**Contact:** James Blue\n"
        "**Thread Time:** 10:34 PM → 12:21 AM\n"
        "### Messages:\n1. Vato (you): $120, 1 week deadline"
    )
    assert ctx_mod._is_channel_thread_paraphrase(contaminating_log) is True

    tools_called_this_turn = [
        "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_get_messages",
        "upwork_last_conversation",
    ]
    assert any(
        agent_mod._is_channel_read_tool(n) for n in tools_called_this_turn
    )

    bad_draft = _REAL_INCIDENT_DRAFT_HEAD
    real_tool_result = (
        "James Blue (2026-05-18 22:34): I need an auto-accept bot.\n"
        "James Blue (2026-05-19 16:13): $120 budget, due Tuesday."
    )
    reason = agent_mod._check_f1_violation(bad_draft, [real_tool_result])
    assert reason is not None
