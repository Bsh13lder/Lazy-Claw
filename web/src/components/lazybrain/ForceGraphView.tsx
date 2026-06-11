/**
 * ForceGraphView — drop-in replacement for the legacy GraphView.
 *
 * Composes <ForceGraph> (the new pixi+d3 graph) with the must-have
 * overlays from the old GraphView: search, freeze toggle, mode toggle,
 * mini category legend, and a "re-flow" button. Same Props interface as
 * the old GraphView so swapping at the call-site is one line in
 * LazyBrain.tsx.
 *
 * What lives elsewhere:
 *   - GraphPeekCard / GraphInspector are mounted by LazyBrain.tsx (we
 *     just emit onSelect / onPeek).
 *   - Importance slider / FilterBar are also outside this component.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Search,
  X as XIcon,
  Snowflake,
  Sun,
  Network,
  RotateCcw,
} from "lucide-react";
import type { LazyBrainGraph, LazyBrainNote } from "../../api";
import { ForceGraph, __clearPinnedPositions } from "./ForceGraph";
import { FILTER_CATEGORIES, categoryKeysFor } from "./noteColors";

interface Props {
  graph: LazyBrainGraph;
  notesById?: Record<string, LazyBrainNote>;
  /** Legacy — switches view entirely. Prefer onPeek. */
  onSelect?: (nodeId: string) => void;
  /** Preferred: clicking a node shows a preview card without leaving graph. */
  onPeek?: (nodeId: string) => void;
  selectedId?: string | null;
  /** Dismiss the current peek card (background click or Esc). */
  onClearPeek?: () => void;
  highlightQuery?: string;
  /** When provided, ForceGraphView renders a floating search overlay. */
  onSearchChange?: (q: string) => void;
  /** Called per-note — return true to dim this node (e.g. filtered out). */
  dimPredicate?: (note: LazyBrainNote | undefined) => boolean;
  showLegend?: boolean;
  hiddenCategories?: Set<string>;
  onToggleCategory?: (key: string) => void;
  onSetHiddenCategories?: (s: Set<string>) => void;
  categoryCounts?: Record<string, number>;
}

const FREEZE_KEY = "lazybrain-graph-freeze";
const MODE_KEY = "lazybrain-galaxy-mode";

function loadBool(key: string, fallback: boolean): boolean {
  if (typeof window === "undefined") return fallback;
  const raw = localStorage.getItem(key);
  if (raw === "true") return true;
  if (raw === "false") return false;
  return fallback;
}

function loadMode(): "category" | "neural-link" {
  if (typeof window === "undefined") return "category";
  const raw = localStorage.getItem(MODE_KEY);
  return raw === "neural-link" ? "neural-link" : "category";
}

export function ForceGraphView({
  graph,
  notesById,
  onSelect,
  onPeek,
  selectedId,
  onClearPeek,
  highlightQuery,
  onSearchChange,
  dimPredicate,
  showLegend = true,
  hiddenCategories,
  onToggleCategory,
  onSetHiddenCategories,
  categoryCounts,
}: Props) {
  const [frozen, setFrozen] = useState<boolean>(() => loadBool(FREEZE_KEY, false));
  const [mode, setMode] = useState<"category" | "neural-link">(() => loadMode());
  const [legendOpen, setLegendOpen] = useState<boolean>(true);
  // Counter that we bump to force ForceGraph to remount (re-init pixi +
  // re-run the full layout). Used by the "Re-flow" + "Clear pins" actions.
  const [reflowKey, setReflowKey] = useState(0);

  useEffect(() => {
    try {
      localStorage.setItem(FREEZE_KEY, frozen ? "true" : "false");
    } catch {
      /* quota / private mode */
    }
  }, [frozen]);

  useEffect(() => {
    try {
      localStorage.setItem(MODE_KEY, mode);
    } catch {
      /* quota / private mode */
    }
  }, [mode]);

  const matchCount = useMemo(() => {
    const q = (highlightQuery ?? "").trim().toLowerCase();
    if (!q || !notesById) return 0;
    let n = 0;
    for (const node of graph.nodes) {
      const note = notesById[node.id];
      const text = (note?.title ?? node.label ?? "").toLowerCase();
      if (text.includes(q)) n++;
    }
    return n;
  }, [highlightQuery, graph.nodes, notesById]);

  // Remount ForceGraph if `mode` changes — colors are baked in at mount.
  // The remount preserves pinned positions via localStorage.
  const graphForKey = useMemo(() => graph, [graph]);

  // Combine the parent's dimPredicate (e.g. importance threshold) with the
  // legend's hidden-category set. A note is dimmed if either rule says so.
  const combinedDim = useMemo(() => {
    if (!hiddenCategories || hiddenCategories.size === 0) return dimPredicate;
    return (note: LazyBrainNote | undefined) => {
      if (dimPredicate?.(note)) return true;
      if (!note) return false;
      const keys = categoryKeysFor(note.tags, !!note.pinned);
      // Dim if every category this note belongs to is hidden. Notes with
      // multiple categories stay visible if at least one is enabled.
      if (keys.length === 0) return false;
      return keys.every((k) => hiddenCategories.has(k));
    };
  }, [dimPredicate, hiddenCategories]);

  // How many nodes the active filters (categories / importance / owner /
  // telemetry) currently dim. Purely informational — surfaces in the
  // top-left HUD so a sparse-looking graph is never a silent mystery.
  const hiddenCount = useMemo(() => {
    if (!combinedDim) return 0;
    let n = 0;
    for (const node of graph.nodes) {
      if (combinedDim(notesById?.[node.id])) n++;
    }
    return n;
  }, [combinedDim, graph.nodes, notesById]);

  return (
    <div
      className="relative w-full h-full overflow-hidden lb-graph-canvas"
      data-frozen={frozen ? "1" : "0"}
      style={{
        background:
          "radial-gradient(ellipse at center, #1b1620 0%, #12101a 45%, #0a0910 100%)",
      }}
    >
      <ForceGraph
        key={`${reflowKey}-${mode}`}
        graph={graphForKey}
        notesById={notesById}
        selectedId={selectedId}
        highlightQuery={highlightQuery}
        layoutMode={mode}
        dimPredicate={combinedDim}
        frozen={frozen}
        onNodeClick={(id) => {
          // Single click = highlight subgraph only. Selection lights the
          // node + its 1-hop edges and dims everything else; the peek
          // card stays closed so the user can scan related neuralinks
          // without losing canvas focus. (Obsidian behaviour.)
          onSelect?.(id);
        }}
        onNodeDoubleClick={(id) => {
          // Double click = open the peek card / inspector (Obsidian
          // double-click-to-open behaviour). Selection stays so the
          // halo + connected edges remain bright behind the card.
          onSelect?.(id);
          onPeek?.(id);
        }}
        onBackgroundClick={() => {
          // Tap on empty canvas — clear selection so all nodes return
          // to the unfocused weave (no halo, all edges back to α 0.18).
          // LazyBrain.tsx's onClearPeek already nulls peek/select/focus.
          onClearPeek?.();
        }}
      />

      {/* Floating search overlay — top-center */}
      {onSearchChange && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 w-[340px] max-w-[calc(100%-160px)]">
          <div
            className="relative flex items-center gap-2 h-10 px-3 rounded-full backdrop-blur-md transition-shadow"
            style={{
              background: "rgba(27, 22, 32, 0.82)",
              border: "1px solid rgba(168,144,106,0.28)",
              boxShadow:
                "0 10px 30px -12px rgba(0,0,0,0.5), inset 0 1px 0 rgba(240,228,200,0.04)",
            }}
          >
            <Search size={13} strokeWidth={1.9} className="text-text-muted shrink-0" />
            <input
              value={highlightQuery || ""}
              onChange={(e) => onSearchChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") onSearchChange("");
              }}
              placeholder="search the brain"
              className="lb-floating-search flex-1 bg-transparent outline-none text-[13px] tracking-tight text-text-primary placeholder:text-text-muted/70"
              spellCheck={false}
              style={{ fontFamily: "var(--font-display, inherit)" }}
            />
            {(highlightQuery || "").trim() && (
              <>
                <span
                  className="text-[10px] uppercase tracking-[0.08em] tabular-nums shrink-0"
                  style={{ color: matchCount > 0 ? "#d4a26a" : "rgba(200,180,140,0.55)" }}
                >
                  {matchCount > 0
                    ? `${matchCount} match${matchCount === 1 ? "" : "es"}`
                    : "no matches"}
                </span>
                <button
                  onClick={() => onSearchChange("")}
                  className="shrink-0 text-text-muted hover:text-text-primary transition-colors -mr-1"
                  title="Clear (Esc)"
                >
                  <XIcon size={12} strokeWidth={2} />
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {/* Top-left toolbar — freeze, mode toggle, re-flow */}
      <div className="absolute top-3 left-3 z-10 flex items-center gap-1.5">
        <button
          onClick={() => setFrozen((v) => !v)}
          className={`flex items-center gap-1.5 px-2.5 h-8 rounded-md backdrop-blur-md text-[11px] tracking-tight border transition-colors ${
            frozen
              ? "bg-amber-500/15 border-amber-500/40 text-amber-300"
              : "bg-bg-secondary/70 border-border text-text-muted hover:text-text-primary"
          }`}
          title={frozen ? "Resume physics" : "Freeze layout"}
        >
          <Snowflake size={12} strokeWidth={2} />
          <span>{frozen ? "Frozen" : "Freeze"}</span>
        </button>

        <button
          onClick={() => setMode((m) => (m === "category" ? "neural-link" : "category"))}
          className="flex items-center gap-1.5 px-2.5 h-8 rounded-md backdrop-blur-md text-[11px] tracking-tight border border-border bg-bg-secondary/70 text-text-muted hover:text-text-primary transition-colors"
          title={mode === "category" ? "Switch to neural-link colors" : "Switch to category colors"}
        >
          {mode === "category" ? <Sun size={12} /> : <Network size={12} />}
          <span>{mode === "category" ? "Categories" : "Neural-links"}</span>
        </button>

        <button
          onClick={() => setReflowKey((k) => k + 1)}
          className="flex items-center gap-1.5 px-2.5 h-8 rounded-md backdrop-blur-md text-[11px] tracking-tight border border-border bg-bg-secondary/70 text-text-muted hover:text-text-primary transition-colors"
          title="Re-flow layout (clears positions briefly)"
        >
          <RotateCcw size={12} strokeWidth={2} />
          <span>Re-flow</span>
        </button>

        <button
          onClick={() => {
            __clearPinnedPositions();
            setReflowKey((k) => k + 1);
          }}
          className="px-2.5 h-8 rounded-md backdrop-blur-md text-[11px] tracking-tight border border-border bg-bg-secondary/70 text-text-muted hover:text-text-primary transition-colors"
          title="Clear all pinned node positions"
        >
          Unpin all
        </button>

        {hiddenCount > 0 && (
          <span
            className="flex items-center px-2.5 h-8 rounded-md backdrop-blur-md text-[11px] tracking-tight border border-border bg-bg-secondary/70 text-text-muted tabular-nums"
            title={`${hiddenCount} node${hiddenCount === 1 ? "" : "s"} dimmed by category / importance / owner filters`}
          >
            {hiddenCount} hidden by filters
          </span>
        )}
      </div>

      {/* Bottom-left mini legend — category chips */}
      {showLegend && categoryCounts && (
        <div className="absolute bottom-3 left-3 z-10 text-xs w-[210px] flex flex-col max-h-[calc(100vh-7rem)]">
          {legendOpen && (
            <div
              className="rounded-t-lg backdrop-blur-md p-1.5 overflow-y-auto border border-b-0"
              style={{
                background: "rgba(27, 22, 32, 0.82)",
                borderColor: "rgba(168,144,106,0.22)",
              }}
            >
              {FILTER_CATEGORIES.filter((c) => (categoryCounts[c.key] ?? 0) > 0).map((c) => {
                const hidden = hiddenCategories?.has(c.key);
                const count = categoryCounts[c.key] ?? 0;
                return (
                  <button
                    key={c.key}
                    onClick={() => onToggleCategory?.(c.key)}
                    className={`w-full flex items-center gap-2 px-1.5 py-1 rounded transition-colors ${
                      hidden ? "opacity-40 hover:opacity-70" : "hover:bg-white/5"
                    }`}
                    title={hidden ? `Show ${c.label}` : `Hide ${c.label}`}
                  >
                    <span
                      className="w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ backgroundColor: c.ring }}
                    />
                    <span className="flex-1 text-left text-[11px] text-text-primary truncate">
                      {c.label}
                    </span>
                    <span className="text-[10px] tabular-nums text-text-muted">{count}</span>
                  </button>
                );
              })}
            </div>
          )}
          <button
            onClick={() => setLegendOpen((v) => !v)}
            className={`w-full flex items-center justify-between px-3 py-1.5 backdrop-blur-md text-[11px] uppercase tracking-[0.12em] text-text-muted hover:text-text-primary transition-colors border ${
              legendOpen ? "rounded-b-lg" : "rounded-lg"
            }`}
            style={{
              background: "rgba(27, 22, 32, 0.82)",
              borderColor: "rgba(168,144,106,0.22)",
            }}
          >
            <span>Legend</span>
            <span className="tabular-nums">{graph.nodes.length} nodes</span>
          </button>
          {legendOpen && onSetHiddenCategories && (
            <div className="flex gap-2 mt-1.5">
              <button
                onClick={() => onSetHiddenCategories(new Set())}
                className="flex-1 text-[10px] uppercase tracking-[0.08em] text-text-muted hover:text-text-primary px-2 py-1 rounded border border-border bg-bg-secondary/60 backdrop-blur-md"
              >
                Show all
              </button>
              <button
                onClick={() => {
                  const all = new Set(FILTER_CATEGORIES.map((c) => c.key));
                  onSetHiddenCategories(all);
                }}
                className="flex-1 text-[10px] uppercase tracking-[0.08em] text-text-muted hover:text-text-primary px-2 py-1 rounded border border-border bg-bg-secondary/60 backdrop-blur-md"
              >
                Hide all
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
