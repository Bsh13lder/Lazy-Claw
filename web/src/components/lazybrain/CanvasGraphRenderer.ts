/** Canvas-2D renderer for the LazyBrain graph view.
 *
 *  One DOM element (the `<canvas>`) replaces the previous ~600+ SVG
 *  elements per render. Drawing is fully imperative — no React reconcile
 *  per frame, no per-element style recalc, no compositor cost beyond a
 *  single layer. Comfortably handles 5000+ nodes at 60fps.
 *
 *  Apart from the entry point `drawGraph`, this module is pure (no React,
 *  no DOM globals besides the canvas context). All inputs flow in via
 *  `DrawState` so the caller controls invalidation.
 *
 *  Coordinate spaces:
 *    - Canvas backing buffer = display × DPR (set by GraphView).
 *    - `setTransform(dpr,0,0,dpr,0,0)` applied once per frame so the
 *      rest of the pipeline paints in CSS pixels.
 *    - `translate(view.tx, view.ty); scale(view.k, view.k)` then
 *      composes pan + zoom on top — every coord we draw with is in
 *      world (sim) space. Stroke widths divide by `view.k` so they
 *      stay visually constant as the user zooms.
 */

export interface DrawNode {
  /** World-space position. */
  x: number;
  y: number;
  /** Visual radius before any focus scale-up. */
  r: number;
  /** Body fill colour (e.g. category ring). */
  fill: string;
  /** Body stroke colour. Empty string = no stroke. */
  stroke: string;
  /** Body stroke width in CSS px (rendered scaled-by-1/k so it stays
   *  visually constant under zoom). */
  strokeWidth: number;
  /** Apply a dashed stroke (rollup-incident nodes). */
  dash: boolean;
  /** Phosphor codepoint to fill on top of the body, or empty. */
  glyph: string;
  /** Date-style numeric label (journal/daily-log) — drawn instead of
   *  glyph when present. */
  badge: string;
  /** Side-label string. Empty means "skip label for this node". */
  label: string;
  /** Hover/selected/match nodes get the emphasised label style. */
  labelEmphasized: boolean;
  /** Dim factor 0..1 — multiplied into ctx.globalAlpha for the body. */
  opacity: number;
  /** Body scale-up under hover/selection (1.0 / 1.08 / 1.18). */
  scale: number;
  /** Render the wider "halo" disc beneath the body. Reserved for the
   *  hovered or selected node. Cheap (one extra arc, ≤ 2 nodes per
   *  frame). */
  haloColor: string;
  haloAlpha: number;
}

export interface DrawEdge {
  ax: number;
  ay: number;
  bx: number;
  by: number;
  /** Bow direction sign (+1 or -1) — alternates per index in the
   *  caller so adjacent edges curve opposite ways. */
  bowSign: 1 | -1;
  stroke: string;
  width: number;
  opacity: number;
  dashed: boolean;
}

export interface DrawState {
  width: number;
  height: number;
  dpr: number;
  view: { tx: number; ty: number; k: number };
  nodes: DrawNode[];
  edges: DrawEdge[];
  /** Global gate: skip glyph rendering below this zoom (text is illegible
   *  + costs the most per-frame). 0.6 is the readable threshold. */
  glyphZoomThreshold: number;
}

/** Single entry point — paints one frame. Caller is responsible for
 *  scheduling (RAF, settled-skip, etc.) and for building the DrawState
 *  inputs from React state + ForceSimulation positions. */
export function drawGraph(
  ctx: CanvasRenderingContext2D,
  state: DrawState,
): void {
  const { width, height, dpr, view, nodes, edges } = state;

  // Reset transform + clear. setTransform is faster than save/restore
  // pairs; we eat the explicit math instead.
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  // Compose pan + zoom. From here, all coordinates are world-space.
  ctx.translate(view.tx, view.ty);
  ctx.scale(view.k, view.k);

  const k = view.k;

  // ── Edges ───────────────────────────────────────────────────────────
  // Two passes:
  //   1. Inactive — faint background wires. Drawn first so active edges
  //      sit above them visually but the network shape stays readable.
  //   2. Active  — lit subgraph. Drawn over the top with one sharp
  //      stroke (no glow halo — that drowned the canvas in purple at
  //      hub rollups with 50+ neighbours). Brightness comes from
  //      contrast with the dim background, not from extra layers.
  for (let i = 0; i < edges.length; i++) {
    const e = edges[i];
    if (e.opacity <= 0.001) continue;
    if (e.opacity > 0.5) continue; // active → second pass
    ctx.globalAlpha = e.opacity;
    ctx.strokeStyle = e.stroke;
    ctx.lineWidth = e.width / k;
    if (e.dashed) ctx.setLineDash([2 / k, 4 / k]);
    else ctx.setLineDash(EMPTY_DASH);
    drawEdgePath(ctx, e);
    ctx.stroke();
  }
  for (let i = 0; i < edges.length; i++) {
    const e = edges[i];
    if (e.opacity <= 0.5) continue;
    ctx.setLineDash(EMPTY_DASH);
    ctx.globalAlpha = e.opacity;
    ctx.strokeStyle = e.stroke;
    ctx.lineWidth = e.width / k;
    drawEdgePath(ctx, e);
    ctx.stroke();
  }
  ctx.setLineDash(EMPTY_DASH);
  ctx.globalAlpha = 1;

  // ── Halos (only for hovered + selected nodes — at most 2 per frame) ─
  // Two-layer glow: a wide diffuse outer ring + a tight inner ring.
  // Together they read as a real luminous halo against the dark
  // canvas, where a single thin ring used to vanish into the background.
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    if (!n.haloColor) continue;
    const r = n.r * n.scale;
    // Outer diffuse — wide, low alpha
    ctx.globalAlpha = n.haloAlpha * 0.45;
    ctx.fillStyle = n.haloColor;
    ctx.beginPath();
    ctx.arc(n.x, n.y, r + 18, 0, TAU);
    ctx.fill();
    // Inner tight — small, full halo alpha
    ctx.globalAlpha = n.haloAlpha;
    ctx.beginPath();
    ctx.arc(n.x, n.y, r + 6, 0, TAU);
    ctx.fill();
  }
  ctx.globalAlpha = 1;

  // ── Node bodies ─────────────────────────────────────────────────────
  // Per-node fill + stroke. Looks expensive but a 2500-node loop here
  // measures ~3ms on M1 — way under our 16ms budget.
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    if (n.opacity <= 0.001) continue;
    const r = n.r * n.scale;
    ctx.globalAlpha = n.opacity;
    ctx.fillStyle = n.fill;
    ctx.beginPath();
    ctx.arc(n.x, n.y, r, 0, TAU);
    ctx.fill();
    if (n.stroke && n.strokeWidth > 0) {
      ctx.strokeStyle = n.stroke;
      ctx.lineWidth = n.strokeWidth / k;
      if (n.dash) ctx.setLineDash([2 / k, 3 / k]);
      else ctx.setLineDash(EMPTY_DASH);
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
  ctx.setLineDash(EMPTY_DASH);

  // ── Glyphs / date badges ────────────────────────────────────────────
  // Skip text rendering when zoomed out — glyphs become unreadable smudges
  // below ~0.6× and the loop costs the most per-frame after edges. The
  // body circle still conveys the category via colour.
  if (k >= state.glyphZoomThreshold) {
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = GLYPH_FILL;
    let lastFont = "";
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.opacity <= 0.001) continue;
      if (!n.glyph && !n.badge) continue;
      const r = n.r * n.scale;
      const isBadge = !!n.badge;
      const fontSize = isBadge
        ? Math.max(9, r * 0.48)
        : Math.max(11, r * 0.95);
      const font = isBadge
        ? `bold ${fontSize}px sans-serif`
        : `${fontSize}px Phosphor`;
      if (font !== lastFont) {
        ctx.font = font;
        lastFont = font;
      }
      ctx.globalAlpha = n.opacity;
      ctx.fillText(isBadge ? n.badge : n.glyph, n.x, n.y);
    }
    ctx.globalAlpha = 1;
  }

  // ── Side labels (hover / selected / search match only) ──────────────
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    if (!n.label) continue;
    const r = n.r * n.scale;
    const fontPx = n.labelEmphasized ? 11 : 9;
    ctx.font = `${n.labelEmphasized ? 600 : 500} ${fontPx}px Inter, system-ui, sans-serif`;
    // Drop shadow — two cheap fills offset by 1px so the label stays
    // legible over any background colour. Avoids the SVG textShadow
    // CSS we used to lean on (which caused per-element repaints).
    ctx.globalAlpha = 0.88;
    ctx.fillStyle = "rgba(0,0,0,0.85)";
    ctx.fillText(n.label, n.x + 1, n.y + r + 7);
    ctx.globalAlpha = 1;
    ctx.fillStyle = n.labelEmphasized
      ? "rgba(245,209,154,0.96)"
      : "rgba(232,213,176,0.82)";
    ctx.fillText(n.label, n.x, n.y + r + 6);
  }
  ctx.globalAlpha = 1;
}

const TAU = Math.PI * 2;
const EMPTY_DASH: number[] = [];
const GLYPH_FILL = "#0a0a0a";

/** Build a quadratic-bow path between two endpoints. Reused by the
 *  inactive + active edge passes so the bow geometry stays consistent. */
function drawEdgePath(ctx: CanvasRenderingContext2D, e: DrawEdge): void {
  const dx = e.bx - e.ax;
  const dy = e.by - e.ay;
  const len = Math.hypot(dx, dy) || 1;
  const bow = len * 0.12 * e.bowSign;
  const mx = (e.ax + e.bx) / 2 + (-dy / len) * bow;
  const my = (e.ay + e.by) / 2 + (dx / len) * bow;
  ctx.beginPath();
  ctx.moveTo(e.ax, e.ay);
  ctx.quadraticCurveTo(mx, my, e.bx, e.by);
}
