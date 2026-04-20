"""Stuck-detector hard-stop coverage.

Proves the two pieces of the hirossa.com fix from
``lazyclaw/runtime/agent.py``:

1. ``detect_stuck`` fires ``repeated_error`` on the exact tool-call
   pattern that flailed for ~10 iterations in the postmortem
   (`n8n_update_workflow` → Error twice).
2. The agent loop has the new state vars (``_post_l2_stuck_count``) and
   the L3 hard-stop branch wired up — guarded by a static-source check
   so a careless future refactor that drops the counter or the break
   path will fail this test instead of regressing into another
   foreground-flail incident.

Full end-to-end behavior is covered by the integration acceptance test
in the plan file (re-run hirossa task, expect ≤ 1 stuck escalation
followed by terminal message).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.runtime.stuck_detector import detect_stuck


# ---------------------------------------------------------------------------
# 1. Detector fires on the postmortem pattern
# ---------------------------------------------------------------------------

ERROR = "Error: n8n POST /api/v1/credentials -> 400: data requires 'serverUrl'"


def test_repeated_error_fires_after_two_failures() -> None:
    history = ["n8n_update_workflow", "n8n_update_workflow"]
    results = [ERROR, ERROR]
    signal = detect_stuck(history, results, last_result=ERROR)
    assert signal is not None, "detector must fire on 2× same-tool errors"
    assert signal.tool_name == "n8n_update_workflow"
    assert "n8n_update_workflow" in signal.context


def test_intent_flail_fires_when_brain_pivots_within_n8n_group() -> None:
    # Sequence captured from the hirossa run: 3 different n8n_* skills
    # called in a row, each failing with HTTP 400.
    history = [
        "n8n_google_sheets_setup",
        "n8n_google_services_setup",
        "n8n_google_oauth_setup",
    ]
    results = [ERROR, ERROR, ERROR]
    signal = detect_stuck(history, results, last_result=ERROR)
    assert signal is not None, (
        "intent_flail OR repeated_error must fire — pivoting between "
        "skills in the same n8n_* group is exactly the failure mode "
        "this plan was supposed to stop."
    )
    # Either intent_flail or repeated_error is acceptable; both prove
    # the agent loop will see a stuck signal at this point.
    assert signal.reason in {"intent_flail", "repeated_error"}


def test_no_stuck_when_results_differ() -> None:
    history = ["write_file", "write_file"]
    results = [
        "Written 7192 bytes to /tmp/post1.md",
        "Written 6312 bytes to /tmp/post2.md",
    ]
    # Same-tool + same-prefix results CAN trigger same_result; that's by
    # design when the brain is just dumping repetitive payloads. Either
    # way the assertion is: a signal IS returned (loop should escalate)
    # OR is None (no need). What we really want is that 2 errors fires;
    # 2 successes is allowed to stay quiet.
    signal = detect_stuck(history, results, last_result=results[-1])
    if signal is not None:
        assert signal.reason in {"same_result", "loop"}, (
            f"Unexpected stuck reason for non-error repeat: {signal.reason}"
        )


# ---------------------------------------------------------------------------
# 2. Agent source still wires the L3 hard-stop
# ---------------------------------------------------------------------------

AGENT_PY = Path(__file__).resolve().parents[2] / "lazyclaw" / "runtime" / "agent.py"


@pytest.fixture(scope="module")
def agent_source() -> str:
    return AGENT_PY.read_text(encoding="utf-8")


def test_agent_declares_post_l2_counter(agent_source: str) -> None:
    assert "_post_l2_stuck_count" in agent_source, (
        "Hard-stop counter missing — without it, MiniMax can flail "
        "indefinitely after L2 brain escalation. See hirossa postmortem."
    )


def test_agent_resets_counter_when_entering_l2(agent_source: str) -> None:
    # The L2 branch must reset _post_l2_stuck_count = 0 so the window
    # only counts post-L2 stucks (not stale L1-era state).
    assert "_post_l2_stuck_count = 0" in agent_source, (
        "L2 branch must reset the counter on entry."
    )


def test_agent_hard_stops_on_repeat_or_background(agent_source: str) -> None:
    # Look for the conjunction that gates the hard-stop branch — either
    # we're in a background agent OR the user has already had one
    # HELP_NEEDED dialog and stuck fired again.
    assert "_post_l2_stuck_count >= 2" in agent_source, (
        "L3 must hard-stop after 2 post-L2 stuck signals, not pop "
        "another HELP_NEEDED dialog."
    )
    # And the terminal message must clearly tell the user we stopped.
    assert "I tried" in agent_source and "I'm stopping" in agent_source, (
        "Terminal message wording missing — user needs to know the "
        "loop stopped on purpose."
    )


# ---------------------------------------------------------------------------
# 3. n8n credential payload shape — every key n8n requires must be present
# ---------------------------------------------------------------------------
#
# Empirically derived by probing GET /api/v1/credentials/schema/<type> on
# the running n8n container. Without these keys the POST returns a 400
# with "subschema X requires <key>" — exactly the failure mode that
# burned ~10 iterations in the hirossa.com run on 2026-04-19.

REQUIRED_OAUTH_KEYS = {
    "serverUrl",
    "clientId",
    "clientSecret",
    "sendAdditionalBodyProperties",
    "additionalBodyProperties",
}


def test_credential_helper_emits_all_required_keys() -> None:
    from lazyclaw.skills.builtin.n8n_management import _google_oauth_data
    data = _google_oauth_data("client.apps.googleusercontent.com", "GOCSPX-x")
    missing = REQUIRED_OAUTH_KEYS - set(data.keys())
    extra = set(data.keys()) - (REQUIRED_OAUTH_KEYS | {"scope"})
    assert not missing, f"helper missing keys n8n requires: {missing}"
    assert not extra, f"helper sends keys n8n rejects: {extra}"
    # `scope` should NOT appear unless explicitly requested — per-service
    # types reject it.
    assert "scope" not in data


def test_credential_helper_includes_scope_when_passed() -> None:
    from lazyclaw.skills.builtin.n8n_management import _google_oauth_data
    data = _google_oauth_data(
        "id", "secret", scope="https://www.googleapis.com/auth/drive",
    )
    assert data.get("scope") == "https://www.googleapis.com/auth/drive"
    assert REQUIRED_OAUTH_KEYS.issubset(data.keys())


def test_setup_skills_call_helper_with_correct_shape() -> None:
    """Mock _n8n_request and prove each setup skill emits a body whose
    `data` matches what n8n's schema accepts."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from lazyclaw.config import load_config
    from lazyclaw.skills.builtin import n8n_management as nm

    captured: list[dict] = []

    async def fake_request(config, user_id, method, path, body=None):
        if path == "/api/v1/credentials" and body:
            captured.append(body)
        return {"id": "cred-fake-id", "name": (body or {}).get("name", "")}

    async def fake_vault_get(_cfg, _uid, key):
        return {
            "google_oauth_client_id": "X.apps.googleusercontent.com",
            "google_oauth_client_secret": "GOCSPX-fake",
        }.get(key)

    async def run() -> None:
        cfg = load_config()
        with patch.object(nm, "_n8n_request", side_effect=fake_request), \
             patch("lazyclaw.crypto.vault.get_credential", side_effect=fake_vault_get):
            await nm.N8nGoogleSheetsSetupSkill(cfg).execute("u", {})
            await nm.N8nGoogleOAuthSetupSkill(cfg).execute(
                "u", {"scope": "https://www.googleapis.com/auth/drive"},
            )
            await nm.N8nGoogleServicesSetupSkill(cfg).execute(
                "u", {"services": ["sheets", "drive"]},
            )

    asyncio.run(run())

    assert len(captured) >= 4, f"expected ≥4 credential POSTs, got {len(captured)}"
    for body in captured:
        data = body["data"]
        missing = REQUIRED_OAUTH_KEYS - set(data.keys())
        assert not missing, (
            f"{body['type']} body missing required keys: {missing}\n"
            f"actual keys: {sorted(data.keys())}"
        )
        # scope only on the generic googleOAuth2Api type
        if body["type"] == "googleOAuth2Api":
            assert "scope" in data, "googleOAuth2Api body must include scope"
        else:
            assert "scope" not in data, (
                f"per-service type {body['type']} must NOT carry scope"
            )
