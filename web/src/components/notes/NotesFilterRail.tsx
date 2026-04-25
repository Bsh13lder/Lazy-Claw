import { KIND_META, SOURCE_LABEL, type NoteKind } from "./noteHelpers";

type Props = {
  kindFilter: NoteKind | "all";
  onKindFilter: (k: NoteKind | "all") => void;
  sourceFilter: string | null;
  onSourceFilter: (s: string | null) => void;
  pinnedOnly: boolean;
  onPinnedOnly: (v: boolean) => void;
  counts: {
    all: number;
    note: number;
    idea: number;
    memory: number;
    bySource: Record<string, number>;
    pinned: number;
  };
};

const KINDS: { id: NoteKind | "all"; label: string }[] = [
  { id: "all", label: "All" },
  { id: "note", label: "Notes" },
  { id: "idea", label: "Ideas" },
  { id: "memory", label: "Memory" },
];

/**
 * Left rail — Kind / Source / Pinned filters. Compact, single column.
 * Same chrome density as Tasks page rail so it reads as part of the same app.
 */
export function NotesFilterRail({
  kindFilter,
  onKindFilter,
  sourceFilter,
  onSourceFilter,
  pinnedOnly,
  onPinnedOnly,
  counts,
}: Props) {
  return (
    <aside className="w-[180px] flex-none px-1 py-2 space-y-5 hidden md:block">
      <div>
        <h3 className="px-2 mb-1.5 text-[10px] uppercase tracking-[0.14em] text-text-muted">
          Kind
        </h3>
        <ul className="space-y-0.5">
          {KINDS.map((k) => {
            const active = kindFilter === k.id;
            const count =
              k.id === "all"
                ? counts.all
                : counts[k.id as NoteKind];
            const dot = k.id !== "all" ? KIND_META[k.id as NoteKind].dot : null;
            return (
              <li key={k.id}>
                <button
                  onClick={() => onKindFilter(k.id)}
                  className={[
                    "w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left text-[12px] transition-colors",
                    active
                      ? "bg-accent-soft text-accent"
                      : "text-text-secondary hover:bg-bg-hover/40 hover:text-text-primary",
                  ].join(" ")}
                >
                  {dot ? (
                    <span
                      className="inline-block w-1.5 h-1.5 rounded-full flex-none"
                      style={{ background: dot }}
                    />
                  ) : (
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-text-muted/50 flex-none" />
                  )}
                  <span className="flex-1">{k.label}</span>
                  <span className="text-[10px] text-text-muted tabular-nums">{count}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      <div>
        <h3 className="px-2 mb-1.5 text-[10px] uppercase tracking-[0.14em] text-text-muted">
          Source
        </h3>
        <ul className="space-y-0.5">
          <li>
            <button
              onClick={() => onSourceFilter(null)}
              className={[
                "w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left text-[12px] transition-colors",
                sourceFilter === null
                  ? "bg-bg-tertiary text-text-primary"
                  : "text-text-secondary hover:bg-bg-hover/40 hover:text-text-primary",
              ].join(" ")}
            >
              <span className="flex-1">All</span>
            </button>
          </li>
          {Object.entries(counts.bySource)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 6)
            .map(([src, n]) => (
              <li key={src}>
                <button
                  onClick={() => onSourceFilter(src)}
                  className={[
                    "w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left text-[12px] transition-colors",
                    sourceFilter === src
                      ? "bg-bg-tertiary text-text-primary"
                      : "text-text-secondary hover:bg-bg-hover/40 hover:text-text-primary",
                  ].join(" ")}
                >
                  <span className="flex-1">{SOURCE_LABEL[src] || src}</span>
                  <span className="text-[10px] text-text-muted tabular-nums">{n}</span>
                </button>
              </li>
            ))}
        </ul>
      </div>

      <div>
        <button
          onClick={() => onPinnedOnly(!pinnedOnly)}
          className={[
            "w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-[12px] transition-colors",
            pinnedOnly
              ? "bg-amber/15 text-amber"
              : "text-text-secondary hover:bg-bg-hover/40",
          ].join(" ")}
          style={pinnedOnly ? { color: "#f59e0b" } : undefined}
        >
          <span style={{ color: "#f59e0b" }}>★</span>
          <span className="flex-1">Pinned only</span>
          <span className="text-[10px] text-text-muted tabular-nums">{counts.pinned}</span>
        </button>
      </div>

      {/* Quiet helper card — keeps the rail from feeling sparse. */}
      <div className="mx-2 mt-6 rounded-lg border border-border/50 bg-bg-secondary/30 p-3 text-[11px] leading-relaxed text-text-muted">
        <div className="text-text-secondary font-medium mb-1">Capture anywhere</div>
        Type <code className="text-text-secondary">note:</code>,{" "}
        <code className="text-text-secondary">idea:</code>,{" "}
        <code className="text-text-secondary">remember:</code> in this page or in
        Telegram. Or use <code className="text-text-secondary">/note</code>{" "}
        on Telegram.
      </div>
    </aside>
  );
}
