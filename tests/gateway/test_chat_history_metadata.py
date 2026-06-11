"""Regression tests for agent_messages.metadata shape tolerance.

2026-06-10 incident: the metadata-encryption pass changed the stored
tool-calls shape from ``{"tool_calls": [...]}`` to a bare list.
``chat_history.get_session_messages`` still assumed a dict and raised
``AttributeError`` mid-loop — one new-shape row 500'd the whole history
endpoint, making the chat look like the DB was wiped.
"""

import json

from lazyclaw.gateway.routes.chat_history import _extract_tool_calls


class TestExtractToolCalls:
    def test_new_bare_list_shape(self):
        """Post-codec rows store the tool-call list directly."""
        raw = json.dumps([{"id": "tc1", "name": "web_search", "arguments": "{}"}])
        result = _extract_tool_calls(raw)
        assert result == [{"id": "tc1", "name": "web_search", "arguments": "{}"}]

    def test_legacy_dict_shape(self):
        """Pre-codec rows wrap the list under a tool_calls key."""
        raw = json.dumps({"tool_calls": [{"id": "tc1", "name": "browser"}]})
        result = _extract_tool_calls(raw)
        assert result == [{"id": "tc1", "name": "browser"}]

    def test_legacy_dict_without_tool_calls_key(self):
        assert _extract_tool_calls(json.dumps({"other": 1})) is None

    def test_invalid_json_returns_none(self):
        assert _extract_tool_calls("not json at all") is None

    def test_decrypt_fallback_sentinel_returns_none(self):
        """decrypt_field falls back to '[encrypted]' on key mismatch."""
        assert _extract_tool_calls("[encrypted]") is None

    def test_none_returns_none(self):
        assert _extract_tool_calls(None) is None

    def test_scalar_json_returns_none(self):
        """A bare string/number is valid JSON but not a tool-call shape."""
        assert _extract_tool_calls(json.dumps("hello")) is None
        assert _extract_tool_calls(json.dumps(42)) is None

    def test_empty_list_passes_through(self):
        assert _extract_tool_calls(json.dumps([])) == []
