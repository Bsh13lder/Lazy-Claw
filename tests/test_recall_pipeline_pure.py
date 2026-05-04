"""Pure-python coverage for the second-brain substrate.

No DB, no Ollama, no encryption. Anything that can be tested as a
plain function lives here so the suite stays fast and runs in any
environment. DB-backed paths are covered separately under
``test_lazybrain_aliases.py`` and ``test_lazybrain_typed_edges.py``.
"""
from __future__ import annotations

from collections import OrderedDict

import pytest

from lazyclaw.lazybrain import embeddings as emb
from lazyclaw.runtime import context_builder as cb
from lazyclaw.skills.builtin import memory_recall as mr


# ─── MMR rerank ────────────────────────────────────────────────────────


def _v(*xs: float) -> list[float]:
    return [float(x) for x in xs]


def test_mmr_returns_at_most_k_items() -> None:
    """k cap respected; returns sorted-by-relevance prefix when k=1."""
    scored = [
        ("a", 0.95, _v(1.0, 0.0)),
        ("b", 0.90, _v(0.9, 0.1)),
        ("c", 0.80, _v(0.0, 1.0)),
    ]
    assert len(emb._mmr_rerank(scored, k=2)) == 2
    out = emb._mmr_rerank(scored, k=1)
    assert out == [scored[0]], "k=1 returns the highest-similarity item"


def test_mmr_diversifies_paraphrase_cluster() -> None:
    """Five near-identical vectors + one orthogonal → MMR pulls in
    the orthogonal one before exhausting paraphrases. Without MMR the
    top-2 would both be paraphrases."""
    cluster = [
        ("p1", 0.90, _v(1.0, 0.05, 0.0)),
        ("p2", 0.89, _v(1.0, 0.06, 0.0)),
        ("p3", 0.88, _v(1.0, 0.07, 0.0)),
        ("p4", 0.87, _v(1.0, 0.08, 0.0)),
        ("p5", 0.86, _v(1.0, 0.09, 0.0)),
    ]
    other = ("ortho", 0.55, _v(0.0, 0.0, 1.0))
    scored = sorted(cluster + [other], key=lambda t: t[1], reverse=True)
    top2 = emb._mmr_rerank(scored, k=2, lambda_=0.5)
    ids = [t[0] for t in top2]
    assert ids[0] == "p1", "highest-similarity always seeds the result"
    assert ids[1] == "ortho", (
        "MMR with λ=0.5 must pick the orthogonal hit over a near-duplicate; "
        f"got {ids}"
    )


def test_mmr_falls_through_when_pool_smaller_than_k() -> None:
    scored = [("only", 0.5, _v(1.0, 0.0))]
    assert emb._mmr_rerank(scored, k=5) == scored


def test_min_similarity_default_is_lowered_for_paraphrase_recall() -> None:
    """Regression guard: don't accidentally raise the threshold back to 0.45."""
    assert emb.MIN_SIMILARITY <= 0.35
    assert 0.0 < emb.MMR_LAMBDA <= 1.0


# ─── Embedding warm cache ──────────────────────────────────────────────


def test_cache_put_evicts_oldest_when_over_cap(monkeypatch) -> None:
    """LRU spill at the per-user cap. We patch the cap so we don't have to
    push 5000 vectors just to verify eviction order."""
    monkeypatch.setattr(emb, "_VECTOR_CACHE_CAP", 3)
    user = "user-cap-test"
    emb._VECTOR_CACHE.pop(user, None)
    for i in range(5):
        emb._cache_put(user, f"n{i}", emb.EMBED_MODEL, emb.EMBED_DIM, _v(i, 0))
    slot = emb._VECTOR_CACHE[user]
    assert list(slot.keys()) == ["n2", "n3", "n4"], (
        "oldest entries should evict first under LRU; got "
        f"{list(slot.keys())}"
    )
    assert len(slot) == 3
    emb._VECTOR_CACHE.pop(user, None)


def test_cache_evict_note_removes_across_all_users() -> None:
    emb._VECTOR_CACHE.pop("u1", None)
    emb._VECTOR_CACHE.pop("u2", None)
    emb._cache_put("u1", "shared", emb.EMBED_MODEL, emb.EMBED_DIM, _v(1, 0))
    emb._cache_put("u2", "shared", emb.EMBED_MODEL, emb.EMBED_DIM, _v(0, 1))
    emb._cache_evict_note("shared")
    assert "shared" not in (emb._VECTOR_CACHE.get("u1") or {})
    assert "shared" not in (emb._VECTOR_CACHE.get("u2") or {})


def test_cache_invalidate_user_drops_only_target_user() -> None:
    emb._VECTOR_CACHE["keep"] = OrderedDict([
        ("a", (emb.EMBED_MODEL, emb.EMBED_DIM, _v(1.0, 0.0))),
    ])
    emb._VECTOR_CACHE["drop"] = OrderedDict([
        ("b", (emb.EMBED_MODEL, emb.EMBED_DIM, _v(0.0, 1.0))),
    ])
    emb._cache_invalidate_user("drop")
    assert "drop" not in emb._VECTOR_CACHE
    assert "keep" in emb._VECTOR_CACHE
    emb._VECTOR_CACHE.pop("keep", None)


# ─── Confidence markers + conflict detection ───────────────────────────


def test_format_memory_marker_lazybrain_includes_verified_date() -> None:
    """Marker carries source + importance + last_verified date so the brain
    can reason about staleness."""
    out = cb._format_memory_marker({
        "memory_type": "lazybrain",
        "importance": 7,
        "created_at": "2026-04-12 18:30:00",
    })
    assert out.startswith("[note:7"), out
    assert "✓ 2026-04-12" in out, "ISO date must be visible to the LLM"


def test_format_memory_marker_personal_uses_subtype() -> None:
    out = cb._format_memory_marker({
        "memory_type": "preference",
        "importance": 4,
        "created_at": "2026-04-01 09:00:00",
    })
    assert out.startswith("[preference:4"), out


def test_detect_conflicts_xor_negation_only() -> None:
    """Pair with shared subject keys + EXACTLY ONE side carrying a
    negation token must produce a conflict. Same-polarity pairs do not."""
    a = {"id": "1", "content": "I prefer oat milk in coffee"}
    b = {"id": "2", "content": "I never drink oat milk anymore"}
    c = {"id": "3", "content": "I prefer almond milk in coffee"}

    conflicts = cb._detect_conflicts([a, b])
    pairs = [(p[0]["id"], p[1]["id"]) for p in conflicts]
    assert ("1", "2") in pairs or ("2", "1") in pairs

    same_polarity = cb._detect_conflicts([a, c])
    assert same_polarity == [], (
        "two non-negated facts that disagree are a *preference change*, "
        "not a logical contradiction — heuristic must not flag"
    )


def test_detect_conflicts_caps_at_three_pairs() -> None:
    """Don't flood the warning block when many memories collide."""
    items: list[dict] = []
    for i in range(10):
        items.append({"id": f"y{i}", "content": "I drink oat milk daily"})
    for i in range(10):
        items.append({"id": f"n{i}", "content": "I never drink oat milk"})
    conflicts = cb._detect_conflicts(items)
    assert len(conflicts) <= 3


def test_subject_key_strips_negation_tokens() -> None:
    """Negation tokens (no/never/not) are stripped from the bucket key so
    "I prefer oat milk" and "I never prefer oat milk" land in the same
    bucket and become candidates for conflict."""
    k1 = cb._memory_subject_key("I prefer oat milk daily")
    k2 = cb._memory_subject_key("I never prefer oat milk daily")
    assert k1 == k2, f"keys must collide for conflict detection: {k1!r} vs {k2!r}"


# ─── Unified recall merge + dedup ──────────────────────────────────────


def test_content_hash_folds_case_accent_whitespace() -> None:
    """A personal_memory row and its LazyBrain mirror collapse to one hash."""
    a = mr._content_hash("I  live in MADRID, Spain")
    b = mr._content_hash("i live in madrid, spain")
    c = mr._content_hash("I live in Madrid, Spain.")  # punctuation differs
    assert a == b
    # Punctuation isn't normalized — that's intentional, "Madrid" vs
    # "Madrid." is content-different. We just want case+whitespace+accent
    # equivalence.
    assert c != a


def test_merge_lessons_claim_slot_before_notes_then_personal() -> None:
    """Source priority: lesson > note > personal when content collides."""
    same_content = "Use the brave-search browser tool first"
    lesson = {
        "source": mr.SRC_LESSON, "id": "lb:l1",
        "title": "Skill shape · web/search · brave-first",
        "content": same_content, "conf": 7,
        "last_verified": "2026-04-10",
        "_score": 0.88, "outcome": "verified", "tags": [],
    }
    note = {
        "source": mr.SRC_NOTE, "id": "lb:n1",
        "title": "Search via Brave",
        "content": same_content, "conf": 5,
        "last_verified": "2026-04-09",
        "_score": 0.82, "tags": [],
    }
    personal = {
        "source": mr.SRC_PERSONAL, "id": "p1",
        "title": "fact",
        "content": same_content, "conf": 3,
        "last_verified": None, "tags": [],
    }
    out = mr._merge_and_dedupe([personal], [note], [lesson])
    assert len(out) == 1, "all three collapse to one row by content hash"
    assert out[0]["source"] == mr.SRC_LESSON, (
        "lesson must claim the slot first so the verified shape wins"
    )


def test_merge_sorts_by_confidence_then_score() -> None:
    rows: list[dict] = [
        {"source": mr.SRC_NOTE, "id": "low",
         "content": "alpha", "conf": 3, "_score": 0.9, "tags": []},
        {"source": mr.SRC_LESSON, "id": "high",
         "content": "beta", "conf": 8, "_score": 0.5, "tags": [],
         "outcome": "verified"},
        {"source": mr.SRC_NOTE, "id": "mid",
         "content": "gamma", "conf": 5, "_score": 0.7, "tags": []},
    ]
    out = mr._merge_and_dedupe([], rows, [])
    ids = [r["id"] for r in out]
    assert ids == ["high", "mid", "low"], (
        f"expected confidence-desc ordering, got {ids}"
    )


def test_row_label_lesson_carries_outcome_and_mark() -> None:
    """Verified shapes get a ✓; pending get a ?. The brain reads the
    distinction on every line so it doesn't replay a shape that's
    only awaiting verification as if it were proven."""
    verified = {
        "source": mr.SRC_LESSON, "conf": 7,
        "last_verified": "2026-04-12",
        "outcome": "verified", "tags": [],
    }
    pending = {
        "source": mr.SRC_LESSON, "conf": 4,
        "last_verified": "2026-04-15",
        "outcome": "pending", "tags": [],
    }
    assert "✓ verified" in mr._row_label(verified)
    assert "? pending" in mr._row_label(pending)
