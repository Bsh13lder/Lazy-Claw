"""Unit tests for the LazyBrain auto-capture / lesson dedup work.

Covers the two pieces that must be deterministic:

  1. The content-addressable keys in ``lesson_store`` and ``auto_capture`` —
     same content → same key (so re-saving UPSERTS instead of inserting),
     trivial whitespace/case drift → same key, distinct content → distinct key.
  2. The ``select_survivor`` keep-newest selection in the one-shot collapse
     script (extracted as a pure function so it's testable without a DB).

All targets here are pure functions — no DB, no encryption, no event loop.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from lazyclaw.lazybrain import auto_capture
from lazyclaw.runtime import lesson_store


# --------------------------------------------------------------------------
# Import select_survivor from the (non-package) script via importlib.
# --------------------------------------------------------------------------

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "graph_dedup_2026_06_01.py"
)
_spec = importlib.util.spec_from_file_location("graph_dedup_2026_06_01", _SCRIPT)
assert _spec and _spec.loader
graph_dedup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(graph_dedup)  # type: ignore[union-attr]


# ==========================================================================
# lesson_store content-addressable key
# ==========================================================================

class TestLessonContentKey:
    def test_same_content_same_hash(self):
        c = "for [job:upwork_inbox_watch], call upwork_get_messages first"
        assert lesson_store._content_hash(c) == lesson_store._content_hash(c)

    def test_same_content_same_canonical_title(self):
        c = "always quote the 3 most recent messages before summarizing"
        assert (
            lesson_store.lesson_canonical_title(c)
            == lesson_store.lesson_canonical_title(c)
        )

    def test_whitespace_and_case_drift_collapse(self):
        a = "Call upwork_get_messages first"
        b = "  call   UPWORK_get_messages    first  "
        assert lesson_store._content_hash(a) == lesson_store._content_hash(b)
        assert (
            lesson_store.lesson_canonical_title(a)
            == lesson_store.lesson_canonical_title(b)
        )

    def test_distinct_content_distinct_key(self):
        a = "call upwork_get_messages first"
        b = "call upwork_get_unread_count first"
        assert lesson_store._content_hash(a) != lesson_store._content_hash(b)
        assert (
            lesson_store.lesson_canonical_title(a)
            != lesson_store.lesson_canonical_title(b)
        )

    def test_drift_past_char_60_still_collapses(self):
        """The bug the fix targets: the old key was ``content[:60]`` so two
        saves that differed only AFTER char 60 made two rows. The hash covers
        the FULL content, so a *re-save of identical* long content collapses,
        while content differing past char 60 stays distinct."""
        prefix = "x" * 70
        same = prefix + " identical tail"
        assert (
            lesson_store.lesson_canonical_title(same)
            == lesson_store.lesson_canonical_title(same)
        )
        diff = prefix + " DIFFERENT tail"
        assert (
            lesson_store.lesson_canonical_title(same)
            != lesson_store.lesson_canonical_title(diff)
        )

    def test_title_has_readable_prefix_and_hash(self):
        title = lesson_store.lesson_canonical_title("be concise")
        assert title.startswith("Lesson: ")
        assert title.endswith("]")
        assert "[" in title

    def test_empty_content_is_stable(self):
        assert (
            lesson_store.lesson_canonical_title("")
            == lesson_store.lesson_canonical_title("")
        )


# ==========================================================================
# auto_capture content-addressable key
# ==========================================================================

class TestCaptureContentKey:
    def test_same_kind_and_content_same_key(self):
        k1 = auto_capture.capture_content_key("price", "Reference: www.upwork.com")
        k2 = auto_capture.capture_content_key("price", "Reference: www.upwork.com")
        assert k1 == k2

    def test_whitespace_case_drift_collapses(self):
        a = auto_capture.capture_content_key("url", "Reference: WWW.upwork.com")
        b = auto_capture.capture_content_key("url", "  reference:   www.UPWORK.com ")
        assert a == b

    def test_different_kind_distinct_key(self):
        same_content = "Reference: www.upwork.com"
        assert (
            auto_capture.capture_content_key("url", same_content)
            != auto_capture.capture_content_key("price", same_content)
        )

    def test_different_content_distinct_key(self):
        assert (
            auto_capture.capture_content_key("deadline", "**Deadline** today — A")
            != auto_capture.capture_content_key("deadline", "**Deadline** today — B")
        )

    def test_canonical_title_collapses_identical_content(self):
        t1 = auto_capture.capture_canonical_title(
            "url", "Reference: www.upwork.com", "ref: www.upwork.com",
        )
        t2 = auto_capture.capture_canonical_title(
            "url", "Reference: www.upwork.com", "ref: www.upwork.com",
        )
        assert t1 == t2

    def test_canonical_title_distinct_when_content_differs(self):
        """Same display title, different content (the
        ``deadline: (no subject captured)`` collision) → distinct titles, so
        genuinely different captures are NOT over-collapsed."""
        base = "deadline: (no subject captured)"
        t1 = auto_capture.capture_canonical_title(
            "deadline", "**Deadline** tomorrow — pay invoice", base,
        )
        t2 = auto_capture.capture_canonical_title(
            "deadline", "**Deadline** friday — submit proposal", base,
        )
        assert t1 != t2

    def test_canonical_title_falls_back_when_no_base_title(self):
        t = auto_capture.capture_canonical_title("idea", "💡 ship it", None)
        assert t.startswith("idea:")
        assert t.endswith("]")

    def test_canonical_title_embeds_base_title(self):
        t = auto_capture.capture_canonical_title(
            "price", "**X** = $5", "Price: X",
        )
        assert t.startswith("Price: X")


# ==========================================================================
# select_survivor — keep newest, tie-breaks, protection
# ==========================================================================

def _note(nid, created, *, pinned=False, importance=5, mt="fact"):
    return {
        "id": nid,
        "created_at": created,
        "pinned": pinned,
        "importance": importance,
        "memory_type": mt,
    }


class TestSelectSurvivor:
    def test_keeps_newest(self):
        members = [
            _note("a", "2026-05-01 10:00:00"),
            _note("b", "2026-05-29 10:00:00"),
            _note("c", "2026-05-10 10:00:00"),
        ]
        survivor, to_delete = graph_dedup.select_survivor(members)
        assert survivor == "b"
        assert set(to_delete) == {"a", "c"}

    def test_single_member_no_delete(self):
        members = [_note("solo", "2026-05-01 10:00:00")]
        survivor, to_delete = graph_dedup.select_survivor(members)
        assert survivor == "solo"
        assert to_delete == []

    def test_empty_group(self):
        survivor, to_delete = graph_dedup.select_survivor([])
        assert survivor is None
        assert to_delete == []

    def test_tie_created_at_pinned_wins(self):
        members = [
            _note("a", "2026-05-29 10:00:00", pinned=False),
            _note("b", "2026-05-29 10:00:00", pinned=True),
        ]
        survivor, to_delete = graph_dedup.select_survivor(members)
        assert survivor == "b"
        # a is not protected → deletable
        assert to_delete == ["a"]

    def test_tie_created_at_importance_breaks(self):
        members = [
            _note("a", "2026-05-29 10:00:00", importance=4),
            _note("b", "2026-05-29 10:00:00", importance=7),
        ]
        survivor, _ = graph_dedup.select_survivor(members)
        assert survivor == "b"

    def test_protected_never_deleted_pinned(self):
        # Newest is non-pinned; an older PINNED sibling must NOT be deleted.
        members = [
            _note("new", "2026-05-29 10:00:00", pinned=False),
            _note("old_pinned", "2026-05-01 10:00:00", pinned=True),
        ]
        survivor, to_delete = graph_dedup.select_survivor(members)
        assert survivor == "new"
        assert "old_pinned" not in to_delete
        assert to_delete == []

    def test_protected_never_deleted_high_importance(self):
        members = [
            _note("new", "2026-05-29 10:00:00", importance=5),
            _note("old_imp", "2026-05-01 10:00:00", importance=9),
        ]
        _, to_delete = graph_dedup.select_survivor(members)
        assert "old_imp" not in to_delete

    def test_protected_never_deleted_durable_type(self):
        for mt in ("user", "feedback"):
            members = [
                _note("new", "2026-05-29 10:00:00", mt="fact"),
                _note("old_durable", "2026-05-01 10:00:00", mt=mt),
            ]
            _, to_delete = graph_dedup.select_survivor(members)
            assert "old_durable" not in to_delete, mt

    def test_mixed_protected_and_deletable(self):
        members = [
            _note("newest", "2026-05-30 10:00:00"),
            _note("dup1", "2026-05-20 10:00:00"),
            _note("pinned_old", "2026-05-01 10:00:00", pinned=True),
            _note("dup2", "2026-05-10 10:00:00"),
        ]
        survivor, to_delete = graph_dedup.select_survivor(members)
        assert survivor == "newest"
        assert set(to_delete) == {"dup1", "dup2"}
        assert "pinned_old" not in to_delete

    def test_deterministic_id_tiebreak(self):
        # Everything identical except id → larger id sorts first (stable).
        members = [
            _note("aaa", "2026-05-29 10:00:00"),
            _note("zzz", "2026-05-29 10:00:00"),
        ]
        survivor, _ = graph_dedup.select_survivor(members)
        assert survivor == "zzz"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
