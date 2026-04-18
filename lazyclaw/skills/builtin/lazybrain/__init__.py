"""LazyBrain skills — natural-language PKM for the agent.

All skills call :mod:`lazyclaw.lazybrain.store` directly (no HTTP, no MCP).
"""
from lazyclaw.skills.builtin.lazybrain.notes import (
    DeleteNoteSkill,
    SaveNoteSkill,
    UpdateNoteSkill,
)
from lazyclaw.skills.builtin.lazybrain.recall import (
    GetNoteSkill,
    SearchNotesSkill,
)
from lazyclaw.skills.builtin.lazybrain.graph_skill import (
    FindLinkedSkill,
    GraphNeighborsSkill,
)
from lazyclaw.skills.builtin.lazybrain.journal_skill import (
    AppendJournalSkill,
    ListJournalSkill,
)
from lazyclaw.skills.builtin.lazybrain.pins import (
    ListPinnedSkill,
    PinNoteSkill,
    UnpinNoteSkill,
)
from lazyclaw.skills.builtin.lazybrain.rollup import EnableWeeklyRollupSkill

__all__ = [
    "SaveNoteSkill",
    "UpdateNoteSkill",
    "DeleteNoteSkill",
    "GetNoteSkill",
    "SearchNotesSkill",
    "FindLinkedSkill",
    "GraphNeighborsSkill",
    "AppendJournalSkill",
    "ListJournalSkill",
    "PinNoteSkill",
    "UnpinNoteSkill",
    "ListPinnedSkill",
    "EnableWeeklyRollupSkill",
]
