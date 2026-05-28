"""Tests for upwork_last_conversation skill + instant_dispatch route.

Covers:
  - _normalize_inbox / _normalize_conversation shape coercion
  - Skill happy path (mocked MCP) — fetches latest + formats markdown
  - Empty inbox → user-friendly "no conversations found"
  - Empty conversation → "tab may be lost" hint
  - MCP tools missing → graceful error
  - me_name pulled from SkillsProfile and forwarded to get_conversation
  - me_name=None when profile lookup fails (no crash)
  - String JSON responses parsed
  - instant_dispatch route matches many natural phrasings
  - instant_dispatch does NOT overmatch unrelated requests
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.runtime.instant_dispatch import INSTANT_ROUTES, _CRON_PREFIX_RE
from lazyclaw.skills.builtin.survival.upwork_last_conversation import (
    UpworkLastConversationSkill,
    _match_contact,
    _normalize_conversation,
    _normalize_inbox,
    _normalize_profile,
)


@pytest.fixture
async def tmp_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


# ── Normalizers ─────────────────────────────────────────────────────


def test_normalize_inbox_list():
    raw = [{"contact_name": "X"}]
    assert _normalize_inbox(raw) == raw


def test_normalize_inbox_string():
    raw = json.dumps([{"contact_name": "X"}])
    assert _normalize_inbox(raw) == [{"contact_name": "X"}]


def test_normalize_inbox_result_wrapper():
    assert _normalize_inbox({"result": [{"contact_name": "X"}]}) == [
        {"contact_name": "X"}
    ]


def test_normalize_inbox_garbage():
    assert _normalize_inbox(None) == []
    assert _normalize_inbox("not-json") == []
    assert _normalize_inbox({"weird": 1}) == []
    assert _normalize_inbox([1, "x", None, {"ok": True}]) == [{"ok": True}]


def test_normalize_conversation_dict():
    raw = {"contact_name": "X", "messages": []}
    assert _normalize_conversation(raw) == raw


def test_normalize_conversation_string():
    raw = json.dumps({"contact_name": "X", "messages": []})
    assert _normalize_conversation(raw) == {"contact_name": "X", "messages": []}


def test_normalize_conversation_result_wrapper():
    assert _normalize_conversation({"result": {"contact_name": "X"}}) == {
        "contact_name": "X"
    }


def test_normalize_conversation_garbage():
    assert _normalize_conversation(None) == {}
    assert _normalize_conversation("not-json") == {}
    assert _normalize_conversation([]) == {}


# ── Skill: registry / MCP plumbing ──────────────────────────────────


class _FakeTool:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    async def execute(self, user_id: str, params: dict):
        self.calls.append({"user_id": user_id, **params})
        return self._response


class _Registry:
    def __init__(
        self,
        get_messages_response=None,
        get_conversation_response=None,
        include_messages=True,
        include_conversation=True,
        get_my_profile_response=None,
        include_my_profile=False,
    ):
        self._get_messages = (
            _FakeTool(get_messages_response) if include_messages else None
        )
        self._get_conversation = (
            _FakeTool(get_conversation_response) if include_conversation else None
        )
        # Profile tool is opt-in so existing tests (which never registered
        # it) keep exercising the no-fallback path.
        self._get_my_profile = (
            _FakeTool(get_my_profile_response) if include_my_profile else None
        )

    def list_mcp_tools(self):
        names = []
        if self._get_messages is not None:
            names.append({"function": {"name": "mcp_xyz_upwork_get_messages"}})
        if self._get_conversation is not None:
            names.append({"function": {"name": "mcp_xyz_upwork_get_conversation"}})
        if self._get_my_profile is not None:
            names.append({"function": {"name": "mcp_xyz_upwork_get_my_profile"}})
        return names

    def get(self, name):
        n = name or ""
        # Order matters: "upwork_get_my_profile" must be matched before the
        # broader "upwork_get_messages"/"_conversation" substrings (none
        # overlap, but match the most specific intent first).
        if "upwork_get_my_profile" in n:
            return self._get_my_profile
        if "upwork_get_messages" in n:
            return self._get_messages
        if "upwork_get_conversation" in n:
            return self._get_conversation
        return None


@pytest.mark.asyncio
async def test_skill_no_registry(tmp_config):
    skill = UpworkLastConversationSkill(config=tmp_config, registry=None)
    result = await skill.execute("u1", {})
    assert "registry not available" in result


@pytest.mark.asyncio
async def test_skill_mcp_tools_missing(tmp_config):
    skill = UpworkLastConversationSkill(
        config=tmp_config,
        registry=_Registry(include_messages=False, include_conversation=False),
    )
    result = await skill.execute("u1", {})
    assert "mcp-upwork tools not registered" in result


@pytest.mark.asyncio
async def test_skill_empty_inbox(tmp_config):
    skill = UpworkLastConversationSkill(
        config=tmp_config,
        registry=_Registry(get_messages_response={"result": []}),
    )
    result = await skill.execute("u1", {})
    assert "No Upwork conversations found" in result


@pytest.mark.asyncio
async def test_skill_empty_conversation(tmp_config):
    inbox = [{"contact_name": "James Blue", "room_url": "https://x/r/abc"}]
    skill = UpworkLastConversationSkill(
        config=tmp_config,
        registry=_Registry(
            get_messages_response={"result": inbox},
            get_conversation_response={"result": {"messages": []}},
        ),
    )
    result = await skill.execute("u1", {})
    assert "James Blue" in result
    assert "empty" in result.lower()


@pytest.mark.asyncio
async def test_skill_happy_path(tmp_config):
    inbox = [{
        "contact_name": "James Blue",
        "room_url": "https://www.upwork.com/ab/messages/rooms/room_abc",
    }]
    conv = {
        "contact_name": "James Blue",
        "messages": [
            {"timestamp": "10:39 PM", "sender": "James Blue",
             "content": "Right now this is everything I need it to do",
             "is_mine": False},
            {"timestamp": "10:40 PM", "sender": "Vato Tchipa",
             "content": "lets go for $120", "is_mine": True},
        ],
    }
    registry = _Registry(
        get_messages_response={"result": inbox},
        get_conversation_response={"result": conv},
    )
    skill = UpworkLastConversationSkill(
        config=tmp_config, registry=registry,
    )
    result = await skill.execute("u1", {})

    # Header
    assert "Last Upwork conversation — James Blue" in result
    # Both messages rendered
    assert "Right now this is everything" in result
    assert "lets go for $120" in result
    # Direction arrows
    assert "←" in result  # them
    assert "→" in result  # me
    # MCP calls fired exactly ONCE each — no looping
    assert len(registry._get_messages.calls) == 1
    assert len(registry._get_conversation.calls) == 1
    # get_conversation was passed the room URL from the inbox
    cv_call = registry._get_conversation.calls[0]
    assert cv_call["room_id"] == "https://www.upwork.com/ab/messages/rooms/room_abc"


@pytest.mark.asyncio
async def test_skill_falls_back_when_room_url_missing(tmp_config):
    """Some MCP responses omit room_url — must fall back to the bare
    /rooms URL so the host bridge can pick whichever tab is open."""
    inbox = [{"contact_name": "James Blue"}]  # no room_url
    conv = {"messages": [{"sender": "James Blue", "content": "hi", "is_mine": False}]}
    registry = _Registry(
        get_messages_response={"result": inbox},
        get_conversation_response={"result": conv},
    )
    skill = UpworkLastConversationSkill(
        config=tmp_config, registry=registry,
    )
    result = await skill.execute("u1", {})
    cv_call = registry._get_conversation.calls[0]
    assert cv_call["room_id"] == "https://www.upwork.com/ab/messages/rooms"
    assert "James Blue" in result


@pytest.mark.asyncio
async def test_skill_string_responses_parsed(tmp_config):
    inbox = json.dumps({"result": [{"contact_name": "X", "room_url": "https://x"}]})
    conv = json.dumps({"messages": [
        {"sender": "X", "content": "hi", "is_mine": False},
    ]})
    registry = _Registry(
        get_messages_response=inbox,
        get_conversation_response=conv,
    )
    skill = UpworkLastConversationSkill(
        config=tmp_config, registry=registry,
    )
    result = await skill.execute("u1", {})
    assert "Last Upwork conversation" in result


@pytest.mark.asyncio
async def test_skill_forwards_me_name_from_profile(tmp_config):
    """display_name from SkillsProfile flows into get_conversation
    so is_mine flagging works end-to-end."""
    # Set the user's profile display_name
    from lazyclaw.survival.profile import update_profile
    await update_profile(tmp_config, "u1", {"display_name": "Vato Tchipa"})

    inbox = [{"contact_name": "X", "room_url": "https://x"}]
    conv = {"messages": [{"sender": "X", "content": "y", "is_mine": False}]}
    registry = _Registry(
        get_messages_response={"result": inbox},
        get_conversation_response={"result": conv},
    )
    skill = UpworkLastConversationSkill(
        config=tmp_config, registry=registry,
    )
    await skill.execute("u1", {})
    cv_call = registry._get_conversation.calls[0]
    assert cv_call.get("me_name") == "Vato Tchipa"


@pytest.mark.asyncio
async def test_skill_me_name_none_when_profile_blank(tmp_config):
    """No display_name set → me_name=None passed; no crash."""
    inbox = [{"contact_name": "X", "room_url": "https://x"}]
    conv = {"messages": [{"sender": "X", "content": "y"}]}
    registry = _Registry(
        get_messages_response={"result": inbox},
        get_conversation_response={"result": conv},
    )
    skill = UpworkLastConversationSkill(
        config=tmp_config, registry=registry,
    )
    result = await skill.execute("u1", {})
    cv_call = registry._get_conversation.calls[0]
    assert cv_call.get("me_name") is None
    assert "Last Upwork conversation" in result


@pytest.mark.asyncio
async def test_me_name_falls_back_to_live_profile_when_display_name_blank(
    tmp_config,
):
    """Empty stored display_name → derive me_name from the live Upwork
    profile via upwork_get_my_profile, so the user's own bubbles are
    still attributed correctly (sender-confabulation defense)."""
    inbox = [{"contact_name": "X", "room_url": "https://x"}]
    conv = {"messages": [{"sender": "X", "content": "y", "is_mine": False}]}
    registry = _Registry(
        get_messages_response={"result": inbox},
        get_conversation_response={"result": conv},
        include_my_profile=True,
        get_my_profile_response={"result": {"name": "Vato Tchipa"}},
    )
    skill = UpworkLastConversationSkill(config=tmp_config, registry=registry)
    await skill.execute("u1", {})
    cv_call = registry._get_conversation.calls[0]
    # Live profile name flowed into me_name even though display_name was
    # never set.
    assert cv_call.get("me_name") == "Vato Tchipa"
    # The profile fallback was actually invoked.
    assert len(registry._get_my_profile.calls) == 1


@pytest.mark.asyncio
async def test_stored_display_name_short_circuits_live_profile(tmp_config):
    """When display_name IS set, the live-profile fallback must NOT be
    called — it's the slow scrape path, only a last resort."""
    from lazyclaw.survival.profile import update_profile
    await update_profile(tmp_config, "u1", {"display_name": "Stored Name"})

    inbox = [{"contact_name": "X", "room_url": "https://x"}]
    conv = {"messages": [{"sender": "X", "content": "y", "is_mine": False}]}
    registry = _Registry(
        get_messages_response={"result": inbox},
        get_conversation_response={"result": conv},
        include_my_profile=True,
        get_my_profile_response={"result": {"name": "Live Name"}},
    )
    skill = UpworkLastConversationSkill(config=tmp_config, registry=registry)
    await skill.execute("u1", {})
    cv_call = registry._get_conversation.calls[0]
    assert cv_call.get("me_name") == "Stored Name"
    # Fallback must not have been touched.
    assert len(registry._get_my_profile.calls) == 0


@pytest.mark.asyncio
async def test_me_name_unresolved_emits_loud_warning(tmp_config, caplog):
    """display_name blank AND live profile returns no name → me_name=None
    AND a loud warning is logged so unreliable attribution is
    diagnosable."""
    import logging as _logging

    inbox = [{"contact_name": "X", "room_url": "https://x"}]
    conv = {"messages": [{"sender": "X", "content": "y", "is_mine": False}]}
    registry = _Registry(
        get_messages_response={"result": inbox},
        get_conversation_response={"result": conv},
        include_my_profile=True,
        # Live profile scrape missed the name (e.g. selectors all failed).
        get_my_profile_response={"result": {}},
    )
    skill = UpworkLastConversationSkill(config=tmp_config, registry=registry)
    with caplog.at_level(
        _logging.WARNING,
        logger="lazyclaw.skills.builtin.survival.upwork_last_conversation",
    ):
        await skill.execute("u1", {})
    cv_call = registry._get_conversation.calls[0]
    assert cv_call.get("me_name") is None
    # A WARNING-level record about unreliable attribution must exist.
    warnings = [
        r for r in caplog.records
        if r.levelno >= _logging.WARNING
        and "UNRELIABLE" in r.getMessage()
    ]
    assert warnings, "expected a loud me_name-unresolved warning"


def test_normalize_profile_shapes():
    """_normalize_profile coerces dict / JSON string / envelope / garbage."""
    assert _normalize_profile({"name": "A"}) == {"name": "A"}
    assert _normalize_profile(json.dumps({"name": "A"})) == {"name": "A"}
    assert _normalize_profile({"result": {"name": "A"}}) == {"name": "A"}
    assert _normalize_profile(None) == {}
    assert _normalize_profile("not-json") == {}
    assert _normalize_profile([1, 2]) == {}


@pytest.mark.asyncio
async def test_skill_message_limit_forwarded(tmp_config):
    inbox = [{"contact_name": "X", "room_url": "https://x"}]
    conv = {"messages": [{"sender": "X", "content": "y"}]}
    registry = _Registry(
        get_messages_response={"result": inbox},
        get_conversation_response={"result": conv},
    )
    skill = UpworkLastConversationSkill(
        config=tmp_config, registry=registry,
    )
    await skill.execute("u1", {"message_limit": 50})
    assert registry._get_conversation.calls[0]["limit"] == 50


# ── instant_dispatch route ──────────────────────────────────────────


@pytest.mark.parametrize("phrase", [
    "tell me what's in the last conversation",
    "tell me whats in the last conversation",
    "show me the last upwork message",
    "show last conversation",
    "show me the latest message",
    "what's in the latest conversation",
    "latest upwork chat",
    "most recent conversation",
    "most recent message",
    "show the recent conversation",
    "the last conversation",
    "recent dm",
])
def test_instant_dispatch_matches_user_phrases(phrase):
    for route in INSTANT_ROUTES:
        if route.skill_name == "upwork_last_conversation":
            assert route.pattern.match(phrase.lower()), (
                f"phrase {phrase!r} should match upwork_last_conversation"
            )
            return
    pytest.fail("upwork_last_conversation route not in INSTANT_ROUTES")


@pytest.mark.parametrize("phrase", [
    "check my upwork inbox",        # → upwork_inbox_check
    "check my upwork contracts",     # → upwork_contract_poll
    "send a message to james",       # not a read
    "what time is it",               # totally unrelated
    "delete the last conversation",  # write op, not a read
    "intake contract https://x",     # different intent
])
def test_instant_dispatch_does_not_overmatch(phrase):
    for route in INSTANT_ROUTES:
        if route.skill_name == "upwork_last_conversation":
            assert not route.pattern.match(phrase.lower()), (
                f"phrase {phrase!r} wrongly matched last_conversation route"
            )
            return


# ── Contact-name matching ──────────────────────────────────────────


def test_match_contact_substring():
    inbox = [
        {"contact_name": "Sarah Chen"},
        {"contact_name": "James Blue"},
    ]
    hit = _match_contact(inbox, "blue")
    assert hit is not None
    assert hit["contact_name"] == "James Blue"


def test_match_contact_first_name_only():
    inbox = [
        {"contact_name": "Sarah Chen"},
        {"contact_name": "James Blue"},
    ]
    hit = _match_contact(inbox, "James")
    assert hit is not None
    assert hit["contact_name"] == "James Blue"


def test_match_contact_case_insensitive():
    inbox = [{"contact_name": "James Blue"}]
    hit = _match_contact(inbox, "JAMES")
    assert hit is not None
    assert hit["contact_name"] == "James Blue"


def test_match_contact_returns_first_match_for_freshness():
    """Inboxes are newest-first; for multiple matches the freshest wins."""
    inbox = [
        {"contact_name": "James Wong", "ts": "now"},
        {"contact_name": "James Blue", "ts": "old"},
    ]
    hit = _match_contact(inbox, "james")
    assert hit is not None
    assert hit["contact_name"] == "James Wong"


def test_match_contact_no_match():
    inbox = [{"contact_name": "James Blue"}]
    assert _match_contact(inbox, "Sarah") is None


def test_match_contact_empty_query():
    inbox = [{"contact_name": "James Blue"}]
    assert _match_contact(inbox, "") is None
    assert _match_contact(inbox, "   ") is None


def test_match_contact_handles_missing_name_key():
    inbox = [{"room_url": "x"}, {"contact_name": "James"}]
    hit = _match_contact(inbox, "james")
    assert hit is not None
    assert hit["contact_name"] == "James"


# ── Skill: contact_name parameter (planning path) ───────────────────


@pytest.mark.asyncio
async def test_skill_finds_specific_contact(tmp_config):
    """When contact_name is given, the skill returns THAT thread, not the
    newest. This is the planning path — brain says 'what does James want',
    NOT 'what's in the most recent inbox slot'."""
    inbox = [
        {"contact_name": "Sarah Chen",
         "room_url": "https://www.upwork.com/ab/messages/rooms/room_sarah"},
        {"contact_name": "James Blue",
         "room_url": "https://www.upwork.com/ab/messages/rooms/room_james"},
    ]
    conv = {
        "contact_name": "James Blue",
        "messages": [
            {"sender": "James Blue", "content": "scope is real estate",
             "is_mine": False},
        ],
    }
    registry = _Registry(
        get_messages_response={"result": inbox},
        get_conversation_response={"result": conv},
    )
    skill = UpworkLastConversationSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {"contact_name": "James"})

    # Header names the right person — NOT Sarah Chen (newest)
    assert "James Blue" in result
    assert "Sarah Chen" not in result
    # get_conversation was passed James's room, not Sarah's
    cv_call = registry._get_conversation.calls[0]
    assert cv_call["room_id"].endswith("/room_james")


@pytest.mark.asyncio
async def test_skill_contact_name_widens_inbox_scan(tmp_config):
    """When matching by contact, we need a wider inbox scan than limit=1
    so the target thread isn't missed if it's not the freshest."""
    inbox = [{"contact_name": "James", "room_url": "https://x"}]
    conv = {"messages": [{"sender": "James", "content": "hi", "is_mine": False}]}
    registry = _Registry(
        get_messages_response={"result": inbox},
        get_conversation_response={"result": conv},
    )
    skill = UpworkLastConversationSkill(config=tmp_config, registry=registry)
    await skill.execute("u1", {"contact_name": "James"})
    # Without contact_name the inbox call uses limit=1.
    # With contact_name the inbox limit must widen so a non-top thread
    # is still findable.
    assert registry._get_messages.calls[0]["limit"] >= 20


@pytest.mark.asyncio
async def test_skill_contact_not_found_returns_helpful_hint(tmp_config):
    """When the named contact isn't in the inbox top, the error message
    must surface what IS available so the user/brain can correct."""
    inbox = [{"contact_name": "Sarah Chen", "room_url": "https://x"}]
    registry = _Registry(
        get_messages_response={"result": inbox},
        # get_conversation should NOT be called when match fails
        get_conversation_response={"result": {"messages": []}},
    )
    skill = UpworkLastConversationSkill(config=tmp_config, registry=registry)
    result = await skill.execute("u1", {"contact_name": "James"})
    assert "James" in result
    assert "no" in result.lower() or "match" in result.lower()
    # Inbox preview shown so the brain can pick the right name on retry
    assert "Sarah Chen" in result
    # Did NOT proceed to fetch a wrong conversation
    assert len(registry._get_conversation.calls) == 0


@pytest.mark.asyncio
async def test_skill_no_contact_name_keeps_legacy_behavior(tmp_config):
    """Omitting contact_name preserves the 'most recent' behavior so
    existing 'show me the last conversation' callers don't regress."""
    inbox = [
        {"contact_name": "Sarah Chen", "room_url": "https://x/sarah"},
        {"contact_name": "James Blue", "room_url": "https://x/james"},
    ]
    conv = {"messages": [{"sender": "Sarah", "content": "hi", "is_mine": False}]}
    registry = _Registry(
        get_messages_response={"result": inbox},
        get_conversation_response={"result": conv},
    )
    skill = UpworkLastConversationSkill(config=tmp_config, registry=registry)
    await skill.execute("u1", {})  # no contact_name
    cv_call = registry._get_conversation.calls[0]
    # Newest (Sarah) thread fetched — NOT James
    assert cv_call["room_id"] == "https://x/sarah"
    # Legacy inbox scan is still limit=1 to minimize cost
    assert registry._get_messages.calls[0]["limit"] == 1
