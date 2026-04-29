import { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../../api";
import type { LazyBrainNote, LazyBrainTag } from "../../api";
import { FilterBar } from "./FilterBar";
import type { Owner } from "./noteColors";
import {
  categoryKeysFor,
  colorForTags,
  isSystemTag,
  SKILLS_VAULT_KEYS,
} from "./noteColors";
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
  Sparkles,
  X,
  Check,
  Pin,
  PinOff,
  Trash2,
  CategoryIcon,
} from "./icons";
import {
  Download,
  PanelLeftClose,
  ListTodo,
  ChevronRight,
  Link2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
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
  /** Called after a pin toggle or delete succeeds so the parent can refetch.
   *  When omitted, optimistic local state masks the change until next reload. */
  onNoteMutated?: (noteId: string) => void;
}

const MAX_RECENT = 20;
const MAX_TAGS = 30;

type SectionKey = "tasks" | "topics" | "pinned" | "recent" | "journal" | "tags";
type SortKey = "recent" | "importance" | "alpha";

const LS_OPEN = "lazybrain-sidebar-open";
const LS_SORT = "lazybrain-sidebar-sort";
const LS_JM = "lazybrain-sidebar-jmonths";

const DEFAULT_OPEN: Record<SectionKey, boolean> = {
  tasks: true,
  topics: true,
  pinned: true,
  recent: true,
  journal: true,
  tags: true,
};

const UNDO_WINDOW_MS = 5000;

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
  onNoteMutated,
}: Props) {
  const [showSystemTags, setShowSystemTags] = useState(false);
  const [completingIds, setCompletingIds] = useState<Set<string>>(new Set());
  const [completedIds, setCompletedIds] = useState<Set<string>>(new Set());

  // Per-section open/closed state. Persisted as a single JSON blob in
  // localStorage so the user's collapse choices survive reloads. We
  // spread DEFAULT_OPEN over the parsed value so newly-added sections
  // default to open even on existing installs.
  const [openSections, setOpenSections] = useState<Record<SectionKey, boolean>>(() => {
    try {
      const raw = localStorage.getItem(LS_OPEN);
      if (raw) return { ...DEFAULT_OPEN, ...(JSON.parse(raw) as Partial<Record<SectionKey, boolean>>) };
    } catch {
      // Private mode / disabled storage — fall through to defaults.
    }
    return DEFAULT_OPEN;
  });
  const toggleSection = (k: SectionKey) =>
    setOpenSections((prev) => {
      const next = { ...prev, [k]: !prev[k] };
      try {
        localStorage.setItem(LS_OPEN, JSON.stringify(next));
      } catch {
        // Persistence is best-effort.
      }
      return next;
    });

  // Closed-month set for the Journal section. We persist the *closed*
  // months (rather than open) so the default — every month visible —
  // stays free of localStorage entries on a fresh install.
  const [closedMonths, setClosedMonths] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(LS_JM);
      if (raw) return new Set(JSON.parse(raw) as string[]);
    } catch {
      // ignore
    }
    return new Set<string>();
  });
  const toggleMonth = (m: string) =>
    setClosedMonths((prev) => {
      const next = new Set(prev);
      if (next.has(m)) next.delete(m);
      else next.add(m);
      try {
        localStorage.setItem(LS_JM, JSON.stringify([...next]));
      } catch {
        // ignore
      }
      return next;
    });

  // Recent-section sort key. Each value maps to a deterministic
  // comparator below; "recent" preserves the server-supplied order.
  const [sortKey, setSortKey] = useState<SortKey>(() => {
    try {
      const v = localStorage.getItem(LS_SORT);
      if (v === "recent" || v === "importance" || v === "alpha") return v;
    } catch {
      // ignore
    }
    return "recent";
  });
  const changeSortKey = (k: SortKey) => {
    setSortKey(k);
    try {
      localStorage.setItem(LS_SORT, k);
    } catch {
      // ignore
    }
  };

  // Optimistic mutation state — pin toggles + deletes apply locally
  // immediately and the API call is fire-and-forget. On failure the
  // override is rolled back so the row pops back into its prior state.
  const [pinOverride, setPinOverride] = useState<Map<string, boolean>>(() => new Map());
  const [deletedIds, setDeletedIds] = useState<Set<string>>(() => new Set());
  const [mutBusy, setMutBusy] = useState<Set<string>>(() => new Set());
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // Two-step delete state — first click arms a row, second click commits.
  // Auto-disarms after 3 seconds. Replaces the jarring `window.confirm`
  // dialog with an inline "Delete?" pill that stays in the editorial
  // aesthetic. Single armed row at a time keeps the surface obvious.
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const pendingTimerRef = useRef<number | null>(null);
  const clearPendingTimer = () => {
    if (pendingTimerRef.current !== null) {
      window.clearTimeout(pendingTimerRef.current);
      pendingTimerRef.current = null;
    }
  };
  useEffect(() => {
    // Auto-disarm on unmount or when arming flips.
    return () => clearPendingTimer();
  }, []);

  const isPinnedFor = (n: LazyBrainNote) => pinOverride.get(n.id) ?? n.pinned;

  const togglePin = async (note: LazyBrainNote) => {
    if (mutBusy.has(note.id)) return;
    const next = !isPinnedFor(note);
    setPinOverride((prev) => new Map(prev).set(note.id, next));
    setMutBusy((prev) => new Set(prev).add(note.id));
    try {
      await api.updateLazyBrainNote(note.id, { pinned: next });
      onNoteMutated?.(note.id);
    } catch {
      // Rollback the override so the row reflects ground truth again.
      setPinOverride((prev) => {
        const m = new Map(prev);
        m.delete(note.id);
        return m;
      });
    } finally {
      setMutBusy((prev) => {
        const s = new Set(prev);
        s.delete(note.id);
        return s;
      });
    }
  };

  // Trash queue — a row that's been confirm-deleted goes here and the
  // API call is deferred for `UNDO_WINDOW_MS`. The user can hit Undo on
  // the toast within that window to fully restore. On unmount we flush
  // remaining timers so confirmed deletes still hit the server.
  type TrashEntry = { noteId: string; title: string; timeoutId: number };
  const [trash, setTrash] = useState<TrashEntry[]>([]);
  const trashRef = useRef(trash);
  useEffect(() => {
    trashRef.current = trash;
  }, [trash]);
  useEffect(() => {
    return () => {
      // Flush any in-flight undo windows on unmount — confirmed deletes
      // shouldn't silently disappear because the user navigated away.
      for (const t of trashRef.current) {
        window.clearTimeout(t.timeoutId);
        void api.deleteLazyBrainNote(t.noteId);
      }
    };
  }, []);

  const finalizeTrash = async (noteId: string) => {
    setTrash((prev) => prev.filter((t) => t.noteId !== noteId));
    try {
      await api.deleteLazyBrainNote(noteId);
      onNoteMutated?.(noteId);
    } catch {
      // Server-side delete failed — restore the row so the user sees it
      // again rather than silently losing it.
      setDeletedIds((prev) => {
        const s = new Set(prev);
        s.delete(noteId);
        return s;
      });
    }
  };

  const undoTrash = (noteId: string) => {
    const entry = trashRef.current.find((t) => t.noteId === noteId);
    if (!entry) return;
    window.clearTimeout(entry.timeoutId);
    setTrash((prev) => prev.filter((t) => t.noteId !== noteId));
    setDeletedIds((prev) => {
      const s = new Set(prev);
      s.delete(noteId);
      return s;
    });
  };

  const requestDelete = (note: LazyBrainNote) => {
    if (mutBusy.has(note.id)) return;
    // Already in the trash queue — Undo handles it via the toast.
    if (trashRef.current.some((t) => t.noteId === note.id)) return;
    // First click on the trash icon arms the row. Second click commits.
    if (pendingDeleteId !== note.id) {
      clearPendingTimer();
      setPendingDeleteId(note.id);
      pendingTimerRef.current = window.setTimeout(() => {
        setPendingDeleteId((curr) => (curr === note.id ? null : curr));
        pendingTimerRef.current = null;
      }, 3000);
      return;
    }
    // Armed — confirm. Hide row optimistically and queue the API call.
    clearPendingTimer();
    setPendingDeleteId(null);
    setDeletedIds((prev) => new Set(prev).add(note.id));
    const timeoutId = window.setTimeout(() => {
      void finalizeTrash(note.id);
    }, UNDO_WINDOW_MS);
    setTrash((prev) => [
      ...prev.filter((t) => t.noteId !== note.id),
      { noteId: note.id, title: note.title || "(untitled)", timeoutId },
    ]);
  };

  const copyWikilink = (note: LazyBrainNote) => {
    const t = note.title || "(untitled)";
    try {
      navigator.clipboard?.writeText(`[[${t}]]`);
      setCopiedId(note.id);
      window.setTimeout(
        () => setCopiedId((c) => (c === note.id ? null : c)),
        1200,
      );
    } catch {
      // Clipboard unavailable (HTTP / sandboxed) — silently no-op.
    }
  };

  // Skills vault toggle (Obsidian "workspaces" metaphor) — `kind/shape*`
  // chips hide by default. Persisted to localStorage so the user's
  // choice survives reloads. The default-closed state keeps the agent's
  // own learning corpus out of the user's daily view.
  const [skillsVaultOpen, setSkillsVaultOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem("lazybrain-skills-vault-open") === "true";
    } catch {
      return false;
    }
  });

  // Synchronise the vault state with the parent's `hiddenCategories`
  // set so shape notes don't just lose their chips — they actually
  // filter out of the graph too. Runs once on mount + on toggle.
  useEffect(() => {
    for (const key of SKILLS_VAULT_KEYS) {
      const isCurrentlyHidden = hiddenCategories.has(key);
      const shouldBeHidden = !skillsVaultOpen;
      if (isCurrentlyHidden !== shouldBeHidden) {
        onToggleCategory(key);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skillsVaultOpen]);

  const toggleSkillsVault = () => {
    setSkillsVaultOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("lazybrain-skills-vault-open", next ? "true" : "false");
      } catch {
        // Private mode / disabled storage — value just doesn't persist.
      }
      return next;
    });
  };

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

  // Aggregate `topic/*` tags so we can render a "Topics" section grouped by
  // topic name with note counts. The agent auto-records lessons here from
  // every skill execution, plus weekly rollup notes get tagged with
  // `kind/rollup`.
  const topicGroups = useMemo(() => {
    const groups: Array<{ topic: string; count: number; hasRollup: boolean }> = [];
    const seen = new Map<string, { count: number; hasRollup: boolean }>();
    for (const t of tags) {
      if (!t.tag.startsWith("topic/")) continue;
      const name = t.tag.slice(6);
      if (!name) continue;
      const prev = seen.get(name) ?? { count: 0, hasRollup: false };
      prev.count += t.count;
      seen.set(name, prev);
    }
    // Note: per-topic rollup detection lives at the `kind/rollup` level —
    // we surface a sparkle when ANY rollup tag is present overall by
    // checking the tag list separately.
    const hasAnyRollup = tags.some((t) => t.tag === "kind/rollup");
    for (const [topic, info] of seen) {
      groups.push({ topic, count: info.count, hasRollup: hasAnyRollup });
    }
    groups.sort((a, b) => b.count - a.count);
    return groups.slice(0, 12);
  }, [tags]);

  // Visible-tasks list — drop notes the user just deleted optimistically.
  const visibleTasks = useMemo(
    () => tasks.filter((n) => !deletedIds.has(n.id)),
    [tasks, deletedIds],
  );

  // Visible-pinned merges the parent's `pinned` array with the user's
  // local pin overrides. Locally-unpinned drop out, locally-pinned recent
  // notes promote up. De-duplicated by id.
  const visiblePinned = useMemo(() => {
    const seen = new Set<string>();
    const out: LazyBrainNote[] = [];
    for (const n of pinned) {
      if (deletedIds.has(n.id)) continue;
      if (pinOverride.get(n.id) === false) continue;
      if (seen.has(n.id)) continue;
      seen.add(n.id);
      out.push(n);
    }
    for (const n of recent) {
      if (deletedIds.has(n.id)) continue;
      if (pinOverride.get(n.id) === true && !seen.has(n.id)) {
        seen.add(n.id);
        out.push(n);
      }
    }
    return out;
  }, [pinned, recent, deletedIds, pinOverride]);

  const sortedRecent = useMemo(() => {
    const visible = recent.filter((n) => !deletedIds.has(n.id));
    if (sortKey === "recent") return visible;
    const arr = [...visible];
    if (sortKey === "importance") {
      arr.sort((a, b) => {
        const aP = (pinOverride.get(a.id) ?? a.pinned) ? 1 : 0;
        const bP = (pinOverride.get(b.id) ?? b.pinned) ? 1 : 0;
        if (aP !== bP) return bP - aP;
        return (b.importance ?? 0) - (a.importance ?? 0);
      });
    } else {
      arr.sort((a, b) =>
        (a.title || "(untitled)").localeCompare(b.title || "(untitled)", undefined, {
          sensitivity: "base",
        }),
      );
    }
    return arr;
  }, [recent, sortKey, deletedIds, pinOverride]);

  const visibleJournalByMonth = useMemo(
    () => groupJournalByMonth(journal.filter((n) => !deletedIds.has(n.id)).slice(0, 14)),
    [journal, deletedIds],
  );
  const journalCount = useMemo(
    () => visibleJournalByMonth.reduce((s, g) => s + g.notes.length, 0),
    [visibleJournalByMonth],
  );

  // ── Keyboard navigation ──────────────────────────────────────────────
  // Maintain a flat ordered list of every navigable note rendered in the
  // sidebar (skipping notes inside collapsed sections / collapsed months).
  // j / ArrowDown moves down, k / ArrowUp moves up, Enter opens the
  // focused note, P toggles its pin, Del/Backspace arms+commits the
  // two-step delete, Esc clears focus.
  const [focusedNoteId, setFocusedNoteId] = useState<string | null>(null);

  const navigable: LazyBrainNote[] = useMemo(() => {
    const out: LazyBrainNote[] = [];
    if (openSections.tasks) {
      for (const n of visibleTasks.slice(0, 15)) out.push(n);
    }
    if (openSections.pinned) {
      for (const n of visiblePinned.slice(0, 10)) out.push(n);
    }
    if (openSections.recent) {
      for (const n of sortedRecent.slice(0, MAX_RECENT)) out.push(n);
    }
    if (openSections.journal) {
      for (const g of visibleJournalByMonth) {
        if (closedMonths.has(g.month)) continue;
        for (const n of g.notes) out.push(n);
      }
    }
    return out;
  }, [
    openSections,
    visibleTasks,
    visiblePinned,
    sortedRecent,
    visibleJournalByMonth,
    closedMonths,
  ]);

  const navById = useMemo(() => {
    const m = new Map<string, LazyBrainNote>();
    for (const n of navigable) m.set(n.id, n);
    return m;
  }, [navigable]);

  // Whether a given note id corresponds to a row that owns pin/delete
  // actions (TaskRow doesn't, so P/Del should no-op on tasks).
  const isTaskId = useMemo(() => {
    const s = new Set<string>();
    for (const n of visibleTasks) s.add(n.id);
    return s;
  }, [visibleTasks]);

  // Latest-value refs so the global keydown handler doesn't have to
  // re-bind on every state change.
  const navRef = useRef(navigable);
  const focusedRef = useRef(focusedNoteId);
  const navByIdRef = useRef(navById);
  const isTaskIdRef = useRef(isTaskId);
  useEffect(() => {
    navRef.current = navigable;
  }, [navigable]);
  useEffect(() => {
    focusedRef.current = focusedNoteId;
  }, [focusedNoteId]);
  useEffect(() => {
    navByIdRef.current = navById;
  }, [navById]);
  useEffect(() => {
    isTaskIdRef.current = isTaskId;
  }, [isTaskId]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Skip when the user is typing somewhere — search box, editor,
      // contenteditable. This keeps every shortcut safe to bind globally.
      const target = e.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable) return;
      }
      // Modifier-key chords belong to the OS / browser.
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      const list = navRef.current;
      const focused = focusedRef.current;
      if (e.key === "j" || e.key === "ArrowDown") {
        if (list.length === 0) return;
        e.preventDefault();
        const idx = focused ? list.findIndex((n) => n.id === focused) : -1;
        const next = list[Math.min(idx + 1, list.length - 1)] ?? list[0];
        if (next) setFocusedNoteId(next.id);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        if (list.length === 0) return;
        e.preventDefault();
        const idx = focused ? list.findIndex((n) => n.id === focused) : list.length;
        const next = list[Math.max(idx - 1, 0)] ?? list[0];
        if (next) setFocusedNoteId(next.id);
      } else if (e.key === "Enter") {
        if (!focused) return;
        const n = navByIdRef.current.get(focused);
        if (!n) return;
        e.preventDefault();
        onSelect(n);
      } else if (e.key === "p" || e.key === "P") {
        if (!focused) return;
        if (isTaskIdRef.current.has(focused)) return;
        const n = navByIdRef.current.get(focused);
        if (!n) return;
        e.preventDefault();
        void togglePin(n);
      } else if (e.key === "Delete" || e.key === "Backspace") {
        if (!focused) return;
        if (isTaskIdRef.current.has(focused)) return;
        const n = navByIdRef.current.get(focused);
        if (!n) return;
        e.preventDefault();
        requestDelete(n);
      } else if (e.key === "Escape") {
        if (!focused) return;
        e.preventDefault();
        setFocusedNoteId(null);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When focus moves, scroll the matching row into view (if rendered).
  useEffect(() => {
    if (!focusedNoteId) return;
    const el = document.querySelector<HTMLElement>(
      `[data-lb-row="${cssEscape(focusedNoteId)}"]`,
    );
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focusedNoteId]);

  const sortControl = (
    <div
      className="flex items-center gap-0 rounded bg-bg-primary/40 p-[1px]"
      onClick={(e) => e.stopPropagation()}
    >
      {(["recent", "importance", "alpha"] as const).map((k) => {
        const active = sortKey === k;
        const titles: Record<SortKey, string> = {
          recent: "Sort by most recent",
          importance: "Sort by importance (pinned first)",
          alpha: "Sort alphabetically",
        };
        return (
          <button
            key={k}
            onClick={() => changeSortKey(k)}
            title={titles[k]}
            className={`flex items-center justify-center w-5 h-4 rounded transition-colors ${
              active
                ? "bg-bg-hover text-accent"
                : "text-text-muted/70 hover:text-text-primary"
            }`}
          >
            {k === "recent" ? (
              <Clock size={10} strokeWidth={2} />
            ) : k === "importance" ? (
              <Star size={10} strokeWidth={2} />
            ) : (
              <span
                className="text-[9px] font-semibold tracking-tight leading-none"
                style={{ fontFamily: "'Source Serif 4', 'Source Serif Pro', Georgia, serif" }}
              >
                Aa
              </span>
            )}
          </button>
        );
      })}
    </div>
  );

  const rowProps = (n: LazyBrainNote) => ({
    selected: selectedId === n.id,
    focused: focusedNoteId === n.id,
    isPinned: isPinnedFor(n),
    busy: mutBusy.has(n.id),
    copied: copiedId === n.id,
    pendingDelete: pendingDeleteId === n.id,
    onClick: () => {
      setFocusedNoteId(n.id);
      onSelect(n);
    },
    onPin: () => togglePin(n),
    onCopy: () => copyWikilink(n),
    onDelete: () => requestDelete(n),
  });

  return (
    <aside className="w-60 shrink-0 h-full flex flex-col bg-bg-secondary border-r border-border">
      {/* Header */}
      <div className="px-4 py-2 border-b border-border">
        <div className="flex items-center gap-2">
          <Brain size={16} strokeWidth={1.75} className="text-accent" />
          <div className="flex-1 min-w-0 flex items-baseline gap-1.5">
            <span className="text-sm font-semibold text-text-primary tracking-tight">
              LazyBrain
            </span>
            <span className="text-[11px] text-text-muted flex items-center gap-1">
              <Lock size={9} strokeWidth={2} />
              <span className="tabular-nums">{noteCount}</span>
            </span>
          </div>
          {onCollapse && (
            <button
              onClick={onCollapse}
              title="Hide sidebar"
              className="p-1.5 rounded text-text-secondary hover:text-accent hover:bg-bg-hover transition-colors"
            >
              <PanelLeftClose size={15} strokeWidth={1.9} />
            </button>
          )}
        </div>
        <div className="mt-2 grid grid-cols-4 gap-1">
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
        skillsVaultOpen={skillsVaultOpen}
        onToggleSkillsVault={toggleSkillsVault}
      />

      {/* Quick sections */}
      <div className="flex-1 overflow-y-auto py-1">
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
                  {recent.filter((n) => !deletedIds.has(n.id)).length}
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
            {recent.filter((n) => !deletedIds.has(n.id)).length === 0 ? (
              <div className="px-4 py-6 text-[11px] text-text-muted italic text-center leading-relaxed">
                No results for "{searchQuery}".<br />
                Try a different word or clear search.
              </div>
            ) : (
              recent
                .filter((n) => !deletedIds.has(n.id))
                .map((n) => <PageRow key={n.id} note={n} {...rowProps(n)} />)
            )}
          </div>
        ) : (
          <>
            {/* Today's journal — feature button. The date tile mirrors the
                stamp on each JournalRow so the click target visually
                previews where the user is about to land. */}
            <TodayJournalButton onClick={onOpenJournalToday} />


            {/* Tasks & reminders — auto-pulled from notes tagged `task`.
                Each row gets an inline checkbox that calls the
                mark-task-done endpoint, so the user can complete tasks
                without opening the note. */}
            {visibleTasks.length > 0 && (
              <SidebarSection
                label="Tasks"
                count={visibleTasks.length - completedIds.size}
                Icon={ListTodo}
                iconColor="var(--color-lb-cat-task)"
                open={openSections.tasks}
                onToggle={() => toggleSection("tasks")}
              >
                {visibleTasks.slice(0, 15).map((n) => (
                  <TaskRow
                    key={n.id}
                    note={n}
                    selected={selectedId === n.id}
                    focused={focusedNoteId === n.id}
                    completing={completingIds.has(n.id)}
                    done={completedIds.has(n.id)}
                    onClick={() => {
                      setFocusedNoteId(n.id);
                      onSelect(n);
                    }}
                    onComplete={() => handleCompleteTask(n.id)}
                  />
                ))}
              </SidebarSection>
            )}

            {/* Topics — auto-grouped from `topic/*` tags. Each row drills
                into the existing tag filter, reusing the same pipeline
                clicking a tag chip uses. */}
            {topicGroups.length > 0 && (
              <SidebarSection
                label="Topics"
                count={topicGroups.length}
                Icon={Sparkles}
                iconColor="#a78bfa"
                open={openSections.topics}
                onToggle={() => toggleSection("topics")}
              >
                {topicGroups.map((g) => {
                  const tagFull = `topic/${g.topic}`;
                  const active = activeTag === tagFull;
                  return (
                    <button
                      key={tagFull}
                      onClick={() => onTagToggle(tagFull)}
                      title={`${g.count} note${g.count === 1 ? "" : "s"} tagged ${tagFull}`}
                      className={`w-full flex items-center gap-2 px-4 py-1 text-sm text-left transition-colors ${
                        active
                          ? "bg-accent-soft text-accent"
                          : "text-text-secondary hover:bg-bg-hover hover:text-text-primary"
                      }`}
                    >
                      <Hash size={11} strokeWidth={1.75} className={active ? "text-accent" : "text-text-muted"} />
                      <span className="truncate flex-1">{g.topic}</span>
                      <span className="text-[10px] text-text-muted/70 tabular-nums">
                        {g.count}
                      </span>
                    </button>
                  );
                })}
              </SidebarSection>
            )}

            {/* Pinned */}
            {visiblePinned.length > 0 && (
              <SidebarSection
                label="Pinned"
                count={visiblePinned.length}
                Icon={Star}
                iconColor="#fbbf24"
                open={openSections.pinned}
                onToggle={() => toggleSection("pinned")}
              >
                {visiblePinned.slice(0, 10).map((n) => (
                  <PageRow key={n.id} note={n} {...rowProps(n)} />
                ))}
              </SidebarSection>
            )}

            {/* Recent — with sort segmented control */}
            <SidebarSection
              label="Recent"
              count={sortedRecent.length}
              Icon={Clock}
              open={openSections.recent}
              onToggle={() => toggleSection("recent")}
              headerExtra={openSections.recent ? sortControl : undefined}
            >
              {sortedRecent.slice(0, MAX_RECENT).map((n) => (
                <PageRow key={n.id} note={n} {...rowProps(n)} />
              ))}
              {sortedRecent.length === 0 && (
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

            {/* Journal pages — visually set apart via accent banding,
                grouped by month with each month independently
                collapsible. Entries use a calendar-stamp row layout
                rather than the standard PageRow, so journals always
                read distinctly from regular notes at a glance. */}
            {visibleJournalByMonth.length > 0 && (
              <SidebarSection
                label="Journal"
                count={journalCount}
                Icon={BookOpen}
                accent
                open={openSections.journal}
                onToggle={() => toggleSection("journal")}
              >
                {visibleJournalByMonth.map(({ month, notes }) => {
                  const closed = closedMonths.has(month);
                  return (
                    <div key={month} className="px-1 mb-0.5 last:mb-0">
                      <button
                        onClick={() => toggleMonth(month)}
                        className="w-full flex items-center gap-1.5 px-3 py-0.5 rounded hover:bg-bg-primary/40 transition-colors"
                      >
                        <ChevronRight
                          size={9}
                          strokeWidth={2.2}
                          className={`text-text-muted/60 transition-transform ${closed ? "" : "rotate-90"}`}
                        />
                        <span className="text-[10px] tabular-nums text-text-muted/80 tracking-wide">
                          {monthLabel(month)}
                        </span>
                        <span className="ml-auto text-[9px] tabular-nums text-text-muted/50">
                          {notes.length}
                        </span>
                      </button>
                      {!closed && (
                        <div className="mt-0.5">
                          {notes.map((n) => (
                            <JournalRow key={n.id} note={n} {...rowProps(n)} />
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </SidebarSection>
            )}
          </>
        )}

        {/* Tags — collapsible like other sections; the system-tags toggle
            rides along in the header so users can flip both states from
            the same row without expanding to reach a different control. */}
        {visibleTags.length > 0 && (
          <SidebarSection
            label="Tags"
            count={visibleTags.length}
            Icon={Hash}
            open={openSections.tags}
            onToggle={() => toggleSection("tags")}
            headerExtra={
              <button
                onClick={() => setShowSystemTags((v) => !v)}
                className={`p-0.5 rounded transition-colors ${
                  showSystemTags
                    ? "text-accent"
                    : "text-text-muted hover:text-text-primary"
                }`}
                title={showSystemTags ? "Hide system tags" : "Show system tags"}
              >
                <Settings2 size={11} strokeWidth={1.75} />
              </button>
            }
          >
            <div className="px-3 pb-2 pt-1 flex flex-wrap gap-1">
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
          </SidebarSection>
        )}
      </div>

      {/* Undo toast stack — sticky to the sidebar bottom. Each entry has
          a 5s undo window before the API delete fires; clicking Undo
          cancels that timer and restores the row from `deletedIds`. */}
      {trash.length > 0 && (
        <div
          className="shrink-0 border-t border-border bg-bg-secondary/95 backdrop-blur-sm px-2 py-2 space-y-1"
          aria-live="polite"
        >
          {trash.map((t) => (
            <UndoToast
              key={t.noteId}
              title={t.title}
              onUndo={() => undoTrash(t.noteId)}
            />
          ))}
        </div>
      )}
    </aside>
  );
}

function UndoToast({
  title,
  onUndo,
}: {
  title: string;
  onUndo: () => void;
}) {
  return (
    <div className="group flex items-center gap-2 px-2.5 py-1.5 rounded bg-bg-primary/80 border border-border text-[11px] text-text-secondary shadow-[0_4px_18px_-12px_rgba(0,0,0,0.5)]">
      <Trash2 size={11} strokeWidth={1.9} className="text-rose-400/80 shrink-0" />
      <span className="flex-1 truncate leading-tight">
        Deleted{" "}
        <span className="text-text-muted italic">"{title}"</span>
      </span>
      <button
        onClick={onUndo}
        className="px-1.5 py-0.5 rounded text-accent hover:bg-accent/10 transition-colors uppercase tracking-wider text-[10px] font-medium"
      >
        Undo
      </button>
    </div>
  );
}

/** Escape an arbitrary string for safe use inside a CSS attribute selector.
 *  Note ids are UUIDs so this is mostly defensive — we still go through the
 *  built-in CSS.escape() when available. */
function cssEscape(s: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(s);
  }
  return s.replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}

function TodayJournalButton({ onClick }: { onClick: () => void }) {
  // Slim single-row variant — keeps the date affordance from the original
  // editorial design but stops dominating sidebar vertical space. The day
  // pill on the right is small enough to disappear into the row rhythm
  // when the user isn't paying attention to it.
  const now = new Date();
  const day = String(now.getDate()).padStart(2, "0");
  const month = MONTH_NAMES[now.getMonth()] ?? "";
  return (
    <button
      onClick={onClick}
      className="group w-full flex items-center gap-2 px-4 py-1.5 text-left transition-colors hover:bg-bg-hover"
      title="Open today's journal"
    >
      <Calendar size={13} strokeWidth={1.75} className="text-accent shrink-0" />
      <span className="flex-1 truncate text-sm text-text-secondary group-hover:text-text-primary">
        Today's journal
      </span>
      <span
        className="shrink-0 px-1.5 py-[1px] rounded text-[10px] tabular-nums font-medium tracking-tight"
        style={{
          color: "var(--color-accent)",
          background: "color-mix(in srgb, var(--color-accent) 12%, transparent)",
          fontFamily: "'Source Serif 4', 'Source Serif Pro', Georgia, serif",
        }}
      >
        {day} {month}
      </span>
    </button>
  );
}

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
  open,
  onToggle,
  headerExtra,
  accent,
  children,
}: {
  label: string;
  count?: number;
  Icon: LucideIcon;
  iconColor?: string;
  /** When provided, the section header becomes a clickable disclosure.
   *  When omitted, the section is always shown. */
  open?: boolean;
  onToggle?: () => void;
  /** Optional element shown on the right side of the header (e.g. a sort
   *  segmented control). Click events inside are auto-stopPropagation'd. */
  headerExtra?: React.ReactNode;
  /** Wraps the section in a tinted band — used for the Journal block to
   *  visually set it apart from regular note lists above. */
  accent?: boolean;
  children: React.ReactNode;
}) {
  const collapsible = onToggle !== undefined;
  const expanded = open ?? true;
  return (
    <div
      className={`mt-2 ${
        accent
          ? "py-1 bg-bg-hover/30 border-y border-border/60"
          : ""
      }`}
    >
      <div
        className={`px-4 py-1 flex items-center gap-2 ${
          collapsible ? "cursor-pointer select-none group/section" : ""
        }`}
        onClick={collapsible ? onToggle : undefined}
      >
        {collapsible && (
          <ChevronRight
            size={9}
            strokeWidth={2.2}
            className={`text-text-muted/60 transition-transform ${
              expanded ? "rotate-90" : ""
            }`}
          />
        )}
        <Icon
          size={11}
          strokeWidth={1.75}
          color={iconColor}
          className={iconColor ? undefined : "text-text-muted"}
        />
        <span
          className={`text-[10px] uppercase tracking-wider text-text-muted ${
            collapsible ? "group-hover/section:text-text-secondary" : ""
          }`}
        >
          {label}
        </span>
        <span className="ml-auto flex items-center gap-2 shrink-0">
          {count !== undefined && (
            <span className="text-[10px] text-text-muted/60 tabular-nums">
              {count}
            </span>
          )}
          {headerExtra && (
            <span onClick={(e) => e.stopPropagation()}>{headerExtra}</span>
          )}
        </span>
      </div>
      {expanded && children}
    </div>
  );
}

function PageRow({
  note,
  selected,
  focused,
  isPinned,
  busy,
  copied,
  pendingDelete,
  onClick,
  onPin,
  onCopy,
  onDelete,
}: {
  note: LazyBrainNote;
  selected: boolean;
  focused: boolean;
  isPinned: boolean;
  busy: boolean;
  copied: boolean;
  pendingDelete: boolean;
  onClick: () => void;
  onPin: () => void;
  onCopy: () => void;
  onDelete: () => void;
}) {
  const title = note.title || "(untitled)";
  const color = colorForTags(note.tags || [], isPinned);
  const categoryKey = pickCategoryKey(note.tags, isPinned);
  const time = relTime(note.updated_at || note.created_at);
  const userTag = (note.tags || []).find((t) => !isSystemTag(t));
  const showSubLine = !!time || !!userTag;

  return (
    <div
      data-lb-row={note.id}
      onClick={onClick}
      className={`group relative flex flex-col pl-4 pr-1 py-1 cursor-pointer transition-colors border-l-2 ${
        selected
          ? "bg-accent-soft border-l-[color:var(--color-accent)]"
          : focused
          ? "bg-bg-hover/70 border-l-[color:color-mix(in_srgb,var(--color-accent)_50%,transparent)]"
          : "border-l-transparent hover:bg-bg-hover"
      } ${busy ? "opacity-60 pointer-events-none" : ""}`}
      title={`${color.label} — ${title}`}
    >
      <div className="flex items-center gap-2">
        <CategoryIcon keyName={categoryKey} size={12} color={color.ring} />
        <span
          className={`flex-1 truncate text-sm ${
            selected
              ? "text-accent"
              : "text-text-secondary group-hover:text-text-primary"
          }`}
        >
          {title}
        </span>
        {isPinned && (
          <Pin
            size={9}
            strokeWidth={2}
            className={`shrink-0 transition-opacity group-hover:opacity-0 ${
              selected ? "text-accent/70" : "text-text-muted/60"
            }`}
          />
        )}
      </div>
      {showSubLine && (
        <div className="flex items-center gap-1.5 pl-[20px] text-[10px] text-text-muted/70 leading-tight">
          {time && <span className="tabular-nums">{time}</span>}
          {userTag && (
            <>
              {time && <span className="opacity-40">·</span>}
              <span className="truncate max-w-[7.5rem]" title={`#${userTag}`}>
                <span className="opacity-50">#</span>
                {userTag}
              </span>
            </>
          )}
        </div>
      )}
      <RowActions
        isPinned={isPinned}
        copied={copied}
        pendingDelete={pendingDelete}
        onPin={onPin}
        onCopy={onCopy}
        onDelete={onDelete}
      />
    </div>
  );
}

function JournalRow({
  note,
  selected,
  focused,
  isPinned,
  busy,
  copied,
  pendingDelete,
  onClick,
  onPin,
  onCopy,
  onDelete,
}: {
  note: LazyBrainNote;
  selected: boolean;
  focused: boolean;
  isPinned: boolean;
  busy: boolean;
  copied: boolean;
  pendingDelete: boolean;
  onClick: () => void;
  onPin: () => void;
  onCopy: () => void;
  onDelete: () => void;
}) {
  const title = (note.title || "(untitled)").trim();
  const d = note.created_at ? new Date(note.created_at) : null;
  const valid = d && !Number.isNaN(d.getTime());
  const day = valid ? String(d!.getDate()).padStart(2, "0") : "—";
  const dow = valid
    ? d!.toLocaleDateString(undefined, { weekday: "short" }).slice(0, 3).toUpperCase()
    : "";
  const stampBorder = selected
    ? "color-mix(in srgb, var(--color-accent) 50%, transparent)"
    : "var(--color-border)";
  const stampBg = selected
    ? "color-mix(in srgb, var(--color-accent) 14%, transparent)"
    : "color-mix(in srgb, var(--color-bg-primary) 60%, transparent)";

  return (
    <div
      data-lb-row={note.id}
      onClick={onClick}
      className={`group relative flex items-stretch gap-2 pl-3 pr-1 py-0.5 cursor-pointer transition-colors border-l-2 ${
        selected
          ? "bg-accent-soft border-l-[color:var(--color-accent)]"
          : focused
          ? "bg-bg-hover/70 border-l-[color:color-mix(in_srgb,var(--color-accent)_50%,transparent)]"
          : "border-l-transparent hover:bg-bg-hover"
      } ${busy ? "opacity-60 pointer-events-none" : ""}`}
      title={title}
    >
      <div
        className="flex flex-col items-center justify-center w-9 shrink-0 leading-none rounded border py-1"
        style={{ borderColor: stampBorder, background: stampBg }}
      >
        <span
          className="text-[14px] tabular-nums font-medium tracking-tight"
          style={{
            fontFamily: "'Source Serif 4', 'Source Serif Pro', Georgia, serif",
            color: selected ? "var(--color-accent)" : "var(--color-text-primary)",
          }}
        >
          {day}
        </span>
        <span className="text-[8px] uppercase tracking-[0.18em] text-text-muted/70 mt-0.5">
          {dow}
        </span>
      </div>
      <div className="flex-1 min-w-0 flex items-center">
        <span
          className={`truncate text-[13px] ${
            selected
              ? "text-accent"
              : "text-text-secondary group-hover:text-text-primary"
          }`}
        >
          {title}
        </span>
        {isPinned && (
          <Pin
            size={9}
            strokeWidth={2}
            className={`ml-1.5 shrink-0 transition-opacity group-hover:opacity-0 ${
              selected ? "text-accent/70" : "text-text-muted/60"
            }`}
          />
        )}
      </div>
      <RowActions
        isPinned={isPinned}
        copied={copied}
        pendingDelete={pendingDelete}
        onPin={onPin}
        onCopy={onCopy}
        onDelete={onDelete}
      />
    </div>
  );
}

function RowActions({
  isPinned,
  copied,
  pendingDelete,
  onPin,
  onCopy,
  onDelete,
}: {
  isPinned: boolean;
  copied: boolean;
  pendingDelete: boolean;
  onPin: () => void;
  onCopy: () => void;
  onDelete: () => void;
}) {
  // Toolbar fades in on row hover. While a row is armed for delete we
  // pin it to fully visible so the "tap-again-to-confirm" state is
  // unmistakable. We deliberately do NOT keep the toolbar visible on
  // selected rows — it would crowd the title in this 240px-wide sidebar.
  const visibility = pendingDelete
    ? "opacity-100"
    : "opacity-0 group-hover:opacity-100";
  return (
    <div
      className={`absolute top-0.5 right-1 flex items-center gap-0 transition-opacity ${visibility}`}
    >
      {!pendingDelete && (
        <>
          <ActionBtn
            title={isPinned ? "Unpin" : "Pin"}
            onClick={onPin}
            Icon={isPinned ? PinOff : Pin}
            accent={isPinned}
          />
          <ActionBtn
            title={copied ? "Copied!" : "Copy [[link]]"}
            onClick={onCopy}
            Icon={copied ? Check : Link2}
            accent={copied}
          />
        </>
      )}
      {pendingDelete ? (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          title="Click again to confirm — auto-cancels in 3s"
          className="flex items-center gap-1 px-1.5 py-[3px] rounded text-[10px] uppercase tracking-wider font-medium text-rose-300 bg-rose-500/15 hover:bg-rose-500/25 hover:text-rose-200 transition-colors"
        >
          <Trash2 size={10} strokeWidth={2} />
          <span>Delete?</span>
        </button>
      ) : (
        <ActionBtn
          title="Delete"
          onClick={onDelete}
          Icon={Trash2}
          danger
        />
      )}
    </div>
  );
}

function ActionBtn({
  title,
  onClick,
  Icon,
  accent,
  danger,
}: {
  title: string;
  onClick: () => void;
  Icon: LucideIcon;
  accent?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={title}
      className={`p-1 rounded transition-colors ${
        danger
          ? "text-text-muted/70 hover:text-rose-400 hover:bg-rose-500/10"
          : accent
          ? "text-accent bg-accent/10"
          : "text-text-muted/70 hover:text-text-primary hover:bg-bg-primary/40"
      }`}
    >
      <Icon size={11} strokeWidth={1.9} />
    </button>
  );
}

/** Derive the single category key that matches a note (for icon selection).
 *  Thin wrapper around noteColors.categoryKeysFor so the sidebar, GraphView,
 *  and filter legend all agree on which category wins. */
function pickCategoryKey(tags: string[] | null | undefined, pinned: boolean): string {
  return categoryKeysFor(tags, pinned)[0] ?? "_default";
}

/** Compact relative-time badge ("2d", "3h", "5m") for the sub-line under
 *  each note title. Returns null when the input doesn't parse. */
function relTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const diff = (Date.now() - t) / 1000;
  if (diff < 45) return "now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d`;
  if (diff < 86400 * 30) return `${Math.floor(diff / (86400 * 7))}w`;
  if (diff < 86400 * 365) return `${Math.floor(diff / (86400 * 30))}mo`;
  return `${Math.floor(diff / (86400 * 365))}y`;
}

const MONTH_NAMES = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** Format an ISO `YYYY-MM` string as a compact "Apr 2026" header. */
function monthLabel(yyyymm: string): string {
  const [y, m] = yyyymm.split("-");
  const idx = parseInt(m, 10) - 1;
  if (idx >= 0 && idx < 12) return `${MONTH_NAMES[idx]} ${y}`;
  return yyyymm;
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
  // Short date (D/M — European format, matches Madrid locale) for future.
  const [, m, d] = due.split("-");
  return { text: `${parseInt(d, 10)}/${parseInt(m, 10)}`, tone: "future" };
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
  focused,
  completing,
  done,
  onClick,
  onComplete,
}: {
  note: LazyBrainNote;
  selected: boolean;
  focused: boolean;
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
      data-lb-row={note.id}
      onClick={onClick}
      className={`group w-full flex items-start gap-2.5 pl-3 pr-3 py-1.5 cursor-pointer transition-colors border-l-2 ${
        selected
          ? "bg-accent-soft border-l-[color:var(--color-accent)]"
          : focused
          ? "bg-bg-hover/70 border-l-transparent"
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
