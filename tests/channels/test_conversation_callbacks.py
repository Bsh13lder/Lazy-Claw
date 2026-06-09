"""TDD tests for parse_conversation_callback — Task E8."""

from lazyclaw.channels.telegram_commands import parse_conversation_callback


def test_parse_ok():
    assert parse_conversation_callback("convok:c123") == ("c123", True)


def test_parse_cancel():
    assert parse_conversation_callback("convno:c123") == ("c123", False)


def test_parse_other_returns_none():
    assert parse_conversation_callback("accept:slug") is None
    assert parse_conversation_callback("") is None
    assert parse_conversation_callback("convok:") == ("", True)  # empty id edge — documented
