import type { LazyBrainNote, LazyBrainTag } from "../../api";
import { FilterBar } from "./FilterBar";
import type { Owner } from "./noteColors";
import { colorForTags } from "./noteColors";

interface Props {
  recent: LazyBrainNote[];
  pinned: LazyBrainNote[];
  journal: LazyBrainNote[];
  tags: LazyBrainTag[];
  selectedId: string | null;
  activeTag: string | null;
  hiddenCategories: Set<string>;
  ownerFilter: Owner | "all";
  categoryCounts: Record<string, number>;
  ownerCounts: Record<Owner, number>;
  onToggleCategory: (key: string) => void;
  onSetOwner: (o: Owner | "all") => void;
  onSelect: (note: LazyBrainNote) => void;
  onTagToggle: (tag: string) => void;
  onOpenJournalToday: () => void;
  onNewPage: () => void;
  onOpenGraph: () => void;
  onSearchFocus: () => void;
  noteCount: number;
}

const MAX_RECENT = 20;
const MAX_TAGS = 30;


export function PageListSidebar({
  recent,
  pinned,
  journal,
  tags,
  selectedId,
  activeTag,
  hiddenCategories,
  ownerFilter,
  categoryCounts,
  ownerCounts,
  onToggleCategory,
  onSetOwner,
  onSelect,
  onTagToggle,
  onOpenJournalToday,
  onNewPage,
  onOpenGraph,
  onSearchFocus,
  noteCount,
}: Props) {
  return (
    <aside className="w-60 shrink-0 h-full flex flex-col bg-bg-secondary border-r border-border">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="text-accent">
            <path d="M12 2a7 7 0 0 0-7 7c0 2.4 1.2 4.5 3 5.7V17a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-2.3c1.8-1.3 3-3.3 3-5.7a7 7 0 0 0-7-7Z" />
            <path d="M9 21h6" />
            <path d="M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z" />
          </svg>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-text-primary">LazyBrain</div>
            <div className="text-[11px] text-text-muted">
              {noteCount} note{noteCount === 1 ? "" : "s"} · encrypted
            </div>
          </div>
        </div>
        <div className="mt-3 flex gap-1.5">
          <button
            onClick={onNewPage}
            className="flex-1 px-2 py-1.5 rounded bg-accent text-bg-primary text-xs font-medium hover:opacity-90"
          >
            + New
          </button>
          <button
            onClick={onSearchFocus}
            className="px-2 py-1.5 rounded bg-bg-hover text-text-secondary hover:text-accent text-xs"
            title="Search  (⌘K)"
          >
            🔍
          </button>
          <button
            onClick={onOpenGraph}
            className="px-2 py-1.5 rounded bg-bg-hover text-text-secondary hover:text-accent text-xs"
            title="Graph"
          >
            🕸
          </button>
          <button
            onClick={() => window.open("/?page=lazybrain", "_blank", "noopener")}
            className="px-2 py-1.5 rounded bg-bg-hover text-text-secondary hover:text-accent text-xs"
            title="Open in new tab"
          >
            ↗
          </button>
        </div>
      </div>

      {/* Filter bar */}
      <FilterBar
        hiddenCategories={hiddenCategories}
        ownerFilter={ownerFilter}
        counts={categoryCounts}
        ownerCounts={ownerCounts}
        onToggleCategory={onToggleCategory}
        onSetOwner={onSetOwner}
      />

      {/* Quick sections */}
      <div className="flex-1 overflow-y-auto py-2">
        {/* Today */}
        <button
          onClick={onOpenJournalToday}
          className="w-full flex items-center gap-2 px-4 py-1.5 text-sm text-text-secondary hover:bg-bg-hover hover:text-text-primary"
        >
          <span>📅</span>
          <span>Today's journal</span>
        </button>

        {/* Pinned */}
        {pinned.length > 0 && (
          <div className="mt-3">
            <div className="px-4 py-1 text-[10px] uppercase tracking-wider text-text-muted">
              📌 Pinned · {pinned.length}
            </div>
            {pinned.slice(0, 10).map((n) => (
              <PageRow
                key={n.id}
                note={n}
                selected={selectedId === n.id}
                onClick={() => onSelect(n)}
              />
            ))}
          </div>
        )}

        {/* Recent */}
        <div className="mt-3">
          <div className="px-4 py-1 text-[10px] uppercase tracking-wider text-text-muted">
            Recent
          </div>
          {recent.slice(0, MAX_RECENT).map((n) => (
            <PageRow
              key={n.id}
              note={n}
              selected={selectedId === n.id}
              onClick={() => onSelect(n)}
            />
          ))}
          {recent.length === 0 && (
            <div className="px-4 py-2 text-[11px] text-text-muted italic">
              No notes yet.
            </div>
          )}
        </div>

        {/* Journal pages */}
        {journal.length > 0 && (
          <div className="mt-3">
            <div className="px-4 py-1 text-[10px] uppercase tracking-wider text-text-muted">
              📓 Journal pages
            </div>
            {journal.slice(0, 7).map((n) => (
              <PageRow
                key={n.id}
                note={n}
                selected={selectedId === n.id}
                onClick={() => onSelect(n)}
              />
            ))}
          </div>
        )}

        {/* Tags */}
        {tags.length > 0 && (
          <div className="mt-3">
            <div className="px-4 py-1 text-[10px] uppercase tracking-wider text-text-muted">
              Tags
            </div>
            <div className="px-3 pb-2 flex flex-wrap gap-1">
              {tags.slice(0, MAX_TAGS).map((t) => (
                <button
                  key={t.tag}
                  onClick={() => onTagToggle(t.tag)}
                  className={`px-1.5 py-0.5 rounded text-[11px] transition-colors ${
                    activeTag === t.tag
                      ? "bg-accent text-bg-primary"
                      : "bg-bg-hover text-text-muted hover:text-accent"
                  }`}
                >
                  #{t.tag}
                  <span className="opacity-60 ml-1">{t.count}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}


function PageRow({
  note,
  selected,
  onClick,
}: {
  note: LazyBrainNote;
  selected: boolean;
  onClick: () => void;
}) {
  const title = note.title || "(untitled)";
  const color = colorForTags(note.tags, note.pinned);
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-2 px-4 py-1 text-sm text-left transition-colors ${
        selected
          ? "bg-accent-soft text-accent"
          : "text-text-secondary hover:bg-bg-hover hover:text-text-primary"
      }`}
      title={`${color.label} — ${title}`}
    >
      <span
        className="shrink-0 w-[6px] h-[6px] rounded-full inline-block"
        style={{ backgroundColor: color.ring }}
        aria-hidden
      />
      <span className="shrink-0 text-[10px] opacity-70">{color.emoji}</span>
      <span className="truncate flex-1">{title}</span>
    </button>
  );
}
