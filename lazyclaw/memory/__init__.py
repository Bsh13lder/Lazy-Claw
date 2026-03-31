"""Memory subsystem — conversation compression, personal facts, and multi-layer context.

Public API:
  personal.py     — DB-backed encrypted personal facts (save_memory, get_memories, ...)
  daily_log.py    — Daily/weekly activity summaries (encrypted, DB-backed)
  compressor.py   — Sliding-window context compression + summary reuse
  layers.py       — 5-layer file-based memory (Global/Project/Channel/User/Session)

Layer hierarchy (layers.py):
  Global → Project → Channel → User → Session
  Priority (conflicts): User > Channel > Project > Global
"""

from lazyclaw.memory.layers import (
    MemoryLayer,
    MemoryResult,
    append_memory,
    auto_extract,
    load_session_context,
    read_memory,
    search_memory,
    write_memory,
)

__all__ = [
    "MemoryLayer",
    "MemoryResult",
    "append_memory",
    "auto_extract",
    "load_session_context",
    "read_memory",
    "search_memory",
    "write_memory",
]
