/** Shared command modal — palette (⌘K) + quick switcher (⌘O).
 *
 *  Palette mode: actions, notes, tags — fuzzy-matched.
 *  Switcher mode: notes only + "Create new: <query>" on Enter.
 *
 *  Zero deps. Hand-rolled fuzzy score (subsequence match + bonuses).
 *  Designed to feel like Obsidian's quick switcher: open fast, no chrome. */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { LazyBrainNote, LazyBrainTag } from "../../api";
import {
  BookOpen,
  Brain,
  FileText,
  Hash,
  Network,
  Plus,
  Save,
  Search,
  Star,
  PinOff,
  Pin,
  Sparkles,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

export type CommandModalMode = "palette" | "switcher";

export interface CommandAction {
  id: string;
  label: string;
  hint?: string;
  Icon?: LucideIcon;
  run: () => void | Promise<void>;
  keywords?: string; // extra tokens for fuzzy match
}

interface Props {
  open: boolean;
  mode: CommandModalMode;
  onClose: () => void;
  notes: LazyBrainNote[];
  tags: LazyBrainTag[];
  actions: CommandAction[];
  onSelectNote: (note: LazyBrainNote) => void;
  onSelectTag: (tag: string) => void;
  onCreateNote: (title: string) => void;
}

type Row =
  | { kind: "section"; id: string; label: string }
  | { kind: "action"; id: string; action: CommandAction; score: number }
  | { kind: "note"; id: string; note: LazyBrainNote; score: number }
  | { kind: "tag"; id: string; tag: LazyBrainTag; score: number }
  | { kind: "create"; id: string; query: string };

/** Cheap subsequence-fuzzy score.
 *  Returns > 0 when every query char appears in order within the target.
 *  Bonuses: exact match, prefix, word-start, shorter targets. */
function fuzzyScore(query: string, target: string): number {
  if (!query) return 1;
  const q = query.toLowerCase();
  const t = target.toLowerCase();
  if (t === q) return 1000;
  if (t.startsWith(q)) return 800 - (t.length - q.length);

  let qi = 0;
  let score = 0;
  let prevMatch = -2;
  let bonus = 0;
  for (let i = 0; i < t.length && qi < q.length; i++) {
    if (t[i] === q[qi]) {
      // word boundary bonus
      if (i === 0 || /[\s/_-]/.test(t[i - 1])) bonus += 30;
      // consecutive bonus
      if (i === prevMatch + 1) bonus += 15;
      score += 10 + bonus;
      prevMatch = i;
      qi++;
      bonus = Math.max(0, bonus - 5);
    } else {
      bonus = 0;
    }
  }
  if (qi < q.length) return 0;
  // shorter targets win ties
  return score - Math.floor(t.length / 8);
}

export function CommandModal({
  open,
  mode,
  onClose,
  notes,
  tags,
  actions,
  onSelectNote,
  onSelectTag,
  onCreateNote,
}: Props) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  // Mount-time focus — parent re-mounts via `key` on mode/open change, so
  // we don't need an effect that resets state on open/mode transitions.
  useLayoutEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  const rows = useMemo<Row[]>(() => {
    const q = query.trim();
    const out: Row[] = [];

    // Palette mode — actions + notes + tags
    if (mode === "palette") {
      const scoredActions = actions
        .map((a) => ({
          a,
          s: Math.max(
            fuzzyScore(q, a.label),
            a.keywords ? fuzzyScore(q, a.keywords) * 0.7 : 0,
          ),
        }))
        .filter((x) => x.s > 0)
        .sort((a, b) => b.s - a.s)
        .slice(0, 10);
      if (scoredActions.length > 0) {
        out.push({ kind: "section", id: "s-actions", label: "Actions" });
        for (const { a, s } of scoredActions) {
          out.push({ kind: "action", id: `a-${a.id}`, action: a, score: s });
        }
      }

      const scoredNotes = notes
        .map((n) => ({ n, s: fuzzyScore(q, n.title || "(untitled)") }))
        .filter((x) => x.s > 0)
        .sort((a, b) => b.s - a.s)
        .slice(0, 20);
      if (scoredNotes.length > 0) {
        out.push({ kind: "section", id: "s-notes", label: "Notes" });
        for (const { n, s } of scoredNotes) {
          out.push({ kind: "note", id: `n-${n.id}`, note: n, score: s });
        }
      }

      const scoredTags = tags
        .map((t) => ({ t, s: fuzzyScore(q, t.tag) }))
        .filter((x) => x.s > 0)
        .sort((a, b) => b.s - a.s)
        .slice(0, 10);
      if (scoredTags.length > 0) {
        out.push({ kind: "section", id: "s-tags", label: "Tags" });
        for (const { t, s } of scoredTags) {
          out.push({ kind: "tag", id: `t-${t.tag}`, tag: t, score: s });
        }
      }
      return out;
    }

    // Switcher mode — notes only + "Create new"
    const scoredNotes = notes
      .map((n) => ({ n, s: fuzzyScore(q, n.title || "(untitled)") }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s)
      .slice(0, 40);
    const hasExact = scoredNotes.some(
      ({ n }) => (n.title || "").trim().toLowerCase() === q.toLowerCase(),
    );
    for (const { n, s } of scoredNotes) {
      out.push({ kind: "note", id: `n-${n.id}`, note: n, score: s });
    }
    if (q && !hasExact) {
      out.push({ kind: "create", id: "create-new", query: q });
    }
    return out;
  }, [query, mode, actions, notes, tags]);

  // Track which rows are selectable (skip section headers for cursor).
  const selectableIndex = useMemo(() => {
    const map: number[] = [];
    rows.forEach((r, i) => {
      if (r.kind !== "section") map.push(i);
    });
    return map;
  }, [rows]);

  // Clamp cursor into the visible row range without triggering an extra
  // render — derive during read instead of storing an out-of-range value.
  const clampedCursor =
    selectableIndex.length === 0
      ? 0
      : Math.min(Math.max(0, cursor), selectableIndex.length - 1);

  const commit = useCallback(
    (row: Row) => {
      if (row.kind === "action") {
        onClose();
        void row.action.run();
      } else if (row.kind === "note") {
        onClose();
        onSelectNote(row.note);
      } else if (row.kind === "tag") {
        onClose();
        onSelectTag(row.tag.tag);
      } else if (row.kind === "create") {
        onClose();
        onCreateNote(row.query);
      }
    },
    [onClose, onSelectNote, onSelectTag, onCreateNote],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (selectableIndex.length === 0) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setCursor((c) => {
          const base = Math.min(Math.max(0, c), selectableIndex.length - 1);
          return (base + 1) % selectableIndex.length;
        });
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setCursor((c) => {
          const base = Math.min(Math.max(0, c), selectableIndex.length - 1);
          return (base - 1 + selectableIndex.length) % selectableIndex.length;
        });
      } else if (e.key === "Enter") {
        e.preventDefault();
        const rowIdx = selectableIndex[clampedCursor];
        if (rowIdx != null) commit(rows[rowIdx]);
      }
    },
    [selectableIndex, clampedCursor, rows, commit, onClose],
  );

  // Scroll cursor into view.
  useEffect(() => {
    const idx = selectableIndex[clampedCursor];
    if (idx == null || !listRef.current) return;
    const el = listRef.current.querySelector<HTMLElement>(
      `[data-row-idx="${idx}"]`,
    );
    el?.scrollIntoView({ block: "nearest" });
  }, [clampedCursor, selectableIndex]);

  return (
    <AnimatePresence>
    {open && (
    <motion.div
      className="lb-cmdk-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.14, ease: "easeOut" }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <motion.div
        className="lb-cmdk-panel"
        initial={{ opacity: 0, scale: 0.96, y: -6 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.97, y: -4 }}
        transition={{ type: "spring", stiffness: 420, damping: 32, mass: 0.7 }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-5 pt-0.5">
          <Search size={15} strokeWidth={1.75} color="#10b981" />
          <input
            ref={inputRef}
            className="lb-cmdk-input"
            style={{ padding: "16px 0" }}
            placeholder={
              mode === "palette"
                ? "Type a command, note title, or #tag…"
                : "Jump to page — or type a new title…"
            }
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <span className="lb-cmdk-kbd">Esc</span>
        </div>

        <div ref={listRef} className="lb-cmdk-list">
          {rows.length === 0 && (
            <div className="lb-cmdk-empty">
              {mode === "palette"
                ? "No matches — try fewer characters."
                : query
                  ? "Press Enter to create a new page."
                  : "Start typing to search."}
            </div>
          )}
          {rows.map((row, i) => {
            if (row.kind === "section") {
              return (
                <div key={row.id} className="lb-cmdk-section">
                  {row.label}
                </div>
              );
            }
            const activeIdx = selectableIndex[clampedCursor];
            const active = activeIdx === i;
            return (
              <div
                key={row.id}
                data-row-idx={i}
                data-active={active ? "true" : "false"}
                className="lb-cmdk-row"
                onMouseEnter={() => {
                  const sIdx = selectableIndex.indexOf(i);
                  if (sIdx >= 0) setCursor(sIdx);
                }}
                onMouseDown={(e) => {
                  e.preventDefault();
                  commit(row);
                }}
              >
                <RowIcon row={row} />
                <RowLabel row={row} />
                <RowHint row={row} />
              </div>
            );
          })}
        </div>
        <div
          className="px-4 py-2 border-t text-[11px] text-text-muted flex items-center gap-3"
          style={{ borderColor: "rgba(255,255,255,0.05)" }}
        >
          <span>
            <span className="lb-cmdk-kbd">↑↓</span> navigate
          </span>
          <span>
            <span className="lb-cmdk-kbd">↵</span> open
          </span>
          {mode === "switcher" && (
            <span>
              <span className="lb-cmdk-kbd">↵</span> on empty → create
            </span>
          )}
          <span className="ml-auto text-text-muted/70">
            {mode === "palette" ? "Command Palette" : "Quick Switcher"}
          </span>
        </div>
      </motion.div>
    </motion.div>
    )}
    </AnimatePresence>
  );
}

function RowIcon({ row }: { row: Row }) {
  if (row.kind === "action") {
    const I = row.action.Icon ?? Sparkles;
    return <I size={14} strokeWidth={1.75} color="#10b981" />;
  }
  if (row.kind === "note") {
    const isJournal = (row.note.tags || []).some(
      (t) => t.toLowerCase() === "journal" || t.toLowerCase().startsWith("journal/"),
    );
    if (row.note.pinned)
      return <Star size={14} strokeWidth={1.75} color="#fbbf24" fill="#fbbf24" />;
    if (isJournal) return <BookOpen size={14} strokeWidth={1.75} color="#60a5fa" />;
    return <FileText size={14} strokeWidth={1.75} color="#9ca3af" />;
  }
  if (row.kind === "tag") {
    return <Hash size={14} strokeWidth={1.75} color="#10b981" />;
  }
  if (row.kind === "create") {
    return <Plus size={14} strokeWidth={2} color="#34d399" />;
  }
  return <Brain size={14} strokeWidth={1.75} color="#10b981" />;
}

function RowLabel({ row }: { row: Row }) {
  if (row.kind === "action") return <span>{row.action.label}</span>;
  if (row.kind === "note")
    return (
      <span className="truncate max-w-[460px]">
        {row.note.title || "(untitled)"}
      </span>
    );
  if (row.kind === "tag")
    return (
      <span>
        #{row.tag.tag}{" "}
        <span style={{ color: "#6b6878", fontSize: 11 }}>
          ({row.tag.count})
        </span>
      </span>
    );
  if (row.kind === "create")
    return (
      <span>
        <span style={{ color: "#34d399" }}>Create page:</span>{" "}
        <span className="font-semibold">{row.query}</span>
      </span>
    );
  return null;
}

function RowHint({ row }: { row: Row }) {
  if (row.kind === "action" && row.action.hint)
    return <span className="lb-cmdk-hint">{row.action.hint}</span>;
  if (row.kind === "note") {
    const n = row.note;
    const ts = new Date(n.updated_at + "Z").toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
    return <span className="lb-cmdk-hint tabular-nums">{ts}</span>;
  }
  if (row.kind === "create")
    return <span className="lb-cmdk-hint">↵ to create</span>;
  return null;
}

// Re-export icons that actions commonly use so callers don't re-import.
export { Plus, Save, BookOpen, Brain, Network, Pin, PinOff, Star };
