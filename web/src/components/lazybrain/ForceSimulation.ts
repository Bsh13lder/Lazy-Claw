/** Orbital "observatory" simulation — nodes on concentric rings that
 *  slowly rotate at distinct cadences. Adds richer introspection than the
 *  previous version so the UI can render orbit labels, per-orbit counts,
 *  and identify "hub" nodes (top-k by degree) with zero extra passes.
 *
 *  Galaxy-belt spread: when a ring carries more nodes than fit at ~42px
 *  arc spacing, radial jitter scales up so the ring fans into a thicker
 *  belt instead of stacking nodes on top of each other. Combined with
 *  ±28% angular slot scatter so dense rings never look like a clockface.
 *
 *  Public API:
 *    nodes, edges, step(), cooled(), pin(id,x,y), unpin(id), warm(),
 *    setHover(id|null), center(), orbitRadii(), orbitMeta(),
 *    isolateOrbit(idx|null), hubIds(k), degreeOf(id)
 */
export interface SimNode {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  pinned?: boolean;
  orbit: number;
  angle: number;
  radius: number;
  wobblePhase: number;
}

export interface SimEdge {
  source: string;
  target: string;
}

export type SimMode = "orbital" | "force";

export interface SimOptions {
  width: number;
  height: number;
  /** Map a node id to an orbit index (0 inner .. NUM_ORBITS-1 outer).
   *  Only used in `mode: "orbital"`. */
  orbitOf?: (id: string) => number;
  /** "orbital" → concentric rings rotating at distinct cadences (default).
   *  "force"   → spring + repulsion + gravity, true Obsidian-style layout. */
  mode?: SimMode;
  /** Optional node id to anchor at the canvas center (skipped by every
   *  force pass — perfect for "the hub is the sun" in force mode).      */
  pinCenter?: string;
  /** Previously-saved positions, keyed by node id. Applied after the
   *  default seeding so known nodes skip the random scatter and open at
   *  the coordinates the user left them. Unknown nodes keep their seed. */
  savedPositions?: Record<string, [number, number]>;
}

export interface OrbitMeta {
  index: number;
  radius: number;
  nodeCount: number;
  /** Period in seconds at 60fps. */
  periodSec: number;
}

export const NUM_ORBITS = 4;

// Angular velocity per orbit, radians per step at ~60fps. Outer = slower.
// Periods: ~42s, ~58s, ~77s, ~104s. Slow enough to feel calm, visible.
const OMEGA = [0.0025, 0.00181, 0.00136, 0.00101];

// ── Cooldown thresholds (force mode) ──────────────────────────────────
// Average per-node speed below this px/tick → "quiet" frame. Using a
// per-node average (not an absolute total) means cooling triggers for
// any graph size — 10 notes or 500 notes. 0.06 px/tick = 1.8 px/sec of
// average drift, well below visual perception.
const COOL_THRESHOLD_PER_NODE = 0.06;
// Velocity below this px/tick in an integrate step → snap to rest.
// Prevents perpetual numerical residue from gravity + springs + repulsion
// leaking ~0.5 px/tick of jitter forever after equilibrium.
const REST_DEADBAND = 0.08;
// Number of consecutive quiet frames before declaring cooled.
const COOL_TICKS = 45;

export class ForceSimulation {
  readonly nodes: SimNode[];
  readonly edges: SimEdge[];
  private byId: Map<string, SimNode>;
  private cx: number;
  private cy: number;
  private w: number;
  private h: number;
  private orbitSpeedMul: number[] = new Array(NUM_ORBITS).fill(1);
  private tick = 0;
  private _orbitCounts: number[] = new Array(NUM_ORBITS).fill(0);
  private _degree: Map<string, number> = new Map();
  private _byDegreeDesc: string[] = [];
  private _isolatedOrbit: number | null = null;
  private _mode: SimMode;
  private _pinCenter: string | null;
  // Reusable scratch buffers — allocated ONCE in the constructor and zeroed
  // each tick. Previously stepForce allocated fresh `new Array(N).fill(0)`
  // every frame (≈500 entries × 30fps × 2 arrays = heavy GC pressure).
  // Float32Array is also cheaper to iterate than boxed number arrays.
  private _fx!: Float32Array;
  private _fy!: Float32Array;
  // Edge list as index pairs — precomputed in the constructor so stepForce
  // doesn't rebuild a Map<string, number> from scratch every frame.
  private _edgePairs!: Int32Array;
  /** Force-mode cooldown — counts consecutive ticks where total kinetic
   *  energy is below COOL_THRESHOLD. Once it crosses COOL_TICKS the sim
   *  is "cooled" and the renderer can stop reconciling React on every
   *  RAF frame. Reset to 0 by warm() (hover, drag, or any user action
   *  that should re-stir the layout). Orbital mode never cools (always
   *  rotating by design). */
  private _quietTicks = 0;

  constructor(
    nodes: Array<{ id: string; pinned?: boolean }>,
    edges: SimEdge[],
    options: SimOptions,
  ) {
    this.w = options.width;
    this.h = options.height;
    this.cx = this.w / 2;
    this.cy = this.h / 2;
    this._mode = options.mode ?? "orbital";
    this._pinCenter = options.pinCenter ?? null;
    const orbitOf = options.orbitOf ?? (() => 3);

    // Bucket by orbit with stable hash-based order so rebuilds don't
    // reshuffle positions visually.
    const buckets: Array<Array<{ id: string; pinned?: boolean }>> =
      Array.from({ length: NUM_ORBITS }, () => []);
    for (const n of nodes) {
      const o = Math.max(0, Math.min(NUM_ORBITS - 1, orbitOf(n.id)));
      buckets[o].push(n);
    }
    for (const b of buckets) b.sort((a, c) => hash(a.id) - hash(c.id));

    // Galaxy-style ring radii — pushed outward so the inner core breathes
    // and dense rings have room to fan out into a belt rather than pile up.
    const base = Math.min(this.w, this.h) * 0.14;
    const radii = [base * 1.1, base * 2.0, base * 2.95, base * 3.9];

    this.nodes = [];
    if (this._mode === "force") {
      // Force mode: seed all nodes with a small random jitter around the
      // canvas center. The force pass will sort them out into clusters
      // over the next ~60 ticks. No ring math involved.
      for (let o = 0; o < NUM_ORBITS; o++) {
        this._orbitCounts[o] = buckets[o].length;
      }
      const allBucketed = ([] as Array<{ id: string; pinned?: boolean }>).concat(
        ...buckets,
      );
      for (const n of allBucketed) {
        const a = hash(n.id + "fa") * Math.PI * 2;
        const r = 80 + hash(n.id + "fr") * 220;
        this.nodes.push({
          id: n.id,
          orbit: 0, // unused in force mode but required by the type
          angle: 0,
          radius: 0,
          wobblePhase: hash(n.id + "w") * Math.PI * 2,
          x: this.cx + Math.cos(a) * r,
          y: this.cy + Math.sin(a) * r,
          vx: 0,
          vy: 0,
          pinned: n.pinned,
        });
      }
    } else {
      // Orbital mode (original): density-aware galaxy-belt placement.
      for (let o = 0; o < NUM_ORBITS; o++) {
        const bucket = buckets[o];
        const count = bucket.length;
        this._orbitCounts[o] = count;
        const minArcPerNode = 42;
        const ringCircumference = 2 * Math.PI * radii[o];
        const usedPerNode = count > 0 ? ringCircumference / count : ringCircumference;
        const crowding = Math.max(1, minArcPerNode / Math.max(1, usedPerNode));
        const maxJitter = Math.min(72, 18 + crowding * 18);
        const slotScatter = 0.28;
        bucket.forEach((n, idx) => {
          const slot = (idx / Math.max(1, count)) * Math.PI * 2;
          const angleJitter =
            (hash(n.id + "a") - 0.5) *
            (Math.PI * 2 / Math.max(1, count)) *
            slotScatter *
            2;
          const angle = slot + o * 0.41 + angleJitter;
          const rSign = hash(n.id + "s") > 0.5 ? 1 : -1;
          const rJitter = rSign * hash(n.id + "r") * maxJitter;
          const r = radii[o] + rJitter;
          this.nodes.push({
            id: n.id,
            orbit: o,
            angle,
            radius: r,
            wobblePhase: hash(n.id + "w") * Math.PI * 2,
            x: this.cx + Math.cos(angle) * r,
            y: this.cy + Math.sin(angle) * r,
            vx: 0,
            vy: 0,
            pinned: n.pinned,
          });
        });
      }
    }

    this.byId = new Map(this.nodes.map((n) => [n.id, n]));

    // Restore previously-saved coordinates. Applied AFTER the default
    // scatter/orbit seed so nodes the user has already arranged open at
    // their last known spot; nodes not in the map keep the seed and
    // settle in via the force pass.
    const saved = options.savedPositions;
    if (saved) {
      for (const n of this.nodes) {
        const p = saved[n.id];
        if (!p) continue;
        const [sx, sy] = p;
        if (!Number.isFinite(sx) || !Number.isFinite(sy)) continue;
        n.x = sx;
        n.y = sy;
        n.vx = 0;
        n.vy = 0;
        if (this._mode === "orbital") {
          // Keep the orbit metadata coherent with the restored position
          // so orbital rotation math doesn't snap the node back to its
          // seeded angle on the next tick.
          const dx = sx - this.cx;
          const dy = sy - this.cy;
          n.angle = Math.atan2(dy, dx);
          n.radius = Math.max(1, Math.sqrt(dx * dx + dy * dy));
        }
      }
    }

    // Pin the requested center node (force mode hub). Skipped in orbital
    // mode because GraphView pins via its own pin() call there.
    if (this._mode === "force" && this._pinCenter) {
      const hub = this.byId.get(this._pinCenter);
      if (hub) {
        hub.x = this.cx;
        hub.y = this.cy;
        hub.vx = 0;
        hub.vy = 0;
        hub.pinned = true;
      }
    }

    // Filter edges to known nodes, then compute degree.
    this.edges = edges.filter(
      (e) => this.byId.has(e.source) && this.byId.has(e.target),
    );
    for (const e of this.edges) {
      this._degree.set(e.source, (this._degree.get(e.source) ?? 0) + 1);
      this._degree.set(e.target, (this._degree.get(e.target) ?? 0) + 1);
    }
    this._byDegreeDesc = [...this.byId.keys()].sort(
      (a, b) => (this._degree.get(b) ?? 0) - (this._degree.get(a) ?? 0),
    );

    // Scratch buffers + precomputed edge index pairs. Allocated once here
    // so the hot loop in stepForce() stays allocation-free.
    const N = this.nodes.length;
    this._fx = new Float32Array(N);
    this._fy = new Float32Array(N);
    const nodeIndex: Map<string, number> = new Map(
      this.nodes.map((n, i) => [n.id, i]),
    );
    this._edgePairs = new Int32Array(this.edges.length * 2);
    let ep = 0;
    for (const e of this.edges) {
      const i = nodeIndex.get(e.source);
      const j = nodeIndex.get(e.target);
      if (i === undefined || j === undefined) continue;
      this._edgePairs[ep++] = i;
      this._edgePairs[ep++] = j;
    }
    // Trim trailing unused slots if any edges were dropped after the filter.
    if (ep < this._edgePairs.length) {
      this._edgePairs = this._edgePairs.slice(0, ep);
    }
  }

  setHover(id: string | null): void {
    this.orbitSpeedMul = new Array(NUM_ORBITS).fill(1);
    if (id) {
      const n = this.byId.get(id);
      if (n) this.orbitSpeedMul[n.orbit] = 0;
    }
  }

  /** Isolate one orbit (others freeze and are dimmed by the renderer).
   *  Pass null to release. */
  isolateOrbit(idx: number | null): void {
    this._isolatedOrbit = idx;
  }

  isolatedOrbit(): number | null {
    return this._isolatedOrbit;
  }

  step(): this {
    this.tick += 1;
    if (this._mode === "force") {
      // Skip the expensive O(N²) physics pass once the layout has cooled —
      // but keep applying the cheap rigid rotation around the canvas
      // center every tick so the constellation looks like a real solar
      // system, slowly revolving whether it's converging or settled.
      if (!this.cooled()) this.stepForce();
      this.applyRestRotation();
    } else {
      this.stepOrbital();
    }
    return this;
  }

  /** Slow rigid rotation of every non-pinned node around the canvas
   *  center. Not a physics force — just a 2D rotation matrix per node,
   *  O(N) per tick. Period ≈ 150s for a full revolution, below the
   *  threshold where motion feels unsettling but above "frozen". */
  private applyRestRotation(): void {
    const omega = 0.00075; // radians per tick
    const cosO = Math.cos(omega);
    const sinO = Math.sin(omega);
    for (const n of this.nodes) {
      if (n.pinned) continue;
      const dx = n.x - this.cx;
      const dy = n.y - this.cy;
      n.x = this.cx + dx * cosO - dy * sinO;
      n.y = this.cy + dx * sinO + dy * cosO;
    }
  }

  private stepOrbital(): void {
    const t = this.tick;
    for (const n of this.nodes) {
      if (n.pinned) continue;
      let mul = this.orbitSpeedMul[n.orbit];
      if (this._isolatedOrbit !== null && n.orbit !== this._isolatedOrbit) {
        mul = 0.15;
      }
      n.angle += OMEGA[n.orbit] * mul;
      const breath =
        1 + 0.016 * Math.sin(t * 0.008 + n.orbit * 1.4 + n.wobblePhase);
      const r = n.radius * breath;
      n.x = this.cx + Math.cos(n.angle) * r;
      n.y = this.cy + Math.sin(n.angle) * r;
    }
  }

  private stepForce(): void {
    // Force-directed pass — three forces per node:
    //   1. Gentle gravity toward canvas center keeps everyone on-screen.
    //   2. Pairwise repulsion (Coulomb 1/r²) keeps nodes from overlapping.
    //   3. Edge springs pull linked nodes toward SPRING_LEN apart, so
    //      densely-linked sub-graphs collapse into local clusters around
    //      the pinned hub. This is the "moons orbiting the sun" feel.
    // Plus damping + velocity cap so the system settles instead of jittering.
    // Stronger repulsion + longer spring rest length so clusters actually
    // breathe around the sun instead of piling on top of it. The hub stays
    // clearly visible at center; direct-linked notes sit ~130px out.
    // Radial equilibrium mode — every node wants to sit at distance
    // R_TARGET from the decorative sun at canvas center. Nodes too far
    // out get pulled in; nodes too close get pushed out. This produces
    // a ring/disk of notes around the sun instead of a pile ON TOP of
    // the sun (which is what linear center-gravity gave us). Linked
    // clusters still bunch together via the edge-spring pass below,
    // the bunching just happens ON the ring.
    const REPULSION_K   = 1800;
    const REPULSION_MIN = 22;
    const SPRING_K      = 0.028;
    const SPRING_LEN    = 115;
    const GRAVITY_K     = 0.006;
    const DAMPING       = 0.92;
    const V_MAX         = 5.5;
    const R_TARGET      = Math.min(this.w, this.h) * 0.28;

    const N = this.nodes.length;
    // Reuse preallocated scratch buffers — zero in place instead of
    // allocating fresh arrays every tick. Float32Array.fill is faster
    // than a loop in V8 and doesn't churn the GC.
    const fx = this._fx;
    const fy = this._fy;
    fx.fill(0);
    fy.fill(0);

    // Radial spring — pull each node toward distance R_TARGET from the
    // canvas center. Replaces linear "pull to origin" so nodes form a
    // ring around the sun rather than piling on it.
    for (let i = 0; i < N; i++) {
      const n = this.nodes[i];
      const dxC = n.x - this.cx;
      const dyC = n.y - this.cy;
      // Inline sqrt — Math.hypot is polymorphic + overflow-safe, slow in
      // tight loops. Our coords never overflow float32, so plain sqrt wins.
      const r = Math.sqrt(dxC * dxC + dyC * dyC) || 0.001;
      const rErr = r - R_TARGET;       // +ve: too far out, -ve: too close in
      const rad = -GRAVITY_K * rErr;   // -ve pulls toward center, +ve pushes out
      fx[i] += (dxC / r) * rad;
      fy[i] += (dyC / r) * rad;
    }

    // Pairwise repulsion — O(N²), fine up to ~500 nodes.
    const minD2 = REPULSION_MIN * REPULSION_MIN;
    for (let i = 0; i < N; i++) {
      const a = this.nodes[i];
      const ax = a.x;
      const ay = a.y;
      for (let j = i + 1; j < N; j++) {
        const b = this.nodes[j];
        let dx = b.x - ax;
        let dy = b.y - ay;
        let d2 = dx * dx + dy * dy;
        if (d2 < minD2) {
          if (d2 < 0.0001) { dx = (i - j) * 0.5; dy = (j - i) * 0.5; d2 = 1; }
          const scale = Math.sqrt(minD2 / d2);
          dx *= scale; dy *= scale; d2 = minD2;
        }
        const f = REPULSION_K / d2;
        const d = Math.sqrt(d2);
        const ux = dx / d, uy = dy / d;
        fx[i] -= ux * f; fy[i] -= uy * f;
        fx[j] += ux * f; fy[j] += uy * f;
      }
    }

    // Edge springs — pull linked endpoints to SPRING_LEN apart. This is
    // what makes related notes cluster together as moons. We iterate a
    // preflattened Int32Array of [i0,j0,i1,j1,...] pairs built in the
    // constructor, so no Map lookups in the hot path.
    const edgePairs = this._edgePairs;
    const EP = edgePairs.length;
    for (let k = 0; k < EP; k += 2) {
      const i = edgePairs[k];
      const j = edgePairs[k + 1];
      const a = this.nodes[i];
      const b = this.nodes[j];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.001;
      const delta = d - SPRING_LEN;
      const f = SPRING_K * delta;
      const ux = dx / d, uy = dy / d;
      fx[i] += ux * f; fy[i] += uy * f;
      fx[j] -= ux * f; fy[j] -= uy * f;
    }

    // Integrate — apply force, damp, cap, advance. Sum total speed so we
    // can detect equilibrium and stop driving React re-renders. The
    // deadband below is THE key trick: force sims in steady state leak
    // tiny residual velocities forever (gravity vs springs vs repulsion
    // never cancel to exactly zero in float math). Snapping low speeds
    // to rest is what lets `cooled()` ever fire.
    let totalSpeed = 0;
    let unpinnedCount = 0;
    for (let i = 0; i < N; i++) {
      const n = this.nodes[i];
      if (n.pinned) continue;
      unpinnedCount += 1;
      n.vx = (n.vx + fx[i]) * DAMPING;
      n.vy = (n.vy + fy[i]) * DAMPING;
      const speed = Math.sqrt(n.vx * n.vx + n.vy * n.vy);
      if (speed > V_MAX) {
        n.vx = (n.vx / speed) * V_MAX;
        n.vy = (n.vy / speed) * V_MAX;
        n.x += n.vx;
        n.y += n.vy;
        totalSpeed += V_MAX;
      } else if (speed < REST_DEADBAND) {
        // Snap to rest — prevents perpetual numerical jitter that would
        // otherwise keep `_quietTicks` from ever accumulating.
        n.vx = 0;
        n.vy = 0;
        // Position unchanged — node stays put.
      } else {
        n.x += n.vx;
        n.y += n.vy;
        totalSpeed += speed;
      }
    }
    // Cool-down ratchet — per-node average so the threshold works for any
    // graph size. Any frame with motion above threshold resets the counter.
    // Once we cross COOL_TICKS the renderer stops re-rendering until
    // something pings warm() (hover, drag, mode change, etc.).
    const avgSpeed = unpinnedCount > 0 ? totalSpeed / unpinnedCount : 0;
    if (avgSpeed < COOL_THRESHOLD_PER_NODE) {
      this._quietTicks += 1;
    } else {
      this._quietTicks = 0;
    }
  }

  /** True once the force sim has settled (60 consecutive quiet frames).
   *  Renderer uses this to skip per-frame React re-renders on a static
   *  layout. Always false in orbital mode (perpetual rotation). */
  cooled(): boolean {
    return this._mode === "force" && this._quietTicks >= COOL_TICKS;
  }

  pin(id: string, x: number, y: number): void {
    const n = this.byId.get(id);
    if (!n) return;
    n.x = x;
    n.y = y;
    n.pinned = true;
    const dx = x - this.cx;
    const dy = y - this.cy;
    n.angle = Math.atan2(dy, dx);
    n.radius = Math.max(40, Math.sqrt(dx * dx + dy * dy));
  }

  unpin(id: string): void {
    const n = this.byId.get(id);
    if (n) n.pinned = false;
  }

  /** Re-stir the force sim — resets the cooldown counter so the renderer
   *  starts ticking again. Called on hover, drag, filter change, etc.
   *  No-op in orbital mode (which never cools). */
  warm(): void {
    this._quietTicks = 0;
  }

  /** Overlay saved positions onto existing nodes without rebuilding the
   *  sim. Used when server-side positions arrive after construction —
   *  localStorage gives us an instant first paint, server data wins on
   *  the overlay. Nodes not in the map are left alone so the force pass
   *  can continue settling them. */
  applyPositions(saved: Record<string, [number, number]>): void {
    for (const n of this.nodes) {
      const p = saved[n.id];
      if (!p) continue;
      const [sx, sy] = p;
      if (!Number.isFinite(sx) || !Number.isFinite(sy)) continue;
      n.x = sx;
      n.y = sy;
      n.vx = 0;
      n.vy = 0;
      if (this._mode === "orbital") {
        const dx = sx - this.cx;
        const dy = sy - this.cy;
        n.angle = Math.atan2(dy, dx);
        n.radius = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      }
    }
    // Settle any unscaled neighbours.
    this._quietTicks = 0;
  }

  /** Snapshot {id: [x, y]} for persistence. */
  positionsSnapshot(): Record<string, [number, number]> {
    const out: Record<string, [number, number]> = {};
    for (const n of this.nodes) out[n.id] = [n.x, n.y];
    return out;
  }

  /** Rescale all node positions to a new canvas size without rebuilding
   *  the sim. Previously every resize event re-seeded positions from
   *  scratch, which made window-drag feel like a full reset. This
   *  proportionally moves each node relative to the new center so the
   *  layout the user settled on is preserved. Called from GraphView when
   *  the ResizeObserver fires. */
  resize(w: number, h: number): void {
    if (w <= 0 || h <= 0) return;
    if (w === this.w && h === this.h) return;
    const oldCx = this.cx;
    const oldCy = this.cy;
    const newCx = w / 2;
    const newCy = h / 2;
    // Scale around the old center, then translate to the new center. If
    // the aspect ratio changed, scale by min() to keep nodes visible.
    const sx = w / this.w;
    const sy = h / this.h;
    const s = Math.min(sx, sy);
    for (const n of this.nodes) {
      n.x = newCx + (n.x - oldCx) * s;
      n.y = newCy + (n.y - oldCy) * s;
    }
    this.w = w;
    this.h = h;
    this.cx = newCx;
    this.cy = newCy;
    // Resize may unsettle tight clusters — re-warm so any residual
    // forces can redistribute instead of snapping suddenly.
    this._quietTicks = 0;
  }

  orbitRadii(): number[] {
    const base = Math.min(this.w, this.h) * 0.14;
    return [base * 1.1, base * 2.0, base * 2.95, base * 3.9];
  }

  orbitMeta(): OrbitMeta[] {
    const radii = this.orbitRadii();
    return radii.map((r, i) => ({
      index: i,
      radius: r,
      nodeCount: this._orbitCounts[i] ?? 0,
      periodSec: (Math.PI * 2) / OMEGA[i] / 60,
    }));
  }

  center(): { cx: number; cy: number } {
    return { cx: this.cx, cy: this.cy };
  }

  /** Top-k node ids by degree (precomputed). Zero-cost at call time. */
  hubIds(k: number): string[] {
    return this._byDegreeDesc.slice(0, k);
  }

  degreeOf(id: string): number {
    return this._degree.get(id) ?? 0;
  }
}

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return ((h >>> 0) % 10000) / 10000;
}
