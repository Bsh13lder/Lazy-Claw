/** Obsidian/Logseq-grade force graph for LazyBrain.
 *  - Line-icons (no emoji) via Lucide
 *  - Category color per node (via colorForTags)
 *  - Degree-scaled radius
 *  - Gold halo for pinned
 *  - Deep-neighbor hover: BFS up to 3 hops with progressive brightness,
 *    the rest of the graph dims to ~0.08 so the local sub-graph "pops"
 *  - Hover tooltip with preview card
 *  - Search highlight + filter-dim (from owner/category filters)
 *  - Zoom/pan, collapsible legend, live counts
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  getGraphPositions,
  saveGraphPositions,
  type LazyBrainGraph,
  type LazyBrainNote,
  type GraphLayoutMode,
} from "../../api";
import { ForceSimulation, type SimNode } from "./ForceSimulation";
import { drawGraph, type DrawNode, type DrawEdge, type DrawState } from "./CanvasGraphRenderer";
import {
  CATEGORY_PRIORITY,
  FILTER_CATEGORIES,
  colorForNode,
  colorForTags,
  categoryKeysFor,
  ringForKey,
} from "./noteColors";
import {
  CategoryIcon,
  Star,
  Clock,
  CATEGORY_CODEPOINTS,
  DEFAULT_CATEGORY_CODEPOINT,
} from "./icons";
import {
  displayLabelForNote,
  isRollupNote,
  isMonthlyRollupNote,
} from "./rollupLabel";
import { Plus, Minus, RotateCcw, ChevronDown, ChevronUp, MousePointer2, Move, ZoomIn, Search, X as XIcon } from "lucide-react";

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
  /** When provided, GraphView renders a floating search overlay on the
   *  canvas. Changes to the input are emitted via this callback. */
  onSearchChange?: (q: string) => void;
  /** Called per-note — return true to dim this node (e.g. filtered out). */
  dimPredicate?: (note: LazyBrainNote | undefined) => boolean;
  showLegend?: boolean;
  /** Interactive legend — when provided, legend rows toggle categories. */
  hiddenCategories?: Set<string>;
  onToggleCategory?: (key: string) => void;
  onSetHiddenCategories?: (s: Set<string>) => void;
  categoryCounts?: Record<string, number>;
}

interface DragState {
  kind: "node" | "pan";
  id?: string;
  startX: number;
  startY: number;
  lastX: number;
  lastY: number;
  /** Furthest distance moved during this gesture (px). Used to suppress
   *  the click that fires after a drag-release on the same node. */
  maxMoved: number;
  /** performance.now() at pointerdown — used for hold-based drag detection. */
  downTime: number;
}

/** Compute the visual radius of a node given its note metadata + degree.
 *  Single source of truth: the sim uses it to size its collision pass,
 *  and the JSX uses it to size the rendered <circle>. Keeping the math
 *  in one place is what guarantees collision distance always matches
 *  what the user actually sees. Numbers carry over from the long-tuned
 *  per-node block in the JSX (see "Obsidian-style radius" below). */
function computeNodeRadius(
  note: LazyBrainNote | undefined,
  deg: number,
): number {
  const importance = note?.importance ?? 5;
  const tagsLower = safeTagsLower(note?.tags);
  const isRollup =
    tagsLower.includes("kind/rollup") ||
    tagsLower.some((t) => t.startsWith("rollup/weekly") || t.startsWith("rollup/monthly"));
  const isMonthlyRollup = tagsLower.some((t) => t.startsWith("rollup/monthly"));
  let sourceCount = 0;
  if (isRollup) {
    const sc = tagsLower.find((t) => t.startsWith("source-count/"));
    if (sc) sourceCount = Number(sc.split("/", 2)[1]) || 0;
  }
  const baseR = isRollup
    ? (isMonthlyRollup ? 16 : 13) + Math.min(14, Math.sqrt(sourceCount) * 2.2)
    : 9 + Math.min(6, Math.sqrt(deg) * 1.1);
  return baseR + Math.min(3, importance / 3) + (note?.pinned ? 1 : 0);
}

/** Compact relative-time formatter for the hover tooltip. Output:
 *  "just now", "5m ago", "2h ago", "3d ago", "2w ago", or an
 *  absolute "Mar 12" / "Mar 12 2024" once we cross 6 weeks. Tight
 *  enough for a 260px card; readable enough that the user knows
 *  whether the note is fresh or stale. */
function formatRelativeShort(date: Date): string {
  const now = Date.now();
  const ms = now - date.getTime();
  if (!Number.isFinite(ms) || ms < 0) return "just now";
  const sec = Math.floor(ms / 1000);
  if (sec < 45) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 14) return `${day}d ago`;
  const wk = Math.floor(day / 7);
  if (wk < 6) return `${wk}w ago`;
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, sameYear
    ? { month: "short", day: "numeric" }
    : { month: "short", day: "numeric", year: "numeric" });
}

/** Below this px threshold a pointer-down/up counts as a click, not a drag. */
const DRAG_THRESHOLD = 3;
/** If the pointer is held down longer than this AND the user moves at all,
 *  treat as a drag even if the movement is sub-pixel-threshold. This catches
 *  trackpad drags that barely move the cursor. */
const DRAG_HOLD_MS = 180;

const MIN_ZOOM = 0.2;
const MAX_ZOOM = 4;
const MAX_DEPTH = 3;

// ── localStorage: cached node positions, keyed per layout mode ──────────
// Stored separately from `lazybrain-layout-mode` so clearing one doesn't
// nuke the other. JSON shape: { [nodeId]: [x, y] }. Private-mode Safari
// throws on setItem — wrap every call in try/catch so the graph never
// crashes because storage is unavailable.
//
// `:v3` bump (sun + belt redesign): the v2 categorical zone layout is
// also incompatible with the new topology-driven anchors (supernote
// pinned at centre, components on a belt ring). Old v1/v2 keys are
// simply ignored once; the next cool-down writes a fresh sun+belt
// snapshot under v3.
const POSITIONS_KEY_PREFIX = "lazybrain-graph-positions-v3:";

function readPositionsFromLocalStorage(
  mode: string,
): Record<string, [number, number]> | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const raw = window.localStorage.getItem(POSITIONS_KEY_PREFIX + mode);
    if (!raw) return undefined;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return undefined;
    // Light validation — drop obviously malformed rows instead of handing
    // the sim NaN coords that would blow up the force pass.
    const out: Record<string, [number, number]> = {};
    for (const [id, v] of Object.entries(parsed)) {
      if (
        Array.isArray(v) &&
        v.length === 2 &&
        Number.isFinite(v[0]) &&
        Number.isFinite(v[1])
      ) {
        out[id] = [v[0] as number, v[1] as number];
      }
    }
    return Object.keys(out).length ? out : undefined;
  } catch {
    return undefined;
  }
}

function writePositionsToLocalStorage(
  mode: string,
  positions: Record<string, [number, number]>,
): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      POSITIONS_KEY_PREFIX + mode,
      JSON.stringify(positions),
    );
  } catch {
    // Quota exceeded or private-mode Safari — non-fatal, the server copy
    // (or a fresh in-memory layout) picks up the slack on next reload.
  }
}

// Single source of truth for category priority lives in noteColors.ts —
// imported above so this file's pickCategoryKey, color, icon, and the
// filter chips can never drift apart again.
/** Coerce a tag list to lowercase strings, dropping any non-string entries.
 *  Single non-string in the input would otherwise crash `.toLowerCase()`
 *  and abort the surrounding useMemo factory, causing React error #310
 *  (hook count mismatch on next render). Used everywhere this file walks
 *  a note's tags. */
function safeTagsLower(tags: unknown): string[] {
  if (!Array.isArray(tags)) return [];
  const out: string[] = [];
  for (const t of tags) {
    if (typeof t === "string") out.push(t.toLowerCase());
  }
  return out;
}

function pickCategoryKey(
  tags: string[] | null | undefined,
  pinned: boolean,
): string {
  if (pinned) return "pinned";
  const lower = safeTagsLower(tags);
  if (lower.length === 0) return "_default";
  for (const key of CATEGORY_PRIORITY) {
    if (lower.includes(key)) return key;
    if (lower.some((t) => t.startsWith(`${key}/`))) return key;
  }
  return "_default";
}

// CATEGORY_ORBIT and ORBIT_LABELS removed with the orbital layout.

const BADGE_MAP: Record<string, string> = {
  task: "T",
  deadline: "!",
  journal: "J",
  "daily-log": "D",
  lesson: "L",
  shape: "S",
  "shape-pending": "⌛",
  "shape-failed": "!",
  "shape-known-bad": "✕",
  til: "TI",
  decision: "✓",
  price: "$",
  command: "⌘",
  recipe: "R",
  contact: "@",
  idea: "I",
  rollup: "Σ",
  reference: "→",
  layer: "L",
  survival: "Sv",
  fact: "F",
  learned_preference: "P",
  context: "C",
  memory: "M",
  "site-memory": "W",
  imported: "i",
  auto: "A",
  pinned: "★",
  _default: "•",
};

/** Letter/symbol badge for in-dot rendering. Used as a fallback only —
 *  the canvas renderer prefers the Phosphor `glyph` set on each
 *  `DrawNode`. Returning the BADGE_MAP letter keeps the data path live
 *  for any consumer that still wants the short text form (e.g. legend
 *  rows, tests).
 *
 *  Journal / daily-log nodes used to render `DD/MM` here, which gave
 *  them a visually different in-dot treatment from every other
 *  category (numerics vs Phosphor glyphs). The user reads category
 *  via colour and shape; the date already lives in the side label
 *  + the inspector panel — so this branch was dropped to keep all
 *  category dots visually unified. */
function dotBadge(
  _title: string | null | undefined,
  categoryKey: string,
): string {
  return BADGE_MAP[categoryKey] ?? "?";
}

export function GraphView({
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
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const ctxRef = useRef<CanvasRenderingContext2D | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const simRef = useRef<ForceSimulation | null>(null);
  const frameRef = useRef<number | null>(null);
  // Hover/select state mirrors so the RAF loop can read the latest
  // values without forcing a re-render. Updated by the existing
  // useState setters via a single useEffect.
  const hoverIdRef = useRef<string | null>(null);
  const selectedIdRef = useRef<string | null>(null);

  const [size, setSize] = useState({ width: 800, height: 600 });
  // Auto-fit on first load: the v3 sun+belt layout pushes belt systems
  // out to ~85% of half-canvas and singletons past the canvas edge,
  // which would clip badly at k=1.0. Default to k=0.7 the FIRST time
  // the user sees the redesign; once they've manually zoomed/panned
  // we mark this slot in localStorage so subsequent reloads keep their
  // choice. Cheap, no layout-then-measure flicker.
  const [view, setView] = useState(() => {
    if (typeof window === "undefined") return { tx: 0, ty: 0, k: 1 };
    try {
      if (window.localStorage.getItem("lazybrain-graph-view-fitted") === "true") {
        return { tx: 0, ty: 0, k: 1 };
      }
    } catch {
      // Private mode / disabled — fall through to fitted default.
    }
    return { tx: 0, ty: 0, k: 0.7 };
  });
  // Mark the view as user-managed once the user actually zooms or pans.
  const viewFittedFlagWrittenRef = useRef(false);
  useEffect(() => {
    if (viewFittedFlagWrittenRef.current) return;
    if (view.k === 0.7 && view.tx === 0 && view.ty === 0) return;
    viewFittedFlagWrittenRef.current = true;
    try {
      window.localStorage.setItem("lazybrain-graph-view-fitted", "true");
    } catch {
      // Best-effort.
    }
  }, [view]);
  const [hoverId, setHoverId] = useState<string | null>(null);
  // Mirror hoverId / selectedId into refs so the RAF loop can read
  // the latest value without subscribing to a re-render. These get
  // updated synchronously by an effect below.
  // Legend open/closed — persisted so the user's choice (and the
  // "Filters" header with chevron + all/none controls) doesn't reset on
  // page reload. Default open so first-timers see the full category map.
  const [legendOpen, setLegendOpen] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem("lazybrain-graph-legend-open");
      if (raw === "false") return false;
      if (raw === "true") return true;
    } catch {
      // Private mode / disabled — fall through to default.
    }
    return true;
  });
  useEffect(() => {
    try {
      localStorage.setItem("lazybrain-graph-legend-open", legendOpen ? "true" : "false");
    } catch {
      // Best-effort.
    }
  }, [legendOpen]);

  // Edge-visibility mode. "faint" keeps a ghost constellation (alpha
  // 0.04) so the user sees the graph silhouette before clicking. The
  // user-facing toggle that flipped this to "off" was removed because
  // the label was opaque ("what does Edges faint mean?"). The state
  // itself is preserved (read-only) so anyone who previously chose
  // "off" keeps that preference until they clear localStorage; new
  // users default to "faint", which is what most graph viewers do.
  const [edgesMode] = useState<"faint" | "off">(() => {
    try {
      const raw = localStorage.getItem("lazybrain-edges-mode");
      if (raw === "off") return "off";
      if (raw === "faint") return "faint";
    } catch {
      // Private mode / disabled — fall through to default.
    }
    return "faint";
  });
  // No per-frame `tick` state — the RAF loop writes node transforms
  // and edge `d` attributes directly via setAttribute. React only
  // reconciles on data/hover/select changes (rare). Pulse animations
  // (sun, hub halo, edge flow) are CSS-only on the compositor thread.

  // ── Resize observer ─────────────────────────────────────────────────────
  useLayoutEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          setSize({ width: Math.round(width), height: Math.round(height) });
        }
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Layout is force-only — Obsidian-style spring graph. Orbital
  // "solar system" mode was removed: it ran perpetual JS motion at
  // 60fps writing 30k SVG transforms per second forever, which was
  // the dominant runtime cost. Force layout settles in ~3s after
  // load and then sits at zero CPU until the user drags or hovers.
  const layoutMode: "neural-link" = "neural-link";

  // ── Physics freeze ────────────────────────────────────────────────────
  // Big graphs (500+ notes) crawl when the simulation steps every frame
  // and synapse motes re-render at 15fps. The freeze toggle short-circuits:
  //   - the RAF loop's s.step() + setTick (no physics, no React reconcile)
  //   - the pulseTick RAF loop (no mote/breath animation)
  //   - the synapse mote <g> rendering (drops 7,500 SVG nodes from DOM)
  // Persisted under "lazybrain-physics-frozen". On graphs with saved
  // positions and no explicit user preference, defaults to frozen so
  // re-opening the graph is instantaneous instead of triggering settle.
  const [physicsFrozen, setPhysicsFrozen] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try {
      const saved = window.localStorage.getItem("lazybrain-physics-frozen");
      if (saved === "true") return true;
      if (saved === "false") return false;
    } catch {
      // ignored — fall through to default
    }
    return false;
  });
  // Mirror in a ref so RAF loops can read the latest value without
  // tearing down the effect on every toggle.
  const physicsFrozenRef = useRef<boolean>(physicsFrozen);
  // Track whether the freeze state came from an explicit user click vs an
  // auto-derivation. Only persist user clicks.
  const physicsFrozenIsUserSetRef = useRef<boolean>(false);
  useEffect(() => {
    physicsFrozenRef.current = physicsFrozen;
    if (typeof window === "undefined") return;
    if (!physicsFrozenIsUserSetRef.current) return;
    try {
      window.localStorage.setItem(
        "lazybrain-physics-frozen", String(physicsFrozen),
      );
    } catch {
      // Private-mode Safari — session-only is fine.
    }
  }, [physicsFrozen]);
  const togglePhysicsFrozen = useCallback(() => {
    physicsFrozenIsUserSetRef.current = true;
    setPhysicsFrozen((cur) => {
      const next = !cur;
      // Unfreezing: warm() so the user immediately sees motion;
      // otherwise the sim might still be at quietTicks≥45 → cooled.
      if (!next) simRef.current?.warm();
      return next;
    });
  }, []);

  // ── Build simulation when graph shape changes ─────────────────────────
  // Note: size.width/height are intentionally NOT dependencies. Resizes
  // go through sim.resize() (below) which rescales positions in place
  // instead of discarding them. Rebuilding on resize made window drags
  // feel like a full reset every time.
  const sim = useMemo(() => {
    // Synchronous localStorage read so the first frame already has the
    // previously-settled layout. The async server fetch below overlays
    // anything newer — server is authoritative, localStorage is a cache.
    const savedPositions = readPositionsFromLocalStorage(layoutMode);
    // Degree map for the per-node radius hint passed to the sim. Drives
    // the collision pass so hubs push neighbours out by their actual
    // visual size, not the old global REPULSION_MIN=14 floor.
    const degMap = new Map<string, number>();
    for (const e of graph.edges) {
      degMap.set(e.source, (degMap.get(e.source) ?? 0) + 1);
      degMap.set(e.target, (degMap.get(e.target) ?? 0) + 1);
    }
    const s = new ForceSimulation(
      graph.nodes.map((n) => {
        const note = notesById?.[n.id];
        // Pass `kind` so the sim's per-edge spring length and per-node
        // repulsion mass can give rollup hubs extra breathing room
        // (see ForceSimulation constructor: ROLLUP_LEN_MUL + ×1.5 mass).
        const kind: "rollup" | "monthly-rollup" | undefined =
          isMonthlyRollupNote(note)
            ? "monthly-rollup"
            : isRollupNote(note)
              ? "rollup"
              : undefined;
        // Categorical zone — the load-bearing signal for the new
        // stellar-atlas layout. Falls through to "_default" (peripheral
        // anchor) when the note isn't loaded yet OR when no recognised
        // category tag matched — same priority order the palette uses
        // so dot colour, badge, filter row, and zone all agree.
        const categoryKey = note
          ? pickCategoryKey(note.tags ?? null, !!note.pinned)
          : "_default";
        return {
          id: n.id,
          pinned: false,
          r: computeNodeRadius(note, degMap.get(n.id) ?? 0),
          kind,
          // community_id retained for potential hue-rotation; layout
          // no longer reads it (replaced by categoryKey-driven anchors).
          community_id: n.community_id ?? null,
          categoryKey,
        };
      }),
      graph.edges.map((e) => ({ source: e.source, target: e.target })),
      {
        width: size.width,
        height: size.height,
        // Force layout always — orbital was removed. No `orbitOf` and
        // no central pin from the caller: the sim picks its own
        // supernote (max-degree node) and anchors it at canvas centre.
        mode: "force",
        savedPositions,
      },
    );
    simRef.current = s;
    return s;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph]);

  // Forward size changes to the existing sim so positions are preserved.
  useEffect(() => {
    simRef.current?.resize(size.width, size.height);
  }, [size.width, size.height]);

  // No auto-derived freeze defaults — the user's last toggle (or the
  // first-visit default of "Moving") is the source of truth and stays
  // until the user clicks ❄/✺ again. Earlier versions auto-froze on
  // sim cooldown or on entry-with-saved-positions, which kept undoing
  // an explicit "Moving" choice the moment the simulation settled.

  // Async server overlay — fetch saved positions on mount + mode change
  // and apply them on top of the localStorage cache. Server wins on
  // conflict; unknown nodes (new since last save) keep their seed.
  //
  // Stellar-atlas escape hatch: if the sim was constructed WITHOUT
  // local saved positions, the user is on a fresh categorical layout
  // (post v2 cache bump). Skipping the server overlay here lets the
  // strong CLUSTER_K pull settle into clean constellations without
  // an old blob snapshot yanking everything back to centre. Once the
  // categorical layout cools, it persists to localStorage AND the
  // server (`saveGraphPositions` debounce below), so the next reload
  // sees the new positions and the overlay path becomes safe again.
  useEffect(() => {
    let cancelled = false;
    const mode: GraphLayoutMode =
      layoutMode === "neural-link" ? "neural-link" : "category";
    const hasLocalCache = !!simRef.current?.usedSavedPositions();
    if (!hasLocalCache) return;
    getGraphPositions(mode)
      .then((res) => {
        if (cancelled) return;
        const positions = res.positions ?? {};
        if (Object.keys(positions).length === 0) return;
        // Stale-overlay guard: if the server's snapshot puts the
        // supernote (which v3 always pins at canvas centre) far from
        // the actual centre, the snapshot is from a previous force
        // model (v1 blob or v2 categorical zones). Drop it — the local
        // v3 cache is authoritative.
        const sim = simRef.current;
        if (sim) {
          const supId = sim.supernoteId();
          const centre = sim.centerXY();
          if (supId && positions[supId]) {
            const [sx, sy] = positions[supId];
            const dist = Math.hypot(sx - centre.x, sy - centre.y);
            if (dist > 120) {
              // Server snapshot is stale — silently drop. Don't refresh
              // the localStorage cache either; let the v3 settle stand.
              return;
            }
          }
        }
        simRef.current?.applyPositions(positions);
        // Also refresh the localStorage cache so offline next-load matches.
        writePositionsToLocalStorage(mode, positions);
      })
      .catch(() => {
        // Offline or 401 — localStorage-only is still a valid fallback.
      });
    return () => {
      cancelled = true;
    };
  }, [sim, layoutMode]);

  // ── Persist positions after the sim cools ─────────────────────────────
  // Polls sim.cooled() once a second. When it flips true, snapshot +
  // write to localStorage instantly (cheap) and debounce a POST to
  // the server (3s after the last cool event). Orbital mode never cools,
  // so this effectively runs for neural-link only — which is where the
  // user actively arranges the layout.
  useEffect(() => {
    let cancelled = false;
    let lastSignature = "";
    let postTimer: ReturnType<typeof setTimeout> | null = null;
    const mode: GraphLayoutMode =
      layoutMode === "neural-link" ? "neural-link" : "category";

    const persist = () => {
      const s = simRef.current;
      if (!s) return;
      if (mode === "neural-link" && !s.cooled()) return;
      const snapshot = s.positionsSnapshot();
      // Cheap signature — length + rounded centroid — catches "no change
      // since last save" without a deep diff.
      let sumX = 0;
      let sumY = 0;
      const keys = Object.keys(snapshot);
      for (const k of keys) {
        sumX += snapshot[k][0];
        sumY += snapshot[k][1];
      }
      const sig =
        `${keys.length}|${Math.round(sumX)}|${Math.round(sumY)}`;
      if (sig === lastSignature) return;
      lastSignature = sig;

      writePositionsToLocalStorage(mode, snapshot);
      if (postTimer !== null) clearTimeout(postTimer);
      postTimer = setTimeout(() => {
        if (cancelled) return;
        saveGraphPositions(mode, snapshot).catch(() => {
          // Server down or auth gone — localStorage already has it.
        });
      }, 3000);
    };

    // Category mode persists on every drag cooldown (1s poll) too, so
    // moving a journal node out of its orbit sticks.
    const interval = window.setInterval(persist, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      if (postTimer !== null) clearTimeout(postTimer);
    };
  }, [sim, layoutMode]);

  // Currently-isolated orbit (click a ring chip to focus). Forwarded
  // Orbit isolation removed with the orbital layout. The force layout
  // has no concept of orbits, so there's nothing to isolate.

  // Hub ids — top-5 by degree. Rendered with a warm pulse ring.
  const hubIds = useMemo(() => new Set(sim.hubIds(5)), [sim]);

  // ── At-rest readable labels ─────────────────────────────────────────────
  // Once the sim cools, show labels for the top ~25 highest-priority
  // notes (hubs + pinned + high-importance) with a de-overlap pass so
  // no two labels visually collide. This is the "Obsidian readable
  // graph at default zoom" the user asked for: not every label, but
  // enough to navigate the cluster shape without hovering each node.
  // Labels for the hovered/selected/search-matched nodes still draw on
  // top of this set with stronger emphasis.
  const [restLabelIds, setRestLabelIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    let cancelled = false;
    let lastSig = "";

    const recompute = () => {
      const s = simRef.current;
      if (!s) return;
      // Wait for the sim to settle before placing labels — placing
      // them on a moving graph means they'd jitter every poll.
      if (!s.cooled()) return;

      const degMap = new Map<string, number>();
      for (const e of graph.edges) {
        degMap.set(e.source, (degMap.get(e.source) ?? 0) + 1);
        degMap.set(e.target, (degMap.get(e.target) ?? 0) + 1);
      }

      type Cand = {
        id: string;
        x: number;
        y: number;
        r: number;
        score: number;
        label: string;
      };
      const cands: Cand[] = [];
      for (const sn of s.nodes) {
        const note = notesById?.[sn.id];
        const label = (note?.title || sn.id).trim();
        if (!label) continue;
        const deg = degMap.get(sn.id) ?? 0;
        const importance = note?.importance ?? 0;
        const score =
          deg +
          (note?.pinned ? 5 : 0) +
          (hubIds.has(sn.id) ? 3 : 0) +
          importance / 3;
        cands.push({
          id: sn.id,
          x: sn.x,
          y: sn.y,
          r: computeNodeRadius(note, deg),
          score,
          label,
        });
      }
      cands.sort((a, b) => b.score - a.score);

      // Greedy de-overlap: walk priority desc, accept a label only if
      // its rect doesn't intersect a higher-priority label's rect.
      const MAX_LABELS = 25;
      const FONT_PX = 9;
      type Rect = { x0: number; y0: number; x1: number; y1: number };
      const placed: Rect[] = [];
      const accepted: string[] = [];
      for (const c of cands) {
        if (accepted.length >= MAX_LABELS) break;
        const truncated =
          c.label.length > 14 ? c.label.slice(0, 12) + "…" : c.label;
        const w = truncated.length * (FONT_PX * 0.62) + 6;
        const h = 14;
        const offY = c.r + 6;
        const r: Rect = {
          x0: c.x - w / 2 - 3,
          y0: c.y + offY - 3,
          x1: c.x + w / 2 + 3,
          y1: c.y + offY + h + 3,
        };
        let collides = false;
        for (const p of placed) {
          if (r.x0 < p.x1 && r.x1 > p.x0 && r.y0 < p.y1 && r.y1 > p.y0) {
            collides = true;
            break;
          }
        }
        if (!collides) {
          placed.push(r);
          accepted.push(c.id);
        }
      }

      const sig = accepted.slice().sort().join("|");
      if (sig === lastSig) return;
      lastSig = sig;
      if (!cancelled) setRestLabelIds(new Set(accepted));
    };

    // Try immediately (covers re-mount on already-settled graph), then
    // poll until cooled. 1s cadence matches the persistence effect so
    // we don't introduce a second timer.
    recompute();
    const interval = window.setInterval(recompute, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sim, hubIds, graph, notesById]);

  // Pulse RAF loop deleted — see note on the pulseTick removal above.
  // Every prior pulseTick consumer is now CSS-driven:
  //   • sun corona/core/pupil → lb-sun-* keyframes
  //   • orbit ring shimmer    → lb-orbit-shimmer
  //   • edge flow stripe      → lb-edge-flow (focused edges only)
  //   • hub planet halo       → lb-hub-halo
  // The orbital sim drives positional motion; CSS owns visual
  // pulse/breath effects. Zero React reconciles per frame.

  // Decorative starfield + territorial grid SVG memos removed — the
  // canvas renderer relies on the container's CSS radial gradient for
  // atmosphere instead of ~250 extra DOM elements. The `starfield`
  // computation above stays unused but harmless (one-time work, no
  // per-frame cost).

  // ── id → SimNode lookup ───────────────────────────────────────────────
  // Stable per `sim` — only rebuilds when the simulation is replaced
  // (filter / mode change), not on every tick.
  const nodeById = useMemo(() => {
    const m = new Map<string, SimNode>();
    sim.nodes.forEach((n) => m.set(n.id, n));
    return m;
  }, [sim]);

  // Force one redraw on the next RAF tick. Used by hover/select effects
  // so the canvas updates instantly even when the sim is cooled.
  const needsRedrawRef = useRef<boolean>(true);

  // Sync hover / select state into refs the RAF loop and event handlers
  // read. Mark the canvas dirty so the next frame redraws with the new
  // focus state — same UX as the old `writeScaleFor` setAttribute path,
  // just delegated to the unified canvas draw.
  useEffect(() => {
    hoverIdRef.current = hoverId;
    needsRedrawRef.current = true;
  }, [hoverId]);
  useEffect(() => {
    selectedIdRef.current = selectedId ?? null;
    needsRedrawRef.current = true;
  }, [selectedId]);
  useEffect(() => {
    needsRedrawRef.current = true;
  }, [view, size, highlightQuery]);

  // ── Adjacency + degree ─────────────────────────────────────────────────
  const { adjacency, degree } = useMemo(() => {
    const adj = new Map<string, Set<string>>();
    const deg: Record<string, number> = {};
    for (const e of graph.edges) {
      if (!adj.has(e.source)) adj.set(e.source, new Set());
      if (!adj.has(e.target)) adj.set(e.target, new Set());
      adj.get(e.source)!.add(e.target);
      adj.get(e.target)!.add(e.source);
      deg[e.source] = (deg[e.source] ?? 0) + 1;
      deg[e.target] = (deg[e.target] ?? 0) + 1;
    }
    return { adjacency: adj, degree: deg };
  }, [graph]);

  // ── BFS depth from focus node. Focus priority: SELECTION wins over
  //    HOVER. Once the user clicks a node, hovering elsewhere doesn't
  //    re-aim the focus — that lets you click a node, then move the
  //    cursor over the graph to read other titles without losing your
  //    place. Hover-only focus (no selection) still works for quick
  //    inspection. Isolated focus nodes (no neighbors) skip dimming.
  const focusId = selectedId ?? hoverId ?? null;
  const isSelectFocus = !!selectedId;
  // Hover-induced focus only counts when nothing's selected.
  const isHoverFocus = !!hoverId && !isSelectFocus;
  const depths = useMemo(() => {
    if (!focusId) return null;
    const firstNeighbors = adjacency.get(focusId);
    if (!firstNeighbors || firstNeighbors.size === 0) return null;
    const d = new Map<string, number>();
    d.set(focusId, 0);
    let frontier = new Set([focusId]);
    for (let step = 1; step <= MAX_DEPTH; step++) {
      const next = new Set<string>();
      for (const id of frontier) {
        const neighbors = adjacency.get(id);
        if (!neighbors) continue;
        for (const n of neighbors) {
          if (!d.has(n)) {
            d.set(n, step);
            next.add(n);
          }
        }
      }
      if (next.size === 0) break;
      frontier = next;
    }
    return d;
  }, [focusId, adjacency]);

  // ── Search matcher ─────────────────────────────────────────────────────
  const matcher = useMemo(() => {
    const q = (highlightQuery || "").trim().toLowerCase();
    if (!q) return null;
    return (noteId: string) => {
      const n = notesById?.[noteId];
      const title = (n?.title || "").toLowerCase();
      const content = (n?.content || "").toLowerCase();
      return title.includes(q) || content.includes(q);
    };
  }, [highlightQuery, notesById]);

  // Live match count for the floating search overlay.
  const matchCount = useMemo(() => {
    if (!matcher) return 0;
    let n = 0;
    for (const node of graph.nodes) if (matcher(node.id)) n++;
    return n;
  }, [matcher, graph.nodes]);

  // ── Coord transforms ───────────────────────────────────────────────────
  const screenToSim = useCallback(
    (sx: number, sy: number) => ({
      x: (sx - view.tx) / view.k,
      y: (sy - view.ty) / view.k,
    }),
    [view],
  );

  const clientToSvg = useCallback((cx: number, cy: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    return { x: cx - rect.left, y: cy - rect.top };
  }, []);

  // ── Wheel zoom-to-cursor ───────────────────────────────────────────────
  const handleWheel = useCallback(
    (e: React.WheelEvent<Element>) => {
      e.preventDefault();
      const { x: sx, y: sy } = clientToSvg(e.clientX, e.clientY);
      const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      setView((v) => {
        const kNew = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.k * factor));
        const ratio = kNew / v.k;
        return {
          k: kNew,
          tx: sx - (sx - v.tx) * ratio,
          ty: sy - (sy - v.ty) * ratio,
        };
      });
    },
    [clientToSvg],
  );

  // ── Pan / node drag ────────────────────────────────────────────────────
  // Track whether the most recent pointer-up came from a drag — so the
  // click event that follows can be suppressed. Cleared on next pointer-down.
  const justDraggedRef = useRef(false);

  const handlePointerDownBg = useCallback(
    (e: React.PointerEvent<SVGElement>) => {
      if (e.target !== e.currentTarget && !(e.target as Element).classList?.contains("lb-bg")) {
        return;
      }
      const { x, y } = clientToSvg(e.clientX, e.clientY);
      dragRef.current = { kind: "pan", startX: x, startY: y, lastX: x, lastY: y, maxMoved: 0, downTime: performance.now() };
      justDraggedRef.current = false;
      (e.currentTarget as SVGElement).setPointerCapture(e.pointerId);
    },
    [clientToSvg],
  );

  const handlePointerDownNode = useCallback(
    (e: React.PointerEvent<SVGGElement>, id: string) => {
      e.stopPropagation();
      const { x, y } = clientToSvg(e.clientX, e.clientY);
      dragRef.current = { kind: "node", id, startX: x, startY: y, lastX: x, lastY: y, maxMoved: 0, downTime: performance.now() };
      justDraggedRef.current = false;
      (e.currentTarget as SVGGElement).setPointerCapture(e.pointerId);
    },
    [clientToSvg],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<Element>) => {
      const { x: sx, y: sy } = clientToSvg(e.clientX, e.clientY);
      const drag = dragRef.current;
      if (!drag) return;
      const moved = Math.hypot(sx - drag.startX, sy - drag.startY);
      const maxMoved = Math.max(drag.maxMoved, moved);
      if (drag.kind === "pan") {
        const dx = sx - drag.lastX;
        const dy = sy - drag.lastY;
        setView((v) => ({ ...v, tx: v.tx + dx, ty: v.ty + dy }));
      } else if (drag.kind === "node" && drag.id) {
        // Drag is active if EITHER (a) the user moved more than the pixel
        // threshold OR (b) they've been holding the pointer down for longer
        // than DRAG_HOLD_MS with any non-zero movement. (b) catches trackpad
        // drags that barely travel on screen.
        const held = performance.now() - drag.downTime;
        const isDragging =
          maxMoved > DRAG_THRESHOLD ||
          (held > DRAG_HOLD_MS && maxMoved > 0.5);
        if (isDragging) {
          justDraggedRef.current = true;
          const { x, y } = screenToSim(sx, sy);
          sim.pin(drag.id, x, y);
          // Drag-stir: low-alpha re-warm (0.06) so neighbours redistribute
          // via springs without re-firing full O(N²) repulsion every frame.
          sim.nudge();
        }
      }
      dragRef.current = { ...drag, lastX: sx, lastY: sy, maxMoved };
    },
    [clientToSvg, screenToSim, sim],
  );

  const handlePointerUp = useCallback(() => {
    const drag = dragRef.current;
    const held = drag ? performance.now() - drag.downTime : 0;
    // Drag detected if movement exceeds threshold OR gesture was held a while
    // with any movement. justDraggedRef may also have been set to true during
    // pointermove — respect that (don't flip it back to false).
    const wasDrag =
      !!drag &&
      (drag.maxMoved > DRAG_THRESHOLD ||
        (held > DRAG_HOLD_MS && drag.maxMoved > 0.5));
    if (wasDrag) justDraggedRef.current = true;
    // Release dragged nodes back into their orbit.
    if (drag?.kind === "node" && drag.id && wasDrag) {
      simRef.current?.unpin(drag.id);
      // Wake — released node will accelerate from rest under the
      // spring/repulsion forces; the renderer needs to keep ticking.
      simRef.current?.warm();
    }
    // Background tap (pan gesture with no meaningful movement) = dismiss peek.
    if (drag?.kind === "pan" && !wasDrag && onClearPeek) {
      onClearPeek();
    }
    dragRef.current = null;
  }, [onClearPeek]);

  // Enter/leave handlers — DO NOT pin the node on hover. Pinning was
  // there to make the node "freeze" so it could be clicked reliably, but
  // it caused a sticky-tooltip race: when the leave timer fired and
  // unpinned the node, the simulation would drift it back under the
  // cursor, retriggering enter and re-pinning. Without pinning, the node
  // either stays put (cooled sim) or drifts away (cursor naturally loses
  // hover), both of which are correct behavior.
  const hoverClearRef = useRef<number | null>(null);

  const cancelHoverClear = () => {
    if (hoverClearRef.current !== null) {
      window.clearTimeout(hoverClearRef.current);
      hoverClearRef.current = null;
    }
  };

  const clearHoverNow = useCallback(() => {
    cancelHoverClear();
    setHoverId(null);
    simRef.current?.setHover(null);
    // No warm() — hover is purely visual feedback, scale is updated
    // imperatively by the writeScaleFor() effect above. Waking the
    // sim would burst 45 frames of pointless setAttribute writes.
  }, []);

  const handleNodeEnter = useCallback((id: string) => {
    cancelHoverClear();
    setHoverId(id);
    simRef.current?.setHover(id);
  }, []);

  const handleNodeLeave = useCallback((id: string) => {
    cancelHoverClear();
    hoverClearRef.current = window.setTimeout(() => {
      hoverClearRef.current = null;
      setHoverId((cur) => {
        if (cur === id) {
          simRef.current?.setHover(null);
          return null;
        }
        return cur;
      });
    }, 40);
  }, []);

  // Cleanup on unmount — never leak the timer.
  useEffect(() => () => cancelHoverClear(), []);

  // Orbital sim animates continuously via the main loop — filter/search
  // changes don't need to re-heat anything.

  // Keyboard shortcuts: 1..4 isolate an orbit, Esc clears isolation/search,
  // `/` focuses the search input. Ignored when typing in any input.
  useEffect(() => {
    const isEditable = (el: EventTarget | null) => {
      if (!el || !(el instanceof HTMLElement)) return false;
      const tag = el.tagName;
      return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable;
    };
    const onKey = (e: KeyboardEvent) => {
      if (isEditable(e.target)) {
        // Allow Esc to blur an input for quick clear-then-navigate.
        if (e.key === "Escape" && e.target instanceof HTMLElement) {
          (e.target as HTMLElement).blur();
        }
        return;
      }
      if (e.key === "Escape") {
        // Close peek first — most likely what the user wants to dismiss.
        if (selectedId && onClearPeek) {
          onClearPeek();
          return;
        }
        if (highlightQuery && onSearchChange) onSearchChange("");
      } else if (e.key === "/" && onSearchChange) {
        e.preventDefault();
        const input = containerRef.current?.querySelector<HTMLInputElement>(
          "input.lb-floating-search",
        );
        input?.focus();
      }
      // 1-4 orbit isolation keys removed with orbital layout.
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [highlightQuery, onSearchChange, selectedId, onClearPeek]);

  // Tooltip position — relative to hovered node's on-screen position (stable,
  // doesn't jitter with every pointermove).
  const tooltipPos = useMemo(() => {
    if (!hoverId) return null;
    const sn = nodeById.get(hoverId);
    if (!sn) return null;
    return {
      sx: sn.x * view.k + view.tx + 18,
      sy: sn.y * view.k + view.ty + 18,
    };
  }, [hoverId, nodeById, view]);

  // EMPTY-STATE FLAG — was previously a top-level early return, but
  // because there are still useMemo hooks below this point (edgesJsx,
  // motesJsx, nodesJsx), an early return skipped them on the empty
  // render and called them on the populated render. That is a Rules of
  // Hooks violation (React error #310 — "rendered more hooks than
  // during the previous render"). The empty-state JSX is now rendered
  // conditionally at the end of the component, after every hook has
  // run unconditionally.
  const isEmptyGraph = graph.nodes.length === 0;

  // Labels — Logseq-style, always visible. Overlap is tolerated; clarity
  // wins over a clean layout. User can zoom/pan to resolve overlaps.
  const hoverNote = hoverId ? notesById?.[hoverId] : undefined;

  // Per-node opacity. Obsidian-style: every dot stays clearly visible
  // even when a subgraph is selected — only relative brightness shifts.
  // Earlier tuning faded non-focus nodes to 0.14, which read as "the
  // dots disappeared" on click. New floor is 0.55 for non-focus and
  // 0.5 for momentary hover, so the spatial layout always remains
  // readable.
  //   • Idle (no hover, no select): every node at 0.95 — graph fully on.
  //   • Hover (no selection): hovered + 1-hop pop, the rest at 0.5.
  //   • Selection: selected + 1-hop pop, the rest at 0.55.
  const NON_FOCUS_DIM = isSelectFocus ? 0.55 : isHoverFocus ? 0.5 : 0.95;
  const opacityFor = (noteId: string): number => {
    const note = notesById?.[noteId];
    const dimmed = dimPredicate?.(note) ?? false;
    const match = matcher ? matcher(noteId) : true;
    // Filter / search miss is the ONE place we drop hard — that's an
    // explicit user "hide these" signal. Selection focus does NOT count
    // as such a signal.
    if (dimmed || !match) return 0.08;
    // Hover only brightens when nothing is locked-in via click. Once
    // the user has selected a node, hovering elsewhere does NOT
    // re-light that node — the selection's neighbourhood owns the
    // focus until cleared.
    if (!isSelectFocus && noteId === hoverId) return 1;
    if (depths) {
      const d = depths.get(noteId);
      if (d === undefined) return NON_FOCUS_DIM;
      if (d === 0) return 1;
      if (d === 1) return 1;
      if (d === 2) return isSelectFocus ? 0.85 : 0.9;
      return Math.max(NON_FOCUS_DIM, 0.7);
    }
    return 0.95;
  };

  const edgeState = (src: string, tgt: string) => {
    const srcNote = notesById?.[src];
    const tgtNote = notesById?.[tgt];
    const dimA = dimPredicate?.(srcNote) ?? false;
    const dimB = dimPredicate?.(tgtNote) ?? false;
    const matchA = matcher ? matcher(src) : true;
    const matchB = matcher ? matcher(tgt) : true;
    // Edge stays visible if AT LEAST ONE endpoint is unfiltered. Previously
    // `dimA || dimB` killed an edge whenever either end was filtered, which
    // made connections vanish under common filter setups (e.g. importance
    // slider > 1). Keeping edges to visible notes preserves the wikilink
    // topology even when half the graph is dimmed by user filters.
    if ((dimA && dimB) || !(matchA || matchB)) {
      return { opacity: 0, stroke: "var(--color-text-muted)", width: 0.8 };
    }
    // No-focus baseline. "faint" leaves a ghost constellation visible
    // (alpha 0.04) so the user sees the graph's silhouette from across
    // the room. "off" hides every wire until they click — the strongest
    // possible separation cue.
    const baseAlpha = edgesMode === "off" ? 0 : 0.04;
    if (depths) {
      const dSrc = depths.get(src);
      const dTgt = depths.get(tgt);
      if (dSrc === undefined && dTgt === undefined) {
        // Edge not in focus neighbourhood — fully invisible. Non-neighbor
        // wires never paint while a node is focused, so the lit subgraph
        // pops against an empty background. This is the click-to-reveal
        // experience the user asked for.
        return {
          opacity: 0,
          stroke: "var(--color-text-muted)",
          width: 0.8,
        };
      }
      const minD = Math.min(dSrc ?? 99, dTgt ?? 99);
      if (minD === 0) return { opacity: 0.95, stroke: "var(--color-accent)", width: 2 };
      if (minD === 1) return { opacity: isSelectFocus ? 0.95 : 0.85, stroke: "var(--color-accent)", width: 1.5 };
      // Two-hop edges trace the next ring out — faint but present so
      // users can follow the chain without it looking like fuzz.
      if (minD === 2) return { opacity: 0.18, stroke: "var(--color-accent-dim)", width: 1 };
      // Three+ hops: gone. Keeps the focus subgraph visually isolated.
      return { opacity: 0, stroke: "var(--color-accent-dim)", width: 1 };
    }
    return { opacity: baseAlpha, stroke: "var(--color-text-muted)", width: 1 };
  };

  // ── Canvas drawing pipeline ──────────────────────────────────────────
  // The previous SVG architecture rendered ~600 DOM elements per graph
  // (one <g> with body+rings+icon per node, 1-2 <path> per edge). At
  // 2500+ nodes that maps to ~10,000 SVG elements which the browser
  // cannot composite at 60fps regardless of per-element optimisation.
  //
  // Canvas-2D rendering replaces all of that with a single <canvas>
  // DOM node. drawGraph() paints every frame imperatively — no React
  // reconcile, no per-element style recalc, no compositor cost beyond
  // one layer. Comfortably handles 5000+ nodes at 60fps.
  //
  // Hover/click/drag wire to canvas-level pointer events with a
  // distance-based hit-test (see findNodeAt). The reads-from-React
  // model is preserved: closures capture the current opacityFor /
  // edgeState / depths / matcher / etc. so visual states stay in sync
  // with the existing data hooks.

  // Pre-allocate a draw-node pool keyed by node id. The pool is reused
  // across frames so we don't allocate 2500 objects per RAF tick.
  // Stable per data change; positions are patched in per frame.
  const drawNodesPoolRef = useRef<DrawNode[]>([]);
  const drawEdgesPoolRef = useRef<DrawEdge[]>([]);

  // Hit-test: client coords → world coords → nearest node within
  // visual radius. O(N) iteration; at 2500 nodes that's ~0.1ms per
  // call which is fine for pointer events. Spatial-hash later if
  // pointer-move starts dominating CPU on monster graphs.
  const findNodeAt = useCallback((clientX: number, clientY: number): string | null => {
    const canvas = canvasRef.current;
    const s = simRef.current;
    if (!canvas || !s) return null;
    const rect = canvas.getBoundingClientRect();
    const cx = clientX - rect.left;
    const cy = clientY - rect.top;
    const wx = (cx - view.tx) / view.k;
    const wy = (cy - view.ty) / view.k;
    const nodes = s.nodes;
    const radii = s.radii();
    // Reverse iteration so visually-on-top nodes (drawn last) win the
    // hit-test in ambiguous cases. Add a small tolerance pad so the
    // pointer doesn't have to land dead-center on a small dot.
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const dx = wx - n.x;
      const dy = wy - n.y;
      const r = (radii[i] ?? 12) + 4;
      if (dx * dx + dy * dy <= r * r) return n.id;
    }
    return null;
  }, [view]);

  // ── Canvas DPR setup + RAF draw loop ──────────────────────────────
  const dprRef = useRef(typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1);
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    dprRef.current = dpr;
    canvas.style.width = `${size.width}px`;
    canvas.style.height = `${size.height}px`;
    canvas.width = Math.floor(size.width * dpr);
    canvas.height = Math.floor(size.height * dpr);
    const ctx = canvas.getContext("2d", { alpha: true });
    ctxRef.current = ctx;
    needsRedrawRef.current = true;
  }, [size.width, size.height]);

  // id → pool index, rebuilt by the pool useEffect below. Used by the
  // per-frame patcher to look up each sim node's slot — pool length can
  // be < sim.nodes length (we skip graph nodes with no sim node), so
  // index-based alignment silently drifts and produces hover/label
  // mismatches.
  const poolIndexById = useRef(new Map<string, number>());

  // Build the DrawNode pool when graph data, focus state, or filters
  // change. Per-frame loop only patches x/y/scale/halo on top of this
  // — most fields stay constant for tens of frames at a stretch.
  useEffect(() => {
    const pool: DrawNode[] = [];
    const poolIdx = new Map<string, number>();
    for (const node of graph.nodes) {
      const sn = nodeById.get(node.id);
      if (!sn) continue;
      const note = notesById?.[node.id];
      // Phase I — when the backend stamped a Leiden community_id on the
      // graph node, route coloring through colorForNode so neural-link
      // mode gets topic-distinct hues instead of tag-bucket coloring.
      const color = note
        ? colorForNode(
            {
              pinned: note.pinned,
              tags: note.tags,
              community_id: node.community_id,
            },
            layoutMode,
          )
        : { ring: "#475569", emoji: "", label: "Note" };
      const deg = degree[node.id] ?? 0;
      const r = computeNodeRadius(note, deg);
      const categoryKeys = categoryKeysFor(note?.tags, !!note?.pinned);
      const categoryKey = categoryKeys[0] ?? pickCategoryKey(note?.tags, !!note?.pinned);
      const badge = dotBadge(note?.title, categoryKey);
      // Date-badge branch retired — every category dot now renders the
      // same way (Phosphor glyph at center). Kept the binding for the
      // local read below so the diff stays surgical.
      const isDateBadge = false;
      const isPinned = !!note?.pinned;
      const isHub = hubIds.has(node.id);
      const isMatch = matcher ? matcher(node.id) : false;
      const op = opacityFor(node.id);
      const isFocus = !selectedId && node.id === hoverId;
      const isSelected = node.id === selectedId;
      const isRollup = isRollupNote(note);
      // Depth-1 neighbour of the focused (hovered or selected) node.
      // Drives the "subgraph lights up" effect: neighbours stay bright
      // and get their titles drawn so the user can read what's connected
      // to what without hovering each one. Obsidian's default behaviour.
      const isNeighborOfFocus =
        !!focusId && depths !== null && depths.get(node.id) === 1;
      const isRestLabel = restLabelIds.has(node.id);
      const stroke = isPinned
        ? "#fbbf24"
        : isSelected
          ? "#10b981"
          : isHub
            ? "#d4a26a"
            : isFocus
              ? "rgba(240,228,200,0.55)"
              : isNeighborOfFocus
                ? "rgba(240,228,200,0.30)"
                : "rgba(240,228,200,0.10)";
      const strokeWidth = isPinned
        ? 2
        : isSelected
          ? 2.2
          : isHub
            ? 1
            : isFocus
              ? 1.2
              : isNeighborOfFocus
                ? 0.7
                : 0.5;
      const glyph = isDateBadge ? "" : (CATEGORY_CODEPOINTS[categoryKey] ?? DEFAULT_CATEGORY_CODEPOINT);
      // Three-tier label visibility:
      //   • Focus / selected / search match  → emphasised label
      //   • 1-hop neighbour of focus         → bright label (lit subgraph)
      //   • Rest-label priority (top ~25)    → quiet always-on label
      //   • Rollup nodes                     → always show (anchors)
      const showLabel =
        !!node.label && (
          isFocus ||
          isSelected ||
          isNeighborOfFocus ||
          (highlightQuery ? isMatch : false) ||
          isRollup ||
          isRestLabel
        );
      const { primary: labelText } = displayLabelForNote(note, node.label);
      const maxChars = isSelected || isFocus ? 32 : 18;
      const truncated =
        labelText && labelText.length > maxChars
          ? labelText.slice(0, maxChars - 2) + "…"
          : labelText ?? "";
      const scale = isFocus ? 1.18 : isSelected ? 1.08 : 1;
      pool.push({
        x: sn.x,
        y: sn.y,
        r,
        fill: color.ring,
        stroke,
        strokeWidth,
        dash: isRollup,
        glyph,
        badge: isDateBadge ? badge : "",
        label: showLabel ? truncated : "",
        labelEmphasized: isSelected || isFocus || isNeighborOfFocus,
        opacity: op,
        scale,
        // Halo bumped from 0.10/0.06 → 0.32/0.22. The previous values
        // were near-invisible against the canvas's warm-charcoal
        // gradient, which is why the focus state read flat. With the
        // bump the hovered/selected node now glows visibly enough to
        // pop out of a dense 2500-dot field.
        haloColor: isFocus || isSelected ? color.ring : "",
        haloAlpha: isFocus ? 0.32 : isSelected ? 0.22 : 0,
      });
      poolIdx.set(node.id, pool.length - 1);
    }
    drawNodesPoolRef.current = pool;
    poolIndexById.current = poolIdx;
    needsRedrawRef.current = true;
  }, [
    graph.nodes, nodeById, notesById, degree, hubIds, depths, focusId,
    isSelectFocus, isHoverFocus, selectedId, hoverId, highlightQuery,
    dimPredicate, matcher, restLabelIds,
  ]);

  // Build the DrawEdge pool — same lifecycle as the node pool.
  useEffect(() => {
    const pool: DrawEdge[] = [];
    for (let i = 0; i < graph.edges.length; i++) {
      const e = graph.edges[i];
      const a = nodeById.get(e.source);
      const b = nodeById.get(e.target);
      if (!a || !b) continue;
      const st = edgeState(e.source, e.target);
      const srcNote = notesById?.[e.source];
      const tgtNote = notesById?.[e.target];
      const isRollupEdge = isRollupNote(srcNote) || isRollupNote(tgtNote);
      const isActive =
        !!focusId &&
        depths !== null &&
        ((depths.get(e.source) ?? 99) <= 1 ||
          (depths.get(e.target) ?? 99) <= 1);
      const stroke = isRollupEdge
        ? "#c026d3"
        : srcNote
          ? colorForTags(srcNote.tags, srcNote.pinned).ring
          : "#6b5e4a";
      // Edge opacity tuning — Obsidian / Logseq style:
      //   • No focus    → all edges visible at ~25% so the network
      //                    shape reads as a single weave.
      //   • With focus  → ACTIVE edges (touching focus) at 65%,
      //                    INACTIVE edges drop to 18% (still visible
      //                    so the user can see the rest of the
      //                    network, just receded). The previous 4%
      //                    erased the background entirely.
      // Width bumped only +0.4 vs inactive — width contrast plus
      // opacity contrast carries the "lit" signal without the wall
      // of bright lines a 50-edge rollup hub used to produce.
      const activeStroke = isRollupEdge ? "#c026d3" : stroke;
      pool.push({
        ax: a.x,
        ay: a.y,
        bx: b.x,
        by: b.y,
        bowSign: i % 2 === 0 ? 1 : -1,
        stroke: isActive
          ? activeStroke
          : st.stroke === "var(--color-text-muted)"
            ? "#6b5e4a"
            : st.stroke === "var(--color-accent)"
              ? "#10b981"
              : st.stroke === "var(--color-accent-dim)"
                ? "#059669"
                : stroke,
        width: isActive ? 1.4 : st.width,
        opacity: isActive
          ? 0.65
          : !!focusId
            ? 0.18 * (isRollupEdge ? 0.7 : 1)
            : st.opacity * (isRollupEdge ? 0.65 : 1),
        dashed: isRollupEdge && !isActive,
      });
    }
    drawEdgesPoolRef.current = pool;
    needsRedrawRef.current = true;
  }, [
    graph.edges, nodeById, notesById, depths, focusId,
    isSelectFocus, isHoverFocus, dimPredicate, matcher,
  ]);

  // Sim → pool position patcher. Called per frame to copy the latest sim
  // positions into the existing DrawNode/Edge objects. Looks up the pool
  // slot by node id (NOT array index — those diverge).
  const patchPoolPositions = useCallback(() => {
    const s = simRef.current;
    if (!s) return;
    const sNodes = s.nodes;
    const nodePool = drawNodesPoolRef.current;
    const idxToSim = nodeIndexById.current;
    const idxToPool = poolIndexById.current;
    for (let i = 0; i < sNodes.length; i++) {
      const n = sNodes[i];
      const pi = idxToPool.get(n.id);
      if (pi === undefined || pi >= nodePool.length) continue;
      nodePool[pi].x = n.x;
      nodePool[pi].y = n.y;
    }
    // Edges: walk by graph.edges order.
    const edgePool = drawEdgesPoolRef.current;
    const edges = graph.edges;
    let writeIdx = 0;
    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      const ai = idxToSim.get(e.source);
      const bi = idxToSim.get(e.target);
      if (ai === undefined || bi === undefined) continue;
      const a = sNodes[ai];
      const b = sNodes[bi];
      if (writeIdx < edgePool.length) {
        edgePool[writeIdx].ax = a.x;
        edgePool[writeIdx].ay = a.y;
        edgePool[writeIdx].bx = b.x;
        edgePool[writeIdx].by = b.y;
      }
      writeIdx++;
    }
  }, [graph.edges]);

  // id → sim index. Stable per sim — used by the edge-position patch loop.
  const nodeIndexById = useRef(new Map<string, number>());
  useEffect(() => {
    const m = new Map<string, number>();
    sim.nodes.forEach((n, i) => m.set(n.id, i));
    nodeIndexById.current = m;
  }, [sim]);

  useEffect(() => {
    let alive = true;
    const loop = () => {
      if (!alive) return;
      const s = simRef.current;
      const ctx = ctxRef.current;
      if (!s || !ctx) {
        frameRef.current = requestAnimationFrame(loop);
        return;
      }
      const drag = dragRef.current;
      const dragging = drag?.kind === "node" && !!drag.id;
      const cooled = s.cooled();
      const frozen = physicsFrozenRef.current;

      // Run the physics step when warm + not frozen. Frozen mode still
      // honours active drags so the pinned node tracks the cursor.
      if (!frozen && !(cooled && !dragging)) {
        s.step();
      }

      // Decide whether to repaint. Skip when nothing has changed since
      // the last draw — cooled, no drag, no hover/select/view dirty.
      const dirty = needsRedrawRef.current;
      const motion = !cooled && !frozen;
      if (!dirty && !motion && !dragging) {
        frameRef.current = requestAnimationFrame(loop);
        return;
      }

      patchPoolPositions();
      // Topology-driven layout: no painted zones / serif titles. The
      // structure (sun + belt) carries the meaning. Hub orbital rings
      // remain — they suit this aesthetic and read as the orbital
      // paths of a genuine star chart.
      const hubList = simRef.current?.componentHubs() ?? [];
      const nodeMap = nodeById;
      const hubs = hubList.map((h) => {
        const sn = nodeMap.get(h.hubId);
        const note = sn ? notesById?.[sn.id] : undefined;
        const key = note
          ? pickCategoryKey(note.tags ?? null, !!note.pinned)
          : "_default";
        return {
          x: h.hubX,
          y: h.hubY,
          radius: h.avgRadius,
          size: h.size,
          color: ringForKey(key),
        };
      });
      drawGraph(ctx, {
        width: size.width,
        height: size.height,
        dpr: dprRef.current,
        view,
        nodes: drawNodesPoolRef.current,
        edges: drawEdgesPoolRef.current,
        hubs,
        glyphZoomThreshold: 0.6,
      } satisfies DrawState);
      needsRedrawRef.current = false;

      frameRef.current = requestAnimationFrame(loop);
    };
    frameRef.current = requestAnimationFrame(loop);
    return () => {
      alive = false;
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
    };
  }, [sim, size.width, size.height, view, patchPoolPositions]);


  if (isEmptyGraph) {
    // Render the empty-state placeholder at the END (after all hooks
    // ran) so the early return doesn't skip later useMemo calls and
    // trigger React error #310 on the next render once data lands.
    return (
      <div className="flex items-center justify-center h-full text-text-muted text-sm gap-2">
        <span>No notes to graph yet. Save a note with a</span>
        <code className="text-accent">[[wikilink]]</code>
        <span>to start.</span>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-hidden lb-graph-canvas"
      // Single attribute pauses every CSS animation under the canvas
      // (sun pulse, orbit shimmer, edge flow, hub halo). Wired to the
      // user's freeze toggle so "Frozen" really means everything stops,
      // including the compositor-thread animations that don't go
      // through React.
      data-frozen={physicsFrozen ? "1" : "0"}
      style={{
        // Warm charcoal with the faintest ember glow at the core — the
        // "cozy observatory" atmosphere. No harsh black, no cold blue.
        background:
          "radial-gradient(ellipse at center, #1b1620 0%, #12101a 45%, #0a0910 100%)",
      }}
    >
      {/* Single canvas replaces ~thousands of SVG elements. drawGraph()
          paints every frame in the RAF effect above; pan/zoom apply via
          ctx.setTransform; hover/click/drag wire to canvas-level pointer
          events with distance-based hit-testing (findNodeAt). The
          surrounding `lb-graph-canvas` container keeps the data-frozen
          attribute that pauses CSS keyframe animations. */}
      <canvas
        ref={canvasRef}
        className="block select-none cursor-grab active:cursor-grabbing"
        onWheel={handleWheel}
        onPointerDown={(e) => {
          const id = findNodeAt(e.clientX, e.clientY);
          if (id) {
            handlePointerDownNode(
              e as unknown as React.PointerEvent<SVGGElement>,
              id,
            );
          } else {
            handlePointerDownBg(
              e as unknown as React.PointerEvent<SVGElement>,
            );
            // Background pointerdown also clears any stale hover so the
            // tooltip doesn't linger when the user starts panning.
            if (!dragRef.current) clearHoverNow();
          }
        }}
        onPointerMove={(e) => {
          // Drag-driven pointermove takes precedence — never re-hit-test
          // mid-drag (causes flickering hover state under the cursor).
          if (!dragRef.current) {
            const id = findNodeAt(e.clientX, e.clientY);
            const cur = hoverIdRef.current;
            if (id !== cur) {
              if (id) {
                handleNodeEnter(id);
              } else if (cur) {
                handleNodeLeave(cur);
              }
            }
          }
          handlePointerMove(e);
        }}
        onPointerUp={handlePointerUp}
        onPointerLeave={() => {
          handlePointerUp();
          clearHoverNow();
        }}
        onClick={(e) => {
          if (justDraggedRef.current || dragRef.current) {
            justDraggedRef.current = false;
            return;
          }
          const id = findNodeAt(e.clientX, e.clientY);
          if (id) {
            onSelect?.(id);
          } else {
            // Background click — hand off to the existing peek-clear path.
            onClearPeek?.();
          }
        }}
        onDoubleClick={(e) => {
          if (justDraggedRef.current || dragRef.current) return;
          const id = findNodeAt(e.clientX, e.clientY);
          if (id && onPeek) onPeek(id);
        }}
      />

      {/* Micro-tooltip — minimalist card on hover. Only what the user
          can use at-a-glance: topic title + last-edited time. The bigger
          peek modal (double-click) carries the full body / actions. */}
      {hoverId && hoverNote && tooltipPos && !(
        dragRef.current?.kind === "node" && dragRef.current.maxMoved > DRAG_THRESHOLD
      ) && (() => {
        // Same display-label helper the dot uses, so legacy "topic rollup:"
        // prefixes get stripped and topic slugs come out title-cased.
        const { primary: hoverPrimary, suffix: hoverSuffix } =
          displayLabelForNote(hoverNote);
        // "Edited 2h ago" — relative-time, only one row of metadata.
        // Falls back to created_at when updated_at is missing.
        const editedAt = hoverNote.updated_at || hoverNote.created_at;
        const editedLabel = editedAt
          ? formatRelativeShort(new Date(editedAt + (editedAt.endsWith("Z") ? "" : "Z")))
          : null;
        const tagsLower = safeTagsLower(hoverNote.tags);
        const doneByTag = tagsLower.includes("done") || tagsLower.includes("completed");
        const doneByCheckbox = /^\s*-\s*\[x\]/im.test(hoverNote.content || "");
        const catKey = pickCategoryKey(hoverNote.tags, !!hoverNote.pinned);
        const isTask = catKey === "task";
        const isDone = isTask && (doneByTag || doneByCheckbox);
        return (
          <div
            className="lb-tooltip absolute z-20 pointer-events-none rounded-md border bg-bg-secondary/95 backdrop-blur shadow-2xl px-3 py-2 max-w-[260px] animate-fade-in"
            style={{
              left: Math.max(8, Math.min(size.width - 268, tooltipPos.sx)),
              top: Math.max(8, Math.min(size.height - 64, tooltipPos.sy)),
              borderColor: "rgba(168,144,106,0.32)",
            }}
          >
            {/* Title row — color dot + topic name. Brighter than the
                old 0.55/0.80-opacity styling so it actually reads on
                the dark canvas. */}
            <div className="flex items-center gap-2">
              <span
                className="w-2 h-2 rounded-full inline-block shrink-0"
                style={{
                  backgroundColor: colorForTags(hoverNote.tags, hoverNote.pinned).ring,
                  boxShadow: `0 0 6px ${colorForTags(hoverNote.tags, hoverNote.pinned).ring}99`,
                }}
              />
              {hoverNote.pinned && (
                <Star size={11} strokeWidth={2} fill="#fbbf24" color="#fbbf24" />
              )}
              <div
                className="text-[13px] font-semibold truncate flex-1 tracking-tight leading-tight"
                style={{
                  fontFamily: "var(--font-display, inherit)",
                  // Bumped from `var(--color-text-primary)` → explicit
                  // high-contrast cream so the title is unmistakably
                  // readable over the translucent card backdrop.
                  color: isDone ? "rgba(245,225,193,0.65)" : "#f5e1c1",
                  textDecoration: isDone ? "line-through" : "none",
                  textDecorationColor: "rgba(110,181,131,0.85)",
                  textDecorationThickness: "1.5px",
                }}
              >
                {hoverPrimary}
              </div>
            </div>
            {/* Optional rollup suffix (e.g. "W23 · 8 notes"). Only renders
                when displayLabelForNote produced one. */}
            {hoverSuffix && (
              <div
                className="text-[10px] mt-0.5 truncate"
                style={{
                  color: "rgba(212,162,106,0.85)",
                  fontFamily: "var(--font-display, inherit)",
                  letterSpacing: "0.01em",
                }}
              >
                {hoverSuffix}
              </div>
            )}
            {editedLabel && (
              <div
                className="mt-1 text-[10px] flex items-center gap-1 tabular-nums"
                style={{
                  // Was `text-text-muted/80` (~0.55 alpha). Bumped to
                  // ~0.78 so the timestamp doesn't fade into the card.
                  color: "rgba(232,213,176,0.78)",
                }}
              >
                <Clock size={9} strokeWidth={1.9} />
                <span>edited {editedLabel}</span>
              </div>
            )}
          </div>
        );
      })()}

      {/* Floating search — anchored top-center, over the brain canvas.
          Connects to the parent via onSearchChange; the parent may also
          drive the same searchQ from its top bar — both stay in sync. */}
      {onSearchChange && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 w-[340px] max-w-[calc(100%-120px)]">
          <div
            className="relative flex items-center gap-2 h-10 px-3 rounded-full backdrop-blur-md transition-shadow"
            style={{
              background: "rgba(27, 22, 32, 0.82)",
              border: "1px solid rgba(168,144,106,0.28)",
              boxShadow:
                "0 10px 30px -12px rgba(0,0,0,0.5), inset 0 1px 0 rgba(240,228,200,0.04)",
            }}
          >
            <Search
              size={13}
              strokeWidth={1.9}
              className="text-text-muted shrink-0"
            />
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
                  style={{
                    color: matchCount > 0 ? "#d4a26a" : "rgba(200,180,140,0.55)",
                  }}
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

      {/* Stats HUD — observatory telemetry. Lives top-left. Shows:
          nodes · links · density · pinned · hubs · +today · [focus links] */}
      {(() => {
        const totalNodes = graph.nodes.length;
        const totalEdges = graph.edges.length;
        // Graph density — ratio of real edges to max possible edges (per-mille).
        const density =
          totalNodes > 1
            ? Math.round(
                (2000 * totalEdges) / (totalNodes * (totalNodes - 1)),
              ) / 10
            : 0;
        // Pinned + today's additions (last 24h) derived from notesById.
        let pinnedCount = 0;
        let todayCount = 0;
        const nowMs = Date.now();
        const DAY_MS = 24 * 60 * 60 * 1000;
        if (notesById) {
          for (const node of graph.nodes) {
            const n = notesById[node.id];
            if (!n) continue;
            if (n.pinned) pinnedCount++;
            const ts = n.updated_at || n.created_at;
            if (ts) {
              const t = new Date(ts).getTime();
              if (!isNaN(t) && nowMs - t < DAY_MS) todayCount++;
            }
          }
        }
        const hoverDegree = hoverId ? (degree[hoverId] ?? 0) : null;
        const hubCount = hubIds.size;
        return (
          <div className="absolute top-[58px] left-3 z-10 pointer-events-none">
            <div
              className="rounded-md backdrop-blur-md px-2.5 py-1.5 flex items-center gap-2.5 text-[10px] uppercase tracking-[0.12em] tabular-nums"
              style={{
                background: "rgba(27,22,32,0.82)",
                border: "1px solid rgba(168,144,106,0.22)",
                color: "rgba(232,213,176,0.86)",
                fontFamily: "var(--font-display, inherit)",
              }}
            >
              <div className="flex items-baseline gap-1">
                <span className="text-[13px] font-semibold text-[#f5d19a]">
                  {totalNodes}
                </span>
                <span className="opacity-60">nodes</span>
              </div>
              <span className="opacity-20">│</span>
              <div className="flex items-baseline gap-1">
                <span className="text-[13px] font-semibold text-[#d4a26a]">
                  {totalEdges}
                </span>
                <span className="opacity-60">links</span>
              </div>
              <span className="opacity-20">│</span>
              <div className="flex items-baseline gap-1" title="graph density (‰)">
                <span className="text-[13px] font-semibold text-[#a8906a]">
                  {density}‰
                </span>
                <span className="opacity-60">density</span>
              </div>
              {pinnedCount > 0 && (
                <>
                  <span className="opacity-20">│</span>
                  <div
                    className="flex items-baseline gap-1"
                    title="pinned notes"
                    style={{ color: "#fbbf24" }}
                  >
                    <span className="text-[13px] font-semibold">
                      ★ {pinnedCount}
                    </span>
                    <span className="opacity-70">pinned</span>
                  </div>
                </>
              )}
              <span className="opacity-20">│</span>
              <div
                className="flex items-baseline gap-1"
                title="hubs — top 5 most-connected notes"
              >
                <span className="text-[13px] font-semibold text-[#d4a26a]">
                  ◌ {hubCount}
                </span>
                <span className="opacity-60">hubs</span>
              </div>
              {todayCount > 0 && (
                <>
                  <span className="opacity-20">│</span>
                  <div
                    className="flex items-baseline gap-1"
                    title="notes touched in the last 24h"
                    style={{ color: "#7fd4a6" }}
                  >
                    <span className="text-[13px] font-semibold">
                      +{todayCount}
                    </span>
                    <span className="opacity-70">today</span>
                  </div>
                </>
              )}
              {hoverDegree !== null && (
                <>
                  <span className="opacity-20">│</span>
                  <div
                    className="flex items-baseline gap-1"
                    style={{ color: "#f0a060" }}
                  >
                    <span className="text-[13px] font-semibold">
                      {hoverDegree}
                    </span>
                    <span className="opacity-80">links in focus</span>
                  </div>
                </>
              )}
            </div>
          </div>
        );
      })()}

      {/* Top-left controls — freeze toggle + re-flow trigger. The old
          layout-mode toggle (Categories ↔ Neural-links) was removed when
          orbital mode went away; force layout is the only layout now. */}
      <div className="absolute top-3 left-3 z-10 flex items-center gap-1.5 text-[10px]">
        <button
          type="button"
          onClick={togglePhysicsFrozen}
          title={
            physicsFrozen
              ? "Physics frozen — click to resume spring layout"
              : "Physics on — click to lock all positions in place"
          }
          className={`flex items-center gap-1 rounded-full border border-border bg-bg-secondary/80 backdrop-blur px-2.5 py-1 transition-colors tracking-wide ${
            physicsFrozen
              ? "text-text-primary"
              : "text-text-muted hover:text-text-primary"
          }`}
          style={
            physicsFrozen
              ? { boxShadow: "inset 0 -2px 0 #6ec1d4" }
              : undefined
          }
        >
          <span aria-hidden>{physicsFrozen ? "❄" : "✺"}</span>
          <span>{physicsFrozen ? "Locked" : "Live"}</span>
        </button>
        <button
          type="button"
          onClick={() => {
            // Auto-unfreeze if the user clicks Re-flow while frozen —
            // otherwise the alpha bump would just sit unused. Mark it as
            // a user-initiated freeze change so the persistence layer
            // saves "live" instead of snapping back to frozen on reload.
            if (physicsFrozen) {
              physicsFrozenIsUserSetRef.current = true;
              setPhysicsFrozen(false);
            }
            simRef.current?.reflow();
          }}
          title="Re-flow layout — let the spring model redistribute every node from where it is now (~5s settle). Keeps any pinned nodes."
          className="flex items-center gap-1 rounded-full border border-border bg-bg-secondary/80 backdrop-blur px-2.5 py-1 text-text-muted hover:text-text-primary transition-colors tracking-wide"
        >
          <span aria-hidden>↻</span>
          <span>Re-flow</span>
        </button>
      </div>

      <div className="absolute top-3 right-3 flex flex-col gap-1 z-10">
        <IconButton
          onClick={() => setView((v) => ({ ...v, k: Math.min(MAX_ZOOM, v.k * 1.2) }))}
          title="Zoom in"
        >
          <Plus size={13} strokeWidth={1.9} />
        </IconButton>
        <IconButton
          onClick={() => setView((v) => ({ ...v, k: Math.max(MIN_ZOOM, v.k / 1.2) }))}
          title="Zoom out"
        >
          <Minus size={13} strokeWidth={1.9} />
        </IconButton>
        <IconButton
          onClick={() => {
            setView({ tx: 0, ty: 0, k: 1 });
            // Release any nodes the user had dragged out of their orbits.
            simRef.current?.nodes.forEach((n) => {
              if (n.pinned) simRef.current?.unpin(n.id);
            });
          }}
          title="Reset view"
        >
          <RotateCcw size={11} strokeWidth={1.9} />
        </IconButton>
      </div>

      {/* Interactive legend — doubles as a category filter. The header
          ("Filters" + chevron + all/none) is anchored at the BOTTOM of
          the panel where bottom-3 sits, so it stays visible no matter
          how many categories are listed. The category list grows
          UPWARD from the header and scrolls internally if it would
          otherwise overflow the viewport. */}
      {showLegend && (
        <div className="absolute bottom-3 left-3 z-10 text-xs w-[200px] flex flex-col max-h-[calc(100vh-7rem)]">
          {legendOpen && (
            <div className="border border-b-0 border-border bg-bg-secondary rounded-t-lg p-1.5 space-y-0.5 flex-1 min-h-0 overflow-y-auto">
              {FILTER_CATEGORIES.map((c) => {
                const hidden = hiddenCategories?.has(c.key) ?? false;
                const count = categoryCounts?.[c.key] ?? 0;
                const clickable = !!onToggleCategory;
                return (
                  <button
                    key={c.key}
                    onClick={() => clickable && onToggleCategory?.(c.key)}
                    disabled={!clickable}
                    className={`w-full flex items-center gap-2 px-2 py-1 rounded text-[11px] transition-colors ${
                      hidden
                        ? "opacity-40 text-text-muted"
                        : "text-text-secondary hover:bg-bg-hover hover:text-text-primary"
                    } ${clickable ? "cursor-pointer" : "cursor-default"}`}
                    title={
                      clickable
                        ? hidden
                          ? `Show ${c.label}`
                          : `Hide ${c.label}`
                        : c.label
                    }
                  >
                    <CategoryIcon keyName={c.key} size={12} color={hidden ? undefined : c.ring} />
                    <span className={hidden ? "line-through" : ""}>{c.label}</span>
                    {count > 0 && (
                      <span className="ml-auto opacity-60 tabular-nums">{count}</span>
                    )}
                  </button>
                );
              })}
              <div className="flex items-center gap-2 px-2 py-1 text-[11px] text-text-muted pt-1.5 mt-1 border-t border-border">
                <Star size={12} strokeWidth={1.75} fill="#fbbf24" color="#fbbf24" />
                <span>Pinned (halo)</span>
              </div>
            </div>
          )}

          {/* Header — always visible at the BOTTOM of the panel so the
              user can always reach the toggle, all / none controls, and
              chevron, even when the list above is scrolling. */}
          <div
            className={`flex items-center justify-between gap-2 px-3 py-1.5 ${
              legendOpen ? "rounded-b-lg" : "rounded-lg"
            } bg-bg-secondary border border-border shrink-0`}
          >
            <button
              onClick={() => setLegendOpen((v) => !v)}
              className="flex items-center gap-2 text-text-secondary hover:text-text-primary transition-colors flex-1"
            >
              <span className="w-2 h-2 rounded-full inline-block bg-accent" />
              <span className="font-medium">Filters</span>
            </button>
            {legendOpen && onToggleCategory && onSetHiddenCategories && (
              <div className="flex items-center gap-1 text-[10px]">
                <button
                  onClick={() => onSetHiddenCategories(new Set())}
                  className="px-1.5 py-0.5 rounded hover:bg-bg-hover text-text-muted hover:text-accent transition-colors"
                  title="Show all categories"
                >
                  all
                </button>
                <span className="text-text-muted/40">·</span>
                <button
                  onClick={() =>
                    onSetHiddenCategories(new Set(FILTER_CATEGORIES.map((c) => c.key)))
                  }
                  className="px-1.5 py-0.5 rounded hover:bg-bg-hover text-text-muted hover:text-accent transition-colors"
                  title="Hide all categories"
                >
                  none
                </button>
              </div>
            )}
            <button
              onClick={() => setLegendOpen((v) => !v)}
              className="text-text-muted hover:text-text-primary transition-colors"
              title={legendOpen ? "Collapse filter list" : "Expand filter list"}
            >
              {legendOpen ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
            </button>
          </div>
        </div>
      )}

      {/* Interaction hint */}
      <div className="absolute bottom-3 right-3 flex items-center gap-3 text-[10px] text-text-muted bg-bg-secondary/70 backdrop-blur px-2.5 py-1.5 rounded border border-border z-10">
        <span className="flex items-center gap-1">
          <ZoomIn size={10} strokeWidth={1.75} />
          <span>scroll</span>
        </span>
        <span className="flex items-center gap-1">
          <Move size={10} strokeWidth={1.75} />
          <span>drag</span>
        </span>
        <span className="flex items-center gap-1">
          <MousePointer2 size={10} strokeWidth={1.75} />
          <span>hover</span>
        </span>
      </div>
    </div>
  );
}

function IconButton({
  onClick,
  title,
  children,
}: {
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="w-8 h-8 rounded bg-bg-secondary border border-border text-text-secondary hover:text-text-primary hover:border-border-light transition-colors flex items-center justify-center"
    >
      {children}
    </button>
  );
}

