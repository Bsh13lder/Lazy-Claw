"""Unit tests for the Upwork inbox-check skill — Order B fixes.

Covers:
  - Generic fallback in _compose_auto_reply (no more silent empty drops)
  - Founder-contact owner_offer only fires on first contact (is_new=True)
  - Default inbox_check_cron is the new 15-min cadence
  - _push_escalation_alert calls push_telegram with the right prefix per
    category and returns the push outcome
"""

from __future__ import annotations

import pytest

from lazyclaw.skills.builtin.survival.upwork_inbox_check import (
    UpworkInboxCheckSkill,
)
from lazyclaw.survival.profile import SkillsProfile
from lazyclaw.survival.upwork_bot import UpworkBotBehavior


@pytest.fixture
def skill() -> UpworkInboxCheckSkill:
    return UpworkInboxCheckSkill()


@pytest.fixture
def behavior() -> UpworkBotBehavior:
    return UpworkBotBehavior()


@pytest.fixture
def profile() -> SkillsProfile:
    return SkillsProfile(display_name="Vato")


def test_inbox_check_cron_default_is_15min():
    """Old default 0 9 * * * (daily 9am) was too rare — caused 24h misses."""
    assert UpworkBotBehavior().inbox_check_cron == "*/15 * * * *"


def test_compose_auto_reply_unknown_category_no_silent_drop(skill, behavior, profile):
    """Any category not in the 4 hardcoded set used to return "" which
    silently skipped the send. The generic fallback fires now."""
    out = skill._compose_auto_reply(
        category="some_custom_category",
        body="hello",
        profile=profile,
        behavior=behavior,
        is_new_conversation=False,
    )
    assert out, "generic fallback must not be empty"
    assert "Vato" in out, "sign-off must include display_name"


def test_compose_auto_reply_acknowledge_first_contact_offers_owner(skill, behavior, profile):
    """First-contact + intro_offer_owner_contact True → owner_offer is appended."""
    out = skill._compose_auto_reply(
        category="acknowledge",
        body="thanks!",
        profile=profile,
        behavior=behavior,
        is_new_conversation=True,
    )
    assert "Got it" in out
    assert "founder directly" in out


def test_compose_auto_reply_acknowledge_returning_client_no_owner_offer(
    skill, behavior, profile
):
    """Returning client (is_new=False) does NOT get the owner_offer."""
    out = skill._compose_auto_reply(
        category="acknowledge",
        body="thanks!",
        profile=profile,
        behavior=behavior,
        is_new_conversation=False,
    )
    assert "Got it" in out
    assert "founder directly" not in out


def test_compose_auto_reply_all_4_hardcoded_categories_return_non_empty(
    skill, behavior, profile
):
    """The 4 hardcoded categories still return their tailored replies."""
    for cat in ("acknowledge", "status_update", "clarify_work", "request_inputs"):
        out = skill._compose_auto_reply(
            category=cat,
            body="x",
            profile=profile,
            behavior=behavior,
            is_new_conversation=False,
        )
        assert out, f"category {cat} returned empty"
        assert "Vato" in out


def test_compose_auto_reply_signoff_falls_back_to_the_team(skill, behavior):
    """Empty display_name → sign-off uses 'the team' (not blank)."""
    out = skill._compose_auto_reply(
        category="acknowledge",
        body="thanks!",
        profile=SkillsProfile(display_name=""),
        behavior=behavior,
        is_new_conversation=False,
    )
    assert "— the team" in out


# ── Telegram alert path ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_push_escalation_alert_admin_request_uses_loudest_prefix(
    skill, monkeypatch
):
    """admin_request (client asked for the human owner) is the single
    category the user complained about most — must get the 🚨 prefix."""
    captured: dict = {}

    async def _fake_push(config, text, *, parse_mode=None, max_chars=3800):
        captured["text"] = text
        captured["parse_mode"] = parse_mode
        return True

    monkeypatch.setattr(
        "lazyclaw.notifications.push.push_telegram", _fake_push
    )

    ok = await skill._push_escalation_alert(
        client="Acme Corp",
        room_id="r1",
        room_url="https://www.upwork.com/ab/messages/rooms/r1",
        body="can I talk to the actual founder of your team?",
        category="admin_request",
        urgency="business_hours",
    )
    assert ok is True
    assert "🚨" in captured["text"]
    assert "client asked to talk to YOU" in captured["text"]
    assert "Acme Corp" in captured["text"]
    assert "admin_request" in captured["text"]
    # parse_mode must be None — body contains free-form punctuation that
    # would break Telegram Markdown.
    assert captured["parse_mode"] is None


@pytest.mark.asyncio
async def test_push_escalation_alert_complaint_uses_warning_prefix(
    skill, monkeypatch
):
    captured: dict = {}

    async def _fake_push(config, text, *, parse_mode=None, max_chars=3800):
        captured["text"] = text
        return True

    monkeypatch.setattr(
        "lazyclaw.notifications.push.push_telegram", _fake_push
    )

    ok = await skill._push_escalation_alert(
        client="Acme",
        room_id="r2",
        room_url="",
        body="this is awful, refund me",
        category="complaint",
        urgency="business_hours",
    )
    assert ok is True
    assert "⚠️" in captured["text"]
    assert "client unhappy" in captured["text"]


@pytest.mark.asyncio
async def test_push_escalation_alert_returns_false_when_push_fails(
    skill, monkeypatch
):
    """If push_telegram returns False (token missing / network error),
    the helper surfaces False so the caller logs the failure in the
    inbox-sweep report instead of silently swallowing it."""

    async def _failing_push(config, text, *, parse_mode=None, max_chars=3800):
        return False

    monkeypatch.setattr(
        "lazyclaw.notifications.push.push_telegram", _failing_push
    )

    ok = await skill._push_escalation_alert(
        client="X", room_id="r3", room_url="",
        body="hi", category="admin_request",
        urgency="immediate",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_push_escalation_alert_returns_false_when_push_raises(
    skill, monkeypatch
):
    async def _raising_push(config, text, *, parse_mode=None, max_chars=3800):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(
        "lazyclaw.notifications.push.push_telegram", _raising_push
    )

    ok = await skill._push_escalation_alert(
        client="X", room_id="r4", room_url="",
        body="hi", category="admin_request",
        urgency="business_hours",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_push_escalation_alert_falls_back_to_canonical_url(
    skill, monkeypatch
):
    """When room_url is empty but room_id is present, the helper builds
    a canonical /ab/messages/rooms/<id> link instead of dropping the
    open-link line."""
    captured: dict = {}

    async def _fake_push(config, text, *, parse_mode=None, max_chars=3800):
        captured["text"] = text
        return True

    monkeypatch.setattr(
        "lazyclaw.notifications.push.push_telegram", _fake_push
    )

    await skill._push_escalation_alert(
        client="X", room_id="abc123", room_url="",
        body="hi", category="off_platform",
        urgency="business_hours",
    )
    assert "https://www.upwork.com/ab/messages/rooms/abc123" in captured["text"]


# ── empty_or_blocked passthrough (mcp-upwork structured reads) ──────

def test_blocked_inbox_cloudflare_is_loud():
    from lazyclaw.skills.builtin.survival.upwork_inbox_check import (
        _blocked_inbox_result,
    )
    out = _blocked_inbox_result({
        "status": "empty_or_blocked",
        "diagnosis": "cloudflare_challenge",
        "hint": "Solve the Cloudflare check in Brave.",
    })
    assert out is not None
    assert not out.startswith("[SILENT]")
    assert "cloudflare_challenge" in out
    assert "BLOCKED" in out


def test_blocked_inbox_login_required_is_loud():
    from lazyclaw.skills.builtin.survival.upwork_inbox_check import (
        _blocked_inbox_result,
    )
    out = _blocked_inbox_result(
        {"result": {"status": "empty_or_blocked",
                    "diagnosis": "login_required", "hint": "Log in."}}
    )
    assert out is not None and not out.startswith("[SILENT]")
    assert "login_required" in out


def test_blocked_inbox_selector_drift_stays_silent():
    from lazyclaw.skills.builtin.survival.upwork_inbox_check import (
        _blocked_inbox_result,
    )
    out = _blocked_inbox_result({
        "status": "empty_or_blocked",
        "diagnosis": "selector_drift_or_truly_empty",
        "hint": "",
    })
    assert out is not None
    assert out.startswith("[SILENT]")
    # but it must not claim the inbox is verified-empty
    assert "No unread" not in out


def test_blocked_inbox_none_on_normal_list():
    from lazyclaw.skills.builtin.survival.upwork_inbox_check import (
        _blocked_inbox_result,
    )
    assert _blocked_inbox_result([{"room_id": "r1"}]) is None
    assert _blocked_inbox_result({"messages": []}) is None
