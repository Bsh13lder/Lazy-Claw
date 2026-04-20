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
import { Brain, BookOpen, Plus, Network, Search, X as XIcon, Pin, PinOff, Save, Trash2 } from "../components/lazybrain/icons";
import { PanelLeftOpen, PanelRightClose, PanelRightOpen, Command as CommandIcon } from "lucide-react";
import { CommandModal, type CommandAction } from "../components/lazybrain/CommandModal";
import { OutlinePane } from "../components/lazybrain/OutlinePane";
import { AIResultModal } from "../components/lazybrain/AIResultModal";
import { AutolinkResultModal } from "../components/lazybrain/AutolinkResultModal";
import { Canvas } from "../components/lazybrain/Canvas";
import { Sparkles, MessageSquare, Zap, RefreshCw, Layout } from "lucide-react";

const LS_LEFT = "lazybrain.leftCollapsed";
const LS_RIGHT = "lazybrain.rightCollapsed";

type ViewMode = "notes" | "graph" | "canvas";


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

  // Command modal (⌘K palette / ⌘O quick switcher)
  const [cmdkOpen, setCmdkOpen] = useState<null | "palette" | "switcher">(null);

  // Semantic search toggle (search bar ~ 🔍 Semantic)
  const [semanticMode, setSemanticMode] = useState(false);

  // AI modal state — pluggable run() so one component serves Ask/Rollup/Briefing.
  const [aiModal, setAiModal] = useState<null | {
    title: string;
    hint?: string;
    run: () => Promise<string>;
  }>(null);
  // Autolink modal — separate because it renders structured rows with
  // per-row Accept/Skip rather than a flat markdown dump.
  const [autolinkOpen, setAutolinkOpen] = useState(false);

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
  // Phase 3.4 — graph importance slider (1..10). Notes below the threshold
  // dim in the graph; 1 = show all, 10 = pinned-level only.
  const [minImportance, setMinImportance] = useState(1);

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

  // Global keyboard shortcuts — Obsidian-style
  //   ⌘K    → command palette
  //   ⌘O    → quick switcher
  //   ⌘⇧F   → focus search
  //   ⌘N    → new note
  //   Esc   → exit graph / close modal
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey;
      if (meta && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdkOpen("palette");
      } else if (meta && e.key.toLowerCase() === "o") {
        e.preventDefault();
        setCmdkOpen("switcher");
      } else if (meta && e.shiftKey && e.key.toLowerCase() === "f") {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (meta && e.key.toLowerCase() === "n") {
        e.preventDefault();
        handleNew();
      } else if (e.key === "Escape" && viewMode === "graph" && !cmdkOpen) {
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

  const handleCreateWithTitle = useCallback(
    async (title: string) => {
      const t = title.trim();
      if (!t) return;
      const note = await api.createLazyBrainNote({
        content: `# ${t}\n\n`,
        title: t,
      });
      await refresh();
      setSelectedId(note.id);
    },
    [refresh],
  );

  const handleNew = useCallback(async () => {
    const title = window.prompt("New page title:")?.trim();
    if (!title) return;
    await handleCreateWithTitle(title);
  }, [handleCreateWithTitle]);

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

  const handleSearch = useCallback(
    async (q: string) => {
      if (!q.trim()) {
        setSearchResults(null);
        return;
      }
      try {
        if (semanticMode) {
          const r = await api.semanticSearchLazyBrain(q, 25);
          setSearchResults(r.results);
        } else {
          const r = await api.searchLazyBrain(q, undefined, 50);
          setSearchResults(r.results);
        }
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [semanticMode],
  );

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
      if (minImportance > 1 && (note.importance ?? 5) < minImportance) return true;
      if (hiddenCategories.size === 0) return false;
      return FILTER_CATEGORIES.some(
        (c) => hiddenCategories.has(c.key) && matchesCategory(note.tags, c.key),
      );
    },
    [ownerFilter, hiddenCategories, minImportance],
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

  // Commands exposed in the palette. Built lazily so refs update naturally.
  const paletteActions = useMemo<CommandAction[]>(() => {
    const out: CommandAction[] = [
      {
        id: "new-note",
        label: "New page",
        hint: "⌘N",
        Icon: Plus,
        keywords: "create add write note",
        run: handleNew,
      },
      {
        id: "open-journal-today",
        label: "Open today's journal",
        Icon: BookOpen,
        keywords: "daily journal today",
        run: handleOpenJournalToday,
      },
      {
        id: "toggle-graph",
        label: viewMode === "graph" ? "Back to notes" : "Open graph view",
        hint: "Esc",
        Icon: Network,
        keywords: "graph network visualize",
        run: () => setViewMode(viewMode === "graph" ? "notes" : "graph"),
      },
      {
        id: "toggle-canvas",
        label: viewMode === "canvas" ? "Back to notes" : "Open canvas",
        Icon: Layout,
        keywords: "canvas board whiteboard flow",
        run: () => setViewMode(viewMode === "canvas" ? "notes" : "canvas"),
      },
      {
        id: "focus-search",
        label: "Focus search bar",
        hint: "⌘⇧F",
        Icon: Search,
        keywords: "find search grep",
        run: () => searchRef.current?.focus(),
      },
      {
        id: "toggle-left",
        label: leftCollapsed ? "Show pages sidebar" : "Hide pages sidebar",
        Icon: PanelLeftOpen,
        keywords: "sidebar left pages",
        run: () => setLeftCollapsed((v) => !v),
      },
      {
        id: "toggle-right",
        label: rightCollapsed ? "Show backlinks" : "Hide backlinks",
        Icon: PanelRightOpen,
        keywords: "sidebar right backlinks",
        run: () => setRightCollapsed((v) => !v),
      },
      {
        id: "clear-tag-filter",
        label: "Clear tag filter",
        Icon: XIcon,
        keywords: "reset filter tag",
        run: () => setTagFilter(null),
      },
      {
        id: "clear-search",
        label: "Clear search",
        Icon: XIcon,
        keywords: "reset search query",
        run: () => {
          setSearchQ("");
          setSearchResults(null);
        },
      },
      {
        id: "quick-switcher",
        label: "Quick switcher — jump to page",
        hint: "⌘O",
        Icon: CommandIcon,
        keywords: "switch open jump",
        run: () => setCmdkOpen("switcher"),
      },
    ];
    if (selected) {
      out.push(
        {
          id: "pin-toggle",
          label: selected.pinned ? "Unpin current page" : "Pin current page",
          Icon: selected.pinned ? PinOff : Pin,
          keywords: "pin favorite star",
          run: handleTogglePin,
        },
        {
          id: "save-current",
          label: "Save current page",
          hint: "⌘S",
          Icon: Save,
          keywords: "save write persist",
          run: () => handleSave({}),
        },
        {
          id: "delete-current",
          label: "Delete current page",
          Icon: Trash2,
          keywords: "remove delete trash",
          run: () => {
            if (window.confirm(`Delete "${selected.title || "(untitled)"}"?`)) {
              void handleDelete();
            }
          },
        },
        {
          id: "topic-rollup-current",
          label: "Topic rollup — synthesize this page",
          hint: "AI",
          Icon: Sparkles,
          keywords: "summary rollup synthesis brain",
          run: () => {
            const topic = selected.title || selected.content.slice(0, 60);
            setAiModal({
              title: `Rollup — ${topic}`,
              hint: "🧠 Topic rollup",
              run: async () => {
                const r = await api.topicRollupLazyBrain(topic);
                return (
                  r.rollup +
                  (r.sources?.length
                    ? `\n\n**${r.source_count} source(s):** ` +
                      r.sources.map((t) => `[[${t}]]`).join(", ")
                    : "")
                );
              },
            });
          },
        },
        {
          id: "autolink-current",
          label: "Autolink suggestions for this page",
          hint: "AI",
          Icon: Zap,
          keywords: "autolink wikilinks suggest connect",
          run: () => setAutolinkOpen(true),
        },
      );
    }
    // Global AI actions — don't depend on a selected note.
    out.push(
      {
        id: "ask-your-notes",
        label: "Ask your notes…",
        hint: "AI",
        Icon: MessageSquare,
        keywords: "ask question qa rag",
        run: () => {
          const q = window.prompt("What do you want to ask your notes?")?.trim();
          if (!q) return;
          setAiModal({
            title: q,
            hint: "💬 Ask",
            run: async () => {
              const r = await api.askLazyBrain(q);
              const srcs = r.sources?.length
                ? `\n\n**Sources:** ${r.sources
                    .map((t) => `[[${t}]]`)
                    .join(", ")}`
                : "";
              return (r.answer || "_(empty answer)_") + srcs;
            },
          });
        },
      },
      {
        id: "morning-briefing",
        label: "Generate morning briefing",
        hint: "AI",
        Icon: BookOpen,
        keywords: "morning briefing daily today",
        run: () => {
          setAiModal({
            title: "Morning briefing",
            hint: "📓 Briefing",
            run: async () => {
              const r = await api.morningBriefingLazyBrain(true);
              if (r.status === "appended")
                return `Appended briefing to **${r.date}**'s journal. Open today's journal to read it.`;
              if (r.status === "skipped")
                return "Briefing already exists for today.";
              return `Error: ${r.reason ?? "unknown"}`;
            },
          });
        },
      },
      {
        id: "reindex-embeddings",
        label: "Rebuild semantic index",
        hint: "AI",
        Icon: RefreshCw,
        keywords: "index embeddings semantic rebuild nomic",
        run: () => {
          setAiModal({
            title: "Rebuild semantic index",
            hint: "🔄 Reindex",
            run: async () => {
              const r = await api.reindexLazyBrainEmbeddings();
              return (
                `Indexed **${r.indexed}** of ${r.total} notes ` +
                `(skipped ${r.skipped}, model \`${r.model}\`).` +
                (r.indexed === 0 && r.skipped > 0
                  ? "\n\n_Ollama may be down or the embedding model isn't pulled. " +
                    "Run `ollama pull nomic-embed-text` to enable semantic search._"
                  : "")
              );
            },
          });
        },
      },
      {
        id: "toggle-semantic",
        label: semanticMode
          ? "Switch search to substring mode"
          : "Switch search to semantic mode",
        hint: "AI",
        Icon: Sparkles,
        keywords: "semantic substring search mode toggle",
        run: () => setSemanticMode((v) => !v),
      },
    );
    return out;
  }, [
    handleNew,
    handleOpenJournalToday,
    viewMode,
    leftCollapsed,
    rightCollapsed,
    selected,
    handleTogglePin,
    handleSave,
    handleDelete,
    semanticMode,
  ]);

  // Flat list of candidate notes for the modal — combines pinned + recent +
  // journal + search results so the user can jump to anything visible.
  const modalNotes = useMemo<LazyBrainNote[]>(() => {
    const seen = new Set<string>();
    const out: LazyBrainNote[] = [];
    const push = (arr: LazyBrainNote[]) => {
      for (const n of arr) {
        if (!seen.has(n.id)) {
          seen.add(n.id);
          out.push(n);
        }
      }
    };
    push(pinned);
    push(searchResults ?? []);
    push(notes);
    push(journal);
    return out;
  }, [pinned, searchResults, notes, journal]);

  return (
    <div className="lazybrain-root h-full w-full flex overflow-hidden bg-bg-primary">
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
          onTaskCompleted={() => {
            // Re-pull notes so the freshly-completed task drops out of the
            // sidebar list (the backend marks it done + updates the mirror).
            void refresh();
          }}
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
                    : semanticMode
                      ? "Semantic search — ask by meaning…"
                      : "Search notes…"
                }
                className="w-full pl-9 pr-32 py-2 rounded bg-bg-primary border border-border text-sm outline-none focus:border-accent transition-colors"
              />
              <button
                onClick={() => {
                  setSemanticMode((v) => !v);
                  if (searchQ.trim()) void handleSearch(searchQ);
                }}
                className={`absolute right-[64px] top-1/2 -translate-y-1/2 h-6 px-2 rounded text-[10px] font-semibold transition-colors flex items-center gap-1 ${
                  semanticMode
                    ? "bg-accent/20 text-accent"
                    : "text-text-muted hover:text-text-primary"
                }`}
                title="Toggle semantic search (needs nomic-embed-text)"
              >
                <Sparkles size={10} strokeWidth={2} />
                {semanticMode ? "SEMANTIC" : "semantic"}
              </button>
              <kbd className="absolute right-3 top-1/2 -translate-y-1/2 px-1.5 py-0.5 rounded bg-bg-hover border border-border font-mono text-[10px] text-text-muted pointer-events-none">
                ⌘⇧F
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

        {viewMode === "canvas" ? (
          <div className="flex-1 min-h-0">
            <Canvas
              onOpenNote={(id) => {
                setSelectedId(id);
                setViewMode("notes");
              }}
              resolveLink={resolveLink}
            />
          </div>
        ) : viewMode === "graph" ? (
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
            {/* Phase 3.4 — importance filter slider */}
            <div className="absolute top-3 right-3 z-10 bg-bg-secondary/80 backdrop-blur px-3 py-1.5 rounded border border-border flex items-center gap-2 text-[11px] text-text-muted">
              <span className="tabular-nums">importance ≥</span>
              <input
                type="range"
                min={1}
                max={10}
                value={minImportance}
                onChange={(e) => setMinImportance(Number(e.target.value))}
                className="w-28 accent-[color:var(--color-accent)]"
              />
              <span className="tabular-nums text-accent font-semibold w-4 text-right">
                {minImportance}
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
                backlinks={backlinks}
                notes={modalNotes}
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

      {/* AI result modal — Ask / Rollup / Briefing / Reindex */}
      <AIResultModal
        open={aiModal !== null}
        title={aiModal?.title ?? ""}
        hint={aiModal?.hint}
        run={aiModal?.run ?? (async () => "")}
        onClose={() => setAiModal(null)}
        onLinkClick={handleLinkClick}
        onTagClick={handleTagClick}
        resolveLink={resolveLink}
      />

      {/* Structured autolink modal — per-row Accept/Skip + Accept-all. */}
      {selected && (
        <AutolinkResultModal
          open={autolinkOpen}
          noteTitle={selected.title || ""}
          noteContent={selected.content || ""}
          onClose={() => setAutolinkOpen(false)}
          onApplyContent={(next) => handleSave({ content: next })}
        />
      )}

      {/* Command modal (⌘K palette / ⌘O switcher).
          Keyed on mode so state resets cleanly between palette ↔ switcher. */}
      <CommandModal
        key={cmdkOpen ?? "closed"}
        open={cmdkOpen !== null}
        mode={cmdkOpen ?? "palette"}
        onClose={() => setCmdkOpen(null)}
        notes={modalNotes}
        tags={tags}
        actions={paletteActions}
        onSelectNote={(n) => {
          setSelectedId(n.id);
          if (viewMode === "graph") setViewMode("notes");
        }}
        onSelectTag={(t) => setTagFilter(t)}
        onCreateNote={handleCreateWithTitle}
      />

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
            <div className="flex-1 min-h-0 overflow-y-auto flex flex-col">
              <BacklinksPanel
                note={selected}
                backlinks={backlinks}
                onSelect={(n) => setSelectedId(n.id)}
              />
              {selected && (
                <div className="border-t border-border">
                  <OutlinePane content={selected.content} />
                </div>
              )}
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
      <div className="mt-4 text-[11px] text-text-muted flex gap-3 flex-wrap justify-center">
        <Shortcut label="palette"  keys="⌘K" />
        <Shortcut label="switcher" keys="⌘O" />
        <Shortcut label="search"   keys="⌘⇧F" />
        <Shortcut label="new"      keys="⌘N" />
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
