/** Orbital "observatory" simulation — nodes on concentric rings that
 *  slowly rotate at distinct cadences. Adds richer introspection than the
 *  previous version so the UI can render orbit labels, per-orbit counts,
 *  and identify "hub" nodes (top-k by degree) with zero extra passes.
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

export interface SimOptions {
  width: number;
  height: number;
  /** Map a node id to an orbit index (0 inner .. NUM_ORBITS-1 outer). */
  orbitOf?: (id: string) => number;
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
  /** Per-orbit counts — computed once at construction. */
  private _orbitCounts: number[] = new Array(NUM_ORBITS).fill(0);
  /** Per-node degree — computed once at construction. */
  private _degree: Map<string, number> = new Map();
  /** Node ids sorted by degree desc — precomputed for hubIds(). */
  private _byDegreeDesc: string[] = [];
  /** When set, only this orbit's nodes rotate / are visible. */
  private _isolatedOrbit: number | null = null;

  constructor(
    nodes: Array<{ id: string; pinned?: boolean }>,
    edges: SimEdge[],
    options: SimOptions,
  ) {
    this.w = options.width;
    this.h = options.height;
    this.cx = this.w / 2;
    this.cy = this.h / 2;
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

    // Orbit radii — tuned so the four rings feel distinct at typical
    // window sizes. Slightly denser inner ring (core is smaller).
    const base = Math.min(this.w, this.h) * 0.13;
    const radii = [base * 1.0, base * 1.75, base * 2.5, base * 3.2];

    this.nodes = [];
    for (let o = 0; o < NUM_ORBITS; o++) {
      const bucket = buckets[o];
      const count = bucket.length;
      this._orbitCounts[o] = count;
      bucket.forEach((n, idx) => {
        const angle = (idx / Math.max(1, count)) * Math.PI * 2 + o * 0.41;
        const rJitter = (hash(n.id + "r") - 0.5) * 14;
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

    this.byId = new Map(this.nodes.map((n) => [n.id, n]));

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
    const t = this.tick;
    for (const n of this.nodes) {
      if (n.pinned) continue;
      // Orbit rotation — frozen for non-isolated rings when isolation
      // is active, so attention lands on the isolated one.
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
    return this;
  }

  cooled(): boolean {
    return false;
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

  warm(): void {
    /* no-op */
  }

  orbitRadii(): number[] {
    const base = Math.min(this.w, this.h) * 0.13;
    return [base * 1.0, base * 1.75, base * 2.5, base * 3.2];
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
