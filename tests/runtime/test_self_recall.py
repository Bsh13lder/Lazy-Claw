"""Unit tests for runtime.self_recall — pure detector behavior only.

Async build_recall_block is exercised separately via integration tests
(needs DB + Ollama); these tests focus on the deterministic detection logic.
"""

from __future__ import annotations

import pytest

from lazyclaw.runtime.self_recall import detect_knowledge_gap


class TestGapPhraseDetection:
    """Phrase-based confusion triggers in the last sentence of the brain."""

    def test_simple_dont_know_triggers(self):
        is_gap, query = detect_knowledge_gap(
            assistant_text="I don't know your favorite color.",
            user_message="what's my favorite color?",
            prior_user_messages=[],
        )
        assert is_gap is True
        assert "favorite color" in query

    def test_could_you_tell_me_triggers(self):
        is_gap, _ = detect_knowledge_gap(
            assistant_text="To help, could you tell me your dog's name?",
            user_message="what's my dog's name?",
            prior_user_messages=[],
        )
        assert is_gap is True

    def test_phrase_in_middle_does_not_trigger(self):
        # "I don't know if X is true, let me check" — that's reasoning, not gap.
        is_gap, _ = detect_knowledge_gap(
            assistant_text=(
                "I don't know if that's the right URL. Let me verify by "
                "fetching the page."
            ),
            user_message="check the homepage",
            prior_user_messages=[],
        )
        assert is_gap is False

    def test_empty_assistant_no_trigger(self):
        is_gap, _ = detect_knowledge_gap("", "anything", [])
        assert is_gap is False

    def test_normal_answer_no_trigger(self):
        is_gap, _ = detect_knowledge_gap(
            assistant_text="Your favorite color is teal.",
            user_message="what's my color?",
            prior_user_messages=[],
        )
        assert is_gap is False


class TestRepeatedQuestion:
    """User repeating same question 1-2 turns earlier."""

    def test_same_question_within_history_triggers(self):
        is_gap, _ = detect_knowledge_gap(
            assistant_text="Got it, I'll help.",
            user_message="what is my favorite color we discussed?",
            prior_user_messages=[
                "what is my favorite color we discussed",
                "ok let me ask another way",
            ],
        )
        assert is_gap is True

    def test_different_question_no_trigger(self):
        is_gap, _ = detect_knowledge_gap(
            assistant_text="Got it.",
            user_message="what's the weather in Tokyo?",
            prior_user_messages=[
                "tell me about my work schedule",
                "what time is sunset",
            ],
        )
        assert is_gap is False

    def test_short_question_no_trigger(self):
        # < 3 content tokens — too noisy to ranking off Jaccard.
        is_gap, _ = detect_knowledge_gap(
            assistant_text="Hi.",
            user_message="hi?",
            prior_user_messages=["hi", "hello"],
        )
        assert is_gap is False


class TestQueryExtraction:
    """The returned query should be the user message (what they want)."""

    def test_query_is_user_message(self):
        _, q = detect_knowledge_gap(
            assistant_text="I don't have that.",
            user_message="what is my dog's name?",
            prior_user_messages=[],
        )
        assert q == "what is my dog's name?"

    def test_query_falls_back_to_assistant_when_user_empty(self):
        _, q = detect_knowledge_gap(
            assistant_text="I don't recall the name you mentioned.",
            user_message="",
            prior_user_messages=["my friend's name is alice"],
        )
        # Fallback shape — last-sentence content, lowercased by the detector.
        assert "name" in q.lower()


class TestNoFalsePositiveOnSuccessfulTaskCompletion:
    """The action-claim hallucination tests live elsewhere; we just want
    to confirm a clean success message doesn't flip the gap detector."""

    def test_task_done_message_no_trigger(self):
        is_gap, _ = detect_knowledge_gap(
            assistant_text="Done — saved your note 'Tofu' to LazyBrain.",
            user_message="save my dog's name as Tofu",
            prior_user_messages=[],
        )
        assert is_gap is False
