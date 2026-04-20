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
import type { LazyBrainGraph, LazyBrainNote } from "../../api";
import { ForceSimulation, type SimNode } from "./ForceSimulation";
import { CATEGORY_PRIORITY, FILTER_CATEGORIES, colorForTags, readableTextOn } from "./noteColors";
import { CategoryIcon, Star } from "./icons";
import { Plus, Minus, RotateCcw, ChevronDown, ChevronUp, MousePointer2, Move, ZoomIn } from "lucide-react";

interface Props {
  graph: LazyBrainGraph;
  notesById?: Record<string, LazyBrainNote>;
  /** Legacy — switches view entirely. Prefer onPeek. */
  onSelect?: (nodeId: string) => void;
  /** Preferred: clicking a node shows a preview card without leaving graph. */
  onPeek?: (nodeId: string) => void;
  selectedId?: string | null;
  highlightQuery?: string;
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
}

/** Below this px threshold a pointer-down/up counts as a click, not a drag. */
const DRAG_THRESHOLD = 4;

const MIN_ZOOM = 0.2;
const MAX_ZOOM = 4;
const MAX_DEPTH = 3;

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

/** 1–3 char badge that goes INSIDE the dot. Dates for journals. */
function dotBadge(
  title: string | null | undefined,
  categoryKey: string,
): string {
  if (categoryKey === "journal" || categoryKey === "daily-log") {
    const m = (title || "").match(/(\d{2})-(\d{2})$/);
    if (m) return `${m[1]}/${m[2]}`;
    const m2 = (title || "").match(/\d{4}-(\d{2})-(\d{2})/);
    if (m2) return `${m2[1]}/${m2[2]}`;
  }
  return BADGE_MAP[categoryKey] ?? "?";
}

export function GraphView({
  graph,
  notesById,
  onSelect,
  onPeek,
  selectedId,
  highlightQuery,
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

  // ── Build simulation when graph shape changes ──────────────────────────
  const sim = useMemo(() => {
    const s = new ForceSimulation(
      graph.nodes.map((n) => ({ id: n.id, pinned: false })),
      graph.edges.map((e) => ({ source: e.source, target: e.target })),
      { width: size.width, height: size.height },
    );
    // Pre-settle a few hundred frames so the first paint isn't a circle —
    // but stop early so the rest of the layout animates in. Obsidian does
    // this and it makes the graph feel alive instead of frozen.
    let iters = 0;
    while (!s.cooled(1.0) && iters < 600) {
      s.step();
      iters++;
    }
    simRef.current = s;
    return s;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph]);

  // ── Animation loop ─────────────────────────────────────────────────────
  // Runs until sim cools (~10 idle frames), then stops. CPU-free when idle.
  useEffect(() => {
    let alive = true;
    let idle = 0;
    const loop = () => {
      if (!alive) return;
      const s = simRef.current;
      if (!s) return;
      s.step();
      setTick((t) => (t + 1) % 1_000_000);
      if (s.cooled(1.0)) {
        idle += 1;
        if (idle > 10 && !dragRef.current) {
          frameRef.current = null;
          return;
        }
      } else {
        idle = 0;
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
      dragRef.current = { kind: "pan", startX: x, startY: y, lastX: x, lastY: y, maxMoved: 0 };
      justDraggedRef.current = false;
      (e.currentTarget as SVGElement).setPointerCapture(e.pointerId);
    },
    [clientToSvg],
  );

  const handlePointerDownNode = useCallback(
    (e: React.PointerEvent<SVGGElement>, id: string) => {
      e.stopPropagation();
      const { x, y } = clientToSvg(e.clientX, e.clientY);
      dragRef.current = { kind: "node", id, startX: x, startY: y, lastX: x, lastY: y, maxMoved: 0 };
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
      } else if (drag.kind === "node" && drag.id && maxMoved > DRAG_THRESHOLD) {
        // Only start moving the node once we've crossed the click threshold.
        // Below that, a tiny jitter shouldn't yank the node off its position.
        const { x, y } = screenToSim(sx, sy);
        sim.pin(drag.id, x, y);
        sim.warm();
        if (frameRef.current === null) {
          const loop = () => {
            sim.step();
            setTick((t) => (t + 1) % 1_000_000);
            frameRef.current = requestAnimationFrame(loop);
          };
          frameRef.current = requestAnimationFrame(loop);
        }
      }
      dragRef.current = { ...drag, lastX: sx, lastY: sy, maxMoved };
    },
    [clientToSvg, screenToSim, sim],
  );

  const handlePointerUp = useCallback(() => {
    const drag = dragRef.current;
    // Mark "just dragged" so the click event browser fires after pointerup
    // can be suppressed in the node onClick handler.
    justDraggedRef.current = !!drag && drag.maxMoved > DRAG_THRESHOLD;
    if (drag?.kind === "node" && drag.id) sim.unpin(drag.id);
    dragRef.current = null;
  }, [sim]);

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
  }, []);

  const handleNodeEnter = useCallback((id: string) => {
    cancelHoverClear();
    setHoverId(id);
  }, []);

  const handleNodeLeave = useCallback((id: string) => {
    // Tiny grace so a fast cursor crossing a 1-2px gap between sibling
    // nodes doesn't flicker the tooltip off and back on.
    cancelHoverClear();
    hoverClearRef.current = window.setTimeout(() => {
      hoverClearRef.current = null;
      setHoverId((cur) => (cur === id ? null : cur));
    }, 40);
  }, []);

  // Cleanup on unmount — never leak the timer.
  useEffect(() => () => cancelHoverClear(), []);

  // Re-heat on filter/search change. `dimPredicate` MUST be stable (useCallback
  // in the parent) or the sim will re-warm on every render and burn CPU.
  useEffect(() => {
    simRef.current?.warm();
    if (frameRef.current === null) {
      let idle = 0;
      const loop = () => {
        const s = simRef.current;
        if (!s) return;
        s.step();
        setTick((t) => (t + 1) % 1_000_000);
        if (s.cooled(1.0)) {
          idle += 1;
          if (idle > 10 && !dragRef.current) {
            frameRef.current = null;
            return;
          }
        } else {
          idle = 0;
        }
        frameRef.current = requestAnimationFrame(loop);
      };
      frameRef.current = requestAnimationFrame(loop);
    }
  }, [highlightQuery, dimPredicate]);

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
  // Label font size for the selected-only side label.
  const labelFontSize = Math.max(10, 11 / view.k);

  // Compute per-node opacity using depth, filter, and search.
  // Hover dims hard (0.08) for strong momentary focus. Selection dims soft
  // (0.35) so the whole graph stays readable while your current page is
  // clearly highlighted.
  const NON_FOCUS_DIM = isHoverFocus ? 0.08 : 0.35;
  const opacityFor = (noteId: string): number => {
    if (noteId === hoverId) return 1; // hovered always fully lit
    const note = notesById?.[noteId];
    const dimmed = dimPredicate?.(note) ?? false;
    const match = matcher ? matcher(noteId) : true;
    if (dimmed || !match) return 0.08;
    if (depths) {
      const d = depths.get(noteId);
      if (d === undefined) return NON_FOCUS_DIM;
      if (d === 0) return 1;
      if (d === 1) return 0.95;
      if (d === 2) return 0.7;
      return Math.max(NON_FOCUS_DIM, 0.45);
    }
    return 0.85;
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
      className="relative w-full h-full overflow-hidden bg-bg-primary"
    >
      <svg
        ref={svgRef}
        width={size.width}
        height={size.height}
        className="block bg-bg-primary select-none cursor-grab active:cursor-grabbing"
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
          <radialGradient id="node-glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="rgba(16,185,129,0.35)" />
            <stop offset="100%" stopColor="rgba(16,185,129,0)" />
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
          {/* Edges */}
          {graph.edges.map((edge, idx) => {
            const a = nodeById.get(edge.source);
            const b = nodeById.get(edge.target);
            if (!a || !b) return null;
            const st = edgeState(edge.source, edge.target);
            return (
              <line
                key={`e-${idx}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={st.stroke}
                strokeWidth={st.width}
                strokeOpacity={st.opacity}
                strokeLinecap="round"
              />
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
            const categoryKey = pickCategoryKey(note?.tags, !!note?.pinned);
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
            // Side-label only for the currently selected node — keeps the
            // graph clean; full detail shows via hover tooltip otherwise.
            const showSideLabel = isSelected;

            // Hover scale — use the SVG transform attribute with both
            // translate + scale combined. Putting a CSS `style.transform`
            // on the same element OVERRIDES the attribute, which would
            // collapse every node to (0,0). Attribute is the safe path.
            const scale = isFocus ? 1.28 : isSelected ? 1.12 : 1;

            return (
              <g
                key={node.id}
                transform={`translate(${sn.x} ${sn.y}) scale(${scale})`}
                onPointerDown={(e) => handlePointerDownNode(e, node.id)}
                onPointerEnter={() => handleNodeEnter(node.id)}
                onPointerLeave={() => handleNodeLeave(node.id)}
                onClick={(e) => {
                  // Suppress the click that browsers fire after a drag-release
                  // — `justDraggedRef` is set in handlePointerUp when the
                  // gesture exceeded DRAG_THRESHOLD.
                  if (justDraggedRef.current || dragRef.current) {
                    justDraggedRef.current = false;
                    return;
                  }
                  e.stopPropagation();
                  if (onPeek) onPeek(node.id);
                  else onSelect?.(node.id);
                }}
                opacity={op}
                className="cursor-pointer"
              >
                {/* Pulsing focus glow — only rendered when hovered/selected-focus */}
                {isFocus && (
                  <circle
                    r={r + 14}
                    fill={color.ring}
                    opacity={0.35}
                    pointerEvents="none"
                    style={{ filter: "blur(6px)" }}
                  >
                    <animate
                      attributeName="r"
                      values={`${r + 10};${r + 22};${r + 10}`}
                      dur="1.8s"
                      repeatCount="indefinite"
                    />
                    <animate
                      attributeName="opacity"
                      values="0.55;0.18;0.55"
                      dur="1.8s"
                      repeatCount="indefinite"
                    />
                  </circle>
                )}
                {/* Pinned halo */}
                {note?.pinned && (
                  <circle
                    r={r + 4}
                    fill="none"
                    stroke="#fbbf24"
                    strokeWidth={2}
                    strokeOpacity={0.9}
                  />
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
                {/* Body — brightness boosts "deep" (high-importance) nodes,
                    drop-shadow lifts focused node. */}
                <circle
                  r={r}
                  fill={color.ring}
                  stroke={isFocus ? "var(--color-bg-primary)" : "rgba(0,0,0,0.3)"}
                  strokeWidth={isFocus ? 2 : 1}
                  style={{
                    filter: isFocus
                      ? `drop-shadow(0 0 8px ${color.ring}) brightness(${brightness + 0.1})`
                      : isNeighbor
                      ? `drop-shadow(0 0 4px ${color.ring}) brightness(${brightness + 0.05})`
                      : `brightness(${brightness})`,
                  }}
                />
                {/* In-dot badge — the 1–3 char code (T, P, 04/18, etc.).
                    Text color picks itself against the dot fill so it
                    reads on both bright and dark categories. */}
                <text
                  x={0}
                  y={0}
                  textAnchor="middle"
                  dominantBaseline="central"
                  className="pointer-events-none select-none"
                  style={{
                    fontSize: `${Math.max(10, r * 0.55)}px`,
                    fontWeight: 700,
                    fill: readableTextOn(color.ring),
                    letterSpacing: badge.length >= 2 ? "-0.03em" : "0",
                  }}
                >
                  {badge}
                </text>
                {/* Side-label ONLY for selected node — a small pill so the
                    user knows which page they've opened. Bounds-checked
                    against the SVG viewport so it can never clip the right
                    edge: when the node sits in the right ~third of the
                    canvas, the pill anchors to its LEFT side instead. */}
                {showSideLabel && label && (() => {
                  const labelW = Math.min(280, label.length * 7 + 14);
                  const snScreenX = sn.x * view.k + view.tx;
                  const wouldOverflow =
                    snScreenX + (r + 12 + labelW) * view.k > size.width - 8;
                  const offsetX = wouldOverflow
                    ? -(r + 8 + labelW - 4)
                    : r + 8;
                  return (
                    <g transform={`translate(${offsetX} ${-8})`}>
                      <rect
                        x={-4}
                        y={-2}
                        rx={4}
                        ry={4}
                        width={labelW}
                        height={18}
                        fill="var(--color-bg-secondary)"
                        stroke="var(--color-border)"
                        strokeWidth={1}
                      />
                      <text
                        x={3}
                        y={11}
                        className="fill-text-primary pointer-events-none"
                        style={{
                          fontSize: `${Math.max(10, labelFontSize)}px`,
                          fontWeight: 500,
                        }}
                      >
                        {label.length > 38 ? label.slice(0, 36) + "…" : label}
                      </text>
                    </g>
                  );
                })()}
              </g>
            );
          })}
        </g>
      </svg>

      {/* Hover tooltip — short preview. Click for full peek card. */}
      {hoverId && hoverNote && tooltipPos && (
        <div
          className="absolute z-20 pointer-events-none rounded-lg border border-border bg-bg-secondary shadow-2xl p-3 w-[280px] animate-fade-in"
          style={{
            left: Math.max(8, Math.min(size.width - 296, tooltipPos.sx)),
            top: Math.max(8, Math.min(size.height - 160, tooltipPos.sy)),
          }}
        >
          <div className="flex items-center gap-2 mb-1.5">
            <span
              className="w-2 h-2 rounded-full inline-block shrink-0"
              style={{ backgroundColor: colorForTags(hoverNote.tags, hoverNote.pinned).ring }}
            />
            {hoverNote.pinned && <Star size={10} strokeWidth={2} fill="#fbbf24" color="#fbbf24" />}
            <div className="text-sm font-semibold text-text-primary truncate tracking-tight">
              {hoverNote.title || "(untitled)"}
            </div>
          </div>
          <div className="text-[10px] uppercase tracking-wider text-text-muted mb-1.5 tabular-nums">
            {degree[hoverId] ?? 0} link{(degree[hoverId] ?? 0) === 1 ? "" : "s"} · importance {hoverNote.importance}/10
          </div>
          <div className="text-xs text-text-secondary line-clamp-3 whitespace-pre-wrap leading-relaxed">
            {hoverNote.content.slice(0, 160)}
            {hoverNote.content.length > 160 ? "…" : ""}
          </div>
          <div className="text-[10px] text-text-muted mt-2 italic">click to open preview</div>
        </div>
      )}

      {/* Zoom controls */}
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
          onClick={() => setView({ tx: 0, ty: 0, k: 1 })}
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
