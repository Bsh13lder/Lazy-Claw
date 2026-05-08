"""Reciprocal Rank Fusion — fuse multiple ranked lists into one.

RRF (Cormack et al., 2009) is the standard score-free fusion method
shipped in production hybrid-search stacks at Elastic, Weaviate, Vertex AI,
and others. It's literally one line:

    score(doc) = Σ_i  1 / (k + rank_i(doc))

with ``k=60`` empirically chosen so the bottom of long lists doesn't
dominate. We don't normalise scores — RRF only looks at *positions* — so it
fuses BM25 (unbounded) and cosine (0-1) safely without per-engine tuning.
"""
from __future__ import annotations

from typing import Iterable, Sequence

# Empirical default from the original RRF paper. Larger k = lower-ranked
# items contribute less; smaller k = the top dominates. 60 is the
# everyone-converged value.
DEFAULT_K: int = 60


def fuse(
    lists: Sequence[Sequence[str]],
    *,
    k: int = DEFAULT_K,
    limit: int | None = None,
) -> list[tuple[str, float]]:
    """Fuse N pre-ranked id lists. Higher score = better.

    Each input list is treated as a ranking — element 0 is rank 1, element
    1 is rank 2, etc. Documents present in multiple lists accumulate their
    reciprocal-rank contributions. The output is sorted by score DESC.

    >>> fuse([["a", "b", "c"], ["b", "a", "d"]])  # doctest: +ELLIPSIS
    [('b', ...), ('a', ...), ('c', ...), ('d', ...)]

    ``k`` defaults to 60 per the original paper; pass a different value
    only when reproducing a specific reference implementation.

    ``limit`` truncates the fused output. ``None`` returns every fused doc.
    """
    if k < 1:
        raise ValueError("RRF k must be >= 1")
    scores: dict[str, float] = {}
    for ranked in lists:
        for rank_zero_based, doc_id in enumerate(ranked):
            if not doc_id:
                continue
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank_zero_based + 1)
    fused = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return fused[:limit] if limit else fused


def fuse_to_ids(
    lists: Sequence[Sequence[str]],
    *,
    k: int = DEFAULT_K,
    limit: int | None = None,
) -> list[str]:
    """Convenience: fuse and return ids only (drops the score)."""
    return [doc_id for doc_id, _ in fuse(lists, k=k, limit=limit)]


def __all__() -> Iterable[str]:  # pragma: no cover — explicit symbol list
    return ("DEFAULT_K", "fuse", "fuse_to_ids")
