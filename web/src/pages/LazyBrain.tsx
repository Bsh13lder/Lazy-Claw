import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import type { LazyBrainGraph, LazyBrainNote, LazyBrainTag } from "../api";
import { BacklinksPanel } from "../components/lazybrain/BacklinksPanel";
import { GraphView } from "../components/lazybrain/GraphView";
import { NoteEditor } from "../components/lazybrain/NoteEditor";
import { PageListSidebar } from "../components/lazybrain/PageListSidebar";
import {
  FILTER_CATEGORIES,
  matchesCategory,
  ownerOf,
  type Owner,
} from "../components/lazybrain/noteColors";


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

  // Graph overlay
  const [graphOpen, setGraphOpen] = useState(false);
  const [graphSize, setGraphSize] = useState({ width: 960, height: 640 });

  // Loading / errors
  const [error, setError] = useState<string | null>(null);

  // Filter state
  const [hiddenCategories, setHiddenCategories] = useState<Set<string>>(new Set());
  const [ownerFilter, setOwnerFilter] = useState<Owner | "all">("all");

  // ─── Fetchers ───────────────────────────────────────────────────────────
  const refresh = useCallback(async () => {
    try {
      const [recent, pins, journalNotes, tagList] = await Promise.all([
        api.listLazyBrainNotes({ tag: tagFilter || undefined, limit: 200 }),
        api.listLazyBrainNotes({ pinned: true, limit: 50 }),
        api.listLazyBrainJournal(14),
        api.listLazyBrainTags(),
      ]);
      setNotes(recent);
      setPinned(pins);
      setJournal(journalNotes);
      setTags(tagList);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [tagFilter]);

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

  // Fetch graph when overlay opens
  useEffect(() => {
    if (!graphOpen) return;
    let cancelled = false;
    api
      .getLazyBrainGraph({ limit: 500 })
      .then((g) => {
        if (!cancelled) setGraph(g);
      })
      .catch(() => {});
    const onResize = () =>
      setGraphSize({ width: window.innerWidth * 0.85, height: window.innerHeight * 0.8 });
    onResize();
    window.addEventListener("resize", onResize);
    return () => {
      cancelled = true;
      window.removeEventListener("resize", onResize);
    };
  }, [graphOpen]);

  // Global keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        searchRef.current?.focus();
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        handleNew();
      } else if (e.key === "Escape" && graphOpen) {
        setGraphOpen(false);
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

  return (
    <div className="h-full w-full flex overflow-hidden bg-bg-primary">
      {/* LEFT sidebar */}
      <PageListSidebar
        recent={visibleNotes}
        pinned={visiblePinned}
        journal={visibleJournal}
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
        onOpenGraph={() => setGraphOpen(true)}
        onSearchFocus={() => searchRef.current?.focus()}
        noteCount={totalCount}
      />

      {/* CENTER editor */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Search bar */}
        <div className="shrink-0 px-8 py-3 border-b border-border bg-bg-secondary/40">
          <div className="flex items-center gap-3">
            <input
              ref={searchRef}
              value={searchQ}
              onChange={(e) => {
                setSearchQ(e.target.value);
                handleSearch(e.target.value);
              }}
              placeholder="Search notes…  (⌘K)"
              className="flex-1 px-3 py-2 rounded bg-bg-primary border border-border text-sm outline-none focus:border-accent"
            />
            {tagFilter && (
              <button
                onClick={() => setTagFilter(null)}
                className="px-2 py-1 rounded bg-accent-soft text-accent text-xs hover:bg-accent hover:text-bg-primary"
                title="Clear tag filter"
              >
                #{tagFilter} ✕
              </button>
            )}
          </div>
        </div>

        {error && (
          <div className="px-8 py-2 bg-red-500/10 border-b border-red-500/30 text-red-400 text-sm">
            {error}
          </div>
        )}

        {/* Editor or empty state */}
        <div className="flex-1 min-h-0">
          {selected ? (
            <NoteEditor
              note={selected}
              onSave={handleSave}
              onDelete={handleDelete}
              onTogglePin={handleTogglePin}
              onLinkClick={handleLinkClick}
              onTagClick={handleTagClick}
            />
          ) : (
            <EmptyState
              count={totalCount}
              onNew={handleNew}
              onOpenJournal={handleOpenJournalToday}
            />
          )}
        </div>
      </div>

      {/* RIGHT backlinks */}
      <aside className="w-72 shrink-0 h-full border-l border-border bg-bg-secondary/60">
        <BacklinksPanel
          note={selected}
          backlinks={backlinks}
          onSelect={(n) => setSelectedId(n.id)}
        />
      </aside>

      {/* Graph overlay */}
      {graphOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm"
          onClick={() => setGraphOpen(false)}
        >
          <div
            className="bg-bg-secondary rounded-2xl border border-border p-4 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <div className="text-sm font-semibold text-text-primary">
                🕸 Graph · {graph.nodes.length} notes · {graph.edges.length} links
              </div>
              <button
                onClick={() => setGraphOpen(false)}
                className="px-2 py-1 rounded hover:bg-bg-hover text-text-muted"
              >
                ✕ close
              </button>
            </div>
            <GraphView
              graph={graph}
              width={graphSize.width}
              height={graphSize.height}
              notesById={notesById}
              onSelect={(id) => {
                setSelectedId(id);
                setGraphOpen(false);
              }}
            />
          </div>
        </div>
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
    <div className="h-full flex flex-col items-center justify-center gap-4 text-center px-8">
      <div className="text-5xl">🧠</div>
      <div className="text-xl font-semibold text-text-primary">
        {count === 0 ? "Your second brain is empty" : "Pick a page on the left"}
      </div>
      <div className="text-sm text-text-muted max-w-md">
        Write thoughts, link them with <code className="text-accent">[[wikilinks]]</code>, tag with <code className="text-accent">#tags</code>. Everything is encrypted with your personal key. Your agent reads and writes here too — so the brain grows while you work.
      </div>
      <div className="flex gap-2">
        <button
          onClick={onNew}
          className="px-4 py-2 rounded bg-accent text-bg-primary text-sm font-medium"
        >
          + New page
        </button>
        <button
          onClick={onOpenJournal}
          className="px-4 py-2 rounded bg-bg-hover text-text-primary text-sm font-medium"
        >
          📓 Open today's journal
        </button>
      </div>
      <div className="mt-4 text-[11px] text-text-muted">
        Try: <span className="text-accent">⌘K</span> search · <span className="text-accent">⌘N</span> new page · <span className="text-accent">⌘E</span> edit · <span className="text-accent">⌘S</span> save · <span className="text-accent">Esc</span> cancel
      </div>
    </div>
  );
}
