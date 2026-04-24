"""Tests for structured browser action errors."""

from __future__ import annotations

from lazyclaw.browser.action_errors import (
    RETRY_ESCALATE_TO_VISION,
    RETRY_RE_READ,
    ActionError,
    ActionErrorCode,
)


def test_str_format_includes_code_message_hint_retry():
    err = ActionError(
        code=ActionErrorCode.NOT_FOUND,
        message="Selector matched 0 elements.",
        hint="Take a snapshot.",
        retry_strategy=RETRY_RE_READ,
    )
    s = str(err)
    assert s.startswith("[not_found] ")
    assert "Selector matched 0 elements." in s
    assert "Hint: Take a snapshot." in s
    assert f"Retry: {RETRY_RE_READ}" in s


def test_str_format_without_hint_or_retry():
    err = ActionError(
        code=ActionErrorCode.TIMEOUT,
        message="Operation exceeded 10s.",
    )
    s = str(err)
    assert s == "[timeout] Operation exceeded 10s."


def test_tool_result_meta_shape():
    err = ActionError(
        code=ActionErrorCode.DEPENDENCY_MISSING,
        message="tesseract missing",
        retry_strategy=RETRY_ESCALATE_TO_VISION,
    )
    meta = err.to_tool_result_meta()
    assert meta == {
        "error_code": "dependency_missing",
        "retry_strategy": RETRY_ESCALATE_TO_VISION,
    }


def test_frozen_dataclass():
    err = ActionError(
        code=ActionErrorCode.OCCLUDED, message="behind modal",
    )
    try:
        err.message = "other"  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("ActionError should be frozen (immutable)")


def test_all_error_codes_have_string_value():
    for code in ActionErrorCode:
        assert isinstance(code.value, str)
        assert code.value == code.value.lower()
