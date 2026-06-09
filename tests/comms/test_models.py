from lazyclaw.comms.models import ChannelThread, ThreadRef

def test_thread_ref_roundtrip():
    ref = ThreadRef(channel="whatsapp", contact="+34600000000")
    assert ref.as_dict() == {"channel": "whatsapp", "contact": "+34600000000"}

def test_channel_thread_is_frozen():
    t = ChannelThread(
        id="t1", user_id="u1", channel="whatsapp", contact_handle="+34600000000",
        contact_name="Alice", last_preview="hi", unread_count=2,
        last_activity="2026-06-09T10:00:00+00:00", last_seen_msg_id="m9",
        created_at="2026-06-09T09:00:00+00:00", updated_at="2026-06-09T10:00:00+00:00",
        deleted_at=None,
    )
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.unread_count = 3  # type: ignore
