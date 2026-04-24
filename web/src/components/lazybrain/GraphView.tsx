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
import {
  CATEGORY_PRIORITY,
  FILTER_CATEGORIES,
  colorForTags,
  categoryKeysFor,
  categoryMatchCount,
  ringForKey,
} from "./noteColors";
import { CategoryIcon, Star, CATEGORY_ICONS, DEFAULT_CATEGORY_ICON } from "./icons";
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
const POSITIONS_KEY_PREFIX = "lazybrain-graph-positions:";

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
function pickCategoryKey(
  tags: string[] | null | undefined,
  pinned: boolean,
): string {
  if (pinned) return "pinned";
  if (!tags || tags.length === 0) return "_default";
  const lower = tags.map((t) => t.toLowerCase());
  for (const key of CATEGORY_PRIORITY) {
    if (lower.includes(key)) return key;
    if (lower.some((t) => t.startsWith(`${key}/`))) return key;
  }
  return "_default";
}

/** Which concentric orbit a category lives on.
 *  0 CORE — urgent anchors (pinned, deadlines)
 *  1 DOING — active work (tasks, decisions, commands)
 *  2 LIVED — reflection (journal, daily-log, memory)
 *  3 LEARNED — knowledge (lessons, facts, ideas)
 */
const CATEGORY_ORBIT: Record<string, number> = {
  pinned: 0,
  deadline: 0,
  task: 1,
  decision: 1,
  price: 1,
  command: 1,
  recipe: 1,
  contact: 1,
  journal: 2,
  "daily-log": 2,
  memory: 2,
  "site-memory": 2,
  rollup: 2,
  context: 2,
  imported: 2,
  auto: 2,
  lesson: 3,
  til: 3,
  fact: 3,
  idea: 3,
  reference: 3,
  layer: 3,
  survival: 3,
  learned_preference: 3,
  _default: 3,
};

/** Labels drawn along each orbit ring (upper case). Only used in
 *  category layout mode — neural-link mode hides the orbit chrome
 *  entirely since its layout is force-directed, not ring-based. */
const ORBIT_LABELS: { title: string; subtitle: string; color: string }[] = [
  { title: "CORE",      subtitle: "URGENT",    color: "#f0a060" },
  { title: "DOING",     subtitle: "TASKS",     color: "#d4a26a" },
  { title: "LIVED",     subtitle: "MEMORY",    color: "#a8906a" },
  { title: "LEARNED",   subtitle: "KNOWLEDGE", color: "#8a7a6a" },
];

const BADGE_MAP: Record<string, string> = {
  task: "T",
  deadline: "!",
  journal: "J",
  "daily-log": "D",
  lesson: "L",
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
  survival: "S",
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

/** 1–5 char badge that goes INSIDE the dot. Journal / daily-log nodes show
 *  `DD/MM` (European format — matches the user's Madrid locale); every other
 *  category renders its letter/symbol from BADGE_MAP. */
function dotBadge(
  title: string | null | undefined,
  categoryKey: string,
): string {
  if (categoryKey === "journal" || categoryKey === "daily-log") {
    // Trailing `MM-DD` — short titles like "Journal 04-21".
    const m = (title || "").match(/(\d{2})-(\d{2})$/);
    if (m) return `${m[2]}/${m[1]}`;
    // Full `YYYY-MM-DD` anywhere in the title.
    const m2 = (title || "").match(/\d{4}-(\d{2})-(\d{2})/);
    if (m2) return `${m2[2]}/${m2[1]}`;
  }
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
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const simRef = useRef<ForceSimulation | null>(null);
  const frameRef = useRef<number | null>(null);

  const [size, setSize] = useState({ width: 800, height: 600 });
  const [view, setView] = useState({ tx: 0, ty: 0, k: 1 });
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [legendOpen, setLegendOpen] = useState(true);
  const [, setTick] = useState(0);
  // Pulse ticker — dedicated 60fps loop that advances the traveling
  // synapse signals. Only runs while there's a focus so idle CPU is zero.
  const [pulseTick, setPulseTick] = useState(0);

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

  // ── Layout mode — "category" (orbits by note kind) or "neural-link"
  //     (force-directed clustering with a decorative sun at center).
  //     Flipped via the on-canvas toggle below. Persisted in localStorage
  //     so the user's last choice is the default on next reload.
  const [layoutMode, setLayoutMode] = useState<"category" | "neural-link">(() => {
    if (typeof window === "undefined") return "category";
    try {
      const saved = window.localStorage.getItem("lazybrain-layout-mode");
      return saved === "neural-link" || saved === "category" ? saved : "category";
    } catch {
      return "category";
    }
  });
  useEffect(() => {
    try {
      window.localStorage.setItem("lazybrain-layout-mode", layoutMode);
    } catch {
      // Private-mode Safari throws on setItem — session-only is fine.
    }
  }, [layoutMode]);

  // Top hub — highest-degree node. Used as the "sun" in neural-link mode.
  // Computed from graph.edges directly so it's available before the sim.
  const topHubId = useMemo(() => {
    const deg: Record<string, number> = {};
    for (const e of graph.edges) {
      deg[e.source] = (deg[e.source] ?? 0) + 1;
      deg[e.target] = (deg[e.target] ?? 0) + 1;
    }
    let best: string | null = null;
    let bestDeg = -1;
    for (const [id, d] of Object.entries(deg)) {
      if (d > bestDeg) { best = id; bestDeg = d; }
    }
    return best;
  }, [graph.edges]);

  // ── Build simulation when graph shape or layout mode changes ──────────
  // Note: size.width/height are intentionally NOT dependencies. Resizes
  // go through sim.resize() (below) which rescales positions in place
  // instead of discarding them. Rebuilding on resize made window drags
  // feel like a full reset every time.
  const sim = useMemo(() => {
    const useForce = layoutMode === "neural-link" && !!topHubId;
    // Category-bucketed orbits (used by orbital mode + as fallback).
    const orbitOf = (id: string): number => {
      const note = notesById?.[id];
      const key = pickCategoryKey(note?.tags, !!note?.pinned);
      return CATEGORY_ORBIT[key] ?? CATEGORY_ORBIT._default;
    };
    // Synchronous localStorage read so the first frame already has the
    // previously-settled layout. The async server fetch below overlays
    // anything newer — server is authoritative, localStorage is a cache.
    const savedPositions = readPositionsFromLocalStorage(layoutMode);
    const s = new ForceSimulation(
      graph.nodes.map((n) => ({ id: n.id, pinned: false })),
      graph.edges.map((e) => ({ source: e.source, target: e.target })),
      {
        width: size.width,
        height: size.height,
        orbitOf,
        mode: useForce ? "force" : "orbital",
        savedPositions,
        // No forced sun at center — let the most-connected note end up
        // central naturally via gravity + degree, not pinned. Forcing
        // the top-degree node (which can be a random task) was the
        // mistake the user flagged.
      },
    );
    simRef.current = s;
    return s;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, layoutMode, topHubId]);

  // Forward size changes to the existing sim so positions are preserved.
  useEffect(() => {
    simRef.current?.resize(size.width, size.height);
  }, [size.width, size.height]);

  // Async server overlay — fetch saved positions on mount + mode change
  // and apply them on top of the localStorage cache. Server wins on
  // conflict; unknown nodes (new since last save) keep their seed.
  useEffect(() => {
    let cancelled = false;
    const mode: GraphLayoutMode =
      layoutMode === "neural-link" ? "neural-link" : "category";
    getGraphPositions(mode)
      .then((res) => {
        if (cancelled) return;
        const positions = res.positions ?? {};
        if (Object.keys(positions).length === 0) return;
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
  // into the sim so non-isolated rings slow to near-stop.
  const [isolatedOrbit, setIsolatedOrbit] = useState<number | null>(null);
  useEffect(() => {
    simRef.current?.isolateOrbit(isolatedOrbit);
  }, [isolatedOrbit, sim]);

  // Hub ids — top-5 by degree. Rendered with a warm pulse ring.
  const hubIds = useMemo(() => new Set(sim.hubIds(5)), [sim]);

  // ── Pulse loop — advances a per-frame counter used to animate synapse
  //    signals. Always-on at ~30fps (skips every other RAF) so the graph
  //    feels alive in the background even when the user isn't hovering.
  //    162 edges × 1 pulse ≈ 162 circles/frame — well within budget.
  useEffect(() => {
    let raf = 0;
    let skip = false;
    const loop = () => {
      skip = !skip;
      if (!skip) {
        setPulseTick((t) => (t + 1) % 1_000_000);
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  // ── Starfield — deterministic seeded warm specks behind the graph.
  //    Sparse atmospheric dust, not a Webb deep-field. Pure cosmetic.
  const starfield = useMemo(() => {
    const seed = 0x9e3779b9 ^ graph.nodes.length;
    let a = seed;
    const rand = () => {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    const stars: { x: number; y: number; r: number; o: number }[] = [];
    for (let i = 0; i < 55; i++) {
      stars.push({
        x: (rand() - 0.5) * 2400,
        y: (rand() - 0.5) * 2400,
        r: 0.5 + rand() * 0.8,
        o: 0.04 + rand() * 0.07,
      });
    }
    return stars;
  }, [graph]);

  // Memoized starfield JSX — computed once per graph. 55 tiny dots never
  // need to reconcile on every animation tick; keeping this memo stops
  // React from walking them at 30fps for zero benefit.
  const starfieldJsx = useMemo(
    () => (
      <g pointerEvents="none" aria-hidden>
        {starfield.map((s, i) => (
          <circle
            key={`star-${i}`}
            cx={s.x}
            cy={s.y}
            r={s.r}
            fill="#e8d5b0"
            opacity={s.o}
          />
        ))}
      </g>
    ),
    [starfield],
  );

  // ── Territorial backdrop ────────────────────────────────────────────────
  // Faint white dot grid + 4 thin radial sector divider lines. Together they
  // give the canvas a sense of mapped territory (Obsidian-style cartography)
  // without ever competing with the nodes. Memoized by canvas size so the
  // 200+ background circles don't reconcile per animation tick.
  const territorialBackdrop = useMemo(() => {
    const span = Math.max(size.width, size.height) * 1.6;
    const step = 70;
    const dots: { x: number; y: number }[] = [];
    for (let x = -span; x <= span; x += step) {
      for (let y = -span; y <= span; y += step) {
        dots.push({ x, y });
      }
    }
    const cx = size.width / 2;
    const cy = size.height / 2;
    const sectorLen = Math.max(size.width, size.height) * 0.7;
    return (
      <g pointerEvents="none" aria-hidden>
        {/* White dot grid — sparse, almost-invisible territory markers. */}
        {dots.map((d, i) => (
          <circle
            key={`td-${i}`}
            cx={d.x}
            cy={d.y}
            r={0.6}
            fill="#f5ecd0"
            opacity={0.06}
          />
        ))}
        {/* Sector divider lines — at 45/135/225/315° so they sit BETWEEN
            the orbit labels (which live at 12 o'clock). Dashed, near-zero
            opacity, just enough to suggest "this side vs. that side". */}
        {[45, 135, 225, 315].map((deg) => {
          const rad = (deg * Math.PI) / 180;
          return (
            <line
              key={`sec-${deg}`}
              x1={cx}
              y1={cy}
              x2={cx + Math.cos(rad) * sectorLen}
              y2={cy + Math.sin(rad) * sectorLen}
              stroke="#f5ecd0"
              strokeOpacity={0.04}
              strokeWidth={0.6}
              strokeDasharray="1 14"
            />
          );
        })}
      </g>
    );
  }, [size.width, size.height]);

  // ── Animation loop ─────────────────────────────────────────────────────
  // Two regimes:
  //   - HOT (orbital mode, or force mode still settling): ~15fps React
  //     reconciles so motion stays visible.
  //   - COOLED (force mode, equilibrium reached): physics is skipped
  //     internally; we also skip React reconciles entirely for ~60 ticks,
  //     then fire one setTick so the slow rigid rotation advances. Net
  //     effect: idle CPU drops from ~15fps-of-React to ~0.5fps, but the
  //     constellation still visibly revolves over ~150s.
  useEffect(() => {
    let alive = true;
    let skip = false;
    let idleTicks = 0;
    const COOLED_REDRAW_EVERY = 60; // ~2s between React reconciles at 30fps
    const loop = () => {
      if (!alive) return;
      const s = simRef.current;
      if (!s) return;
      const cooled = s.cooled();
      if (cooled) {
        // Skip the expensive force pass — already gated inside s.step().
        // Run the O(N) rotation only every Nth tick so we aren't mutating
        // positions 60× per second just to draw a picture that didn't
        // meaningfully change. Then reconcile React at the same low cadence.
        idleTicks += 1;
        if (idleTicks >= COOLED_REDRAW_EVERY) {
          idleTicks = 0;
          s.step();
          setTick((t) => (t + 1) % 1_000_000);
        }
      } else {
        s.step();
        skip = !skip;
        if (!skip) setTick((t) => (t + 1) % 1_000_000);
        idleTicks = 0;
      }
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
  }, [sim]);

  const nodeById = useMemo(() => {
    const m = new Map<string, SimNode>();
    sim.nodes.forEach((n) => m.set(n.id, n));
    return m;
  }, [sim]);

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

  // ── BFS depth from focus node. Focus = hovered (strong dim) OR selected
  //    (soft dim). Isolated focus nodes (no neighbors) skip dimming.
  const focusId = hoverId ?? selectedId ?? null;
  const isHoverFocus = !!hoverId;
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
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const rect = svg.getBoundingClientRect();
    return { x: cx - rect.left, y: cy - rect.top };
  }, []);

  // ── Wheel zoom-to-cursor ───────────────────────────────────────────────
  const handleWheel = useCallback(
    (e: React.WheelEvent<SVGSVGElement>) => {
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
          // Wake the force sim — dragging a node should re-stir its
          // neighbors via the spring forces.
          sim.warm();
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
    simRef.current?.warm();
  }, []);

  const handleNodeEnter = useCallback((id: string) => {
    cancelHoverClear();
    setHoverId(id);
    simRef.current?.setHover(id);
    // Wake the sim so the renderer paints the hover halo on the next frame.
    simRef.current?.warm();
  }, []);

  const handleNodeLeave = useCallback((id: string) => {
    // Tiny grace so a fast cursor crossing a 1-2px gap between sibling
    // nodes doesn't flicker the tooltip off and back on.
    cancelHoverClear();
    hoverClearRef.current = window.setTimeout(() => {
      hoverClearRef.current = null;
      setHoverId((cur) => {
        if (cur === id) {
          simRef.current?.setHover(null);
          simRef.current?.warm();
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
        if (isolatedOrbit !== null) setIsolatedOrbit(null);
        if (highlightQuery && onSearchChange) onSearchChange("");
      } else if (e.key === "/" && onSearchChange) {
        e.preventDefault();
        const input = containerRef.current?.querySelector<HTMLInputElement>(
          "input.lb-floating-search",
        );
        input?.focus();
      } else if (/^[1-4]$/.test(e.key)) {
        const idx = Number(e.key) - 1;
        setIsolatedOrbit((cur) => (cur === idx ? null : idx));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isolatedOrbit, highlightQuery, onSearchChange, selectedId, onClearPeek]);

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

  if (graph.nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-text-muted text-sm gap-2">
        <span>No notes to graph yet. Save a note with a</span>
        <code className="text-accent">[[wikilink]]</code>
        <span>to start.</span>
      </div>
    );
  }

  // Labels — Logseq-style, always visible. Overlap is tolerated; clarity
  // wins over a clean layout. User can zoom/pan to resolve overlaps.
  const hoverNote = hoverId ? notesById?.[hoverId] : undefined;

  // Compute per-node opacity using depth, filter, and search.
  // Hover dims hard (0.08) for strong momentary focus. Selection dims soft
  // (0.35) so the whole graph stays readable while your current page is
  // clearly highlighted.
  const NON_FOCUS_DIM = isHoverFocus ? 0.08 : 0.35;
  /** Dim factor for orbits other than the isolated one (null = no isolation). */
  const orbitDim = (noteId: string): number => {
    if (isolatedOrbit === null) return 1;
    const sn = nodeById.get(noteId);
    if (!sn) return 1;
    return sn.orbit === isolatedOrbit ? 1 : 0.12;
  };
  const opacityFor = (noteId: string): number => {
    const oDim = orbitDim(noteId);
    if (noteId === hoverId) return 1; // hovered always fully lit
    const note = notesById?.[noteId];
    const dimmed = dimPredicate?.(note) ?? false;
    const match = matcher ? matcher(noteId) : true;
    if (dimmed || !match) return 0.08 * oDim;
    if (depths) {
      const d = depths.get(noteId);
      if (d === undefined) return NON_FOCUS_DIM * oDim;
      if (d === 0) return 1;
      if (d === 1) return 0.95 * oDim;
      if (d === 2) return 0.7 * oDim;
      return Math.max(NON_FOCUS_DIM, 0.45) * oDim;
    }
    return 0.85 * oDim;
  };

  const edgeState = (src: string, tgt: string) => {
    const srcNote = notesById?.[src];
    const tgtNote = notesById?.[tgt];
    const dimA = dimPredicate?.(srcNote) ?? false;
    const dimB = dimPredicate?.(tgtNote) ?? false;
    const matchA = matcher ? matcher(src) : true;
    const matchB = matcher ? matcher(tgt) : true;
    if (dimA || dimB || !(matchA || matchB)) {
      return { opacity: 0.03, stroke: "var(--color-text-muted)", width: 0.8 };
    }
    if (depths) {
      const dSrc = depths.get(src);
      const dTgt = depths.get(tgt);
      if (dSrc === undefined && dTgt === undefined) {
        // Edge not in focus neighborhood
        return {
          opacity: isHoverFocus ? 0.04 : 0.12,
          stroke: "var(--color-text-muted)",
          width: 0.8,
        };
      }
      const minD = Math.min(dSrc ?? 99, dTgt ?? 99);
      if (minD === 0) return { opacity: 1, stroke: "var(--color-accent)", width: 2 };
      if (minD === 1) return { opacity: 0.7, stroke: "var(--color-accent)", width: 1.5 };
      return { opacity: 0.3, stroke: "var(--color-accent-dim)", width: 1 };
    }
    return { opacity: 0.16, stroke: "var(--color-text-muted)", width: 1 };
  };

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-hidden"
      style={{
        // Warm charcoal with the faintest ember glow at the core — the
        // "cozy observatory" atmosphere. No harsh black, no cold blue.
        background:
          "radial-gradient(ellipse at center, #1b1620 0%, #12101a 45%, #0a0910 100%)",
      }}
    >
      <svg
        ref={svgRef}
        width={size.width}
        height={size.height}
        className="block select-none cursor-grab active:cursor-grabbing"
        onWheel={handleWheel}
        onPointerDown={handlePointerDownBg}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={() => {
          // Cursor left the SVG entirely — clear drag and hover both,
          // otherwise the last hover tooltip + pin can stick around.
          handlePointerUp();
          clearHoverNow();
        }}
      >
        <defs>
          {/* Soft synapse bloom — lifts edges + node halos just enough so
              the graph reads as luminous wire rather than painted ink. */}
          <filter id="lb-neural-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="1.2" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          {/* Slightly stronger halo for the central ember core. */}
          <filter id="lb-core-glow" x="-75%" y="-75%" width="250%" height="250%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="8" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          {/* Warm ember radial — the central glow. Candlelight, not neon. */}
          <radialGradient id="lb-ember" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#d4a26a" stopOpacity="0.55" />
            <stop offset="40%" stopColor="#a8754c" stopOpacity="0.22" />
            <stop offset="100%" stopColor="#a8754c" stopOpacity="0" />
          </radialGradient>
        </defs>

        <rect
          x={0}
          y={0}
          width={size.width}
          height={size.height}
          fill="transparent"
          className="lb-bg"
          onPointerDown={handlePointerDownBg}
          onPointerEnter={() => {
            // Cursor crossed into bare canvas — drop any lingering hover
            // even if a per-node leave was missed (fast-move, scaled halos).
            if (!dragRef.current) clearHoverNow();
          }}
        />

        <g transform={`translate(${view.tx} ${view.ty}) scale(${view.k})`}>
          {/* Sparse warm motes — atmospheric dust, not stars. Memoized
              upstream so 55 dots don't walk React each animation tick. */}
          {starfieldJsx}

          {/* Territorial dot grid + sector divider lines — Obsidian-style
              faint cartography behind everything. Both layers are memoized
              upstream + drawn at very low opacity so they read as "this
              canvas has territory" without ever competing with the nodes. */}
          {territorialBackdrop}

          {/* Decorative sun — ember core at canvas center. Shown in BOTH
              modes because both modes orbit around the center: orbital
              mode has concentric rings around it, force mode has a
              force-directed cluster slowly rotating around it. The sun
              is never a real note (that was the "big task at center"
              mistake) — just a gravitational anchor visual.           */}
          {layoutMode === "neural-link" && (() => {
            const { cx, cy } = sim.center();
            const coreBreath = 1 + 0.05 * Math.sin(pulseTick * 0.03);
            const coreR = 24 * coreBreath;
            return (
              <g pointerEvents="none" aria-hidden>
                <circle
                  cx={cx}
                  cy={cy}
                  r={coreR * 3.4}
                  fill="url(#lb-ember)"
                  opacity={0.82}
                />
                <circle
                  cx={cx}
                  cy={cy}
                  r={coreR}
                  fill="#d4a26a"
                  opacity={0.3}
                  filter="url(#lb-core-glow)"
                />
                <circle
                  cx={cx}
                  cy={cy}
                  r={coreR * 0.42}
                  fill="#f5d19a"
                  opacity={0.55}
                />
              </g>
            );
          })()}

          {/* Observatory furniture — orbit guide rings, ring labels, ember
              core, "now" tick. Only in category mode (orbital layout).  */}
          {layoutMode === "category" && (() => {
            const { cx, cy } = sim.center();
            const radii = sim.orbitRadii();
            const coreBreath = 1 + 0.05 * Math.sin(pulseTick * 0.03);
            const coreR = 24 * coreBreath;
            return (
              <g pointerEvents="none" aria-hidden>
                {/* Orbit guide rings — dashed warm strokes in each ring's
                    label color. Subtle, never compete with the nodes. */}
                {radii.map((r, i) => (
                  <circle
                    key={`orbit-${i}`}
                    cx={cx}
                    cy={cy}
                    r={r}
                    fill="none"
                    stroke={ORBIT_LABELS[i].color}
                    strokeOpacity={
                      isolatedOrbit === null
                        ? 0.10 + i * 0.012
                        : isolatedOrbit === i
                          ? 0.32
                          : 0.04
                    }
                    strokeWidth={isolatedOrbit === i ? 1.2 : 0.8}
                    strokeDasharray={isolatedOrbit === i ? "3 5" : "2 7"}
                  />
                ))}
                {/* Orbit labels — static text parked at the TOP of each ring,
                    OUTSIDE the orbit path. Empty orbits are skipped. */}
                {(() => {
                  const metas = sim.orbitMeta();
                  return radii.map((r, i) => {
                    if ((metas[i]?.nodeCount ?? 0) === 0) return null;
                    if (r < 80) return null;
                    const op =
                      isolatedOrbit === null
                        ? 0.6
                        : isolatedOrbit === i
                          ? 0.95
                          : 0.14;
                    const label = ORBIT_LABELS[i];
                    return (
                      <text
                        key={`orbit-label-${i}`}
                        x={cx}
                        y={cy - r - 8}
                        textAnchor="middle"
                        fill={label.color}
                        opacity={op}
                        style={{
                          fontSize: 10,
                          fontWeight: 600,
                          letterSpacing: "0.28em",
                          fontFamily: "var(--font-display, inherit)",
                        }}
                      >
                        {label.title}
                        <tspan
                          dx={6}
                          fill={label.color}
                          opacity={0.6}
                          style={{ fontSize: 9, fontWeight: 500 }}
                        >
                          · {label.subtitle}
                        </tspan>
                      </text>
                    );
                  });
                })()}
                {/* Outer ember halo + warm core + hot center pupil. Only
                    visible in category mode; neural-link mode hides the
                    whole observatory chrome since the hub star is the
                    actual center anchor there. */}
                <circle
                  cx={cx}
                  cy={cy}
                  r={coreR * 3.4}
                  fill="url(#lb-ember)"
                  opacity={0.82}
                />
                <circle
                  cx={cx}
                  cy={cy}
                  r={coreR}
                  fill="#d4a26a"
                  opacity={0.3}
                  filter="url(#lb-core-glow)"
                />
                <circle
                  cx={cx}
                  cy={cy}
                  r={coreR * 0.42}
                  fill="#f5d19a"
                  opacity={0.55}
                />
                {/* "Now" marker — a small amber tick at 12 o'clock on the
                    innermost orbit. Represents the current focus axis. */}
                <g transform={`translate(${cx} ${cy - radii[0]})`}>
                  <line
                    x1={0}
                    y1={-6}
                    x2={0}
                    y2={6}
                    stroke="#f5d19a"
                    strokeWidth={1.5}
                    strokeOpacity={0.9}
                    strokeLinecap="round"
                  />
                  <circle
                    cx={0}
                    cy={-10}
                    r={2.2}
                    fill="#f5d19a"
                    opacity={0.95}
                  />
                </g>
              </g>
            );
          })()}

          {/* Edges — thin curved synapse arcs. Each path is a quadratic
              Bezier bowed gently perpendicular to the straight line, so as
              the orbits rotate, the arcs sweep and flex organically. No
              blur halo — the cozy palette doesn't need it, and removing it
              drops visual noise massively. */}
          {graph.edges.map((edge, idx) => {
            const a = nodeById.get(edge.source);
            const b = nodeById.get(edge.target);
            if (!a || !b) return null;
            const srcNote = notesById?.[edge.source];
            const tgtNote = notesById?.[edge.target];
            const srcColor = srcNote ? colorForTags(srcNote.tags, srcNote.pinned).ring : "#6b5e4a";
            const tgtColor = tgtNote ? colorForTags(tgtNote.tags, tgtNote.pinned).ring : "#6b5e4a";
            return (
              <linearGradient
                key={`g-${idx}`}
                id={`e-grad-${idx}`}
                gradientUnits="userSpaceOnUse"
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
              >
                <stop offset="0%" stopColor={srcColor} stopOpacity={0.78} />
                <stop offset="100%" stopColor={tgtColor} stopOpacity={0.78} />
              </linearGradient>
            );
          })}
          {graph.edges.map((edge, idx) => {
            const a = nodeById.get(edge.source);
            const b = nodeById.get(edge.target);
            if (!a || !b) return null;
            const st = edgeState(edge.source, edge.target);
            const isActive =
              !!focusId &&
              depths !== null &&
              ((depths.get(edge.source) ?? 99) <= 1 ||
                (depths.get(edge.target) ?? 99) <= 1);
            // Quadratic Bezier — bow the line perpendicular to the straight
            // path. Control point sits at the midpoint, pushed outward by
            // ~12% of the edge length. Side (sign) chosen deterministically
            // from the edge index so curves don't all bend the same way.
            const dx = b.x - a.x;
            const dy = b.y - a.y;
            const len = Math.hypot(dx, dy) || 1;
            const bow = len * 0.12 * (idx % 2 === 0 ? 1 : -1);
            const mx = (a.x + b.x) / 2 + (-dy / len) * bow;
            const my = (a.y + b.y) / 2 + (dx / len) * bow;
            const d = `M ${a.x} ${a.y} Q ${mx} ${my} ${b.x} ${b.y}`;
            // Very gentle breathing opacity — barely perceptible, just
            // enough to feel alive. No bright strobe.
            const phase = Math.sin((pulseTick + idx * 13) * 0.04);
            const dimmed = st.opacity < 0.1;
            const baseOp = dimmed
              ? st.opacity * 0.5
              : (isActive ? 0.72 : 0.34) + 0.06 * phase;
            return (
              <path
                key={`e-${idx}`}
                d={d}
                fill="none"
                stroke={`url(#e-grad-${idx})`}
                strokeWidth={isActive ? 1.4 : 0.95}
                strokeOpacity={baseOp}
                strokeLinecap="round"
                pointerEvents="none"
              />
            );
          })}

          {/* Traveling synapse motes — one soft dot per edge, follows the
              curve. Active (1-hop of focus) gets a warmer, slightly brighter
              mote. No paired flashes, no neon. */}
          {graph.edges.map((edge, idx) => {
            const a = nodeById.get(edge.source);
            const b = nodeById.get(edge.target);
            if (!a || !b) return null;
            const st = edgeState(edge.source, edge.target);
            if (st.opacity < 0.1) return null;
            const isActive =
              !!focusId &&
              depths !== null &&
              ((depths.get(edge.source) ?? 99) <= 1 ||
                (depths.get(edge.target) ?? 99) <= 1);
            // Active edges flow TOWARD the focus node. Ambient flow
            // alternates direction by edge index to avoid a uniform drift.
            let flipped = (idx % 2) === 0;
            if (isActive && depths) {
              const dSrc = depths.get(edge.source) ?? 99;
              const dTgt = depths.get(edge.target) ?? 99;
              flipped = dSrc < dTgt;
            }
            const fromX = flipped ? b.x : a.x;
            const fromY = flipped ? b.y : a.y;
            const toX   = flipped ? a.x : b.x;
            const toY   = flipped ? a.y : b.y;
            // Same bow as the curve so the mote rides the arc, not the chord
            const dx = toX - fromX;
            const dy = toY - fromY;
            const len = Math.hypot(dx, dy) || 1;
            const bow = len * 0.12 * (idx % 2 === 0 ? 1 : -1) * (flipped ? -1 : 1);
            const mx = (fromX + toX) / 2 + (-dy / len) * bow;
            const my = (fromY + toY) / 2 + (dx / len) * bow;
            // Cozy slow pace — every edge finishes a trip in ~3.5s (active)
            // or ~7s (ambient), offset per edge so motes don't pulse in sync.
            const dur = isActive ? 200 : 400;
            const t = ((pulseTick + idx * 17) % dur) / dur;
            // Quadratic Bezier point at parameter t.
            const omt = 1 - t;
            const px = omt * omt * fromX + 2 * omt * t * mx + t * t * toX;
            const py = omt * omt * fromY + 2 * omt * t * my + t * t * toY;
            const fill = isActive ? "#f5d19a" : "#d4b388";
            const op = isActive ? 0.75 : 0.28;
            const rOuter = isActive ? 2.4 : 1.6;
            const rInner = isActive ? 1.1 : 0.7;
            return (
              <g key={`pulse-${idx}`} pointerEvents="none">
                <circle cx={px} cy={py} r={rOuter} fill={fill} opacity={op * 0.5} />
                <circle cx={px} cy={py} r={rInner} fill="#fdf2d9" opacity={op} />
              </g>
            );
          })}

          {/* Nodes */}
          {graph.nodes.map((node) => {
            const sn = nodeById.get(node.id);
            if (!sn) return null;
            const note = notesById?.[node.id];
            const color = note
              ? colorForTags(note.tags, note.pinned)
              : { ring: "#475569", emoji: "", label: "Note" };
            const deg = degree[node.id] ?? 0;
            // Base radius 16 is big enough for a 1–3 char badge. Scales up
            // with backlink degree and importance — "deep" nodes pop.
            const importance = note?.importance ?? 5;
            const r =
              16 +
              Math.min(11, Math.sqrt(deg) * 2) +
              Math.min(5, importance / 2) +
              (note?.pinned ? 1 : 0);
            const categoryKeys = categoryKeysFor(note?.tags, !!note?.pinned);
            const categoryKey = categoryKeys[0] ?? pickCategoryKey(note?.tags, !!note?.pinned);
            const badge = dotBadge(note?.title, categoryKey);
            // Brightness filter — higher importance nodes "glow" more.
            // Also boosts the hovered 1-hop neighbors so deep things pop.
            const brightness = 0.85 + Math.min(0.4, importance / 25);
            const op = opacityFor(node.id);
            const depth = depths?.get(node.id);
            // Focus = the node the user is actively hovering (always),
            // regardless of whether it has neighbors (depths may be null
            // for isolated nodes — we still want the hover animation).
            const isFocus = node.id === hoverId;
            const isNeighbor = depth === 1;
            const isSelected = node.id === selectedId;
            const label = (note?.title || node.label || "").trim();
            // Completed-task detection — mirrors the tooltip logic so the
            // two views agree. A task is done if any of: done/completed
            // tag, leading markdown checkbox `- [x]`, or starts with ~~.
            const taskDone = (() => {
              if (categoryKey !== "task") return false;
              const tagsLower = (note?.tags || []).map((t) => t.toLowerCase());
              if (tagsLower.includes("done") || tagsLower.includes("completed")) return true;
              const content = note?.content || "";
              return /^\s*-\s*\[x\]/im.test(content);
            })();
            // Always-on labels — every node carries a short title beneath
            // it so the user can read the constellation without hovering.
            // Selected / focus / neighbor / pinned get the bright pill;
            // everyone else gets a dim ghost label.
            const showSideLabel = !!label;
            const labelEmphasized = isSelected || isFocus || isNeighbor || !!note?.pinned;

            // Gentle hover scale — noticeable but not jumpy. The hover
            // also freezes the node's orbit via sim.setHover, so the user
            // doesn't need aggressive scaling to identify the target.
            const scale = isFocus ? 1.18 : isSelected ? 1.08 : 1;

            return (
              <g
                key={node.id}
                transform={`translate(${sn.x} ${sn.y}) scale(${scale})`}
                onPointerDown={(e) => handlePointerDownNode(e, node.id)}
                onPointerEnter={() => handleNodeEnter(node.id)}
                onPointerLeave={() => handleNodeLeave(node.id)}
                onClick={(e) => {
                  // Suppress the click that browsers fire after a drag-release
                  // — `justDraggedRef` is set the moment DRAG_THRESHOLD is
                  // crossed in handlePointerMove AND again in handlePointerUp.
                  if (justDraggedRef.current || dragRef.current) {
                    justDraggedRef.current = false;
                    return;
                  }
                  e.stopPropagation();
                  // Clicking the currently-peeked node closes the peek
                  // (toggle UX). Otherwise open it.
                  if (node.id === selectedId && onClearPeek) {
                    onClearPeek();
                    return;
                  }
                  if (onPeek) onPeek(node.id);
                  else onSelect?.(node.id);
                }}
                opacity={op}
                className="cursor-pointer"
              >
                {/* Whisper-soft halo — a breath of color around the dot,
                    never a bloom. Cozy, not clinical. */}
                <circle
                  r={r + 1 + deg * 0.15}
                  fill={color.ring}
                  opacity={isFocus ? 0.18 : isNeighbor ? 0.11 : 0.055}
                  pointerEvents="none"
                  filter="url(#lb-neural-glow)"
                />
                {/* Hub ring — top-5 most connected notes get a warm
                    concentric pulse marking them as anchors of the graph. */}
                {hubIds.has(node.id) && !note?.pinned && (
                  <>
                    <circle
                      r={r + 1.6}
                      fill="none"
                      stroke="#d4a26a"
                      strokeWidth={1.2}
                      strokeOpacity={0.5}
                      pointerEvents="none"
                    />
                    <circle
                      r={r + 2}
                      fill="none"
                      stroke="#d4a26a"
                      strokeWidth={1.4}
                      strokeOpacity={0.8}
                      pointerEvents="none"
                    >
                      <animate
                        attributeName="r"
                        values={`${r + 2};${r + 10};${r + 2}`}
                        dur="3.2s"
                        repeatCount="indefinite"
                      />
                      <animate
                        attributeName="stroke-opacity"
                        values="0.8;0;0.8"
                        dur="3.2s"
                        repeatCount="indefinite"
                      />
                    </circle>
                  </>
                )}
                {/* Pinned halo — now with a slow pulse (±1px) */}
                {note?.pinned && (
                  <circle
                    r={r + 4}
                    fill="none"
                    stroke="#fbbf24"
                    strokeWidth={2}
                    strokeOpacity={0.9}
                  >
                    <animate
                      attributeName="r"
                      values={`${r + 3};${r + 5};${r + 3}`}
                      dur="2.4s"
                      repeatCount="indefinite"
                    />
                  </circle>
                )}
                {/* Selected ring */}
                {isSelected && !isFocus && (
                  <circle
                    r={r + 2.5}
                    fill="none"
                    stroke="var(--color-accent)"
                    strokeWidth={2}
                  />
                )}
                {/* Single-planet node body. One colored circle, one centered
                    icon (or date text for journal/daily-log). Secondary
                    category signals live as corner markers below — NEVER as
                    inner slices — so the graph reads like a constellation
                    of distinct stars, not a pie-chart garden.                */}
                {(() => {
                  const bodyOpacity = isFocus ? 0.88 : isNeighbor ? 0.78 : 0.66;
                  const bodyStroke = isFocus
                    ? "rgba(240,228,200,0.3)"
                    : "rgba(240,228,200,0.08)";
                  const bodyStrokeW = isFocus ? 0.9 : 0.5;
                  const bodyFilter = { filter: `brightness(${brightness * 0.9})` };
                  const isDateBadge =
                    (categoryKey === "journal" || categoryKey === "daily-log") &&
                    /\d/.test(badge);
                  return (
                    <>
                      <circle
                        r={r}
                        fill={color.ring}
                        opacity={bodyOpacity}
                        stroke={bodyStroke}
                        strokeWidth={bodyStrokeW}
                        style={bodyFilter}
                      />
                      {isDateBadge ? (
                        <text
                          x={0}
                          y={0}
                          textAnchor="middle"
                          dominantBaseline="central"
                          className="pointer-events-none select-none"
                          style={{
                            fontSize: `${Math.max(9, r * 0.48)}px`,
                            fontWeight: 700,
                            fill: "#0a0a0a",
                            letterSpacing: "-0.03em",
                          }}
                        >
                          {badge}
                        </text>
                      ) : (() => {
                        const IconComp = CATEGORY_ICONS[categoryKey] ?? DEFAULT_CATEGORY_ICON;
                        const iconSize = Math.max(12, r * 0.9);
                        return (
                          <g
                            transform={`translate(${-iconSize / 2} ${-iconSize / 2})`}
                            pointerEvents="none"
                          >
                            <IconComp
                              size={iconSize}
                              color="#0a0a0a"
                              strokeWidth={2}
                              aria-hidden
                            />
                          </g>
                        );
                      })()}
                    </>
                  );
                })()}
                {/* Deadline attendant — a tiny rose moon stamped in the
                    top-right when the note carries a deadline that isn't
                    already its primary category (e.g. a task with a due
                    date). Keeps the primary-icon solar aesthetic intact
                    while still whispering "this has a time pressure".     */}
                {(() => {
                  const hasDeadline =
                    categoryKeys.includes("deadline") && categoryKey !== "deadline";
                  const hasDueTag = (note?.tags || []).some((t) =>
                    t.toLowerCase().startsWith("due/"),
                  );
                  if (!hasDeadline && !hasDueTag) return null;
                  // Chase the 45° diagonal out from center so the chip sits
                  // on the node's skin, flush against the top-right arc.
                  const cx = Math.cos(-Math.PI / 4) * r;
                  const cy = Math.sin(-Math.PI / 4) * r;
                  const chipR = Math.max(5.5, r * 0.32);
                  const iconSize = chipR * 1.25;
                  const AlarmIcon = CATEGORY_ICONS.deadline ?? DEFAULT_CATEGORY_ICON;
                  return (
                    <g transform={`translate(${cx} ${cy})`} pointerEvents="none">
                      {/* Soft outer glow — rose luminance sold as a tiny
                          nebula without competing with the node proper. */}
                      <circle
                        r={chipR + 2}
                        fill={ringForKey("deadline")}
                        opacity={0.22}
                        filter="url(#lb-neural-glow)"
                      />
                      <circle
                        r={chipR}
                        fill={ringForKey("deadline")}
                        stroke="rgba(240,228,200,0.35)"
                        strokeWidth={0.7}
                        opacity={isFocus ? 0.98 : 0.9}
                      />
                      <g
                        transform={`translate(${-iconSize / 2} ${-iconSize / 2})`}
                      >
                        <AlarmIcon
                          size={iconSize}
                          color="#0a0a0a"
                          strokeWidth={2.2}
                          aria-hidden
                        />
                      </g>
                    </g>
                  );
                })()}
                {/* Always-on label — every node carries a short title under
                    it so the constellation reads at a glance. Three weights:
                    • selected / focused / pinned → solid pill, full opacity
                    • neighbor of focus           → bright text, no pill
                    • everything else             → dim ghost text          */}
                {showSideLabel && label && (() => {
                  const emphasized = labelEmphasized;
                  const maxChars =
                    isSelected || isFocus ? 32 : labelEmphasized ? 18 : 14;
                  const truncated =
                    label.length > maxChars
                      ? label.slice(0, maxChars - 2) + "…"
                      : label;
                  const fontPx = isSelected || isFocus ? 11 : labelEmphasized ? 10 : 9;
                  const labelW = truncated.length * (fontPx * 0.62) + (emphasized ? 14 : 6);
                  // Anchor the label BELOW the node by default so even
                  // tightly clustered notes can read their titles without
                  // colliding into other nodes' bodies. Selected/focused
                  // nodes get the side-pill so the eye can rest on them.
                  const placeBelow = !emphasized;
                  const snScreenX = sn.x * view.k + view.tx;
                  const wouldOverflow =
                    !placeBelow &&
                    snScreenX + (r + 12 + labelW) * view.k > size.width - 8;
                  const offsetX = placeBelow
                    ? -labelW / 2
                    : wouldOverflow
                      ? -(r + 8 + labelW - 4)
                      : r + 8;
                  const offsetY = placeBelow ? r + 6 : -8;
                  const baseTextOp = isSelected || isFocus
                    ? 0.96
                    : labelEmphasized ? 0.78 : 0.42;
                  const textColor = taskDone
                    ? `rgba(232,213,176,${baseTextOp * 0.55})`
                    : emphasized
                      ? `rgba(245,209,154,${baseTextOp})`
                      : `rgba(232,213,176,${baseTextOp})`;
                  return (
                    <g
                      transform={`translate(${offsetX} ${offsetY})`}
                      pointerEvents="none"
                      opacity={op}
                    >
                      {emphasized && (isSelected || isFocus) && !placeBelow && (
                        <rect
                          x={-4}
                          y={-2}
                          rx={4}
                          ry={4}
                          width={labelW}
                          height={18}
                          fill="rgba(27,22,32,0.9)"
                          stroke="rgba(212,162,106,0.45)"
                          strokeWidth={1}
                        />
                      )}
                      <text
                        x={placeBelow ? labelW / 2 : (emphasized ? 3 : 0)}
                        y={placeBelow ? 0 : 11}
                        textAnchor={placeBelow ? "middle" : "start"}
                        dominantBaseline={placeBelow ? "hanging" : "auto"}
                        style={{
                          fontSize: `${fontPx}px`,
                          fontWeight: emphasized ? 600 : 500,
                          fill: textColor,
                          fontFamily: "var(--font-display, inherit)",
                          letterSpacing: "-0.01em",
                          textShadow: "0 1px 3px rgba(0,0,0,0.9), 0 0 2px rgba(0,0,0,0.7)",
                          textDecoration: taskDone ? "line-through" : "none",
                          textDecorationColor: "rgba(110,181,131,0.9)",
                          textDecorationThickness: "1.5px",
                        }}
                      >
                        {truncated}
                      </text>
                    </g>
                  );
                })()}
              </g>
            );
          })}
        </g>
      </svg>

      {/* Micro-tooltip — compact card. Hidden during an active node drag
          so the user can see the dot clearly while repositioning. */}
      {hoverId && hoverNote && tooltipPos && !(
        dragRef.current?.kind === "node" && dragRef.current.maxMoved > DRAG_THRESHOLD
      ) && (() => {
        const catKey = pickCategoryKey(hoverNote.tags, !!hoverNote.pinned);
        const catLabelBase = (FILTER_CATEGORIES.find((c) => c.key === catKey)?.label)
          ?? catKey.replace(/[-_]/g, " ");
        const hoverTotalCats = categoryMatchCount(hoverNote.tags, !!hoverNote.pinned);
        const catLabel = hoverTotalCats > 1
          ? `${catLabelBase} · +${hoverTotalCats - 1} more`
          : catLabelBase;
        const links = degree[hoverId] ?? 0;
        // Importance → filled-dot count (1..5). 1–2=1, 3–4=2, 5–6=3, 7–8=4, 9–10=5.
        const filledDots = Math.max(1, Math.min(5, Math.ceil((hoverNote.importance ?? 5) / 2)));
        // Task completion: tags include done/completed, OR body starts
        // with a markdown-checked checkbox.
        const tagsLower = (hoverNote.tags || []).map((t) => t.toLowerCase());
        const doneByTag = tagsLower.includes("done") || tagsLower.includes("completed");
        const doneByCheckbox = /^\s*-\s*\[x\]/im.test(hoverNote.content || "");
        const isTask = catKey === "task";
        const isDeadline = catKey === "deadline";
        const status: "done" | "open" | "pinned" | "deadline" | null =
          hoverNote.pinned ? "pinned"
          : isDeadline ? "deadline"
          : isTask ? (doneByTag || doneByCheckbox ? "done" : "open")
          : null;
        return (
          <div
            className="lb-tooltip absolute z-20 pointer-events-none rounded-md border bg-bg-secondary/95 backdrop-blur shadow-2xl px-2.5 py-2 w-[216px] animate-fade-in"
            style={{
              left: Math.max(8, Math.min(size.width - 224, tooltipPos.sx)),
              top: Math.max(8, Math.min(size.height - 96, tooltipPos.sy)),
              borderColor: "rgba(168,144,106,0.24)",
            }}
          >
            {/* Row 1: status-icon · title (strikethrough if done)
                Open tasks show the task icon itself (not an empty circle).
                Completed tasks get a line-through on the title. */}
            <div className="flex items-center gap-1.5">
              <span
                className="w-2 h-2 rounded-full inline-block shrink-0"
                style={{
                  backgroundColor: colorForTags(hoverNote.tags, hoverNote.pinned).ring,
                  boxShadow: `0 0 6px ${colorForTags(hoverNote.tags, hoverNote.pinned).ring}66`,
                }}
              />
              {status === "open" && (
                <CategoryIcon
                  keyName="task"
                  size={12}
                  color="rgba(232,213,176,0.85)"
                />
              )}
              {status === "pinned" && (
                <Star size={11} strokeWidth={2} fill="#fbbf24" color="#fbbf24" />
              )}
              {status === "deadline" && (
                <span
                  title="deadline"
                  className="shrink-0 text-[10px] leading-none"
                  style={{ color: "#f0a060" }}
                >
                  ⏰
                </span>
              )}
              <div
                className="text-[12px] font-semibold truncate flex-1 tracking-tight leading-tight"
                style={{
                  fontFamily: "var(--font-display, inherit)",
                  color:
                    status === "done"
                      ? "rgba(232,213,176,0.55)"
                      : "var(--color-text-primary)",
                  textDecoration: status === "done" ? "line-through" : "none",
                  textDecorationColor: "rgba(110,181,131,0.85)",
                  textDecorationThickness: "1.5px",
                }}
              >
                {(hoverNote.title || "(untitled)").slice(0, 38)}
                {(hoverNote.title || "").length > 38 ? "…" : ""}
              </div>
            </div>
            {/* Row 2: importance meter — 5 dots, filled by importance/2. */}
            <div className="flex items-center gap-1 mt-1.5" title={`importance ${hoverNote.importance}/10`}>
              <span className="text-[9px] uppercase tracking-wider text-text-muted/80 mr-0.5">
                impact
              </span>
              {Array.from({ length: 5 }).map((_, i) => (
                <span
                  key={i}
                  className="w-1.5 h-1.5 rounded-full inline-block"
                  style={{
                    background: i < filledDots ? "#d4a26a" : "transparent",
                    border: i < filledDots ? "none" : "1px solid rgba(168,144,106,0.3)",
                  }}
                />
              ))}
            </div>
            {/* Row 3: category · link count. Minimal meta. */}
            <div className="mt-1.5 text-[9px] uppercase tracking-[0.08em] text-text-muted/80 flex items-center gap-1.5 tabular-nums">
              <span>{catLabel}</span>
              <span className="opacity-40">·</span>
              <span>{links} link{links === 1 ? "" : "s"}</span>
            </div>
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

      {/* Orbit chip stack — left-middle edge. Click to isolate a ring,
          click again to release. Hover highlights. Shows per-orbit count.
          Hidden in neural-link mode where there are no orbits. */}
      {layoutMode === "category" && (() => {
        const meta = sim.orbitMeta();
        return (
          <div className="absolute left-3 top-1/2 -translate-y-1/2 z-10 flex flex-col gap-1.5">
            {meta.map((m, i) => {
              // Skip empty orbits — no need to show a chip for "CORE 0".
              if (m.nodeCount === 0) return null;
              const label = ORBIT_LABELS[i];
              const isIso = isolatedOrbit === i;
              const dim = isolatedOrbit !== null && !isIso;
              return (
                <button
                  key={`ochip-${i}`}
                  onClick={() => setIsolatedOrbit(isIso ? null : i)}
                  className="group relative flex items-center gap-2 pl-2 pr-2.5 py-1.5 rounded-r-md text-left transition-all"
                  style={{
                    background: isIso
                      ? "rgba(212,162,106,0.16)"
                      : "rgba(27,22,32,0.72)",
                    border: "1px solid",
                    borderColor: isIso
                      ? "rgba(212,162,106,0.55)"
                      : "rgba(168,144,106,0.18)",
                    borderLeft: `3px solid ${label.color}`,
                    opacity: dim ? 0.5 : 1,
                    backdropFilter: "blur(8px)",
                  }}
                  title={
                    isIso
                      ? `Release ${label.title} orbit (${i + 1})`
                      : `Isolate ${label.title} orbit (press ${i + 1})`
                  }
                >
                  <div className="flex flex-col leading-none">
                    <span
                      className="text-[10px] font-semibold tracking-[0.18em]"
                      style={{
                        color: label.color,
                        fontFamily: "var(--font-display, inherit)",
                      }}
                    >
                      {label.title}
                    </span>
                    <span
                      className="text-[8px] opacity-60 mt-0.5"
                      style={{ color: label.color }}
                    >
                      {label.subtitle}
                    </span>
                  </div>
                  <span
                    className="ml-1 text-[10px] tabular-nums px-1 rounded"
                    style={{
                      background: "rgba(240,228,200,0.08)",
                      color: "rgba(232,213,176,0.78)",
                      minWidth: 20,
                      textAlign: "center",
                    }}
                  >
                    {m.nodeCount}
                  </span>
                </button>
              );
            })}
            {isolatedOrbit !== null && (
              <button
                onClick={() => setIsolatedOrbit(null)}
                className="text-[9px] uppercase tracking-[0.15em] text-text-muted hover:text-text-primary transition-colors mt-1 px-2 py-1"
                style={{ fontFamily: "var(--font-display, inherit)" }}
                title="Show all orbits (Esc)"
              >
                ✕ clear focus
              </button>
            )}
          </div>
        );
      })()}

      {/* Zoom controls */}
      {/* Layout-mode toggle — flip between category orbits (each kind on
          its own ring) and neural-link orbits (top-hub at center, others
          on BFS-distance rings). Top-left corner so it doesn't crash into
          the search bar (center) or zoom controls (right). */}
      <div className="absolute top-3 left-3 z-10 flex items-center gap-0 rounded-full border border-border bg-bg-secondary/80 backdrop-blur px-0.5 py-0.5 text-[10px]">
        {(["category", "neural-link"] as const).map((mode) => {
          const active = layoutMode === mode;
          const label = mode === "category" ? "Categories" : "Neural-links";
          const title =
            mode === "category"
              ? "Group notes by kind (task / lesson / memory)"
              : topHubId
                ? "Pin the most-linked note at center; others orbit by distance"
                : "Neural-link mode — needs at least one link";
          return (
            <button
              key={mode}
              type="button"
              disabled={mode === "neural-link" && !topHubId}
              onClick={() => setLayoutMode(mode)}
              title={title}
              className={`px-2.5 py-1 rounded-full transition-colors tracking-wide ${
                active
                  ? "bg-bg-hover text-text-primary"
                  : "text-text-muted hover:text-text-primary"
              } ${mode === "neural-link" && !topHubId ? "opacity-40 cursor-not-allowed" : ""}`}
              style={
                active
                  ? { boxShadow: `inset 0 -2px 0 ${mode === "neural-link" ? "#f0a060" : "#d4a26a"}` }
                  : undefined
              }
            >
              {label}
            </button>
          );
        })}
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

      {/* Interactive legend — doubles as a category filter */}
      {showLegend && (
        <div className="absolute bottom-3 left-3 z-10 text-xs w-[200px]">
          <div className="flex items-center justify-between gap-2 px-3 py-1.5 rounded-t-lg bg-bg-secondary border border-border">
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
            >
              {legendOpen ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
            </button>
          </div>
          {legendOpen && (
            <div className="border border-t-0 border-border bg-bg-secondary rounded-b-lg p-1.5 space-y-0.5">
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
