import dataclasses

import pytest

from lazyclaw.comms.models import ChannelThread, Contact, Msg, SendResult, ThreadRef


# ---------------------------------------------------------------------------
# ThreadRef
# ---------------------------------------------------------------------------

def test_thread_ref_roundtrip():
    ref = ThreadRef(channel="whatsapp", contact="+34600000000")
    assert ref.as_dict() == {"channel": "whatsapp", "contact": "+34600000000"}


def test_thread_ref_is_frozen():
    ref = ThreadRef(channel="email", contact="alice@example.com")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.channel = "telegram"  # type: ignore


# ---------------------------------------------------------------------------
# ChannelThread helpers
# ---------------------------------------------------------------------------

def _make_thread(**overrides) -> ChannelThread:
    defaults = dict(
        id="t1",
        user_id="u1",
        channel="whatsapp",
        contact_handle="+34600000000",
        contact_name="Alice",
        last_preview="hi",
        unread_count=2,
        last_activity="2026-06-09T10:00:00+00:00",
        last_seen_msg_id="m9",
        created_at="2026-06-09T09:00:00+00:00",
        updated_at="2026-06-09T10:00:00+00:00",
        deleted_at=None,
    )
    defaults.update(overrides)
    return ChannelThread(**defaults)


# ---------------------------------------------------------------------------
# ChannelThread
# ---------------------------------------------------------------------------

def test_channel_thread_is_frozen():
    t = _make_thread()
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.unread_count = 3  # type: ignore


def test_channel_thread_as_dict_has_exactly_12_keys():
    t = _make_thread()
    expected_keys = {
        "id",
        "user_id",
        "channel",
        "contact_handle",
        "contact_name",
        "last_preview",
        "unread_count",
        "last_activity",
        "last_seen_msg_id",
        "created_at",
        "updated_at",
        "deleted_at",
    }
    assert set(t.as_dict().keys()) == expected_keys


def test_channel_thread_as_dict_values_roundtrip():
    t = _make_thread(last_seen_msg_id="m42", deleted_at="2026-06-10T00:00:00+00:00")
    d = t.as_dict()
    assert d["id"] == "t1"
    assert d["user_id"] == "u1"
    assert d["last_seen_msg_id"] == "m42"
    assert d["created_at"] == "2026-06-09T09:00:00+00:00"
    assert d["deleted_at"] == "2026-06-10T00:00:00+00:00"


def test_channel_thread_as_dict_optional_fields_can_be_none():
    t = _make_thread(contact_name=None, last_preview=None, last_seen_msg_id=None, deleted_at=None)
    d = t.as_dict()
    assert d["contact_name"] is None
    assert d["last_preview"] is None
    assert d["last_seen_msg_id"] is None
    assert d["deleted_at"] is None


# ---------------------------------------------------------------------------
# Msg
# ---------------------------------------------------------------------------

def test_msg_defaults_is_mine_to_false():
    m = Msg(sender="Alice", text="hello", timestamp="2026-06-09T10:00:00+00:00")
    assert m.is_mine is False


def test_msg_is_mine_can_be_set_true():
    m = Msg(sender="me", text="hey", timestamp="2026-06-09T10:01:00+00:00", is_mine=True)
    assert m.is_mine is True


def test_msg_is_frozen():
    m = Msg(sender="Alice", text="hello", timestamp="2026-06-09T10:00:00+00:00")
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.text = "changed"  # type: ignore


# ---------------------------------------------------------------------------
# Contact
# ---------------------------------------------------------------------------

def test_contact_fields():
    c = Contact(name="Alice", handle="+34600000000")
    assert c.name == "Alice"
    assert c.handle == "+34600000000"


def test_contact_is_frozen():
    c = Contact(name="Alice", handle="+34600000000")
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.name = "Bob"  # type: ignore


# ---------------------------------------------------------------------------
# SendResult
# ---------------------------------------------------------------------------

def test_send_result_ok_defaults_error_to_none():
    r = SendResult(ok=True)
    assert r.ok is True
    assert r.error is None


def test_send_result_failure_carries_error_message():
    r = SendResult(ok=False, error="timeout")
    assert r.ok is False
    assert r.error == "timeout"


def test_send_result_is_frozen():
    r = SendResult(ok=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.ok = False  # type: ignore
