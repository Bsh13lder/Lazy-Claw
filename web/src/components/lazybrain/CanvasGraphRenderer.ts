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

export interface DrawZone {
  /** Zone anchor centre in world (sim) coordinates. */
  x: number;
  y: number;
  /** Hex colour for the nebula bloom + label (matches the category palette). */
  color: string;
  /** Display title — small caps at render time. */
  title: string;
  /** Active member count, used for bloom radius + label size. */
  count: number;
}

/** A connected-component "planet system" — hub + orbiting leaves.
 *  Renderer paints a faint dashed orbital ring at `radius` around
 *  (x, y) so the user sees discrete planet systems rather than just
 *  dots in a cloud. Color is the palette colour of the hub's
 *  category; size is rendered as a tiny chip on the ring. */
export interface DrawHub {
  x: number;
  y: number;
  radius: number;
  size: number;
  color: string;
}

export interface DrawState {
  width: number;
  height: number;
  dpr: number;
  view: { tx: number; ty: number; k: number };
  nodes: DrawNode[];
  edges: DrawEdge[];
  /** Stellar-atlas zones — painted UNDER edges + nodes as faint nebula
   *  blooms with a constellation title. Empty array → no zone rendering
   *  (legacy graphs without category metadata). */
  zones?: DrawZone[];
  /** Planet-system orbital rings — one per connected component (size
   *  4..60). Painted between zone blooms and edges so rings frame
   *  their members without occluding them. */
  hubs?: DrawHub[];
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

  // ── Zone blooms + constellation titles ──────────────────────────────
  // Painted UNDER edges + nodes so the dots float over a soft nebula in
  // their category colour. Each zone gets:
  //   1. A radial gradient bloom centred on the anchor — sized by member
  //      count (sqrt-scaled so a 200-member zone isn't 10× the radius
  //      of a 20-member zone).
  //   2. A single-line title rendered in small caps, letter-spaced, in
  //      the zone's category colour at ~32% alpha. Sized larger when
  //      the user is zoomed out so the labels act as a navigational
  //      atlas; fades when zoomed in so the dots take centre stage.
  // Skipped entirely when no zones are passed — keeps legacy paths free.
  const zones = state.zones;
  if (zones && zones.length > 0) {
    // Bloom pass — additive radial gradients. globalCompositeOperation
    // "lighter" makes overlapping nebulas blend like real glow instead
    // of stacking opaque blobs.
    const prevComposite = ctx.globalCompositeOperation;
    ctx.globalCompositeOperation = "lighter";
    for (let i = 0; i < zones.length; i++) {
      const z = zones[i];
      if (z.count <= 0) continue;
      // Bloom radius: 220px floor + 26px per sqrt(count). A 4-member
      // zone reads as a small wisp; a 200-member zone glows as a wide
      // halo. World-space radius — bloom scales with zoom alongside
      // the nodes it backs.
      const r = 220 + Math.sqrt(z.count) * 26;
      const grad = ctx.createRadialGradient(z.x, z.y, 0, z.x, z.y, r);
      grad.addColorStop(0,    hexWithAlpha(z.color, 0.18));
      grad.addColorStop(0.45, hexWithAlpha(z.color, 0.08));
      grad.addColorStop(1,    hexWithAlpha(z.color, 0.00));
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(z.x, z.y, r, 0, TAU);
      ctx.fill();
    }
    ctx.globalCompositeOperation = prevComposite;

    // Title pass — small-caps serif, letter-spaced. Drawn last among
    // zone passes (above bloom, below nodes) so titles live in the
    // background plane but read clearly against the dark canvas.
    // Sized in CSS px (divide by k) so titles stay readable at any
    // zoom — they're navigational, not decorative.
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (let i = 0; i < zones.length; i++) {
      const z = zones[i];
      if (z.count <= 0) continue;
      // Bigger when zoomed out (atlas mode); softer when zoomed in.
      const fontPx = Math.max(11, Math.min(22, 16 / Math.max(0.55, k)));
      ctx.font = `600 ${fontPx}px "Source Serif 4", "Source Serif Pro", Georgia, serif`;
      // Stroke pass for legibility against any colour bloom underneath.
      ctx.lineWidth = (fontPx * 0.18) / k;
      ctx.strokeStyle = "rgba(8, 6, 18, 0.85)";
      ctx.lineJoin = "round";
      const display = formatZoneTitle(z.title, z.count);
      // Stroke first, fill on top — cheap drop-shadow effect without
      // shadowBlur (which is much more expensive on Canvas2D).
      ctx.globalAlpha = 0.85;
      ctx.strokeText(display, z.x, z.y);
      ctx.globalAlpha = z.count >= 6 ? 0.42 : 0.32;
      ctx.fillStyle = z.color;
      ctx.fillText(display, z.x, z.y);
    }
    ctx.globalAlpha = 1;
  }

  // ── Planet-system orbital rings ─────────────────────────────────────
  // One faint dashed ring around each connected-component hub at the
  // average leaf-distance radius. Reads as "this is a planet system"
  // — the user sees discrete orbital trails rather than a featureless
  // cloud of dots. Skipped at low zoom so the rings don't smear.
  const hubs = state.hubs;
  if (hubs && hubs.length > 0 && k >= 0.4) {
    const dash = [3 / k, 5 / k];
    ctx.setLineDash(dash);
    for (let i = 0; i < hubs.length; i++) {
      const h = hubs[i];
      // Ring opacity scales gently with system size so a 4-member
      // family doesn't outshout a 30-member topic system; capped at
      // 0.25 so rings never overpower the nodes themselves.
      const alpha = Math.min(0.25, 0.10 + Math.sqrt(h.size) * 0.025);
      ctx.globalAlpha = alpha;
      ctx.strokeStyle = h.color;
      ctx.lineWidth = 1.0 / k;
      ctx.beginPath();
      ctx.arc(h.x, h.y, h.radius, 0, TAU);
      ctx.stroke();
      // Tiny accent dot at the ring's "12 o'clock" — a subtle marker
      // that the system has structure (hub + orbit), not just chaos.
      ctx.globalAlpha = alpha + 0.18;
      ctx.fillStyle = h.color;
      ctx.beginPath();
      ctx.arc(h.x, h.y - h.radius, 1.6 / k, 0, TAU);
      ctx.fill();
    }
    ctx.setLineDash(EMPTY_DASH);
    ctx.globalAlpha = 1;
  }

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

/** Hex (#rrggbb / #rgb) → rgba string. Used by the zone bloom pass to
 *  build per-zone radial gradients in the category colour. Returns a
 *  safe slate-grey fallback when the hex is malformed so a stray bad
 *  palette entry can never crash the render loop. */
function hexWithAlpha(hex: string, alpha: number): string {
  let r = 100, g = 116, b = 139;
  if (hex.length === 7) {
    r = parseInt(hex.slice(1, 3), 16);
    g = parseInt(hex.slice(3, 5), 16);
    b = parseInt(hex.slice(5, 7), 16);
  } else if (hex.length === 4) {
    r = parseInt(hex[1] + hex[1], 16);
    g = parseInt(hex[2] + hex[2], 16);
    b = parseInt(hex[3] + hex[3], 16);
  }
  if (!Number.isFinite(r)) r = 100;
  if (!Number.isFinite(g)) g = 116;
  if (!Number.isFinite(b)) b = 139;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Render a zone title as small caps with the active member count
 *  trailing in subscript-style. ("Daily log · 12") — letter-spacing is
 *  applied via en-spaces between letters because Canvas2D doesn't
 *  expose a `letterSpacing` property in the way CSS does (yet — the
 *  property exists on modern Chromium but is unreliable cross-browser
 *  for fillText). En-space-padding is fast, deterministic, and visually
 *  matches the Source Serif aesthetic without a property polyfill. */
function formatZoneTitle(title: string, count: number): string {
  const upper = title.toUpperCase();
  const spaced = upper.split("").join(" "); // thin-space between letters
  return `${spaced} · ${count}`;
}

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
