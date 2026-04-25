import { useEffect, useMemo, useState } from "react";
import { listLazyBrainNotes, type LazyBrainNote } from "../api";
import { NoteCard } from "../components/notes/NoteCard";
import { NoteDetail } from "../components/notes/NoteDetail";
import { NotesFilterRail } from "../components/notes/NotesFilterRail";
import { NotesQuickAdd } from "../components/notes/NotesQuickAdd";
import {
  KIND_META,
  NOTE_KIND_TAGS,
  RECENCY_LABELS,
  noteKind,
  noteSource,
  recencyBucket,
  type NoteKind,
  type RecencyBucket,
} from "../components/notes/noteHelpers";

/**
 * Notes page — three-pane reading room.
 *
 *   ≥lg │ rail (180px) │ list │ detail (440px) │
 *   md  │ rail │ list │ (overlay)              │
 *   <md │ list-only (rail collapses)           │
 *
 * The page is *silent on AI* — no autolink, no semantic search, no rollups.
 * Those live on the LazyBrain page. Here you capture, scan, and read.
 *
 * Body type uses Source Serif 4 in preview mode so opening a note feels
 * like opening a page in a notebook. Edit mode flips to JetBrains Mono so
 * markdown structure reads cleanly while typing.
 */

const PAGE_FETCH_LIMIT = 200;
const DETAIL_OPEN_KEY = "lazyclaw.notes.detailOpen";
const KIND_FILTER_KEY = "lazyclaw.notes.kindFilter";

type KindFilter = NoteKind | "all";

function readDetailOpen(): boolean {
  try {
    const v = localStorage.getItem(DETAIL_OPEN_KEY);
    return v === null ? true : v === "1";
  } catch {
    return true;
  }
}
function writeDetailOpen(open: boolean) {
  try {
    localStorage.setItem(DETAIL_OPEN_KEY, open ? "1" : "0");
  } catch {
    /* ignore */
  }
}
function readKindFilter(): KindFilter {
  try {
    const v = localStorage.getItem(KIND_FILTER_KEY);
    if (v === "all" || v === "note" || v === "idea" || v === "memory") return v;
  } catch {
    /* ignore */
  }
  return "all";
}
function writeKindFilter(k: KindFilter) {
  try {
    localStorage.setItem(KIND_FILTER_KEY, k);
  } catch {
    /* ignore */
  }
}

const RECENCY_ORDER: RecencyBucket[] = [
  "today",
  "yesterday",
  "this_week",
  "this_month",
  "earlier",
];

export default function NotesPage() {
  const [notes, setNotes] = useState<LazyBrainNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState<KindFilter>(readKindFilter());
  const [sourceFilter, setSourceFilter] = useState<string | null>(null);
  const [pinnedOnly, setPinnedOnly] = useState(false);
  const [detailOpen, setDetailOpen] = useState<boolean>(readDetailOpen());

  const refresh = async () => {
    setLoading(true);
    try {
      // Pull a generous page; client-side filter handles kind/source.
      const all = await listLazyBrainNotes({ limit: PAGE_FETCH_LIMIT });
      setNotes(all);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    const id = window.setInterval(refresh, 30_000);
    return () => window.clearInterval(id);
  }, []);

  // Only "personal note" kinds are eligible. This is the single source of
  // truth for what shows up here — task/journal mirrors are filtered out.
  const personalNotes = useMemo(
    () =>
      notes.filter((n) =>
        (n.tags || []).some((t) => NOTE_KIND_TAGS.includes(t as typeof NOTE_KIND_TAGS[number])),
      ),
    [notes],
  );

  const counts = useMemo(() => {
    const c = {
      all: personalNotes.length,
      note: 0,
      idea: 0,
      memory: 0,
      pinned: 0,
      bySource: {} as Record<string, number>,
    };
    for (const n of personalNotes) {
      c[noteKind(n)]++;
      if (n.pinned) c.pinned++;
      const src = noteSource(n);
      if (src) c.bySource[src] = (c.bySource[src] || 0) + 1;
    }
    return c;
  }, [personalNotes]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return personalNotes.filter((n) => {
      if (kindFilter !== "all" && noteKind(n) !== kindFilter) return false;
      if (sourceFilter && noteSource(n) !== sourceFilter) return false;
      if (pinnedOnly && !n.pinned) return false;
      if (q) {
        const blob = `${n.title || ""} ${n.content || ""} ${(n.tags || []).join(" ")}`.toLowerCase();
        if (!blob.includes(q)) return false;
      }
      return true;
    });
  }, [personalNotes, kindFilter, sourceFilter, pinnedOnly, search]);

  // Pinned first, then sorted by recency. Group by recency bucket for the
  // sticky list headers.
  const grouped = useMemo(() => {
    const sorted = [...filtered].sort((a, b) => {
      if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
      const at = new Date(a.updated_at || a.created_at).getTime();
      const bt = new Date(b.updated_at || b.created_at).getTime();
      return bt - at;
    });
    const groups: Record<RecencyBucket, LazyBrainNote[]> = {
      today: [], yesterday: [], this_week: [], this_month: [], earlier: [],
    };
    const now = new Date();
    const pinned: LazyBrainNote[] = [];
    for (const n of sorted) {
      if (n.pinned) {
        pinned.push(n);
      } else {
        groups[recencyBucket(n.updated_at || n.created_at, now)].push(n);
      }
    }
    return { pinned, groups };
  }, [filtered]);

  // When the selected note disappears from the filtered list, clear it.
  useEffect(() => {
    if (selectedId && !filtered.find((n) => n.id === selectedId)) {
      setSelectedId(null);
    }
  }, [filtered, selectedId]);

  const onPickKind = (k: KindFilter) => {
    setKindFilter(k);
    writeKindFilter(k);
  };
  const onToggleDetail = () => {
    const next = !detailOpen;
    setDetailOpen(next);
    writeDetailOpen(next);
  };

  return (
    <div className="grid-bg min-h-full">
      <div className="max-w-[1400px] mx-auto px-4 lg:px-6 py-5 space-y-4">
        {/* Header */}
        <header className="flex items-baseline gap-3 flex-wrap">
          <h1 className="text-lg font-semibold text-text-primary tracking-tight">
            Notes
          </h1>
          <span className="text-[11px] text-text-muted">
            Encrypted · markdown · [[wikilinks]] · no AI
          </span>
          <div className="ml-auto flex items-center gap-2">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search title, body, tags…"
              className="w-44 sm:w-64 bg-bg-secondary/60 border border-border/60 rounded-md px-2.5 py-1.5 text-[12px] text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/50"
            />
          </div>
        </header>

        {/* Capture bar */}
        <NotesQuickAdd onCreated={refresh} />

        {/* Three-pane body */}
        <div className="grid gap-4" style={{ gridTemplateColumns: "auto 1fr auto" }}>
          <NotesFilterRail
            kindFilter={kindFilter}
            onKindFilter={onPickKind}
            sourceFilter={sourceFilter}
            onSourceFilter={setSourceFilter}
            pinnedOnly={pinnedOnly}
            onPinnedOnly={setPinnedOnly}
            counts={counts}
          />

          {/* List pane */}
          <section className="min-w-0 relative">
            {/* Detail-pane toggle (large screens only) */}
            <button
              onClick={onToggleDetail}
              className="hidden lg:flex absolute -top-1 right-0 items-center gap-1 px-2 py-1 rounded-md text-[10px] uppercase tracking-wider text-text-muted hover:bg-bg-hover/40 hover:text-text-primary transition-colors z-10"
              title={detailOpen ? "Hide reading pane" : "Show reading pane"}
            >
              {detailOpen ? "Hide pane →" : "← Show pane"}
            </button>

            {loading && filtered.length === 0 ? (
              <div className="text-center text-text-muted py-12 text-[13px]">
                Loading…
              </div>
            ) : filtered.length === 0 ? (
              <EmptyState
                allCount={counts.all}
                kind={kindFilter}
                hasSearch={!!search.trim()}
              />
            ) : (
              <div className="space-y-5 pr-1 max-h-[calc(100vh-280px)] overflow-y-auto pb-4">
                {grouped.pinned.length > 0 && (
                  <NoteGroup
                    label="Pinned"
                    accent="#f59e0b"
                    notes={grouped.pinned}
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                  />
                )}
                {RECENCY_ORDER.map((bucket) =>
                  grouped.groups[bucket].length > 0 ? (
                    <NoteGroup
                      key={bucket}
                      label={RECENCY_LABELS[bucket]}
                      accent="var(--color-text-muted)"
                      notes={grouped.groups[bucket]}
                      selectedId={selectedId}
                      onSelect={setSelectedId}
                    />
                  ) : null,
                )}
              </div>
            )}
          </section>

          {/* Detail pane (lg only as a fixed column) */}
          {detailOpen ? (
            <aside className="hidden lg:block w-[440px] flex-none">
              <div className="sticky top-4 rounded-2xl border border-border/60 bg-bg-secondary/30 overflow-hidden h-[calc(100vh-180px)]">
                {selectedId ? (
                  <NoteDetail
                    key={selectedId}
                    noteId={selectedId}
                    onChange={refresh}
                    onClose={() => setSelectedId(null)}
                  />
                ) : (
                  <div className="h-full flex items-center justify-center px-6 text-center">
                    <p
                      className="text-text-muted text-[13px] leading-relaxed"
                      style={{ fontFamily: "'Source Serif 4', Georgia, serif" }}
                    >
                      Pick a note on the left to read it here.
                      <br />
                      Or capture a new one above.
                    </p>
                  </div>
                )}
              </div>
            </aside>
          ) : null}
        </div>

        {/* Mobile / md detail overlay */}
        {selectedId && !detailOpen ? (
          <div
            className="fixed inset-0 z-40 bg-bg-primary/80 backdrop-blur-sm flex items-end lg:items-center justify-end lg:justify-center p-0 lg:p-6"
            onClick={() => setSelectedId(null)}
          >
            <div
              className="w-full lg:w-[640px] h-[85vh] lg:h-[80vh] bg-bg-secondary border border-border rounded-t-2xl lg:rounded-2xl overflow-hidden"
              onClick={(e) => e.stopPropagation()}
            >
              <NoteDetail
                key={selectedId}
                noteId={selectedId}
                onChange={refresh}
                onClose={() => setSelectedId(null)}
              />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function NoteGroup({
  label,
  accent,
  notes,
  selectedId,
  onSelect,
}: {
  label: string;
  accent: string;
  notes: LazyBrainNote[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div>
      <div className="sticky top-0 z-[1] bg-bg-primary/95 backdrop-blur-sm pb-1.5 mb-2 flex items-baseline gap-2">
        <span
          className="inline-block w-1 h-3 rounded-sm"
          style={{ background: accent }}
          aria-hidden
        />
        <h2 className="text-[10px] uppercase tracking-[0.16em] font-semibold text-text-secondary">
          {label}
        </h2>
        <span className="text-[10px] text-text-muted tabular-nums">{notes.length}</span>
      </div>
      <ul className="space-y-1.5">
        {notes.map((n) => (
          <li key={n.id}>
            <NoteCard
              note={n}
              selected={n.id === selectedId}
              onSelect={onSelect}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}

function EmptyState({
  allCount,
  kind,
  hasSearch,
}: {
  allCount: number;
  kind: KindFilter;
  hasSearch: boolean;
}) {
  if (hasSearch) {
    return (
      <div className="text-center py-16 text-text-muted text-[13px]">
        Nothing matches your search.
      </div>
    );
  }
  if (allCount === 0) {
    return (
      <div className="text-center py-16 px-6 max-w-md mx-auto">
        <div
          className="text-[48px] leading-none mb-4 opacity-60"
          style={{ fontFamily: "'Source Serif 4', Georgia, serif" }}
        >
          ✎
        </div>
        <p
          className="text-text-secondary text-[15px] leading-relaxed mb-3"
          style={{ fontFamily: "'Source Serif 4', Georgia, serif" }}
        >
          A clean page. Type a thought above and press <kbd className="px-1.5 py-0.5 rounded border border-border-light text-[11px] font-mono">⏎</kbd>.
        </p>
        <p className="text-text-muted text-[12px]">
          Try <code className="text-text-secondary">note: ...</code>,{" "}
          <code className="text-text-secondary">idea: ...</code>, or{" "}
          <code className="text-text-secondary">remember: ...</code>. Or send to
          Telegram with <code className="text-text-secondary">/note</code>.
        </p>
      </div>
    );
  }
  const kindMeta = kind !== "all" ? KIND_META[kind] : null;
  return (
    <div className="text-center py-12 text-text-muted text-[13px]">
      No {kindMeta ? kindMeta.label.toLowerCase() + "s" : "notes"} match.
      <br />
      <button
        onClick={() => window.location.reload()}
        className="text-accent hover:underline mt-2 text-[12px]"
      >
        clear filters
      </button>
    </div>
  );
}
