"""Unit tests for runtime.plan_research — focus on pure helpers.

The full async ``gather_plan_research`` exercises three lanes that need
DB + Ollama; that's covered by integration tests. Here we verify the
pure heuristics that decide which lanes fire in the first place.
"""

from __future__ import annotations

import pytest

from lazyclaw.runtime.plan_research import (
    _file_targets,
    _matched_topics,
)


class TestTopicMatching:
    """_matched_topics returns the top-2 keyword-scored topics."""

    def test_browser_topic_detected(self):
        topics = _matched_topics("open the browser to google.com and click")
        assert "browser" in topics

    def test_email_topic_detected(self):
        topics = _matched_topics("send an email to my boss via gmail")
        assert "email" in topics

    def test_no_topic_match_empty(self):
        topics = _matched_topics("hello, how are you doing today")
        assert topics == []

    def test_caps_at_two_topics(self):
        topics = _matched_topics(
            "open the browser, send an email, post to instagram, "
            "save a note in lazybrain"
        )
        assert len(topics) <= 2


class TestFileTargetExtraction:
    """_file_targets only fires on explicit file references."""

    def test_path_with_slash_extracted(self):
        targets = _file_targets("look at lazyclaw/runtime/agent.py please")
        assert "lazyclaw/runtime/agent.py" in targets

    def test_in_file_pattern_extracted(self):
        targets = _file_targets("change the bug in compressor.py")
        assert any("compressor.py" in t for t in targets)

    def test_no_file_reference_empty(self):
        assert _file_targets("plan me a trip to Tokyo") == []

    def test_caps_at_three_targets(self):
        targets = _file_targets(
            "edit a.py and b.py and c.py and d.py and e.py"
        )
        assert len(targets) <= 3

    def test_extension_only_no_path_still_extracted(self):
        # Lone "agent.py" without slash still has a code extension → counts.
        targets = _file_targets("the agent.py module is huge")
        assert "agent.py" in targets
