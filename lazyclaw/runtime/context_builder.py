from __future__ import annotations

from lazyclaw.config import Config
from lazyclaw.runtime.personality import load_personality


async def build_context(config: Config, user_id: str) -> str:
    """Build system prompt with personality + personal memories."""
    personality = load_personality()

    # Load memories
    from lazyclaw.memory.personal import get_memories

    memories = await get_memories(config, user_id, limit=10)

    if not memories:
        return personality

    # Format memories section
    memory_lines = []
    for m in memories:
        memory_lines.append(f"- {m['content']}")

    memories_section = "\n\n---\n\n## What I know about you\n" + "\n".join(memory_lines)

    return personality + memories_section
