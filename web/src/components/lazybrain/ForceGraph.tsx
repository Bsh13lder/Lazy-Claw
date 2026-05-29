/**
 * ForceGraph — LazyBrain "violet observatory" knowledge graph.
 *
 * Stack mirrors Obsidian and Logseq: pixi.js v8 (WebGL render) + d3-force
 * (physics). Adapted from Quartz 4's `quartz/components/scripts/graph.inline.ts`
 * (MIT, jackyzha0/quartz) — see PLAN file for the full provenance.
 *
 * Aesthetic direction: cozy observatory, not clinical Obsidian. We borrow
 * Obsidian's clarity (long link distance, strong repulsion, weak center,
 * hover-isolation, hierarchical edge alpha) but keep the existing LazyBrain
 * identity — warm-charcoal background, violet primary, amber for pinned,
 * Source Serif 4 italic for labels. Three-layer depth per node: solid
 * fill + always-on subtle outer ring + extra halo on hover/selected.
 */
import { useEffect, useMemo, useRef } from "react";
import {
  forceSimulation,
  forceManyBody,
  forceCenter,
  forceLink,
  forceCollide,
  forceX,
  forceY,
  type Simulation,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from "d3-force";
import { zoom, zoomIdentity, type ZoomTransform } from "d3-zoom";
import { drag } from "d3-drag";
import { select } from "d3-selection";
import { Application, Container, Graphics, Text, Circle } from "pixi.js";
import type { LazyBrainGraph, LazyBrainNote } from "../../api";
import { colorForNode, categoryKeysFor, FILTER_CATEGORIES } from "./noteColors";
import { displayLabelForNote } from "./rollupLabel";

// ── Types ─────────────────────────────────────────────────────────────

type LayoutMode = "category" | "neural-link";

type SimNode = {
  id: string;
  text: string;
  pinned: boolean;
  importance: number;
  community_id?: number | null;
  tags: string[];
  degree: number;
  /** Resolved primary category key (or "_singleton"). Drives forceX/Y anchor. */
  cat: string;
  /** Per-node cluster anchor. Singletons get an individual ring slot so the
   *  halo reads as a round ring instead of collapsing onto one centroid. */
  target?: { x: number; y: number };
  __initialDragPos?: { x: number; y: number; fx: number | null; fy: number | null };
} & SimulationNodeDatum;

type SimLink = {
  source: SimNode;
  target: SimNode;
} & SimulationLinkDatum<SimNode>;

type NodeRender = {
  sim: SimNode;
  /** Always-on disc (solid fill + 1px ring). */
  gfx: Graphics;
  /** Drawn only when hovered/selected — outer halo 4px out. Cleared every frame. */
  halo: Graphics;
  /** Hit-test target. Sits above gfx for crisp pointer events. */
  hit: Graphics;
  label: Text;
  color: string;
  alpha: number;
  active: boolean;
  radius: number;
};

type LinkRender = {
  sim: SimLink;
  gfx: Graphics;
  alpha: number;
  active: boolean;
};

export interface ForceGraphProps {
  graph: LazyBrainGraph;
  notesById?: Record<string, LazyBrainNote>;
  selectedId?: string | null;
  highlightQuery?: string;
  layoutMode?: LayoutMode;
  /** Called per-note — return true to dim this node (filtered out). */
  dimPredicate?: (note: LazyBrainNote | undefined) => boolean;
  /** Single click — Obsidian behaviour: highlight only, no popup. */
  onNodeClick?: (id: string) => void;
  /** Double click — open the peek card / inspector for the node. */
  onNodeDoubleClick?: (id: string) => void;
  /** Optional hover callback for the inspector / preview card. */
  onNodeHover?: (id: string | null) => void;
  /** Click on empty canvas (not on any node, no pan) — clear selection. */
  onBackgroundClick?: () => void;
  /** Pause physics — pinned nodes still track the cursor on drag. */
  frozen?: boolean;
}

// ── Tunables ──────────────────────────────────────────────────────────
//
// These are the constants that turn a piled-up ball of nodes into an
// Obsidian-grade graph. The biggest lessons:
//   • forceCenter strength must stay tiny (≤ 0.05) at 800+ nodes — anything
//     larger crushes the cloud back into a dense disc.
//   • forceManyBody.strength has to scale with sqrt(N); a constant -120
//     looks fine at 50 nodes and disastrous at 800.
//   • forceLink distance also needs to scale — the "breathing" feel
//     comes from links being long enough that two connected hubs sit
//     visibly apart, not stacked on top of each other.
//   • forceRadial REMOVED — it was actively squeezing every node onto
//     a single ring around the centre. d3-force does its own clustering
//     via charge + link + collide; radial just fights it.
//   • Pre-tick the simulation 180 steps before first paint so the user
//     never sees the ball-collapse before nodes spread out.

function chargeStrength(n: number): number {
  // With per-category clustering doing most of the spread work, we can
  // dial repulsion back so within-cluster nodes sit closer (Earth-like
  // continents instead of a uniform spray).
  return -180 - Math.sqrt(n) * 4;
}
function linkDistance(n: number): number {
  // Shorter links — clusters should feel cohesive, not flung apart.
  return 45 + Math.sqrt(n) * 1.4;
}
const CENTER_STRENGTH = 0.02;
const LINK_STRENGTH = 0.7;
const COLLIDE_PADDING = 4;
const COLLIDE_ITERATIONS = 4;
const PRETICK_STEPS = 220;
const ALPHA_DECAY = 0.022;
/** Per-category centroid pull. Higher = tighter island, lower = more
 *  bleed between continents. 0.18 gives Obsidian's signature feel. */
const CLUSTER_STRENGTH = 0.18;
/** Where each category's centroid sits, on a circle around origin.
 *  Radius scales with sqrt(N) so larger graphs get bigger continents. */
function clusterRadius(n: number): number {
  return 180 + Math.sqrt(n) * 14;
}
/** Soft one-sided radial containment. Below CLUSTER_STRENGTH so continents
 *  keep their shape; only reels in nodes that drift past the disc edge. */
const ENVELOPE_STRENGTH = 0.06;
/** Envelope radius as a multiple of clusterRadius — just outside the
 *  singleton halo (clusterRadius * 1.55) plus breathing room. */
const ENVELOPE_RADIUS_MULT = 1.8;

/**
 * d3 custom force: soft radial containment toward a disc of `radius`.
 *
 * One-sided spring — only acts on nodes whose distance from origin exceeds
 * `radius`, so interior cluster structure is untouched but stray arms,
 * singletons, and repulsion filaments get reeled back into a round disc.
 * This is what gives the graph a circular overall silhouette. Scaled by
 * d3's alpha each tick, so it settles cleanly with the existing cooldown.
 */
function forceEnvelope<N extends SimulationNodeDatum>(
  radius: number,
  strength: number,
) {
  let nodes: N[] = [];
  function force(alpha: number) {
    for (const n of nodes) {
      const x = n.x ?? 0;
      const y = n.y ?? 0;
      const d = Math.hypot(x, y);
      if (d > radius && d > 1e-6) {
        const k = ((d - radius) / d) * strength * alpha;
        n.vx = (n.vx ?? 0) - x * k;
        n.vy = (n.vy ?? 0) - y * k;
      }
    }
  }
  force.initialize = (n: N[]) => {
    nodes = n;
  };
  return force;
}

const ZOOM_EXTENT: [number, number] = [0.15, 5];
const LABEL_FONT_SIZE = 12;
const LABEL_FONT_FAMILY =
  '"Source Serif 4", "Source Serif Pro", Georgia, ui-serif, serif';
const LABEL_K_FULL = 1.4;     // all labels visible at zoom k > this
const LABEL_K_HUBS = 0.85;    // hub labels visible at zoom k > this
const HUB_DEGREE_THRESHOLD = 5;

const EDGE_ALPHA_REST = 0.18;
const EDGE_ALPHA_DIM = 0.05;
const EDGE_ALPHA_ACTIVE = 0.85;
const EDGE_WIDTH = 1;

const HOVER_DIM_ALPHA = 0.16;
const PINNED_POSITIONS_KEY = "lazybrain-graph-pinned-positions";

// ── Theme helpers ─────────────────────────────────────────────────────

interface Theme {
  node: number;
  nodeVisited: number;
  nodeHover: number;
  nodeSelected: number;
  link: number;
  linkActive: number;
  text: string;
  haloHover: number;
  haloSelected: number;
  haloPinned: number;
  ringSubtle: number;
}

function hexToInt(hex: string, fallback: number): number {
  const m = /^#?([0-9a-f]{3,8})$/i.exec(hex.trim());
  if (!m) return fallback;
  let h = m[1];
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  if (h.length === 6) return parseInt(h, 16);
  if (h.length === 8) return parseInt(h.slice(0, 6), 16);
  return fallback;
}

function readTheme(): Theme {
  if (typeof window === "undefined") {
    return {
      node: 0xa78bfa,
      nodeVisited: 0xc4b5fd,
      nodeHover: 0xfbbf24,
      nodeSelected: 0x10b981,
      link: 0x3a3450,
      linkActive: 0xa78bfa,
      text: "#f0e4c8",
      haloHover: 0xfbbf24,
      haloSelected: 0x10b981,
      haloPinned: 0xfbbf24,
      ringSubtle: 0x2a2334,
    };
  }
  const root =
    document.querySelector(".lazybrain-root") ?? document.documentElement;
  const cs = getComputedStyle(root);
  const get = (name: string, fb: string) => cs.getPropertyValue(name).trim() || fb;
  return {
    node: hexToInt(get("--lb-graph-node", "#a78bfa"), 0xa78bfa),
    nodeVisited: hexToInt(get("--lb-graph-node-visited", "#c4b5fd"), 0xc4b5fd),
    nodeHover: hexToInt(get("--lb-graph-node-hover", "#fbbf24"), 0xfbbf24),
    nodeSelected: hexToInt(get("--lb-graph-node-selected", "#10b981"), 0x10b981),
    link: hexToInt(get("--lb-graph-link", "#3a3450"), 0x3a3450),
    linkActive: hexToInt(get("--lb-graph-link-active", "#a78bfa"), 0xa78bfa),
    text: get("--lb-graph-text", "#f0e4c8"),
    haloHover: hexToInt(get("--lb-graph-halo-hover", "#fbbf24"), 0xfbbf24),
    haloSelected: hexToInt(get("--lb-graph-halo-selected", "#10b981"), 0x10b981),
    haloPinned: hexToInt(get("--lb-graph-halo-pinned", "#fbbf24"), 0xfbbf24),
    ringSubtle: hexToInt(get("--lb-graph-ring-subtle", "#2a2334"), 0x2a2334),
  };
}

// ── Pinned positions persistence ──────────────────────────────────────

type PinnedMap = Record<string, { x: number; y: number }>;

function loadPinned(): PinnedMap {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(PINNED_POSITIONS_KEY);
    return raw ? (JSON.parse(raw) as PinnedMap) : {};
  } catch {
    return {};
  }
}

function savePinned(map: PinnedMap) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(PINNED_POSITIONS_KEY, JSON.stringify(map));
  } catch {
    /* quota / private mode — non-fatal */
  }
}

// ── Component ─────────────────────────────────────────────────────────

export function ForceGraph({
  graph,
  notesById,
  selectedId,
  highlightQuery,
  layoutMode = "category",
  dimPredicate,
  onNodeClick,
  onNodeDoubleClick,
  onNodeHover,
  onBackgroundClick,
  frozen = false,
}: ForceGraphProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Stable ref so the long-lived pixi closure reads latest props without
  // forcing a full re-init on every render.
  const propsRef = useRef({
    selectedId,
    highlightQuery,
    layoutMode,
    dimPredicate,
    onNodeClick,
    onNodeDoubleClick,
    onNodeHover,
    onBackgroundClick,
    frozen,
    notesById,
  });
  propsRef.current = {
    selectedId,
    highlightQuery,
    layoutMode,
    dimPredicate,
    onNodeClick,
    onNodeDoubleClick,
    onNodeHover,
    onBackgroundClick,
    frozen,
    notesById,
  };

  // Stable graph identity — avoids unnecessary remounts when LazyBrain.tsx
  // recomputes the same logical graph from a fresh fetch.
  const graphKey = useMemo(
    () => `${graph.nodes.length}-${graph.edges.length}`,
    [graph.nodes.length, graph.edges.length],
  );

  useEffect(() => {
    const host = containerRef.current;
    if (!host) return;

    let cancelled = false;
    const cleanups: Array<() => void> = [];

    (async () => {
      const theme = readTheme();
      const pinned = loadPinned();
      const N = graph.nodes.length;

      // ── Build sim graph ────────────────────────────────────────────
      const degMap = new Map<string, number>();
      for (const e of graph.edges) {
        degMap.set(e.source, (degMap.get(e.source) ?? 0) + 1);
        degMap.set(e.target, (degMap.get(e.target) ?? 0) + 1);
      }

      // Build per-category centroids on a circle around origin. Each
      // category that actually appears in this graph gets one slot; the
      // angle is hash-stable so the layout doesn't shuffle continents
      // every reload.
      const presentCats = new Set<string>();
      const categoryFor = (note: LazyBrainNote | undefined, isPinned: boolean): string => {
        const keys = categoryKeysFor(note?.tags, isPinned);
        if (keys.length === 0) return "_singleton";
        return keys[0];
      };
      const tmpCats: string[] = [];
      for (const n of graph.nodes) {
        const note = notesById?.[n.id];
        const c = categoryFor(note, !!n.pinned);
        if (!presentCats.has(c)) {
          presentCats.add(c);
          tmpCats.push(c);
        }
      }
      // Stable ordering — sorted by FILTER_CATEGORIES priority then alpha
      // so `pinned` always lands at angle 0 regardless of what's loaded.
      const orderRank = new Map<string, number>();
      FILTER_CATEGORIES.forEach((c, i) => orderRank.set(c.key, i));
      orderRank.set("_singleton", FILTER_CATEGORIES.length + 99);
      const cats = tmpCats.sort(
        (a, b) =>
          (orderRank.get(a) ?? 9999) - (orderRank.get(b) ?? 9999) ||
          a.localeCompare(b),
      );
      const cR = clusterRadius(graph.nodes.length);
      const catCentroid = new Map<string, { x: number; y: number }>();
      cats.forEach((c, i) => {
        // Singletons go to a wide outer halo so they don't pollute the
        // continent centres.
        const isSingleton = c === "_singleton";
        const r = isSingleton ? cR * 1.55 : cR;
        const angle = (i / Math.max(cats.length - (cats.includes("_singleton") ? 1 : 0), 1)) *
          Math.PI * 2 - Math.PI / 2;
        catCentroid.set(c, { x: Math.cos(angle) * r, y: Math.sin(angle) * r });
      });

      // Spread singletons EVENLY around their own halo ring instead of
      // collapsing them all onto the single "_singleton" centroid — that
      // collapse created one big off-center lobe that broke the circular
      // silhouette. Each singleton gets a stable angle slot on the ring.
      const singletonR = cR * 1.55;
      const singletonIds: string[] = [];
      for (const n of graph.nodes) {
        const note = notesById?.[n.id];
        if (categoryFor(note, !!n.pinned) === "_singleton") singletonIds.push(n.id);
      }
      const singletonAnchor = new Map<string, { x: number; y: number }>();
      singletonIds.forEach((id, k) => {
        const a = (k / Math.max(singletonIds.length, 1)) * Math.PI * 2 - Math.PI / 2;
        singletonAnchor.set(id, {
          x: Math.cos(a) * singletonR,
          y: Math.sin(a) * singletonR,
        });
      });
      const anchorFor = (id: string, cat: string): { x: number; y: number } =>
        singletonAnchor.get(id) ?? catCentroid.get(cat) ?? { x: 0, y: 0 };

      const nodeMap = new Map<string, SimNode>();
      const nodes: SimNode[] = graph.nodes.map((n) => {
        const note = notesById?.[n.id];
        const text =
          (note ? displayLabelForNote(note, n.label).primary : n.label) ||
          n.id.slice(0, 8);
        const pinPos = pinned[n.id];
        const cat = categoryFor(note, !!n.pinned);
        const anchor = anchorFor(n.id, cat);
        const sim: SimNode = {
          id: n.id,
          text,
          pinned: !!n.pinned,
          importance: n.importance ?? 5,
          community_id: n.community_id ?? null,
          tags: note?.tags ?? [],
          degree: degMap.get(n.id) ?? 0,
          cat,
          target: anchor,
          // Seed near the cluster anchor so pre-tick converges with the
          // right topology instead of unwinding from origin.
          x: pinPos?.x ?? anchor.x + (Math.random() - 0.5) * 40,
          y: pinPos?.y ?? anchor.y + (Math.random() - 0.5) * 40,
          fx: pinPos?.x ?? null,
          fy: pinPos?.y ?? null,
        };
        nodeMap.set(n.id, sim);
        return sim;
      });

      const links: SimLink[] = [];
      for (const e of graph.edges) {
        const s = nodeMap.get(e.source);
        const t = nodeMap.get(e.target);
        if (!s || !t) continue;
        links.push({ source: s, target: t });
      }

      const width = host.clientWidth || 800;
      const height = Math.max(host.clientHeight || 600, 250);

      // ── d3-force simulation (Earth-style continents + Obsidian feel) ─
      const simulation: Simulation<SimNode, SimLink> = forceSimulation<SimNode>(
        nodes,
      )
        .force(
          "charge",
          forceManyBody<SimNode>().strength(chargeStrength(N)),
        )
        .force("center", forceCenter<SimNode>(0, 0).strength(CENTER_STRENGTH))
        .force(
          "link",
          forceLink<SimNode, SimLink>(links)
            .distance(linkDistance(N))
            .strength(LINK_STRENGTH)
            .id((n) => n.id),
        )
        .force(
          "collide",
          forceCollide<SimNode>((n) => nodeRadius(n) + COLLIDE_PADDING)
            .iterations(COLLIDE_ITERATIONS),
        )
        // Per-category centroid pull — this is the difference between a
        // uniform spray of nodes and Earth-style continents-with-oceans.
        // forceX/forceY are cheap and play nicely with charge/link.
        .force(
          "clusterX",
          forceX<SimNode>((n) => n.target?.x ?? 0).strength(CLUSTER_STRENGTH),
        )
        .force(
          "clusterY",
          forceY<SimNode>((n) => n.target?.y ?? 0).strength(CLUSTER_STRENGTH),
        )
        // Round the overall silhouette: pull any node past the disc edge
        // back inside. One-sided, so interior continents are untouched.
        .force(
          "envelope",
          forceEnvelope<SimNode>(cR * ENVELOPE_RADIUS_MULT, ENVELOPE_STRENGTH),
        )
        .alphaDecay(ALPHA_DECAY);

      // Pre-tick so the user never sees the dense ball collapse stage.
      // Skip if every node has fixed positions from localStorage (the
      // user already laid this graph out by hand — respect that).
      const allPinned = nodes.every((n) => n.fx !== null && n.fy !== null);
      if (!allPinned) {
        simulation.alpha(1).stop();
        for (let i = 0; i < PRETICK_STEPS; i++) simulation.tick();
        simulation.alpha(0.3).restart();
      }

      if (frozen) simulation.alpha(0);

      // ── pixi.js application ────────────────────────────────────────
      const app = new Application();
      try {
        await app.init({
          width,
          height,
          antialias: true,
          autoStart: false,
          autoDensity: true,
          backgroundAlpha: 0,
          preference: "webgl",
          resolution: window.devicePixelRatio,
          eventMode: "static",
        });
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("[ForceGraph] pixi init failed:", err);
        return;
      }
      if (cancelled) {
        app.destroy(true);
        return;
      }
      host.appendChild(app.canvas);
      app.canvas.style.display = "block";
      app.canvas.style.width = "100%";
      app.canvas.style.height = "100%";

      const stage = app.stage;
      stage.interactive = false;
      stage.sortableChildren = true;

      const linkContainer = new Container({ zIndex: 1, isRenderGroup: true });
      const haloContainer = new Container({ zIndex: 2, isRenderGroup: true });
      const nodeContainer = new Container({ zIndex: 3, isRenderGroup: true });
      const hitContainer = new Container({ zIndex: 4, isRenderGroup: true });
      const labelContainer = new Container({ zIndex: 5, isRenderGroup: true });
      stage.addChild(linkContainer, haloContainer, nodeContainer, hitContainer, labelContainer);

      const nodeData: NodeRender[] = [];
      const linkData: LinkRender[] = [];

      let hoveredId: string | null = null;
      let dragStartTime = 0;
      let currentTransform: ZoomTransform = zoomIdentity;
      // Double-click detection — single-click highlights, double opens.
      let lastClickAt = 0;
      let lastClickId: string | null = null;
      const DBL_CLICK_MS = 320;

      const colorOf = (n: SimNode): number => {
        if (n.id === propsRef.current.selectedId) return theme.nodeSelected;
        const c = colorForNode(
          { pinned: n.pinned, tags: n.tags, community_id: n.community_id },
          propsRef.current.layoutMode ?? "category",
        );
        return hexToInt(c.ring || "#a78bfa", theme.node);
      };

      const opacityFor = (n: SimNode): number => {
        const note = propsRef.current.notesById?.[n.id];
        if (propsRef.current.dimPredicate?.(note)) return 0.18;
        const q = propsRef.current.highlightQuery?.trim().toLowerCase();
        if (q) {
          const text = (n.text ?? "").toLowerCase();
          if (!text.includes(q)) return 0.25;
        }
        return 1;
      };

      // Build pixi nodes (3 layers per node: halo, disc, hit-target) + label
      for (const n of nodes) {
        const r = nodeRadius(n);
        const col = colorOf(n);

        // Layer 1 — halo (only drawn on hover/selected; cleared per frame).
        const halo = new Graphics({ interactive: false, eventMode: "none" });

        // Layer 2 — disc (filled circle + 1px subtle ring on top).
        const gfx = new Graphics({ interactive: false, eventMode: "none" });
        gfx
          .circle(0, 0, r)
          .fill({ color: col })
          .circle(0, 0, r)
          .stroke({ width: 1, color: theme.ringSubtle, alpha: 0.55 });
        if (n.pinned) {
          gfx
            .circle(0, 0, r + 1)
            .stroke({ width: 1.4, color: theme.haloPinned, alpha: 0.85 });
        }

        // Layer 3 — invisible hit-target (slightly oversized for easy hover).
        const hit = new Graphics({
          interactive: true,
          eventMode: "static",
          hitArea: new Circle(0, 0, Math.max(r + 4, 8)),
          cursor: "pointer",
          label: n.id,
        });
        // Need a drawn shape for pixi to receive pointer events even though
        // alpha is 0 — fillalpha 0.001 keeps it pickable but invisible.
        hit.circle(0, 0, Math.max(r + 4, 8)).fill({ color: 0xffffff, alpha: 0.001 });

        const label = new Text({
          text: n.text,
          style: {
            fontSize: LABEL_FONT_SIZE,
            fill: theme.text,
            fontFamily: LABEL_FONT_FAMILY,
            fontStyle: "italic",
            fontWeight: "500",
            // dropShadow not in v8 base options — we rely on the warm bg
            // contrast + label background to stay legible.
          },
          anchor: { x: 0.5, y: -0.55 },
          alpha: 0,
          interactive: false,
          eventMode: "none",
          resolution: window.devicePixelRatio * 2,
        });
        label.scale.set(1);

        hit.on("pointerover", () => {
          updateHover(n.id);
          propsRef.current.onNodeHover?.(n.id);
        });
        hit.on("pointerout", () => {
          updateHover(null);
          propsRef.current.onNodeHover?.(null);
        });

        haloContainer.addChild(halo);
        nodeContainer.addChild(gfx);
        hitContainer.addChild(hit);
        labelContainer.addChild(label);
        nodeData.push({
          sim: n,
          gfx,
          halo,
          hit,
          label,
          color: `#${col.toString(16).padStart(6, "0")}`,
          alpha: opacityFor(n),
          active: false,
          radius: r,
        });
      }

      // Adjacency map — built once, reused per frame to compute the
      // "1-hop subgraph" for whichever node is in focus (selected OR
      // hovered). Computing this in the frame loop instead of on hover
      // events means single-click-to-select also lights up edges.
      const adjacency = new Map<string, Set<string>>();
      for (const l of links) {
        const gfx = new Graphics({ interactive: false, eventMode: "none" });
        linkContainer.addChild(gfx);
        linkData.push({ sim: l, gfx, alpha: EDGE_ALPHA_REST, active: false });
        const a = l.source.id;
        const b = l.target.id;
        if (!adjacency.has(a)) adjacency.set(a, new Set());
        if (!adjacency.has(b)) adjacency.set(b, new Set());
        adjacency.get(a)!.add(b);
        adjacency.get(b)!.add(a);
      }

      // Hover handlers no longer carry state — they just stash the id
      // and bump a redraw flag. The frame loop is the single source of
      // truth for active/alpha so click and hover behave identically.
      function updateHover(id: string | null) {
        hoveredId = id;
      }

      // ── Drag (pin / unpin) ────────────────────────────────────────
      select<HTMLCanvasElement, unknown>(app.canvas).call(
        drag<HTMLCanvasElement, unknown>()
          .container(() => app.canvas)
          .subject(() => {
            if (!hoveredId) return undefined;
            return nodes.find((n) => n.id === hoveredId);
          })
          .on("start", (event) => {
            const subj = event.subject as SimNode;
            if (!event.active) simulation.alphaTarget(0.3).restart();
            subj.fx = subj.x ?? 0;
            subj.fy = subj.y ?? 0;
            subj.__initialDragPos = {
              x: subj.x ?? 0,
              y: subj.y ?? 0,
              fx: subj.fx ?? null,
              fy: subj.fy ?? null,
            };
            dragStartTime = performance.now();
          })
          .on("drag", (event) => {
            const subj = event.subject as SimNode;
            const init = subj.__initialDragPos;
            if (!init) return;
            subj.fx = init.x + (event.x - init.x) / currentTransform.k;
            subj.fy = init.y + (event.y - init.y) / currentTransform.k;
          })
          .on("end", (event) => {
            const subj = event.subject as SimNode;
            if (!event.active) simulation.alphaTarget(0);
            const elapsed = performance.now() - dragStartTime;
            const moved =
              subj.__initialDragPos &&
              Math.hypot(
                (subj.fx ?? 0) - subj.__initialDragPos.x,
                (subj.fy ?? 0) - subj.__initialDragPos.y,
              ) > 12; // bumped from 6 — too easy to mis-pin during a pan

            if (elapsed < 250 && !moved) {
              // Click — release pin. Decide single vs double based on
              // last-click age and target. Single = highlight only;
              // double = open (peek card / inspector).
              subj.fx = null;
              subj.fy = null;
              const now = performance.now();
              if (lastClickId === subj.id && now - lastClickAt < DBL_CLICK_MS) {
                propsRef.current.onNodeDoubleClick?.(subj.id);
                lastClickAt = 0;
                lastClickId = null;
              } else {
                propsRef.current.onNodeClick?.(subj.id);
                lastClickAt = now;
                lastClickId = subj.id;
              }
            } else {
              // Real drag — keep the node pinned where the user dropped it.
              const cur = loadPinned();
              cur[subj.id] = { x: subj.fx ?? 0, y: subj.fy ?? 0 };
              savePinned(cur);
            }
          }),
      );

      // ── Zoom ──────────────────────────────────────────────────────
      const z = zoom<HTMLCanvasElement, unknown>()
        .extent([[0, 0], [width, height]])
        .scaleExtent(ZOOM_EXTENT)
        .on("zoom", ({ transform }) => {
          currentTransform = transform;
          stage.scale.set(transform.k, transform.k);
          stage.position.set(transform.x, transform.y);
        });

      select<HTMLCanvasElement, unknown>(app.canvas).call(z);
      // Initial transform — fit the cloud roughly into view.
      // We measure the spread after pre-tick and scale to fit ~80%.
      const initialFit = computeInitialFit(nodes, width, height);
      select<HTMLCanvasElement, unknown>(app.canvas).call(z.transform, initialFit);

      // ── Background-click → clear selection ────────────────────────
      // Fires only on a true tap (no movement, short duration) when no
      // node was under the cursor at click time. d3-zoom processes pan
      // first; if it didn't drag, the pointer up reaches us cleanly.
      let bgDown: { x: number; y: number; t: number } | null = null;
      const onCanvasDown = (e: PointerEvent) => {
        bgDown = { x: e.clientX, y: e.clientY, t: performance.now() };
      };
      const onCanvasUp = (e: PointerEvent) => {
        if (!bgDown) return;
        const dt = performance.now() - bgDown.t;
        const moved = Math.hypot(e.clientX - bgDown.x, e.clientY - bgDown.y);
        bgDown = null;
        // Tap heuristic: short, no movement, no node under cursor.
        if (dt < 250 && moved < 6 && hoveredId === null) {
          propsRef.current.onBackgroundClick?.();
        }
      };
      app.canvas.addEventListener("pointerdown", onCanvasDown);
      app.canvas.addEventListener("pointerup", onCanvasUp);
      cleanups.push(() => {
        app.canvas.removeEventListener("pointerdown", onCanvasDown);
        app.canvas.removeEventListener("pointerup", onCanvasUp);
      });

      // ── Frame loop ────────────────────────────────────────────────
      let stopRaf = false;
      // Pixi's grab cursor only kicks in when a Graphics has cursor set;
      // for empty-canvas pan we set it on the canvas element directly.
      app.canvas.style.cursor = "grab";

      // Reusable scratch buffer for label-collision placement.
      // World-space rectangles (post-stage-transform) — we test against
      // these in priority order so the most important labels always win
      // a contested pixel.
      type Rect = { x: number; y: number; w: number; h: number };
      const placedRects: Rect[] = [];
      const rectsOverlap = (a: Rect, b: Rect) =>
        Math.abs(a.x - b.x) * 2 < a.w + b.w &&
        Math.abs(a.y - b.y) * 2 < a.h + b.h;

      const loop = (time: number) => {
        if (stopRaf) return;

        // Honour live freeze state.
        if (propsRef.current.frozen) simulation.alpha(0);

        // ── Derive focus state once per frame ────────────────────────
        // selectedId wins (sticky after click), else hoveredId. From
        // there, recompute the 1-hop neighbour set and stamp `active`
        // on every node + link so the rest of the loop reads a single
        // source of truth.
        const k = currentTransform.k;
        const showAllLabels = k > LABEL_K_FULL;
        const showHubLabels = k > LABEL_K_HUBS;
        const selectedId = propsRef.current.selectedId ?? null;
        const focusId = selectedId ?? hoveredId ?? null;
        const q = propsRef.current.highlightQuery?.trim().toLowerCase();
        const focusNeighbours = focusId ? adjacency.get(focusId) ?? null : null;

        // Stamp active onto links + nodes (drives both edge alpha
        // and node alpha below).
        for (const l of linkData) {
          if (focusId === null) {
            l.active = false;
          } else {
            const a = (l.sim.source as SimNode).id;
            const b = (l.sim.target as SimNode).id;
            l.active = a === focusId || b === focusId;
          }
        }
        for (const n of nodeData) {
          if (focusId === null) {
            n.active = false;
          } else {
            n.active =
              n.sim.id === focusId ||
              (focusNeighbours ? focusNeighbours.has(n.sim.id) : false);
          }
        }

        // ── Pass 1: positions + halos + node alpha ────────────────────
        for (const n of nodeData) {
          const x = n.sim.x ?? 0;
          const y = n.sim.y ?? 0;
          n.gfx.position.set(x, y);
          n.hit.position.set(x, y);
          n.halo.position.set(x, y);
          n.label.position.set(x, y);
          // Counter-scale the label so it stays at constant pixel size
          // across the zoom range.
          n.label.scale.set(1 / k);

          const isHovered = n.sim.id === hoveredId;
          const isSelected = n.sim.id === selectedId;

          // Node alpha — focus subgraph stays bright, rest dims.
          // Pre-focus alpha respects the search/dim predicates.
          let nodeAlpha = opacityFor(n.sim);
          if (focusId !== null) nodeAlpha = n.active ? 1 : HOVER_DIM_ALPHA;
          n.gfx.alpha = nodeAlpha;

          // Halo — fresh per frame because alpha changes with
          // hover/selection state. Cheap (Graphics.clear is GPU-side).
          n.halo.clear();
          if (isSelected) {
            n.halo
              .circle(0, 0, n.radius + 6)
              .stroke({ width: 2, color: theme.haloSelected, alpha: 0.55 })
              .circle(0, 0, n.radius + 12)
              .stroke({ width: 1, color: theme.haloSelected, alpha: 0.18 });
          } else if (isHovered) {
            n.halo
              .circle(0, 0, n.radius + 5)
              .stroke({ width: 1.5, color: theme.haloHover, alpha: 0.65 })
              .circle(0, 0, n.radius + 11)
              .stroke({ width: 1, color: theme.haloHover, alpha: 0.22 });
          }
        }

        // ── Pass 2: priority-sorted label placement with collision ──
        // Priority tiers (higher first):
        //   1000  hovered
        //    900  selected
        //    800  search match
        //    700  1-hop neighbour of focus (hovered OR selected)
        //    600  hub at LABEL_K_HUBS zoom
        //    500  any node at LABEL_K_FULL zoom
        //      0  no label
        // Within a tier, larger nodes win (degree → importance).
        placedRects.length = 0;

        const candidates: Array<{ n: NodeRender; prio: number }> = [];
        for (const n of nodeData) {
          const isHovered = n.sim.id === hoveredId;
          const isSelected = n.sim.id === propsRef.current.selectedId;
          const isNeighbour = focusId !== null && n.active;
          const isHub = n.sim.degree >= HUB_DEGREE_THRESHOLD;
          const isHighlighted =
            !!q && (n.sim.text ?? "").toLowerCase().includes(q);

          let prio = 0;
          if (isHovered) prio = 1000;
          else if (isSelected) prio = 900;
          else if (isHighlighted) prio = 800;
          else if (isNeighbour) prio = 700;
          else if (isHub && showHubLabels) prio = 600;
          else if (showAllLabels) prio = 500;

          if (prio > 0) {
            // Tiebreak by node degree so hubs in the focus subgraph win
            // a contested label over a leaf neighbour.
            prio += Math.min(n.sim.degree, 99);
            candidates.push({ n, prio });
          } else {
            n.label.alpha = 0;
          }
        }
        candidates.sort((a, b) => b.prio - a.prio);

        // World-space label dimensions — approximate (no getBounds).
        // 0.55 is the average glyph-width factor for italic serif at
        // the default weight; close enough for collision purposes.
        const labelH = (LABEL_FONT_SIZE * 1.35) / k;
        for (const { n } of candidates) {
          const text = n.sim.text ?? "";
          const labelW = Math.max(
            (text.length * LABEL_FONT_SIZE * 0.55) / k,
            10 / k,
          );
          const x = n.sim.x ?? 0;
          const y = (n.sim.y ?? 0) - n.radius - labelH * 0.55;
          const rect: Rect = { x, y, w: labelW, h: labelH };
          let collides = false;
          for (let i = 0; i < placedRects.length; i++) {
            if (rectsOverlap(rect, placedRects[i])) {
              collides = true;
              break;
            }
          }
          if (collides) {
            n.label.alpha = 0;
          } else {
            n.label.alpha = 1;
            placedRects.push(rect);
          }
        }

        // Link redraw — single pass, hierarchy from focus state.
        // Alpha tiers:
        //   no focus       → EDGE_ALPHA_REST (0.18, calm weave)
        //   active edge    → EDGE_ALPHA_ACTIVE (0.85, bright violet)
        //   inactive edge  → EDGE_ALPHA_DIM (0.05, fades into background)
        for (const l of linkData) {
          const a = l.sim.source as SimNode;
          const b = l.sim.target as SimNode;
          const ax = a.x ?? 0;
          const ay = a.y ?? 0;
          const bx = b.x ?? 0;
          const by = b.y ?? 0;
          const alpha =
            focusId === null
              ? EDGE_ALPHA_REST
              : l.active
                ? EDGE_ALPHA_ACTIVE
                : EDGE_ALPHA_DIM;
          l.gfx
            .clear()
            .moveTo(ax, ay)
            .lineTo(bx, by)
            .stroke({
              alpha,
              width: l.active ? EDGE_WIDTH * 1.4 : EDGE_WIDTH,
              color: l.active ? theme.linkActive : theme.link,
            });
        }

        void time;
        app.renderer.render(stage);
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);

      // ── Resize observer ───────────────────────────────────────────
      const ro = new ResizeObserver(() => {
        if (!host) return;
        const w = host.clientWidth || width;
        const h = host.clientHeight || height;
        app.renderer.resize(w, h);
        z.extent([[0, 0], [w, h]]);
      });
      ro.observe(host);
      cleanups.push(() => ro.disconnect());

      cleanups.push(() => {
        stopRaf = true;
        simulation.stop();
        try {
          app.destroy(true);
        } catch {
          /* ignore */
        }
      });
    })();

    return () => {
      cancelled = true;
      cleanups.forEach((fn) => {
        try {
          fn();
        } catch {
          /* ignore */
        }
      });
    };
    // We re-init when graph identity changes; live updates (selectedId,
    // frozen, dimPredicate, hover, mode) read from propsRef.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphKey, layoutMode]);

  return (
    <div
      ref={containerRef}
      className="w-full h-full"
      style={{ position: "relative", overflow: "hidden" }}
    />
  );
}

// ── helpers ───────────────────────────────────────────────────────────

function nodeRadius(n: { degree: number; importance: number; pinned: boolean }) {
  // Scale subtly with degree so hubs are visibly larger than leaves —
  // the only signal in the unzoomed cloud.
  const base = 5 + Math.sqrt(n.degree) * 1.4;
  const imp = Math.min(3, (n.importance ?? 5) / 3);
  return base + imp + (n.pinned ? 1.2 : 0);
}

function computeInitialFit(
  nodes: { x?: number; y?: number }[],
  width: number,
  height: number,
): ZoomTransform {
  // Sample bounds from already pre-ticked positions; pick a scale that
  // fits the cloud at ~85% of the viewport, then translate so the
  // cloud's centre lands at the viewport's centre.
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  let count = 0;
  for (const n of nodes) {
    const x = n.x;
    const y = n.y;
    if (x === undefined || y === undefined) continue;
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
    count++;
  }
  if (count === 0 || !Number.isFinite(minX)) {
    return zoomIdentity.translate(width / 2, height / 2);
  }
  const spreadX = Math.max(maxX - minX, 1);
  const spreadY = Math.max(maxY - minY, 1);
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  const k = Math.min(
    (width * 0.85) / spreadX,
    (height * 0.85) / spreadY,
    1.2, // never zoom in past 1.2x on first paint — keeps the constellation feel
  );
  const tx = width / 2 - cx * k;
  const ty = height / 2 - cy * k;
  return zoomIdentity.translate(tx, ty).scale(k);
}

// Re-export for callers that want to mutate/persist pinned positions.
export const __clearPinnedPositions = () => savePinned({});
