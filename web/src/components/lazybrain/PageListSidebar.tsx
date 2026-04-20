import { useMemo, useState } from "react";
import * as api from "../../api";
import type { LazyBrainNote, LazyBrainTag } from "../../api";
import { FilterBar } from "./FilterBar";
import type { Owner } from "./noteColors";
import { CATEGORY_PRIORITY, colorForTags, isSystemTag } from "./noteColors";
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
import { Download, PanelLeftClose, ListTodo } from "lucide-react";
import { TaskSymbol } from "./TaskSymbol";

interface Props {
  recent: LazyBrainNote[];
  pinned: LazyBrainNote[];
  journal: LazyBrainNote[];
  tasks: LazyBrainNote[];
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
  viewMode: "notes" | "graph" | "canvas";
  hasMore?: boolean;
  onLoadMore?: () => void;
  searchQuery?: string;
  onClearSearch?: () => void;
  onCollapse?: () => void;
  /** Called after a task checkbox flips to "done" so the parent can refresh
   *  task lists. Optional — section degrades to read-only without it. */
  onTaskCompleted?: (noteId: string) => void;
}

const MAX_RECENT = 20;
const MAX_TAGS = 30;

export function PageListSidebar({
  recent,
  pinned,
  journal,
  tasks,
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
  onTaskCompleted,
}: Props) {
  const [showSystemTags, setShowSystemTags] = useState(false);
  const [completingIds, setCompletingIds] = useState<Set<string>>(new Set());
  const [completedIds, setCompletedIds] = useState<Set<string>>(new Set());

  const handleCompleteTask = async (noteId: string) => {
    if (completingIds.has(noteId) || completedIds.has(noteId)) return;
    setCompletingIds((prev) => new Set(prev).add(noteId));
    try {
      await api.completeTaskByNoteId(noteId);
      setCompletedIds((prev) => new Set(prev).add(noteId));
      onTaskCompleted?.(noteId);
    } catch {
      // Show the row again in pending state so the user can retry.
    } finally {
      setCompletingIds((prev) => {
        const next = new Set(prev);
        next.delete(noteId);
        return next;
      });
    }
  };

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

            {/* Tasks & reminders — auto-pulled from notes tagged `task`.
                Each row gets an inline checkbox that calls the
                mark-task-done endpoint, so the user can complete tasks
                without opening the note. */}
            {tasks.length > 0 && (
              <SidebarSection
                label="Tasks"
                count={tasks.length - completedIds.size}
                Icon={ListTodo}
                iconColor="var(--color-lb-cat-task)"
              >
                {tasks.slice(0, 15).map((n) => (
                  <TaskRow
                    key={n.id}
                    note={n}
                    selected={selectedId === n.id}
                    completing={completingIds.has(n.id)}
                    done={completedIds.has(n.id)}
                    onClick={() => onSelect(n)}
                    onComplete={() => handleCompleteTask(n.id)}
                  />
                ))}
              </SidebarSection>
            )}

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
 *  Walks the shared CATEGORY_PRIORITY so it never drifts from
 *  GraphView's pickCategoryKey or noteColors.colorForTags. */
function pickCategoryKey(tags: string[] | null | undefined, pinned: boolean): string {
  if (pinned) return "pinned";
  if (!tags || tags.length === 0) return "_default";
  const lower = tags.map((t) => t.toLowerCase());
  for (const key of CATEGORY_PRIORITY) {
    if (lower.includes(key)) return key;
    if (lower.some((t) => t.startsWith(`${key}/`))) return key;
  }
  return "_default";
}

/** Format a `due/YYYY-MM-DD` tag relative to today. Returns null if no due tag. */
function dueLabel(tags: string[]): { text: string; tone: "overdue" | "today" | "future" } | null {
  const due = tags
    .map((t) => t.toLowerCase())
    .find((t) => t.startsWith("due/"))
    ?.slice(4);
  if (!due || !/^\d{4}-\d{2}-\d{2}$/.test(due)) return null;
  const today = new Date().toISOString().slice(0, 10);
  if (due < today) return { text: "overdue", tone: "overdue" };
  if (due === today) return { text: "today", tone: "today" };
  // Show short date (M/D) for future
  const [, m, d] = due.split("-");
  return { text: `${parseInt(m, 10)}/${parseInt(d, 10)}`, tone: "future" };
}

/** Strip markdown noise + title repetition so the preview line under the
 *  task title actually says something useful. Returns "" when the body
 *  is just the title or boilerplate. */
function previewFor(note: LazyBrainNote): string {
  const raw = (note.content || "").trim();
  if (!raw) return "";
  // Drop a leading `# Title` (matching note.title) — that's what the
  // sidebar already shows; repeating it is noise.
  const lines = raw
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#") && !/^[-*+]\s+\[/i.test(l));
  const first = lines[0] || "";
  // Strip markdown link/wikilink wrappers for compactness.
  const clean = first
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`]/g, "")
    .trim();
  return clean.length > 70 ? clean.slice(0, 68) + "…" : clean;
}

/** Pick a priority color from a `priority/<level>` tag. */
function priorityRail(tags: string[]): string | null {
  const lower = tags.map((t) => t.toLowerCase());
  if (lower.includes("priority/high") || lower.includes("priority/urgent")) {
    return "var(--color-lb-cat-deadline)";
  }
  if (lower.includes("priority/medium")) return "var(--color-lb-cat-task)";
  if (lower.includes("priority/low")) return "var(--color-lb-cat-learned-preference)";
  return null;
}

function TaskRow({
  note,
  selected,
  completing,
  done,
  onClick,
  onComplete,
}: {
  note: LazyBrainNote;
  selected: boolean;
  completing: boolean;
  done: boolean;
  onClick: () => void;
  onComplete: () => void;
}) {
  const title = note.title || "(untitled)";
  const due = dueLabel(note.tags || []);
  const preview = previewFor(note);
  const priority = priorityRail(note.tags || []);
  const completedColor = "var(--color-lb-cat-til)";
  const checkboxColor = done
    ? completedColor
    : due?.tone === "overdue"
    ? "var(--color-lb-cat-deadline)"
    : due?.tone === "today"
    ? "var(--color-lb-cat-task)"
    : "var(--color-lb-cat-task)";
  return (
    <div
      onClick={onClick}
      className={`group w-full flex items-start gap-2.5 pl-3 pr-3 py-1.5 cursor-pointer transition-colors border-l-2 ${
        selected
          ? "bg-accent-soft border-l-[color:var(--color-accent)]"
          : "border-l-transparent hover:bg-bg-hover"
      }`}
      style={priority && !selected ? { borderLeftColor: priority } : undefined}
    >
      <TaskSymbol
        done={done}
        busy={completing}
        color={checkboxColor}
        size={16}
        title={done ? "Completed" : "Mark task done"}
        onClick={() => {
          if (!done && !completing) onComplete();
        }}
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span
            className={`text-[13px] truncate ${
              done
                ? "text-text-muted line-through"
                : selected
                ? "text-accent font-medium"
                : "text-text-primary"
            }`}
            title={title}
          >
            {title}
          </span>
          {due && !done && (
            <span
              className="ml-auto shrink-0 text-[10px] px-1.5 py-0.5 rounded-md tabular-nums font-medium"
              style={{
                color:
                  due.tone === "overdue"
                    ? "var(--color-lb-cat-deadline)"
                    : due.tone === "today"
                    ? "var(--color-lb-cat-task)"
                    : "var(--color-text-muted)",
                background:
                  due.tone === "overdue"
                    ? "color-mix(in srgb, var(--color-lb-cat-deadline) 14%, transparent)"
                    : due.tone === "today"
                    ? "color-mix(in srgb, var(--color-lb-cat-task) 14%, transparent)"
                    : "rgba(255,255,255,0.04)",
              }}
            >
              {due.text}
            </span>
          )}
        </div>
        {preview && !done && (
          <div
            className="text-[11px] text-text-muted truncate leading-snug mt-0.5"
            title={preview}
          >
            {preview}
          </div>
        )}
      </div>
    </div>
  );
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
