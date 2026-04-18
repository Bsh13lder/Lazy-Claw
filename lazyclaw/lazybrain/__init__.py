"""LazyBrain — Python-native Logseq-style PKM for LazyClaw.

Shared between user and agent:
- Encrypted per-user notes (title + content AES-256-GCM with per-user DEK)
- [[wikilinks]] parser + backlinks index
- #tags for filtering, daily #journal/YYYY-MM-DD pages
- Graph of note relationships
- Zero-token `note_saved` UI events
"""
