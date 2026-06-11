import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import type { LazyBrainGraph, LazyBrainNote, LazyBrainTag } from "../api";
import { BacklinksPanel } from "../components/lazybrain/BacklinksPanel";
import { ForceGraphView as GraphView } from "../components/lazybrain/ForceGraphView";
import { GraphInspector } from "../components/lazybrain/GraphInspector";
import { GraphPeekCard } from "../components/lazybrain/GraphPeekCard";
import { NoteEditor } from "../components/lazybrain/NoteEditor";
import { PageListSidebar } from "../components/lazybrain/PageListSidebar";
import {
  categoryKeysAllFor,
  ownerOf,
  type Owner,
} from "../components/lazybrain/noteColors";
import {
  Brain,
  BookOpen,
  Plus,
  Network,
  Search,
  X as XIcon,
  Pin,
  PinOff,
  Save,
  Trash2,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Command as CommandIcon,
  Sparkles,
  MessageSquare,
  Zap,
  RefreshCw,
  Layout,
} from "../components/lazybrain/icons";
import { CommandModal, type CommandAction } from "../components/lazybrain/CommandModal";
import { OutlinePane } from "../components/lazybrain/OutlinePane";
import { AIResultModal } from "../components/lazybrain/AIResultModal";
import { ShortcutsModal } from "../components/lazybrain/ShortcutsModal";
import { AutolinkResultModal } from "../components/lazybrain/AutolinkResultModal";
import { Canvas } from "../components/lazybrain/Canvas";
import { motion, AnimatePresence } from "framer-motion";

const LS_LEFT = "lazybrain.leftCollapsed";
const LS_RIGHT = "lazybrain.rightCollapsed";
const LS_VIEW_MODE = "lazybrain.viewMode";
const LS_OWNER_FILTER = "lazybrain.ownerFilter";
const LS_MIN_IMPORTANCE = "lazybrain.minImportance";
const LS_NOTES_LIMIT = "lazybrain.notesLimit";
const LS_GRAPH_LIMIT = "lazybrain.graphLimit";
const LS_GRAPH_QUERY = "lazybrain.graphQuery";
const LS_SHOW_ROLLED_UP = "lazybrain.showRolledUp";

// Default fetch sizes. Months-of-data users bump these via the "Load more"
// pill in the sidebar; the localStorage value sticks across reloads so the
// page never re-defaults the user back to a smaller pool.
const DEFAULT_NOTES_LIMIT = 1000;
// Graph fetch ceiling. The backend (lazyclaw/lazybrain/graph.py) caps at 2000
// nodes per request and only returns edges where BOTH endpoints survive the
// node slice, so a low ceiling silently drops most wikilinks: with 670 notes
// at limit=500 we measured 26 of 852 edges surviving (3%). Setting the floor
// to the backend cap means every active user sees their full topology.
const DEFAULT_GRAPH_LIMIT = 2000;
const NOTES_PAGE_SIZE = 1000;

function readNumber(key: string, fallback: number, min: number, max: number): number {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    const n = Number(raw);
    if (!Number.isFinite(n)) return fallback;
    return Math.min(max, Math.max(min, Math.round(n)));
  } catch {
    return fallback;
  }
}

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
  // Graph-mode search query persistence — separate localStorage entry
  // so a search active in graph mode is restored on next visit, even if
  // the user navigated through notes mode in between (where searchQ is
  // typically empty / used differently).
  const graphQueryRestoredRef = useRef(false);

  // View mode (notes editor vs full-page graph vs canvas) — persisted so
  // the user lands back on whichever layout they were last using.
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    try {
      const v = localStorage.getItem(LS_VIEW_MODE);
      if (v === "notes" || v === "graph" || v === "canvas") return v;
    } catch {
      // Private mode / disabled storage — fall through to default.
    }
    return "notes";
  });
  useEffect(() => {
    try { localStorage.setItem(LS_VIEW_MODE, viewMode); } catch { /* noop */ }
  }, [viewMode]);

  // Peek preview (graph DOUBLE-click — show card without leaving graph)
  const [peekId, setPeekId] = useState<string | null>(null);
  // Visual-only graph selection (single click). Highlights the node +
  // dims the rest + surfaces neighbour titles. Distinct from peekId so
  // a single click doesn't auto-pop the card.
  const [graphFocusId, setGraphFocusId] = useState<string | null>(null);

  // Command modal (⌘K palette / ⌘O quick switcher)
  const [cmdkOpen, setCmdkOpen] = useState<null | "palette" | "switcher">(null);

  // Keyboard-shortcuts help modal (⌘/ or palette action)
  const [shortcutsOpen, setShortcutsOpen] = useState(false);

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

  // Filter state — hiddenCategories persisted to localStorage so the
  // user's graph-legend selection sticks across reloads. Stored as a
  // JSON array; rehydrated to a Set on mount.
  const [hiddenCategories, setHiddenCategories] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem("lazybrain-hidden-categories");
      if (raw) return new Set(JSON.parse(raw) as string[]);
    } catch {
      // Bad JSON / private mode — start with no hidden categories.
    }
    return new Set();
  });
  useEffect(() => {
    try {
      localStorage.setItem(
        "lazybrain-hidden-categories",
        JSON.stringify([...hiddenCategories]),
      );
    } catch {
      // Best-effort persistence.
    }
  }, [hiddenCategories]);
  // Owner tab (All/User/Agent) — persisted so the user's last selection
  // sticks across reloads instead of snapping back to "all".
  const [ownerFilter, setOwnerFilter] = useState<Owner | "all">(() => {
    try {
      const v = localStorage.getItem(LS_OWNER_FILTER);
      if (v === "all" || v === "user" || v === "agent" || v === "unknown") {
        return v as Owner | "all";
      }
    } catch {
      // Private mode / disabled storage — fall through to default.
    }
    return "all";
  });
  useEffect(() => {
    try { localStorage.setItem(LS_OWNER_FILTER, ownerFilter); } catch { /* noop */ }
  }, [ownerFilter]);
  // Phase 3.4 — graph importance slider (1..10). Notes below the threshold
  // dim in the graph; 1 = show all, 10 = pinned-level only. Persisted so
  // the slider reflects the user's last threshold across reloads.
  const [minImportance, setMinImportance] = useState<number>(() => {
    try {
      const raw = localStorage.getItem(LS_MIN_IMPORTANCE);
      if (raw !== null) {
        const n = Number(raw);
        if (Number.isFinite(n) && n >= 1 && n <= 10) return Math.round(n);
      }
    } catch {
      // Private mode / disabled storage — fall through to default.
    }
    return 1;
  });
  useEffect(() => {
    try { localStorage.setItem(LS_MIN_IMPORTANCE, String(minImportance)); } catch { /* noop */ }
  }, [minImportance]);

  // Pagination — last fetched ceiling sticks across reloads so a user who
  // hit "Load more" to 4000 doesn't re-default to 1000 every time they
  // refresh. Bounded to [DEFAULT_NOTES_LIMIT, 50_000] so a corrupt
  // localStorage value can never request millions of rows.
  const [notesLimit, setNotesLimit] = useState<number>(() =>
    readNumber(LS_NOTES_LIMIT, DEFAULT_NOTES_LIMIT, DEFAULT_NOTES_LIMIT, 50_000),
  );
  useEffect(() => {
    try { localStorage.setItem(LS_NOTES_LIMIT, String(notesLimit)); } catch { /* noop */ }
  }, [notesLimit]);
  const [hasMore, setHasMore] = useState(false);
  const [totalKnown, setTotalKnown] = useState<number | null>(null);

  // Graph fetch ceiling — same persistence pattern. Default 500, bumped
  // alongside notesLimit so the graph view doesn't lag behind the
  // sidebar's pool size.
  const [graphLimit, setGraphLimit] = useState<number>(() =>
    readNumber(LS_GRAPH_LIMIT, DEFAULT_GRAPH_LIMIT, DEFAULT_GRAPH_LIMIT, 50_000),
  );
  useEffect(() => {
    try { localStorage.setItem(LS_GRAPH_LIMIT, String(graphLimit)); } catch { /* noop */ }
  }, [graphLimit]);

  // Archive toggle — when on, sidebar + graph re-fetch with
  // include_rolled_up=true and the original notes that have been folded
  // into a weekly/monthly rollup come back as smaller dimmer dots.
  // Off by default; persisted so a user who opens the archive once
  // doesn't have to re-open it on every reload.
  const [showRolledUp, setShowRolledUp] = useState<boolean>(() => {
    try { return localStorage.getItem(LS_SHOW_ROLLED_UP) === "true"; }
    catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem(LS_SHOW_ROLLED_UP, showRolledUp ? "true" : "false"); }
    catch { /* noop */ }
  }, [showRolledUp]);

  // ─── Fetchers ───────────────────────────────────────────────────────────
  const refresh = useCallback(async () => {
    try {
      const [recent, pins, journalNotes, tagList] = await Promise.all([
        api.listLazyBrainNotes({
          tag: tagFilter || undefined,
          include_rolled_up: showRolledUp,
          limit: notesLimit,
        }),
        api.listLazyBrainNotes({ pinned: true, limit: 50 }),
        api.listLazyBrainJournal(14),
        api.listLazyBrainTags(),
      ]);
      setNotes(recent);
      setPinned(pins);
      setJournal(journalNotes);
      setTags(tagList);
      const more = recent.length >= notesLimit;
      setHasMore(more);
      // We can only assert "≥ notesLimit" — the API doesn't return a
      // precise total. When the page is full assume more exists; when
      // it's short, the actual count is the floor.
      setTotalKnown(more ? null : recent.length);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [tagFilter, notesLimit, showRolledUp]);

  const loadMore = useCallback(() => {
    setNotesLimit((n) => Math.min(50_000, n + NOTES_PAGE_SIZE));
    // Bump the graph ceiling in lockstep so a user who paged the sidebar
    // doesn't see a smaller graph than what they just loaded.
    setGraphLimit((g) => Math.min(50_000, g + NOTES_PAGE_SIZE));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Auto-select something the FIRST time notes load — and only once.
  // Previous version re-fired every time selectedId changed, so
  // deselecting instantly re-selected pinned[0]. Now it runs once,
  // when notes first arrive, and never interferes with user choice.
  const didAutoSelectRef = useRef(false);
  useEffect(() => {
    if (didAutoSelectRef.current) return;
    if (notes.length === 0 && pinned.length === 0) return;
    didAutoSelectRef.current = true;
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

  // Restore last graph search query on first entry to graph mode, then
  // persist any further changes. Keeps notes-mode searchQ untouched.
  useEffect(() => {
    if (viewMode !== "graph") return;
    if (graphQueryRestoredRef.current) return;
    graphQueryRestoredRef.current = true;
    if (searchQ.trim()) return; // user already typed something — don't overwrite
    try {
      const saved = localStorage.getItem(LS_GRAPH_QUERY);
      if (saved && saved.trim()) setSearchQ(saved);
    } catch {
      // Private mode — ignore.
    }
  }, [viewMode, searchQ]);
  useEffect(() => {
    if (viewMode !== "graph") return;
    try { localStorage.setItem(LS_GRAPH_QUERY, searchQ); } catch { /* noop */ }
  }, [viewMode, searchQ]);

  // Fetch graph when graph view is active. Re-fetches when graphLimit
  // grows (after the user clicks "Load more") so the graph stays in sync
  // with the sidebar's pool. isGraphLoading drives the "Loading graph…"
  // indicator shown over the canvas before the first nodes arrive.
  const [isGraphLoading, setIsGraphLoading] = useState(false);
  useEffect(() => {
    if (viewMode !== "graph") return;
    let cancelled = false;
    setIsGraphLoading(true);
    api
      .getLazyBrainGraph({ limit: graphLimit, include_rolled_up: showRolledUp })
      .then((g) => {
        if (!cancelled) setGraph(g);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setIsGraphLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [viewMode, graphLimit, showRolledUp]);

  // Global keyboard shortcuts — Obsidian-style
  //   ⌘K    → command palette
  //   ⌘O    → quick switcher
  //   ⌘⇧F   → focus search
  //   ⌘N    → new note
  //   ⌘/    → keyboard shortcuts help
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
      } else if (meta && e.key === "/") {
        e.preventDefault();
        setShortcutsOpen(true);
      } else if (e.key === "Escape" && viewMode === "graph" && !cmdkOpen && !shortcutsOpen) {
        setViewMode("notes");
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  });

  // ─── Actions ────────────────────────────────────────────────────────────
  const handleSelect = useCallback((note: LazyBrainNote) => {
    // In graph mode, tapping the same sidebar item toggles selection off
    // (gives users a quick deselect from the sidebar). In notes mode,
    // keep it selected — we don't want to close the editor on re-click.
    setSelectedId((cur) =>
      cur === note.id && viewMode === "graph" ? null : note.id,
    );
    setPeekId((cur) => (cur === note.id ? null : cur));
  }, [viewMode]);

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

  // Category & owner counts across the full set (before filtering).
  // Single pass over notes; categoryKeysAllFor lowercases tags once and
  // uses string ops instead of regex per-category. At 5k notes this drops
  // from ~75k regex evals to one tag-list normalization per note.
  const { categoryCounts, ownerCounts } = useMemo(() => {
    const cats: Record<string, number> = {};
    const owners: Record<Owner, number> = { user: 0, agent: 0, unknown: 0 };
    for (const n of notes) {
      owners[ownerOf(n.tags)] += 1;
      for (const key of categoryKeysAllFor(n.tags)) {
        cats[key] = (cats[key] ?? 0) + 1;
      }
    }
    return { categoryCounts: cats, ownerCounts: owners };
  }, [notes]);

  const applyFilters = useCallback(
    (items: LazyBrainNote[]): LazyBrainNote[] => {
      return items.filter((n) => {
        if (ownerFilter !== "all" && ownerOf(n.tags) !== ownerFilter) return false;
        if (hiddenCategories.size === 0) return true;
        // categoryKeysAllFor lowercases tags once and uses string ops;
        // ~10x faster than the previous FILTER_CATEGORIES regex loop
        // (was 27 regex evals per note per filter pass × 3 passes).
        for (const k of categoryKeysAllFor(n.tags)) {
          if (hiddenCategories.has(k)) return false;
        }
        return true;
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
      for (const k of categoryKeysAllFor(note.tags)) {
        if (hiddenCategories.has(k)) return true;
      }
      return false;
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

  // ── Graph inspector adjacency + component size ────────────────────
  // Used by the right-side `GraphInspector` to show "X connections /
  // in system of N notes" without needing to reach into ForceSimulation.
  // Both maps are cheap (O(N+E)) and only rebuild when graph data
  // changes — typically once per page load.
  const graphAdjacency = useMemo(() => {
    const adj = new Map<string, Set<string>>();
    for (const e of graph.edges) {
      if (!adj.has(e.source)) adj.set(e.source, new Set());
      if (!adj.has(e.target)) adj.set(e.target, new Set());
      adj.get(e.source)!.add(e.target);
      adj.get(e.target)!.add(e.source);
    }
    return adj;
  }, [graph]);

  const graphComponentSize = useMemo(() => {
    // Union-find over edges — same algorithm ForceSimulation uses
    // internally. We keep it duplicated (rather than threading
    // sim state out through a callback) so the inspector works even
    // when graph mode hasn't mounted yet.
    const idToIdx = new Map<string, number>();
    graph.nodes.forEach((n, i) => idToIdx.set(n.id, i));
    const N = graph.nodes.length;
    const parent = new Int32Array(N);
    for (let i = 0; i < N; i++) parent[i] = i;
    const find = (x: number): number => {
      let r = x;
      while (parent[r] !== r) r = parent[r];
      while (parent[x] !== r) {
        const nx = parent[x];
        parent[x] = r;
        x = nx;
      }
      return r;
    };
    for (const e of graph.edges) {
      const a = idToIdx.get(e.source);
      const b = idToIdx.get(e.target);
      if (a === undefined || b === undefined) continue;
      const ra = find(a);
      const rb = find(b);
      if (ra !== rb) parent[ra] = rb;
    }
    const sizeByRoot = new Int32Array(N);
    for (let i = 0; i < N; i++) sizeByRoot[find(i)] += 1;
    const out = new Map<string, number>();
    for (let i = 0; i < N; i++) {
      out.set(graph.nodes[i].id, sizeByRoot[find(i)]);
    }
    return out;
  }, [graph]);

  // Wraps the existing top-level `handleTogglePin` (declared earlier
  // in this file) in a per-note signature so the GraphInspector's prop
  // type stays explicit. The inspector only ever toggles the currently-
  // selected note, but this lets it pass `note` through unambiguously.
  const handleInspectorTogglePin = useCallback(
    async (n: LazyBrainNote) => {
      try {
        const updated = await api.updateLazyBrainNote(n.id, { pinned: !n.pinned });
        if (selected?.id === n.id) setSelected(updated);
        await refresh();
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [selected, refresh],
  );

  const handleOpenInEditor = useCallback((id: string) => {
    setSelectedId(id);
    setViewMode("notes");
  }, []);

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
      {
        id: "keyboard-shortcuts",
        label: "Keyboard shortcuts",
        hint: "⌘/",
        Icon: CommandIcon,
        keywords: "help keys hotkeys bindings shortcuts",
        run: () => setShortcutsOpen(true),
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
          notesLimit={notesLimit}
          totalKnown={totalKnown}
          showRolledUp={showRolledUp}
          onToggleShowRolledUp={() => setShowRolledUp((v) => !v)}
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
          onNoteMutated={() => {
            // Sidebar performed an optimistic pin/delete — refetch so the
            // authoritative server state replaces the local override.
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
                className="lb-search-input w-full pl-9 pr-32 py-2 rounded bg-bg-primary border border-border text-sm outline-none transition-colors"
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

        <AnimatePresence mode="wait" initial={false}>
        {viewMode === "canvas" ? (
          <motion.div
            key="view-canvas"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}
            className="flex-1 min-h-0"
          >
            <Canvas
              onOpenNote={(id) => {
                setSelectedId(id);
                setViewMode("notes");
              }}
              resolveLink={resolveLink}
            />
          </motion.div>
        ) : viewMode === "graph" ? (
          <motion.div
            key="view-graph"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}
            className="flex-1 min-h-0 relative"
          >
            <GraphView
              graph={graph}
              notesById={notesById}
              selectedId={peekId ?? graphFocusId ?? selectedId}
              highlightQuery={searchQ}
              onSearchChange={setSearchQ}
              dimPredicate={graphDimPredicate}
              onSelect={(id) => {
                setGraphFocusId(id);
                // Also update the right-side inspector in real time so
                // the user can read about every node they click without
                // ever leaving the graph.
                setSelectedId(id);
              }}
              onPeek={(id) => setPeekId(id)}
              onClearPeek={() => {
                setPeekId(null);
                setGraphFocusId(null);
                setSelectedId(null);
              }}
              hiddenCategories={hiddenCategories}
              onToggleCategory={toggleCategory}
              onSetHiddenCategories={setHiddenCategories}
              categoryCounts={categoryCounts}
            />
            {/* Loading indicator — shown over the canvas while the first
                graph fetch is in flight so the user never stares at an
                unexplained blank view. */}
            {isGraphLoading && graph.nodes.length === 0 && (
              <div className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none">
                <span className="text-sm text-text-muted animate-pulse">
                  Loading graph…
                </span>
              </div>
            )}
            {/* Importance slider — parked bottom-right to avoid colliding
                with GraphView's top-left stats HUD + top-right zoom stack. */}
            <div className="absolute bottom-3 right-[120px] z-10 bg-bg-secondary/80 backdrop-blur px-3 py-1.5 rounded border border-border flex items-center gap-2 text-[11px] text-text-muted">
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
          </motion.div>
        ) : (
          <motion.div
            key="view-notes"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}
            className="flex-1 min-h-0"
          >
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
          </motion.div>
        )}
        </AnimatePresence>
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

      {/* Keyboard shortcuts help — ⌘/ or palette action */}
      <ShortcutsModal
        open={shortcutsOpen}
        onClose={() => setShortcutsOpen(false)}
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

      {/* RIGHT panel — collapsible. Notes mode shows backlinks +
          outline; graph mode shows the inspector for the clicked node. */}
      {(viewMode === "notes" || viewMode === "graph") && (
        rightCollapsed ? (
          <button
            onClick={() => setRightCollapsed(false)}
            title={viewMode === "graph" ? "Show graph inspector" : "Show backlinks"}
            className="shrink-0 w-5 h-full bg-bg-secondary/60 border-l border-border flex items-center justify-center text-text-muted hover:text-accent hover:bg-bg-hover transition-colors group"
          >
            <PanelRightOpen size={14} strokeWidth={1.75} className="group-hover:scale-110 transition-transform" />
          </button>
        ) : (
          <aside className="w-72 shrink-0 h-full border-l border-border bg-bg-secondary/60 flex flex-col">
            <div className="shrink-0 flex items-center justify-between px-3 py-1.5 border-b border-border">
              <span className="text-[10px] uppercase tracking-wider text-text-muted font-medium">
                {viewMode === "graph" ? "Inspector" : "Backlinks"}
              </span>
              <button
                onClick={() => setRightCollapsed(true)}
                title={viewMode === "graph" ? "Hide inspector" : "Hide backlinks"}
                className="p-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors"
              >
                <PanelRightClose size={13} strokeWidth={1.75} />
              </button>
            </div>
            {viewMode === "graph" ? (
              <GraphInspector
                note={selected}
                backlinks={backlinks}
                notesByTitle={titleMap}
                degree={selected ? (graphAdjacency.get(selected.id)?.size ?? 0) : 0}
                componentSize={selected ? (graphComponentSize.get(selected.id) ?? 1) : 0}
                onSelectId={(id) => setSelectedId(id)}
                onOpenInEditor={handleOpenInEditor}
                onTogglePin={handleInspectorTogglePin}
              />
            ) : (
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
            )}
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
