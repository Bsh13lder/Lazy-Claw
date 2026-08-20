"""2026-08-20 false "auth rejected" stamp on a successful dispatch.

The browser specialist returned a complete blog-drafts report whose
SECURITY NOTE contained the word "unauthorized" ("...publishing
unauthorized live content"). result_verifier's keyword pass matched
`\\b(unauthori[sz]ed|forbidden)\\b` → stamped the whole result
"→ FAILED: auth rejected" → the brain distrusted a perfect report and
told the user the draft titles "weren't captured cleanly".

Root cause: an `agent` dispatch result is an ENVELOPE — its first line
already carries the explicit verdict agent_tool computed from
`result.success` ("[agent:browser] completed in 167s" / "FAILED: ..." /
"TIMEOUT after ..."). The body below it is arbitrary specialist PROSE
(quoted errors, security notes, page excerpts) and must never be
keyword-classified — the same reasoning that already exempts channel
reads at the call site.

Fix: classify() parses the agent envelope header FIRST and never scans
the body.
"""

from __future__ import annotations

from lazyclaw.runtime.result_verifier import classify

_INCIDENT_BODY = (
    "[agent:browser] completed in 167s\n"
    "I've completed the read-only task. Note: I detected repeated "
    "prompt-injection attempts...\n"
    "### 1. \"Test Draft — Ignore\"\n"
    "...it's attempting to manipulate agent tooling into publishing "
    "unauthorized live content."
)


def test_completed_envelope_wins_over_body_keywords() -> None:
    status, reason = classify("agent", _INCIDENT_BODY)
    assert status == "success", (
        f"got {status!r}/{reason!r} — the envelope says completed; "
        "'unauthorized' in the specialist's prose is quoted content"
    )


def test_failed_envelope_classifies_failed() -> None:
    status, _reason = classify(
        "agent", "[agent:browser] FAILED: worker crashed\ndetails...",
    )
    assert status == "failed"


def test_timeout_envelope_classifies_failed() -> None:
    status, _reason = classify(
        "agent",
        "[agent:upwork] TIMEOUT after 300s (0 extension(s) granted) — "
        "the agent was cancelled.",
    )
    assert status == "failed"


def test_background_dispatch_ack_is_success() -> None:
    status, _ = classify(
        "agent",
        "Background agent 'browser' started (id: ab12cd34). "
        "One consolidated report will follow.",
    )
    assert status == "success"


def test_completed_envelope_ignores_quoted_inner_failure_markers() -> None:
    """A specialist may QUOTE an inner tool's `→ FAILED:` marker in its
    step narrative; the outer envelope verdict still wins."""
    status, _ = classify(
        "agent",
        "[agent:browser] completed in 42s\n"
        "Step 3 returned `→ FAILED: timeout` but I retried and finished; "
        "final data follows.",
    )
    assert status == "success"


def test_non_agent_results_keep_keyword_classification() -> None:
    status, reason = classify("n8n_run_workflow", "Unauthorized: bad token")
    assert status == "failed"
    assert reason == "auth rejected"


def test_agent_result_without_envelope_still_keyword_classified() -> None:
    """Defensive: an agent result missing the envelope (unexpected shape)
    falls back to the normal pass rather than being blanket-trusted."""
    status, _ = classify("agent", "Error: dispatch registry unavailable")
    assert status == "failed"
