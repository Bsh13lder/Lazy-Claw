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

/** Per-node visual radius hint passed in at construction. Drives the
 *  per-node collision pass so hubs (r≈27) push neighbours out further
 *  than leaves (r≈9). Without this the global REPULSION_MIN clamp left
 *  small nodes only ~14px of breathing room — labels overlapped on
 *  any cluster denser than a few notes. */
export interface SimNodeInput {
  id: string;
  pinned?: boolean;
  /** Visual radius in px. Defaults to 12 if omitted. */
  r?: number;
  /** Optional node-kind hint. Drives per-node mass scaling and per-edge
   *  spring length so rollup hubs (aggregations of unrelated sub-graphs)
   *  sit further from their sources and from other rollups, instead of
   *  collapsing into a tight blob the way a uniform force model would
   *  pull them. Pass "rollup" for weekly/topic, "monthly-rollup" for
   *  monthly. Omit for plain notes. */
  kind?: "rollup" | "monthly-rollup";
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
  // Per-node visual radius (Float32Array for cache-friendly hot-loop reads).
  // Used by stepForce for the per-node collision pass. Index-aligned with
  // this.nodes.
  private _radii!: Float32Array;
  // Per-node repulsion mass — scales the Coulomb force a node both
  // emits and feels. Computed as `1 + sqrt(deg) * 0.4` so a hub with
  // 130 children (mass ≈ 5.6) pushes much harder than a leaf (mass 1).
  // This is the trick that makes hubs visibly separate into "islands"
  // instead of stacking into one center blob — vanilla d3-force with
  // a uniform charge doesn't do this; Obsidian's graph view does.
  private _repelMass!: Float32Array;
  // Per-edge spring strength scale — d3-force convention:
  //   strength = 1 / min(deg(a), deg(b))
  // High-degree → low-degree edges (hub→leaf) keep full strength, so
  // leaves orbit their hub tightly. Hub→hub edges (rare) get weak
  // strength, so cross-cluster connections don't collapse the hubs
  // back together. Index-aligned to _edgePairs/2.
  private _edgeStrengths!: Float32Array;
  // Per-edge spring TARGET length (px). Index-aligned to _edgePairs/2.
  // Most edges use SPRING_LEN; rollup-incident edges (where one or both
  // endpoints carry kind=rollup/monthly-rollup) use SPRING_LEN * 1.4 so
  // rollup hubs sit further from their sources and from each other.
  // Without this, a 30-source rollup pulls all 30 sources tight enough
  // that two rollups touching overlapping source sets collapse into the
  // same neighborhood.
  private _edgeLengths!: Float32Array;
  /** Force-mode cooldown — counts consecutive ticks where total kinetic
   *  energy is below COOL_THRESHOLD. Once it crosses COOL_TICKS the sim
   *  is "cooled" and the renderer can stop reconciling React on every
   *  RAF frame. Reset to 0 by warm() (hover, drag, or any user action
   *  that should re-stir the layout). Orbital mode never cools (always
   *  rotating by design). */
  private _quietTicks = 0;
  /** d3-force-style alpha. All forces are multiplied by this each tick;
   *  it decays from 1 → 0 over ~120 ticks (`1 - 0.0228` per tick), so
   *  forces shrink as the layout converges. Without this, sustained
   *  close-range Coulomb forces keep re-energizing the spring system
   *  forever and the graph oscillates. With it, kinetic energy is
   *  bounded and the sim settles like Logseq/Obsidian. */
  private _alpha = 1.0;
  /** True if the constructor restored at least one node position from
   *  ``options.savedPositions``. The renderer reads this to decide
   *  whether to start the graph in "frozen physics" mode (we already
   *  have a settled layout) or run a one-time settle pass. */
  private _usedSavedPositions = false;
  /** True after `_finalizeCollisions()` has run for the current settle
   *  cycle. Reset by warm()/nudge()/reflow() so each restir gets its
   *  own finalize pass. Without this flag, the alpha-min fallback path
   *  below would re-run the sweep every frame after alpha bottoms out. */
  private _finalizeRan = false;
  /** Per-node "settled" flag — 1 when speed has dropped below
   *  REST_DEADBAND in the last integrate step. Used by the pairwise
   *  loops (Coulomb + collision) to fast-skip pairs where both
   *  endpoints are at rest, and by the renderer's RAF write loop to
   *  skip setAttribute on unmoved nodes. Reset by warm/nudge/reflow.
   *  Index-aligned with this.nodes. */
  private _settled!: Uint8Array;

  constructor(
    nodes: SimNodeInput[],
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
    const buckets: SimNodeInput[][] =
      Array.from({ length: NUM_ORBITS }, () => []);
    for (const n of nodes) {
      const o = Math.max(0, Math.min(NUM_ORBITS - 1, orbitOf(n.id)));
      buckets[o].push(n);
    }
    for (const b of buckets) b.sort((a, c) => hash(a.id) - hash(c.id));
    // Index radii by id once — looked up after this.nodes is built so
    // _radii is index-aligned with the final node order.
    const radiusOf = new Map<string, number>();
    for (const n of nodes) {
      radiusOf.set(n.id, Number.isFinite(n.r) ? (n.r as number) : 12);
    }

    // Galaxy-style ring radii — pushed outward so the inner core breathes
    // and dense rings have room to fan out into a belt rather than pile
    // up. Spread is wider on outer rings (more lesson/fact nodes there),
    // and bigger overall so halos don't collide with neighbours.
    const base = Math.min(this.w, this.h) * 0.15;
    const radii = [base * 1.3, base * 2.45, base * 3.7, base * 5.0];

    this.nodes = [];
    if (this._mode === "force") {
      // Force mode: seed all nodes with a small random jitter around the
      // canvas center. The force pass will sort them out into clusters
      // over the next ~60 ticks. No ring math involved.
      for (let o = 0; o < NUM_ORBITS; o++) {
        this._orbitCounts[o] = buckets[o].length;
      }
      const allBucketed = ([] as SimNodeInput[]).concat(...buckets);
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
        // 56px minimum arc-per-node + a wider radial belt so a hovered
        // halo never collides with the next planet on the same ring.
        const minArcPerNode = 56;
        const ringCircumference = 2 * Math.PI * radii[o];
        const usedPerNode = count > 0 ? ringCircumference / count : ringCircumference;
        const crowding = Math.max(1, minArcPerNode / Math.max(1, usedPerNode));
        const maxJitter = Math.min(110, 26 + crowding * 26);
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
        this._usedSavedPositions = true;
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
    this._settled = new Uint8Array(N);
    // Build per-node radius array index-aligned with this.nodes. Default
    // to 12 if the caller didn't pass a hint (legacy paths or unknown ids).
    this._radii = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      this._radii[i] = radiusOf.get(this.nodes[i].id) ?? 12;
    }
    const nodeIndex: Map<string, number> = new Map(
      this.nodes.map((n, i) => [n.id, i]),
    );
    this._edgePairs = new Int32Array(this.edges.length * 2);
    this._edgeStrengths = new Float32Array(this.edges.length);
    this._edgeLengths = new Float32Array(this.edges.length);
    // Per-node "kind" flag — true when the input carried kind=rollup or
    // kind=monthly-rollup. Used both for the repel-mass bump below and
    // for the per-edge length decision in this loop. Index-aligned.
    const isRollup = new Uint8Array(N);
    {
      const kindByIdx = new Map(
        nodes.map((n) => [n.id, n.kind] as const),
      );
      for (let i = 0; i < N; i++) {
        const k = kindByIdx.get(this.nodes[i].id);
        if (k === "rollup" || k === "monthly-rollup") isRollup[i] = 1;
      }
    }
    let ep = 0;
    let es = 0;
    // Index-aligned helper: degree by node index, used for both edge
    // strengths (1/min) and per-node repulsion mass below.
    const degByIdx = new Int32Array(N);
    for (let i = 0; i < N; i++) {
      degByIdx[i] = this._degree.get(this.nodes[i].id) ?? 0;
    }
    // Spring-length base — same value used by stepForce. Multiplied by
    // 1.4 for rollup-incident edges so rollup hubs sit further from
    // their sources. Anything that consumes this constant in stepForce
    // now reads from _edgeLengths instead.
    const SPRING_LEN_BASE = 130;
    const ROLLUP_LEN_MUL = 1.4;
    for (const e of this.edges) {
      const i = nodeIndex.get(e.source);
      const j = nodeIndex.get(e.target);
      if (i === undefined || j === undefined) continue;
      this._edgePairs[ep++] = i;
      this._edgePairs[ep++] = j;
      // d3-force link strength: 1 / min(deg(a), deg(b)). Hub→leaf
      // (min=1) → strength 1.0. Hub→hub (min=50+) → strength ≤ 0.02
      // → barely pulls, lets hubs separate under repulsion.
      const minDeg = Math.max(1, Math.min(degByIdx[i], degByIdx[j]));
      this._edgeStrengths[es] = 1 / minDeg;
      // Rollup-incident → longer rest length. Either endpoint counts.
      this._edgeLengths[es] = (isRollup[i] || isRollup[j])
        ? SPRING_LEN_BASE * ROLLUP_LEN_MUL
        : SPRING_LEN_BASE;
      es += 1;
    }
    // Trim trailing unused slots if any edges were dropped after the filter.
    if (ep < this._edgePairs.length) {
      this._edgePairs = this._edgePairs.slice(0, ep);
      this._edgeStrengths = this._edgeStrengths.slice(0, es);
      this._edgeLengths = this._edgeLengths.slice(0, es);
    }

    // Per-node repulsion mass — heavier nodes (hubs) push everything
    // around them harder. sqrt scaling keeps the spread reasonable:
    // leaf (deg 1) = 1.4, average (deg 4) = 1.8, hub (deg 130) = 5.6.
    // The 0.4 coefficient was tuned to make hub clusters visibly
    // separate without flying apart on first frame.
    //
    // Rollup nodes get an additional ×1.5 multiplier so two rollups
    // (each already a hub via their wikilink content) push each other
    // apart harder than two equally-weighted concept hubs would. The
    // kind flag handles this without breaking degree-based scaling.
    this._repelMass = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      const base = 1 + Math.sqrt(degByIdx[i]) * 0.4;
      this._repelMass[i] = isRollup[i] ? base * 1.5 : base;
    }

    // Saved positions: start with a moderate alpha so we get the
    // benefit of remembered layouts but the new force model still has
    // enough energy to redistribute clusters that were saved under the
    // OLD forces (e.g., a previous "everything stacked in one blob"
    // layout will fan out into proper hub-islands on first reload).
    // Cold start (no saved): full alpha=1.0 from random scatter.
    if (this._usedSavedPositions && this._mode === "force") {
      this._alpha = 0.4;
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
      // Force-only motion. After the spring layout settles (cooled),
      // step() is a complete no-op — no rest rotation, no work at all.
      // Combined with the GraphView idle-frame skip, this means a
      // settled graph costs literally zero CPU/GPU until the user
      // interacts. This matches Obsidian's behaviour.
      if (!this.cooled()) this.stepForce();
    } else {
      this.stepOrbital();
    }
    return this;
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
    // d3-force / Logseq-style layout. The critical detail is `alpha` —
    // all forces are multiplied by this scalar, which decays from 1
    // toward 0 over ~120 ticks. As alpha drops, forces shrink, the
    // sim's kinetic energy is bounded, and oscillation dies out. This
    // is how Logseq + Obsidian settle without shaking.
    //
    //   1. Linear gravity toward canvas centre — keeps everyone on-screen.
    //   2. Pairwise repulsion (Coulomb 1/r²) with per-node-radius clamp.
    //   3. Edge springs pull linked nodes toward SPRING_LEN apart.
    // Adaptive decay — large graphs cool ~50% faster (≈80 ticks vs 120)
    // because users scroll/zoom big graphs rather than watching them
    // animate, and the per-tick cost is dominated by the O(N²) pairwise
    // pass which we want to retire ASAP.
    const N = this.nodes.length;
    const ALPHA_DECAY   = N > 200 ? 0.034 : 0.0228;
    const ALPHA_MIN     = 0.005;
    // Base repulsion. Multiplied per-pair by mass[i]*mass[j] so hubs
    // (mass≈5) push other hubs out an order of magnitude harder than
    // leaf-leaf. This is what creates Obsidian-style cluster islands
    // instead of one big stacked blob.
    // Bumped REPULSION_K 360 → 480 and COLLIDE_PAD 8 → 16 so the
    // layout fans out further between nodes. Previously dense clusters
    // (e.g. a 30-source rollup hub) packed dots near-touching, which
    // made connections unreadable. The Obsidian / Logseq look has
    // visibly more breathing room — these knobs match that.
    const REPULSION_K   = 480;
    const COLLIDE_K     = 2.0;
    const COLLIDE_PAD   = 16;
    // Base spring. Multiplied per-edge by 1/min(deg) so hub→leaf
    // springs stay strong (leaves orbit) while hub→hub springs weaken
    // (hubs separate). d3-force convention. 0.08 balances against the
    // new mass-scaled repulsion — hub→leaf nets ~1.6× the old uniform
    // 0.05; hub→hub nets ~0.03× (50× weaker than before, lets hubs
    // drift apart).
    const SPRING_K      = 0.08;
    // SPRING_LEN moved to per-edge `_edgeLengths` (constructor) so
    // rollup-incident edges can sit further apart than concept-link
    // edges. Most edges still resolve to 130px there.
    const GRAVITY_K     = 0.008; // softer pull so cluster islands fan out
    const DAMPING       = 0.88;
    const V_MAX         = 4.0;

    // Stop integrating entirely once alpha drops below ALPHA_MIN. The
    // existing _quietTicks counter then accumulates → cooled() fires →
    // RAF loop stops reading sim positions → zero CPU at rest.
    if (this._alpha < ALPHA_MIN) {
      // Fallback finalize path. The cool-down ratchet below normally
      // runs `_finalizeCollisions()` after 45 quiet frames — but on a
      // saved-positions cold start the sim oscillates too long for
      // _quietTicks to ever accumulate, and alpha decays out from
      // under us first. Catch that case here: when alpha first bottoms
      // out and we haven't finalized yet, run the sweep once and
      // promote the sim straight to cooled state so the renderer stops
      // calling step() entirely. Subsequent ticks early-return cheaply.
      if (!this._finalizeRan) {
        this._finalizeCollisions();
        this._finalizeRan = true;
        this._quietTicks = COOL_TICKS;
      }
      return;
    }
    const alpha = this._alpha;
    this._alpha *= 1 - ALPHA_DECAY;

    // Reuse preallocated scratch buffers — zero in place instead of
    // allocating fresh arrays every tick. Float32Array.fill is faster
    // than a loop in V8 and doesn't churn the GC.
    const fx = this._fx;
    const fy = this._fy;
    fx.fill(0);
    fy.fill(0);

    // Linear gravity toward canvas centre — keeps everyone on-screen
    // and pulls less-connected leaves inward. Replaces the old radial
    // R_TARGET spring (which forced all nodes onto a single ring and
    // left the centre empty — not how Obsidian looks).
    for (let i = 0; i < N; i++) {
      const n = this.nodes[i];
      fx[i] += (this.cx - n.x) * GRAVITY_K;
      fy[i] += (this.cy - n.y) * GRAVITY_K;
    }

    // Pairwise repulsion — Coulomb 1/r², O(N²), fine up to ~500 nodes.
    // Two upgrades vs vanilla Coulomb:
    //   1. Per-node radius-aware minD clamp (replaces old global =14).
    //   2. Per-pair mass scaling (mass[i]*mass[j]) — hubs push hubs
    //      ~30× harder than leaves push leaves, which is what makes
    //      Obsidian-style "cluster islands" instead of one giant blob.
    const radii = this._radii;
    const mass = this._repelMass;
    const settled = this._settled;
    // Far-cutoff: 1/r² Coulomb is < 1% of peak force beyond ~6× the
    // typical clamp distance (~50px → cutoff 300px). Skipping pairs
    // beyond that cutoff is mathematically near-identical (sub-pixel
    // drift over the whole settle cycle) and cheaper than computing
    // the full divide. A single squared-distance compare per pair.
    const FAR_CUTOFF_SQ = 300 * 300;
    for (let i = 0; i < N; i++) {
      const a = this.nodes[i];
      const ax = a.x;
      const ay = a.y;
      const ri = radii[i];
      const mi = mass[i];
      const aSettled = settled[i];
      for (let j = i + 1; j < N; j++) {
        // Both endpoints at rest → this pair contributes nothing new
        // to the force field this tick. Skip the math entirely.
        if (aSettled === 1 && settled[j] === 1) continue;
        const b = this.nodes[j];
        let dx = b.x - ax;
        let dy = b.y - ay;
        let d2 = dx * dx + dy * dy;
        if (d2 > FAR_CUTOFF_SQ) continue;
        const minD = (ri + radii[j]) * COLLIDE_K + COLLIDE_PAD;
        const minD2 = minD * minD;
        if (d2 < minD2) {
          if (d2 < 0.0001) { dx = (i - j) * 0.5; dy = (j - i) * 0.5; d2 = 1; }
          const scale = Math.sqrt(minD2 / d2);
          dx *= scale; dy *= scale; d2 = minD2;
        }
        const f = (REPULSION_K * mi * mass[j]) / d2;
        const d = Math.sqrt(d2);
        const ux = dx / d, uy = dy / d;
        fx[i] -= ux * f; fy[i] -= uy * f;
        fx[j] += ux * f; fy[j] += uy * f;
      }
    }

    // Edge springs — d3-force-style: per-edge strength = SPRING_K *
    // (1/min(deg(a), deg(b))). Strong hub→leaf pulls keep leaves
    // tightly orbiting their hub; weak hub→hub pulls let separate
    // hub clusters drift apart under repulsion. This is THE fix for
    // "all hubs collapse into one center blob".
    //
    // Per-edge target length comes from `_edgeLengths` — most edges
    // use SPRING_LEN; rollup-incident edges sit ~40% further (180px
    // vs 130px) so rollup hubs and their sources have visible breathing
    // room and two rollups touching the same source set don't pile up.
    const edgePairs = this._edgePairs;
    const edgeStrengths = this._edgeStrengths;
    const edgeLengths = this._edgeLengths;
    const EP = edgePairs.length;
    for (let k = 0; k < EP; k += 2) {
      const i = edgePairs[k];
      const j = edgePairs[k + 1];
      const a = this.nodes[i];
      const b = this.nodes[j];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.001;
      const idx = k >> 1;
      const delta = d - edgeLengths[idx];
      const f = SPRING_K * edgeStrengths[idx] * delta;
      const ux = dx / d, uy = dy / d;
      fx[i] += ux * f; fy[i] += uy * f;
      fx[j] -= ux * f; fy[j] -= uy * f;
    }

    // Integrate — apply alpha-scaled force, damp, cap, advance. Sum
    // total speed so we can detect equilibrium and stop driving React
    // re-renders. The deadband below is THE key trick: force sims in
    // steady state leak tiny residual velocities forever (gravity vs
    // springs vs repulsion never cancel to exactly zero in float
    // math). Snapping low speeds to rest is what lets `cooled()` ever
    // fire.
    let totalSpeed = 0;
    let unpinnedCount = 0;
    for (let i = 0; i < N; i++) {
      const n = this.nodes[i];
      if (n.pinned) {
        // Pinned nodes never move → always count as settled for the
        // pairwise fast-path. The settled flag is the source of truth
        // the collision/repulsion loops read.
        settled[i] = 1;
        continue;
      }
      unpinnedCount += 1;
      // Forces scaled by alpha — d3-force convention. As alpha decays
      // from 1 → 0 the energy injected per tick shrinks toward zero.
      n.vx = (n.vx + fx[i] * alpha) * DAMPING;
      n.vy = (n.vy + fy[i] * alpha) * DAMPING;
      const speed = Math.sqrt(n.vx * n.vx + n.vy * n.vy);
      if (speed > V_MAX) {
        n.vx = (n.vx / speed) * V_MAX;
        n.vy = (n.vy / speed) * V_MAX;
        n.x += n.vx;
        n.y += n.vy;
        totalSpeed += V_MAX;
        settled[i] = 0;
      } else if (speed < REST_DEADBAND) {
        // Snap to rest — prevents perpetual numerical jitter that would
        // otherwise keep `_quietTicks` from ever accumulating.
        n.vx = 0;
        n.vy = 0;
        // Position unchanged — node stays put.
        settled[i] = 1;
      } else {
        n.x += n.vx;
        n.y += n.vy;
        totalSpeed += speed;
        settled[i] = 0;
      }
    }
    // Hard collision pass — d3-force-style position correction with
    // a uniform spatial grid so the inner loop only checks neighbours
    // within a 3×3 cell window instead of every other node. The math
    // and the visual outcome are identical to the previous O(N²)
    // pass; the only thing that changed is which pairs we visit.
    //
    // Cell size = 80px — comfortably larger than the maximum minD
    // (≈70px for two hub nodes plus padding) so any overlapping pair
    // is guaranteed to share a cell or sit in adjacent cells. Bigger
    // cells = fewer cells = more pairs per cell = O(N²) again. Smaller
    // cells = lots of empty cells = wasted lookups. 80px hits the
    // sweet spot for our radius range.
    //
    // Iterations: the first pass resolves direct overlaps; the second
    // cleans up cascading overlaps the first pass introduces. Two is
    // overkill on big graphs that won't see many overlaps per tick
    // — drop to 1 for N>200 to halve the cost in the regime where
    // it matters most.
    const COLLIDE_ITERATIONS = N > 200 ? 1 : 2;
    // Use the full pad as the push pad (was 0.5×). At COLLIDE_PAD=16
    // that's 16px guaranteed gap between any two node circles, which
    // lets the user trace edges between adjacent dots instead of
    // having to zoom in. Matches Obsidian's spacing.
    const COLLIDE_PUSH_PAD = COLLIDE_PAD;
    const CELL = 80;
    const CELL_HASH = 1000003;
    for (let iter = 0; iter < COLLIDE_ITERATIONS; iter++) {
      // (Re)build the grid each iteration — positions shift between
      // iterations so the bucket assignments need to refresh.
      const cellMap = new Map<number, number[]>();
      for (let i = 0; i < N; i++) {
        const n = this.nodes[i];
        const key = (((n.x / CELL) | 0) * CELL_HASH) + ((n.y / CELL) | 0);
        let arr = cellMap.get(key);
        if (!arr) { arr = []; cellMap.set(key, arr); }
        arr.push(i);
      }
      for (let i = 0; i < N; i++) {
        const a = this.nodes[i];
        const ri = radii[i];
        const aSettled = settled[i];
        const aPinned = a.pinned;
        const cx0 = (a.x / CELL) | 0;
        const cy0 = (a.y / CELL) | 0;
        for (let cdx = -1; cdx <= 1; cdx++) {
          for (let cdy = -1; cdy <= 1; cdy++) {
            const key = ((cx0 + cdx) * CELL_HASH) + (cy0 + cdy);
            const bucket = cellMap.get(key);
            if (!bucket) continue;
            for (let bi = 0; bi < bucket.length; bi++) {
              const j = bucket[bi];
              if (j <= i) continue;
              // Both at rest → no overlap to resolve. (If they were
              // overlapping when they came to rest, the previous tick
              // already pushed them apart; settled implies stable.)
              if (aSettled === 1 && settled[j] === 1) continue;
              const b = this.nodes[j];
              const rj = radii[j];
              const minD = ri + rj + COLLIDE_PUSH_PAD;
              let dx = b.x - a.x;
              let dy = b.y - a.y;
              let d2 = dx * dx + dy * dy;
              const minD2 = minD * minD;
              if (d2 >= minD2) continue;
              if (d2 < 0.0001) {
                dx = (i - j) * 0.5;
                dy = (j - i) * 0.5;
                d2 = dx * dx + dy * dy;
              }
              const d = Math.sqrt(d2);
              const overlap = (minD - d) * 0.5;
              const ux = dx / d;
              const uy = dy / d;
              const bPinned = b.pinned;
              if (aPinned && bPinned) continue;
              if (aPinned) {
                b.x += ux * overlap * 2;
                b.y += uy * overlap * 2;
                settled[j] = 0;
              } else if (bPinned) {
                a.x -= ux * overlap * 2;
                a.y -= uy * overlap * 2;
                settled[i] = 0;
              } else {
                a.x -= ux * overlap;
                a.y -= uy * overlap;
                b.x += ux * overlap;
                b.y += uy * overlap;
                settled[i] = 0;
                settled[j] = 0;
              }
            }
          }
        }
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
    // One last hard collision sweep on the frame we cross into cooled.
    // Without this, leftover stacked dots never get split because cooled()
    // short-circuits step() entirely from the next frame on.
    if (this._quietTicks === COOL_TICKS && !this._finalizeRan) {
      this._finalizeCollisions();
      this._finalizeRan = true;
    }
  }

  /** Hard collision sweep — 6 iterations, no integration. Called once
   *  when the sim crosses into cooled state to guarantee no two nodes
   *  remain visually overlapping. After this runs, cooled() returns
   *  true forever and the renderer never reads positions again. */
  private _finalizeCollisions(): void {
    const N = this.nodes.length;
    const radii = this._radii;
    const PAD = 4;
    for (let iter = 0; iter < 6; iter++) {
      let moved = false;
      for (let i = 0; i < N; i++) {
        const a = this.nodes[i];
        const ri = radii[i];
        for (let j = i + 1; j < N; j++) {
          const b = this.nodes[j];
          const rj = radii[j];
          const minD = ri + rj + PAD;
          let dx = b.x - a.x;
          let dy = b.y - a.y;
          let d2 = dx * dx + dy * dy;
          if (d2 >= minD * minD) continue;
          if (d2 < 0.0001) {
            dx = (i - j) * 0.5;
            dy = (j - i) * 0.5;
            d2 = dx * dx + dy * dy;
          }
          const d = Math.sqrt(d2);
          const overlap = (minD - d) * 0.5;
          const ux = dx / d;
          const uy = dy / d;
          const aPinned = a.pinned;
          const bPinned = b.pinned;
          if (aPinned && bPinned) continue;
          if (aPinned) {
            b.x += ux * overlap * 2;
            b.y += uy * overlap * 2;
          } else if (bPinned) {
            a.x -= ux * overlap * 2;
            a.y -= uy * overlap * 2;
          } else {
            a.x -= ux * overlap;
            a.y -= uy * overlap;
            b.x += ux * overlap;
            b.y += uy * overlap;
          }
          moved = true;
        }
      }
      if (!moved) break;
    }
  }

  /** True once the force sim has settled (60 consecutive quiet frames).
   *  Renderer uses this to skip per-frame React re-renders on a static
   *  layout. Always false in orbital mode (perpetual rotation). */
  cooled(): boolean {
    return this._mode === "force" && this._quietTicks >= COOL_TICKS;
  }

  /** Per-node settled flag — index-aligned with `nodes`. 1 = at rest
   *  (speed below REST_DEADBAND in last integrate, or pinned). The
   *  renderer's RAF write loop uses this to skip setAttribute on
   *  unmoved nodes, dropping per-frame DOM mutations from O(N) to
   *  O(unsettled-N). Reset by warm/nudge/reflow/applyPositions/resize. */
  settledFlags(): Uint8Array {
    return this._settled;
  }

  /** Per-node visual radius — index-aligned with `nodes`. Read by the
   *  canvas renderer (for circle radii) and the canvas hit-test (for
   *  pointer-vs-node distance checks). Same source the collision pass
   *  uses internally, so visual hit area always matches the simulated
   *  radius. */
  radii(): Float32Array {
    return this._radii;
  }

  /** True if the constructor restored at least one node position from
   *  ``options.savedPositions``. Renderer uses this to start frozen
   *  on prior-session graphs (no warm-up settle storm). */
  usedSavedPositions(): boolean {
    return this._usedSavedPositions;
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

  /** Re-stir the force sim — resets the cooldown counter and gives
   *  alpha a small bump so forces have energy to redistribute. Called
   *  on hover, drag, filter change, etc. We use 0.3 (not 1.0) because
   *  the layout is already mostly settled — full re-warm would shake
   *  every node visibly when the user just dragged one. No-op in
   *  orbital mode (which never cools). */
  warm(): void {
    this._quietTicks = 0;
    this._finalizeRan = false;
    if (this._mode === "force") {
      this._alpha = Math.max(this._alpha, 0.3);
      this._settled.fill(0);
    }
  }

  /** Drag-only warm — keeps the sim running but with a much lower
   *  alpha than warm(). Drag fires on every pointermove (often >60Hz);
   *  re-warming to 0.3 each time means full O(N²) repulsion every frame
   *  for 700 nodes. 0.06 is enough for spring redistribution around the
   *  dragged node without firing the full force pass. */
  nudge(): void {
    this._quietTicks = 0;
    this._finalizeRan = false;
    if (this._mode === "force") {
      this._alpha = Math.max(this._alpha, 0.06);
      this._settled.fill(0);
    }
  }

  /** Full re-flow — wakes the sim with fresh energy (alpha=1.0) so the
   *  current force model can fully redistribute existing positions.
   *  Use case: saved positions came from an older / different force
   *  model and look messy with the current one. Keeps every node where
   *  it is right now, then lets springs + repulsion + collisions push
   *  them into a clean layout over ~5 seconds. Pinned nodes stay put;
   *  everything else drifts. */
  reflow(): void {
    this._quietTicks = 0;
    this._finalizeRan = false;
    if (this._mode === "force") {
      this._alpha = 1.0;
      this._settled.fill(0);
    }
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
    // Settle any unscaled neighbours — small alpha bump rather than full
    // re-warm, so newly arrived server positions don't visibly bounce.
    this._quietTicks = 0;
    if (this._mode === "force") {
      this._alpha = Math.max(this._alpha, 0.15);
      this._settled.fill(0);
    }
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
    // Resize may unsettle tight clusters — small alpha bump so any
    // residual forces can redistribute instead of snapping suddenly.
    this._quietTicks = 0;
    if (this._mode === "force") {
      this._alpha = Math.max(this._alpha, 0.2);
      this._settled.fill(0);
    }
  }

  orbitRadii(): number[] {
    // MUST stay in sync with the seeding values in the constructor.
    const base = Math.min(this.w, this.h) * 0.15;
    return [base * 1.3, base * 2.45, base * 3.7, base * 5.0];
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
