"""LazyBrain skills — natural-language PKM for the agent.

All skills call :mod:`lazyclaw.lazybrain.store` directly (no HTTP, no MCP).
"""
from lazyclaw.skills.builtin.lazybrain.notes import (
    DeleteNoteSkill,
    ListTagsSkill,
    ListTitlesSkill,
    MergeNotesSkill,
    RenamePageSkill,
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
    DeleteJournalLineSkill,
    DeleteJournalSkill,
    GetJournalSkill,
    ListJournalSkill,
    RewriteJournalSkill,
)
from lazyclaw.skills.builtin.lazybrain.pins import (
    ListPinnedSkill,
    PinNoteSkill,
    UnpinNoteSkill,
)
from lazyclaw.skills.builtin.lazybrain.rollup import EnableWeeklyRollupSkill
from lazyclaw.skills.builtin.lazybrain.ai_skills import (
    AskNotesSkill,
    MorningBriefingSkill,
    ReindexEmbeddingsSkill,
    SemanticSearchSkill,
    SuggestLinksSkill,
    SuggestMetadataSkill,
    TopicRollupSkill,
)
from lazyclaw.skills.builtin.lazybrain.morning_review import MorningReviewSkill

__all__ = [
    "SaveNoteSkill",
    "UpdateNoteSkill",
    "DeleteNoteSkill",
    "ListTagsSkill",
    "ListTitlesSkill",
    "RenamePageSkill",
    "MergeNotesSkill",
    "GetNoteSkill",
    "SearchNotesSkill",
    "FindLinkedSkill",
    "GraphNeighborsSkill",
    "AppendJournalSkill",
    "ListJournalSkill",
    "GetJournalSkill",
    "DeleteJournalSkill",
    "DeleteJournalLineSkill",
    "RewriteJournalSkill",
    "PinNoteSkill",
    "UnpinNoteSkill",
    "ListPinnedSkill",
    "EnableWeeklyRollupSkill",
    "AskNotesSkill",
    "MorningBriefingSkill",
    "ReindexEmbeddingsSkill",
    "SemanticSearchSkill",
    "SuggestLinksSkill",
    "SuggestMetadataSkill",
    "TopicRollupSkill",
    "MorningReviewSkill",
]
