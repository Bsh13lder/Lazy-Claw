"""BUG C2 — topic_rollup must pass include_rolled_up=True to store.search_notes.

Without this flag, notes tagged `rolled-up` (i.e. every note absorbed into a
prior rollup) are excluded from the corpus — cumulative knowledge is lost on
the second and subsequent rollups for the same topic.

Test strategy: monkeypatch `store.search_notes` and `store.get_backlinks` to
record kwargs, call `topic_rollup.topic_rollup(...)`, assert that
`include_rolled_up=True` was passed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lazyclaw.lazybrain import topic_rollup as tr

pytestmark = pytest.mark.asyncio

_USER_ID = "test-user-topic"
_TOPIC = "Madrid"

# A fake note returned by both backlinks and search_notes.
_FAKE_NOTES = [
    {
        "id": "note-001",
        "title": "Moved to Madrid",
        "content": "We relocated to Madrid in April 2026.",
        "tags": ["project", "rolled-up"],
        "updated_at": "2026-04-01T10:00:00",
        "created_at": "2026-04-01T10:00:00",
    }
]


async def test_search_notes_called_with_include_rolled_up_true(monkeypatch) -> None:
    """Core assertion: store.search_notes must receive include_rolled_up=True so
    that notes absorbed into prior rollups are still included in the corpus."""
    captured_kwargs: list[dict] = []

    async def fake_search_notes(config, user_id, query, **kwargs):
        captured_kwargs.append(kwargs)
        return _FAKE_NOTES

    async def fake_get_backlinks(config, user_id, topic):
        return []

    # Patch LLM so the test doesn't need a live API key.
    class FakeResp:
        content = "## Summary\nMoved to Madrid.\n\n### Decisions\n(none yet)\n\n### Open questions\n(none yet)\n\n### Sources\n[[Moved to Madrid]]"

    async def fake_eco_chat(*args, **kwargs):
        return FakeResp()

    monkeypatch.setattr(tr.store, "search_notes", fake_search_notes)
    monkeypatch.setattr(tr.store, "get_backlinks", fake_get_backlinks)

    # Patch the LLM router chain so we don't need real credentials.
    # EcoRouter is a local import inside the function body, so we patch
    # at the source module level.
    with patch("lazyclaw.llm.eco_router.EcoRouter") as MockEco, \
         patch("lazyclaw.llm.router.LLMRouter"):
        instance = MockEco.return_value
        instance.chat = AsyncMock(return_value=FakeResp())

        result = await tr.topic_rollup(config=None, user_id=_USER_ID, topic=_TOPIC)

    assert captured_kwargs, "store.search_notes was never called"
    assert captured_kwargs[0].get("include_rolled_up") is True, (
        f"store.search_notes must be called with include_rolled_up=True to "
        f"preserve cumulative knowledge after rollups. "
        f"Got kwargs: {captured_kwargs[0]}"
    )


async def test_topic_rollup_empty_topic_returns_error() -> None:
    """Empty topic string must return an error dict, not crash."""
    result = await tr.topic_rollup(config=None, user_id=_USER_ID, topic="")
    assert result.get("error") == "empty topic"
    assert result["source_count"] == 0


async def test_topic_rollup_returns_sources_list(monkeypatch) -> None:
    """When notes are found, the return value must include a `sources` list
    with note titles — confirms the corpus is being used."""
    async def fake_search_notes(config, user_id, query, **kwargs):
        return _FAKE_NOTES

    async def fake_get_backlinks(config, user_id, topic):
        return []

    class FakeResp:
        content = "## Summary\nTest.\n\n### Decisions\n(none yet)\n\n### Open questions\n(none yet)\n\n### Sources\n[[Moved to Madrid]]"

    monkeypatch.setattr(tr.store, "search_notes", fake_search_notes)
    monkeypatch.setattr(tr.store, "get_backlinks", fake_get_backlinks)

    with patch("lazyclaw.llm.eco_router.EcoRouter") as MockEco, \
         patch("lazyclaw.llm.router.LLMRouter"):
        instance = MockEco.return_value
        instance.chat = AsyncMock(return_value=FakeResp())

        result = await tr.topic_rollup(config=None, user_id=_USER_ID, topic=_TOPIC)

    assert "Moved to Madrid" in result["sources"]
    assert result["source_count"] == 1


async def test_topic_rollup_no_notes_skips_llm(monkeypatch) -> None:
    """When corpus is empty (no backlinks, no search hits), must return
    the 'no notes' message without calling the LLM."""
    async def fake_search_notes(config, user_id, query, **kwargs):
        return []

    async def fake_get_backlinks(config, user_id, topic):
        return []

    llm_called = []

    monkeypatch.setattr(tr.store, "search_notes", fake_search_notes)
    monkeypatch.setattr(tr.store, "get_backlinks", fake_get_backlinks)

    with patch("lazyclaw.llm.eco_router.EcoRouter") as MockEco, \
         patch("lazyclaw.llm.router.LLMRouter"):
        instance = MockEco.return_value
        instance.chat = AsyncMock(side_effect=lambda *a, **k: llm_called.append(True))

        result = await tr.topic_rollup(config=None, user_id=_USER_ID, topic=_TOPIC)

    assert result["source_count"] == 0
    assert not llm_called, "LLM must not be called when corpus is empty"
