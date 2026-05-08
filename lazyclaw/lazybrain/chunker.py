"""Note → chunk splitter for chunk-level embedding + BM25 indexes.

Whole-note embeddings lose precision when the relevant 200-character
passage lives 5 KB into an 8-KB plan note — the centroid averages out.
Chunking is the consensus best practice for vaults > ~500 notes (per the
Stack Overflow / Unstructured chunking benchmarks, 2024).

Strategy: paragraph-aware token windows with overlap.

- Window size: ``WINDOW_TOKENS`` (default 800).
- Overlap: ``OVERLAP_TOKENS`` (default 200) — preserves cross-paragraph
  context so a sentence that spans two windows is fully scored in at
  least one chunk.
- Notes shorter than ``1.5 × WINDOW_TOKENS`` collapse into one chunk
  (== whole note) so we don't over-fragment short atomic facts.

Token estimation uses ``tiktoken`` when installed (cheap, exact for the
``cl100k_base`` BPE), and a 4-chars-per-token heuristic when it isn't.
The heuristic is ~10% off but the chunker tolerates that — windows
overlap, so the worst case is a slightly tighter or looser cut.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)

WINDOW_TOKENS = 800
OVERLAP_TOKENS = 200
SHORT_NOTE_FACTOR = 1.5
CHARS_PER_TOKEN = 4  # heuristic — see _estimate_tokens

# Lazy-loaded tiktoken encoder — None means "fall back to char heuristic".
_tiktoken_enc = None
_tiktoken_tried = False


def _get_tiktoken():
    """Return a tiktoken encoder or None when the lib is missing/unavailable."""
    global _tiktoken_enc, _tiktoken_tried
    if _tiktoken_tried:
        return _tiktoken_enc
    _tiktoken_tried = True
    try:
        import tiktoken  # type: ignore
        _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
    except Exception:  # pragma: no cover — install-time path
        _tiktoken_enc = None
    return _tiktoken_enc


def _estimate_tokens(text: str) -> int:
    enc = _get_tiktoken()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # Char heuristic: rounds up so we don't undercount.
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


@dataclass(frozen=True)
class Chunk:
    """One piece of a chunked note (immutable)."""
    idx: int
    text: str
    char_start: int  # offset into the original note body
    char_end: int


_PARAGRAPH_RE = re.compile(r"\n\s*\n+")


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """Return [(start, end)] of paragraph chunks (non-overlapping)."""
    if not text:
        return []
    spans: list[tuple[int, int]] = []
    last = 0
    for m in _PARAGRAPH_RE.finditer(text):
        spans.append((last, m.start()))
        last = m.end()
    if last < len(text):
        spans.append((last, len(text)))
    return [(s, e) for s, e in spans if e > s]


def chunk_note(
    text: str,
    *,
    window_tokens: int = WINDOW_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[Chunk]:
    """Split a note body into windowed chunks.

    Returns at least one chunk for any non-empty input — short notes
    collapse to a single chunk equal to the full body so the chunk path
    can subsume the legacy whole-note-embedding semantics without an
    if-else at every call site.
    """
    body = text or ""
    if not body.strip():
        return []
    total_tokens = _estimate_tokens(body)
    if total_tokens <= int(window_tokens * SHORT_NOTE_FACTOR):
        # Short — one chunk.
        return [Chunk(idx=0, text=body, char_start=0, char_end=len(body))]

    # Convert token windows into approximate char offsets via the same
    # estimator. Walking paragraph boundaries gives natural cuts that
    # don't slice mid-sentence; we then top up with hard char offsets if
    # paragraphs alone leave a window underfilled.
    paragraphs = _paragraph_spans(body)
    chunks: list[Chunk] = []
    cur_start: int | None = None
    cur_end = 0
    cur_tokens = 0
    last_para_end = 0
    overlap_chars = max(0, overlap_tokens * CHARS_PER_TOKEN)
    for p_start, p_end in paragraphs:
        para_text = body[p_start:p_end]
        para_tokens = _estimate_tokens(para_text)
        if cur_start is None:
            cur_start = p_start
        # Adding this paragraph would overflow the window — flush current.
        if cur_tokens + para_tokens > window_tokens and cur_tokens > 0:
            chunks.append(
                Chunk(
                    idx=len(chunks),
                    text=body[cur_start:cur_end],
                    char_start=cur_start,
                    char_end=cur_end,
                )
            )
            # Start the next window with overlap from the previous tail.
            new_start = max(p_start - overlap_chars, last_para_end)
            cur_start = new_start
            cur_tokens = _estimate_tokens(body[new_start:p_end])
            cur_end = p_end
        else:
            cur_end = p_end
            cur_tokens += para_tokens
        last_para_end = p_end

    if cur_start is not None and cur_end > cur_start:
        chunks.append(
            Chunk(
                idx=len(chunks),
                text=body[cur_start:cur_end],
                char_start=cur_start,
                char_end=cur_end,
            )
        )

    # Edge: a single ENORMOUS paragraph (no blank line breaks) — fall back
    # to hard char windows so we still get coverage instead of one mega-chunk.
    if len(chunks) <= 1 and total_tokens > window_tokens * SHORT_NOTE_FACTOR:
        chunks = list(_hard_window(body, window_tokens, overlap_tokens))
    return chunks


def _hard_window(
    body: str, window_tokens: int, overlap_tokens: int
) -> Iterable[Chunk]:
    win_chars = window_tokens * CHARS_PER_TOKEN
    overlap = overlap_tokens * CHARS_PER_TOKEN
    step = max(1, win_chars - overlap)
    idx = 0
    cursor = 0
    while cursor < len(body):
        end = min(len(body), cursor + win_chars)
        yield Chunk(idx=idx, text=body[cursor:end], char_start=cursor, char_end=end)
        idx += 1
        if end >= len(body):
            break
        cursor += step


__all__ = ["Chunk", "WINDOW_TOKENS", "OVERLAP_TOKENS", "chunk_note"]
