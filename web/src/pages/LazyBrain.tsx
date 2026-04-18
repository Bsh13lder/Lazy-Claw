import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import type { LazyBrainGraph, LazyBrainNote, LazyBrainTag } from "../api";
import { BacklinksPanel } from "../components/lazybrain/BacklinksPanel";
import { GraphView } from "../components/lazybrain/GraphView";
import { GraphPeekCard } from "../components/lazybrain/GraphPeekCard";
import { NoteEditor } from "../components/lazybrain/NoteEditor";
import { PageListSidebar } from "../components/lazybrain/PageListSidebar";
import {
  FILTER_CATEGORIES,
  matchesCategory,
  ownerOf,
  type Owner,
} from "../components/lazybrain/noteColors";
import { Brain, BookOpen, Plus, Network, Search, X as XIcon } from "../components/lazybrain/icons";
import { PanelLeftOpen, PanelRightClose, PanelRightOpen } from "lucide-react";

const LS_LEFT = "lazybrain.leftCollapsed";
const LS_RIGHT = "lazybrain.rightCollapsed";

type ViewMode = "notes" | "graph";


function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function LazyBrain() {
  // Data
  const [notes, setNotes] = useState<LazyBrainNote[]>([]);
  const [pinned, setPinned] = useState<LazyBrainNote[]>([]);
  const [journal, setJournal] = useState<LazyBrainNote[]>([]);
  const [tags, setTags] = useState<LazyBrainTag[]>([]);
  const [graph, setGraph] = useState<LazyBrainGraph>({ nodes: [], edges: [] });

  // Selection / view
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<LazyBrainNote | null>(null);
  const [backlinks, setBacklinks] = useState<LazyBrainNote[]>([]);
  const [tagFilter, setTagFilter] = useState<string | null>(null);

  // Search
  const [searchQ, setSearchQ] = useState("");
  const [searchResults, setSearchResults] = useState<LazyBrainNote[] | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  // View mode (notes editor vs full-page graph)
  const [viewMode, setViewMode] = useState<ViewMode>("notes");

  // Peek preview (graph click — show card without leaving graph)
  const [peekId, setPeekId] = useState<string | null>(null);

  // Collapsible side panels (persisted in localStorage)
  const [leftCollapsed, setLeftCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(LS_LEFT) === "1"; } catch { return false; }
  });
  const [rightCollapsed, setRightCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(LS_RIGHT) === "1"; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem(LS_LEFT, leftCollapsed ? "1" : "0"); } catch { /* noop */ }
  }, [leftCollapsed]);
  useEffect(() => {
    try { localStorage.setItem(LS_RIGHT, rightCollapsed ? "1" : "0"); } catch { /* noop */ }
  }, [rightCollapsed]);

  // Loading / errors
  const [error, setError] = useState<string | null>(null);

  // Filter state
  const [hiddenCategories, setHiddenCategories] = useState<Set<string>>(new Set());
  const [ownerFilter, setOwnerFilter] = useState<Owner | "all">("all");

  // Pagination — how many notes loaded so far. "Load more" increments by 500.
  const PAGE_SIZE = 1000;
  const [notesLimit, setNotesLimit] = useState(PAGE_SIZE);
  const [hasMore, setHasMore] = useState(false);

  // ─── Fetchers ───────────────────────────────────────────────────────────
  const refresh = useCallback(async () => {
    try {
      const [recent, pins, journalNotes, tagList] = await Promise.all([
        api.listLazyBrainNotes({ tag: tagFilter || undefined, limit: notesLimit }),
        api.listLazyBrainNotes({ pinned: true, limit: 50 }),
        api.listLazyBrainJournal(14),
        api.listLazyBrainTags(),
      ]);
      setNotes(recent);
      setPinned(pins);
      setJournal(journalNotes);
      setTags(tagList);
      setHasMore(recent.length >= notesLimit);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [tagFilter, notesLimit]);

  const loadMore = useCallback(() => {
    setNotesLimit((n) => n + PAGE_SIZE);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Auto-select something the first time
  useEffect(() => {
    if (selectedId) return;
    const candidate = pinned[0] || notes[0];
    if (candidate) {
      setSelectedId(candidate.id);
      setSelected(candidate);
    }
  }, [notes, pinned, selectedId]);

  // Fetch selected note fresh + backlinks
  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      setBacklinks([]);
      return;
    }
    let cancelled = false;
    Promise.all([
      api.getLazyBrainNote(selectedId),
      api.getLazyBrainBacklinks(selectedId),
    ])
      .then(([n, bl]) => {
        if (cancelled) return;
        setSelected(n);
        setBacklinks(bl.backlinks);
      })
      .catch(() => {
        if (!cancelled) {
          setSelected(null);
          setBacklinks([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  // Fetch graph when graph view is active
  useEffect(() => {
    if (viewMode !== "graph") return;
    let cancelled = false;
    api
      .getLazyBrainGraph({ limit: 500 })
      .then((g) => {
        if (!cancelled) setGraph(g);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [viewMode]);

  // Global keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        searchRef.current?.focus();
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        handleNew();
      } else if (e.key === "Escape" && viewMode === "graph") {
        setViewMode("notes");
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  });

  // ─── Actions ────────────────────────────────────────────────────────────
  const handleSelect = useCallback((note: LazyBrainNote) => {
    setSelectedId(note.id);
  }, []);

  const handleLinkClick = useCallback(
    async (pageName: string) => {
      const match = notes.find(
        (n) => (n.title || "").toLowerCase() === pageName.toLowerCase(),
      );
      if (match) {
        setSelectedId(match.id);
        return;
      }
      const created = await api.createLazyBrainNote({
        content: `# ${pageName}\n\nNew page created from a wikilink.`,
        title: pageName,
      });
      await refresh();
      setSelectedId(created.id);
    },
    [notes, refresh],
  );

  const handleTagClick = useCallback((tag: string) => {
    setTagFilter((prev) => (prev === tag ? null : tag));
  }, []);

  const handleNew = useCallback(async () => {
    const title = window.prompt("New page title:")?.trim();
    if (!title) return;
    const note = await api.createLazyBrainNote({
      content: `# ${title}\n\n`,
      title,
    });
    await refresh();
    setSelectedId(note.id);
  }, [refresh]);

  const handleOpenJournalToday = useCallback(async () => {
    const iso = todayIso();
    const r = await api.getLazyBrainJournal(iso);
    if (r.note) {
      setSelectedId(r.note.id);
    } else {
      const created = await api.appendLazyBrainJournal(
        iso,
        "Started new journal page.",
      );
      await refresh();
      setSelectedId(created.id);
    }
  }, [refresh]);

  const handleSave = useCallback(
    async (patch: { title?: string; content?: string }) => {
      if (!selected) return;
      try {
        const updated = await api.updateLazyBrainNote(selected.id, patch);
        setSelected(updated);
        await refresh();
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [selected, refresh],
  );

  const handleDelete = useCallback(async () => {
    if (!selected) return;
    try {
      await api.deleteLazyBrainNote(selected.id);
      setSelected(null);
      setSelectedId(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }, [selected, refresh]);

  const handleTogglePin = useCallback(async () => {
    if (!selected) return;
    const updated = await api.updateLazyBrainNote(selected.id, {
      pinned: !selected.pinned,
    });
    setSelected(updated);
    await refresh();
  }, [selected, refresh]);

  const handleSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setSearchResults(null);
      return;
    }
    try {
      const r = await api.searchLazyBrain(q, undefined, 50);
      setSearchResults(r.results);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const notesById = useMemo(() => {
    const m: Record<string, LazyBrainNote> = {};
    [...notes, ...pinned, ...journal].forEach((n) => {
      m[n.id] = n;
    });
    return m;
  }, [notes, pinned, journal]);

  // Category & owner counts across the full set (before filtering)
  const { categoryCounts, ownerCounts } = useMemo(() => {
    const cats: Record<string, number> = {};
    const owners: Record<Owner, number> = { user: 0, agent: 0, unknown: 0 };
    for (const n of notes) {
      owners[ownerOf(n.tags)] += 1;
      for (const c of FILTER_CATEGORIES) {
        if (matchesCategory(n.tags, c.key)) cats[c.key] = (cats[c.key] ?? 0) + 1;
      }
    }
    return { categoryCounts: cats, ownerCounts: owners };
  }, [notes]);

  const applyFilters = useCallback(
    (items: LazyBrainNote[]): LazyBrainNote[] => {
      return items.filter((n) => {
        if (ownerFilter !== "all" && ownerOf(n.tags) !== ownerFilter) return false;
        if (hiddenCategories.size === 0) return true;
        // If a note matches NO hidden category, show it.
        const matchedHidden = FILTER_CATEGORIES.some(
          (c) => hiddenCategories.has(c.key) && matchesCategory(n.tags, c.key),
        );
        return !matchedHidden;
      });
    },
    [hiddenCategories, ownerFilter],
  );

  const toggleCategory = useCallback((key: string) => {
    setHiddenCategories((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const baseNotes = searchResults ?? notes;
  const visibleNotes = useMemo(() => applyFilters(baseNotes), [applyFilters, baseNotes]);
  const visiblePinned = useMemo(() => applyFilters(pinned), [applyFilters, pinned]);
  const visibleJournal = useMemo(() => applyFilters(journal), [applyFilters, journal]);
  const totalCount = notes.length;

  // Tasks/reminders surface — any note tagged `task` (auto-mirrored from the
  // tasks table + task_manager skill) gets pulled out into its own section so
  // the user can see what's pending without digging through Recent.
  const visibleTasks = useMemo(
    () => visibleNotes.filter((n) =>
      (n.tags || []).some((t) => t.toLowerCase() === "task" || t.toLowerCase().startsWith("task/")),
    ),
    [visibleNotes],
  );

  // Stable dimPredicate — identity only changes when the filter actually does,
  // so the graph simulation can settle instead of re-warming on every render.
  const graphDimPredicate = useCallback(
    (note?: LazyBrainNote) => {
      if (!note) return false;
      if (ownerFilter !== "all" && ownerOf(note.tags) !== ownerFilter) return true;
      if (hiddenCategories.size === 0) return false;
      return FILTER_CATEGORIES.some(
        (c) => hiddenCategories.has(c.key) && matchesCategory(note.tags, c.key),
      );
    },
    [ownerFilter, hiddenCategories],
  );

  const peekNote = peekId ? notesById[peekId] ?? null : null;

  // Wikilink hover resolver — O(1) lookup by lowercased title.
  const titleMap = useMemo(() => {
    const m = new Map<string, LazyBrainNote>();
    Object.values(notesById).forEach((n) => {
      if (n.title) m.set(n.title.toLowerCase(), n);
    });
    return m;
  }, [notesById]);

  const resolveLink = useCallback(
    (name: string): LazyBrainNote | null =>
      titleMap.get(name.trim().toLowerCase()) ?? null,
    [titleMap],
  );

  // Global escape: close peek first, then graph mode
  useEffect(() => {
    if (!peekId) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setPeekId(null);
      }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [peekId]);

  return (
    <div className="h-full w-full flex overflow-hidden bg-bg-primary">
      {/* LEFT sidebar — collapsible */}
      {leftCollapsed ? (
        <button
          onClick={() => setLeftCollapsed(false)}
          title="Show pages sidebar"
          className="shrink-0 w-5 h-full bg-bg-secondary border-r border-border flex items-center justify-center text-text-muted hover:text-accent hover:bg-bg-hover transition-colors group"
        >
          <PanelLeftOpen size={14} strokeWidth={1.75} className="group-hover:scale-110 transition-transform" />
        </button>
      ) : (
        <PageListSidebar
          recent={visibleNotes}
          pinned={visiblePinned}
          journal={visibleJournal}
          tasks={visibleTasks}
          tags={tags}
          selectedId={selectedId}
          activeTag={tagFilter}
          hiddenCategories={hiddenCategories}
          ownerFilter={ownerFilter}
          categoryCounts={categoryCounts}
          ownerCounts={ownerCounts}
          onToggleCategory={toggleCategory}
          onSetOwner={setOwnerFilter}
          onSelect={handleSelect}
          onTagToggle={handleTagClick}
          onOpenJournalToday={handleOpenJournalToday}
          onNewPage={handleNew}
          onOpenGraph={() => setViewMode(viewMode === "graph" ? "notes" : "graph")}
          onSearchFocus={() => searchRef.current?.focus()}
          noteCount={totalCount}
          viewMode={viewMode}
          hasMore={hasMore}
          onLoadMore={loadMore}
          searchQuery={searchQ}
          onClearSearch={() => {
            setSearchQ("");
            setSearchResults(null);
          }}
          onCollapse={() => setLeftCollapsed(true)}
        />
      )}

      {/* CENTER — notes view OR graph view */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Search bar (shared between modes) */}
        <div className="shrink-0 px-6 py-3 border-b border-border bg-bg-secondary/40">
          <div className="flex items-center gap-3">
            <div className="flex-1 relative">
              <Search
                size={14}
                strokeWidth={1.75}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none"
              />
              <input
                ref={searchRef}
                value={searchQ}
                onChange={(e) => {
                  setSearchQ(e.target.value);
                  if (viewMode === "notes") handleSearch(e.target.value);
                }}
                placeholder={
                  viewMode === "graph"
                    ? "Highlight in graph…"
                    : "Search notes…"
                }
                className="w-full pl-9 pr-14 py-2 rounded bg-bg-primary border border-border text-sm outline-none focus:border-accent transition-colors"
              />
              <kbd className="absolute right-3 top-1/2 -translate-y-1/2 px-1.5 py-0.5 rounded bg-bg-hover border border-border font-mono text-[10px] text-text-muted pointer-events-none">
                ⌘K
              </kbd>
            </div>
            {tagFilter && (
              <button
                onClick={() => setTagFilter(null)}
                className="h-8 px-2 rounded bg-accent-soft text-accent text-xs hover:bg-accent hover:text-bg-primary flex items-center gap-1 transition-colors"
                title="Clear tag filter"
              >
                <span>#{tagFilter}</span>
                <XIcon size={11} strokeWidth={2} />
              </button>
            )}
            {viewMode === "graph" && (
              <button
                onClick={() => setViewMode("notes")}
                className="h-8 px-2.5 rounded bg-bg-hover text-text-muted hover:text-text-primary text-xs flex items-center gap-1 transition-colors"
                title="Back to notes (Esc)"
              >
                <span>notes</span>
              </button>
            )}
          </div>
        </div>

        {error && (
          <div className="px-8 py-2 bg-red-500/10 border-b border-red-500/30 text-red-400 text-sm">
            {error}
          </div>
        )}

        {viewMode === "graph" ? (
          <div className="flex-1 min-h-0 relative">
            <GraphView
              graph={graph}
              notesById={notesById}
              selectedId={peekId ?? selectedId}
              highlightQuery={searchQ}
              dimPredicate={graphDimPredicate}
              onPeek={(id) => setPeekId(id)}
              hiddenCategories={hiddenCategories}
              onToggleCategory={toggleCategory}
              onSetHiddenCategories={setHiddenCategories}
              categoryCounts={categoryCounts}
            />
            <div className="absolute top-3 left-3 text-xs text-text-muted bg-bg-secondary/80 backdrop-blur px-2.5 py-1.5 rounded z-10 flex items-center gap-2 border border-border">
              <Network size={12} strokeWidth={1.75} />
              <span className="tabular-nums">
                {graph.nodes.length} notes · {graph.edges.length} links
              </span>
            </div>
            {peekNote && (
              <GraphPeekCard
                note={peekNote}
                onClose={() => setPeekId(null)}
                onOpen={() => {
                  setSelectedId(peekNote.id);
                  setPeekId(null);
                  setViewMode("notes");
                }}
                onTogglePin={async () => {
                  const updated = await api.updateLazyBrainNote(peekNote.id, {
                    pinned: !peekNote.pinned,
                  });
                  await refresh();
                  void updated;
                }}
                onLinkClick={handleLinkClick}
                onTagClick={handleTagClick}
                resolveLink={resolveLink}
              />
            )}
          </div>
        ) : (
          <div className="flex-1 min-h-0">
            {selected ? (
              <NoteEditor
                note={selected}
                onSave={handleSave}
                onDelete={handleDelete}
                onTogglePin={handleTogglePin}
                onLinkClick={handleLinkClick}
                onTagClick={handleTagClick}
                resolveLink={resolveLink}
              />
            ) : (
              <EmptyState
                count={totalCount}
                onNew={handleNew}
                onOpenJournal={handleOpenJournalToday}
              />
            )}
          </div>
        )}
      </div>

      {/* RIGHT backlinks — only in notes mode, collapsible */}
      {viewMode === "notes" && (
        rightCollapsed ? (
          <button
            onClick={() => setRightCollapsed(false)}
            title="Show backlinks"
            className="shrink-0 w-5 h-full bg-bg-secondary/60 border-l border-border flex items-center justify-center text-text-muted hover:text-accent hover:bg-bg-hover transition-colors group"
          >
            <PanelRightOpen size={14} strokeWidth={1.75} className="group-hover:scale-110 transition-transform" />
          </button>
        ) : (
          <aside className="w-72 shrink-0 h-full border-l border-border bg-bg-secondary/60 flex flex-col">
            <div className="shrink-0 flex items-center justify-end px-2 py-1.5 border-b border-border">
              <button
                onClick={() => setRightCollapsed(true)}
                title="Hide backlinks"
                className="p-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors"
              >
                <PanelRightClose size={13} strokeWidth={1.75} />
              </button>
            </div>
            <div className="flex-1 min-h-0">
              <BacklinksPanel
                note={selected}
                backlinks={backlinks}
                onSelect={(n) => setSelectedId(n.id)}
              />
            </div>
          </aside>
        )
      )}
    </div>
  );
}


function EmptyState({
  count,
  onNew,
  onOpenJournal,
}: {
  count: number;
  onNew: () => void;
  onOpenJournal: () => void;
}) {
  return (
    <div className="h-full flex flex-col items-center justify-center gap-5 text-center px-8">
      <div className="w-16 h-16 rounded-full bg-accent-soft flex items-center justify-center">
        <Brain size={32} strokeWidth={1.5} className="text-accent" />
      </div>
      <div className="text-xl font-semibold text-text-primary tracking-tight">
        {count === 0 ? "Your second brain is empty" : "Pick a page on the left"}
      </div>
      <div className="text-sm text-text-muted max-w-md leading-relaxed">
        Write thoughts, link them with <code className="text-accent">[[wikilinks]]</code>, tag with <code className="text-accent">#tags</code>. Everything is encrypted with your personal key. Your agent reads and writes here too — so the brain grows while you work.
      </div>
      <div className="flex gap-2">
        <button
          onClick={onNew}
          className="h-9 px-4 rounded bg-accent text-bg-primary text-sm font-medium flex items-center gap-1.5 hover:opacity-90"
        >
          <Plus size={14} strokeWidth={2} />
          <span>New page</span>
        </button>
        <button
          onClick={onOpenJournal}
          className="h-9 px-4 rounded bg-bg-hover text-text-primary text-sm font-medium flex items-center gap-1.5 hover:bg-bg-tertiary"
        >
          <BookOpen size={14} strokeWidth={1.75} />
          <span>Today's journal</span>
        </button>
      </div>
      <div className="mt-4 text-[11px] text-text-muted flex gap-3">
        <Shortcut label="search"   keys="⌘K" />
        <Shortcut label="new"      keys="⌘N" />
        <Shortcut label="edit"     keys="⌘E" />
        <Shortcut label="save"     keys="⌘S" />
        <Shortcut label="cancel"   keys="Esc" />
      </div>
    </div>
  );
}

function Shortcut({ keys, label }: { keys: string; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <kbd className="px-1.5 py-0.5 rounded bg-bg-hover border border-border font-mono text-[10px]">
        {keys}
      </kbd>
      <span>{label}</span>
    </span>
  );
}
