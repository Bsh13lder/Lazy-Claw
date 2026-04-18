import { useMemo, useState } from "react";
import type { LazyBrainNote, LazyBrainTag } from "../../api";
import { FilterBar } from "./FilterBar";
import type { Owner } from "./noteColors";
import { colorForTags, isSystemTag } from "./noteColors";
import {
  Brain,
  Lock,
  Plus,
  Search,
  Network,
  ExternalLink,
  Calendar,
  Star,
  BookOpen,
  Clock,
  Hash,
  Settings2,
  X,
  CategoryIcon,
} from "./icons";
import { Download, PanelLeftClose } from "lucide-react";

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
  viewMode: "notes" | "graph";
  hasMore?: boolean;
  onLoadMore?: () => void;
  searchQuery?: string;
  onClearSearch?: () => void;
  onCollapse?: () => void;
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
  viewMode,
  hasMore,
  onLoadMore,
  searchQuery,
  onClearSearch,
  onCollapse,
}: Props) {
  const [showSystemTags, setShowSystemTags] = useState(false);

  const visibleTags = useMemo(
    () => (showSystemTags ? tags : tags.filter((t) => !isSystemTag(t.tag))),
    [tags, showSystemTags],
  );

  const journalByMonth = useMemo(() => groupJournalByMonth(journal.slice(0, 14)), [journal]);

  return (
    <aside className="w-60 shrink-0 h-full flex flex-col bg-bg-secondary border-r border-border">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <Brain size={18} strokeWidth={1.75} className="text-accent" />
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-text-primary tracking-tight">
              LazyBrain
            </div>
            <div className="text-[11px] text-text-muted flex items-center gap-1">
              <Lock size={9} strokeWidth={2} />
              <span className="tabular-nums">{noteCount}</span>
            </div>
          </div>
          {onCollapse && (
            <button
              onClick={onCollapse}
              title="Hide sidebar"
              className="p-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors"
            >
              <PanelLeftClose size={13} strokeWidth={1.75} />
            </button>
          )}
        </div>
        <div className="mt-3 grid grid-cols-4 gap-1">
          <HeaderButton
            onClick={onNewPage}
            label="New"
            title="New page  (⌘N)"
            primary
            Icon={Plus}
          />
          <HeaderButton
            onClick={onSearchFocus}
            title="Search  (⌘K)"
            Icon={Search}
          />
          <HeaderButton
            onClick={onOpenGraph}
            title={viewMode === "graph" ? "Back to notes (Esc)" : "Open graph"}
            Icon={Network}
            active={viewMode === "graph"}
          />
          <HeaderButton
            onClick={() => window.open("/?page=lazybrain", "_blank", "noopener")}
            title="Open in new tab"
            Icon={ExternalLink}
          />
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
        {searchQuery && searchQuery.trim() !== "" ? (
          /* Search mode — single section, pinned/recent/journal hidden */
          <div className="mt-1">
            <div className="px-4 py-1.5 flex items-center gap-2 border-b border-border">
              <Search size={11} strokeWidth={1.75} className="text-accent" />
              <span className="text-[10px] uppercase tracking-wider text-text-secondary">
                Search
              </span>
              <span className="text-[10px] text-text-muted truncate">
                "{searchQuery}"
              </span>
              <span className="ml-auto flex items-center gap-1">
                <span className="text-[10px] text-text-muted tabular-nums">
                  {recent.length}
                </span>
                {onClearSearch && (
                  <button
                    onClick={onClearSearch}
                    className="p-0.5 rounded hover:bg-bg-hover text-text-muted hover:text-text-primary transition-colors"
                    title="Clear search"
                  >
                    <X size={11} strokeWidth={2} />
                  </button>
                )}
              </span>
            </div>
            {recent.length === 0 ? (
              <div className="px-4 py-6 text-[11px] text-text-muted italic text-center leading-relaxed">
                No results for "{searchQuery}".<br />
                Try a different word or clear search.
              </div>
            ) : (
              recent.map((n) => (
                <PageRow
                  key={n.id}
                  note={n}
                  selected={selectedId === n.id}
                  onClick={() => onSelect(n)}
                />
              ))
            )}
          </div>
        ) : (
          <>
            {/* Today's journal — feature button */}
            <button
              onClick={onOpenJournalToday}
              className="w-full flex items-center gap-2 px-4 py-1.5 text-sm text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors"
            >
              <Calendar size={14} strokeWidth={1.75} />
              <span>Today's journal</span>
            </button>

            {/* Pinned */}
            {pinned.length > 0 && (
              <SidebarSection label="Pinned" count={pinned.length} Icon={Star} iconColor="#fbbf24">
                {pinned.slice(0, 10).map((n) => (
                  <PageRow
                    key={n.id}
                    note={n}
                    selected={selectedId === n.id}
                    onClick={() => onSelect(n)}
                  />
                ))}
              </SidebarSection>
            )}

            {/* Recent */}
            <SidebarSection label="Recent" Icon={Clock}>
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
              {hasMore && onLoadMore && (
                <button
                  onClick={onLoadMore}
                  className="w-full flex items-center justify-center gap-1.5 px-4 py-1.5 mt-1 text-[11px] text-text-muted hover:text-accent hover:bg-bg-hover transition-colors"
                  title="Load older notes"
                >
                  <Download size={11} strokeWidth={1.75} />
                  <span>Load older notes</span>
                </button>
              )}
            </SidebarSection>

            {/* Journal pages — grouped by month */}
            {journalByMonth.length > 0 && (
              <SidebarSection label="Journal" Icon={BookOpen}>
                {journalByMonth.map(({ month, notes }) => (
                  <div key={month}>
                    <div className="px-4 py-0.5 text-[10px] text-text-muted/70 tabular-nums">
                      {month}
                    </div>
                    {notes.map((n) => (
                      <PageRow
                        key={n.id}
                        note={n}
                        selected={selectedId === n.id}
                        onClick={() => onSelect(n)}
                      />
                    ))}
                  </div>
                ))}
              </SidebarSection>
            )}
          </>
        )}

        {/* Tags */}
        {visibleTags.length > 0 && (
          <div className="mt-3">
            <div className="px-4 py-1 flex items-center gap-2">
              <Hash size={11} strokeWidth={1.75} className="text-text-muted" />
              <span className="text-[10px] uppercase tracking-wider text-text-muted">
                Tags
              </span>
              <button
                onClick={() => setShowSystemTags((v) => !v)}
                className={`ml-auto p-0.5 rounded transition-colors ${
                  showSystemTags ? "text-accent" : "text-text-muted hover:text-text-primary"
                }`}
                title={showSystemTags ? "Hide system tags" : "Show system tags"}
              >
                <Settings2 size={11} strokeWidth={1.75} />
              </button>
            </div>
            <div className="px-3 pb-2 flex flex-wrap gap-1">
              {visibleTags.slice(0, MAX_TAGS).map((t) => (
                <button
                  key={t.tag}
                  onClick={() => onTagToggle(t.tag)}
                  className={`px-1.5 py-0.5 rounded text-[11px] transition-colors tabular-nums ${
                    activeTag === t.tag
                      ? "bg-accent text-bg-primary"
                      : "bg-bg-hover/60 text-text-muted hover:text-accent hover:bg-bg-hover"
                  }`}
                >
                  <span className="opacity-60">#</span>
                  {t.tag}
                  <span className="opacity-50 ml-1">{t.count}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

import type { LucideIcon } from "lucide-react";

function HeaderButton({
  onClick,
  title,
  label,
  primary,
  active,
  Icon,
}: {
  onClick: () => void;
  title: string;
  label?: string;
  primary?: boolean;
  active?: boolean;
  Icon: LucideIcon;
}) {
  const base = "h-7 flex items-center justify-center gap-1 rounded text-xs transition-colors";
  const variant = primary
    ? "bg-accent text-bg-primary font-medium hover:opacity-90"
    : active
    ? "bg-accent text-bg-primary"
    : "bg-bg-hover text-text-muted hover:text-accent";
  return (
    <button onClick={onClick} title={title} className={`${base} ${variant}`}>
      <Icon size={13} strokeWidth={1.9} />
      {label && <span>{label}</span>}
    </button>
  );
}

function SidebarSection({
  label,
  count,
  Icon,
  iconColor,
  children,
}: {
  label: string;
  count?: number;
  Icon: LucideIcon;
  iconColor?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mt-3">
      <div className="px-4 py-1 flex items-center gap-2">
        <Icon size={11} strokeWidth={1.75} color={iconColor} className={iconColor ? undefined : "text-text-muted"} />
        <span className="text-[10px] uppercase tracking-wider text-text-muted">
          {label}
        </span>
        {count !== undefined && (
          <span className="text-[10px] text-text-muted/60 tabular-nums ml-auto">
            {count}
          </span>
        )}
      </div>
      {children}
    </div>
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
  const categoryKey = pickCategoryKey(note.tags, note.pinned);
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
      <CategoryIcon keyName={categoryKey} size={12} color={color.ring} />
      <span className="truncate flex-1">{title}</span>
    </button>
  );
}

/** Derive the single category key that matches a note (for icon selection).
 *  Mirrors the colorForTags priority but returns the key string. */
function pickCategoryKey(tags: string[] | null | undefined, pinned: boolean): string {
  if (pinned) return "pinned";
  if (!tags || tags.length === 0) return "_default";
  const priority = [
    "task", "deadline", "journal", "lesson", "til",
    "decision", "price", "command", "recipe", "contact",
    "idea", "rollup", "reference", "layer", "imported", "auto",
    "memory", "site-memory", "daily-log",
  ];
  const lower = tags.map((t) => t.toLowerCase());
  for (const key of priority) {
    if (lower.includes(key)) return key;
    if (lower.some((t) => t.startsWith(`${key}/`))) return key;
  }
  return "_default";
}

function groupJournalByMonth(
  notes: LazyBrainNote[],
): { month: string; notes: LazyBrainNote[] }[] {
  const groups = new Map<string, LazyBrainNote[]>();
  for (const n of notes) {
    const month = (n.created_at || "").slice(0, 7);
    if (!month) continue;
    const list = groups.get(month);
    if (list) list.push(n);
    else groups.set(month, [n]);
  }
  return Array.from(groups.entries())
    .sort((a, b) => (a[0] > b[0] ? -1 : 1))
    .map(([month, ns]) => ({ month, notes: ns }));
}
