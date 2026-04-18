/** Tiny force-directed layout engine, zero deps.
 *
 *  Velocity-Verlet integration with three forces:
 *    - Repulsion (Coulomb-like, O(n²) — swap for Barnes-Hut past 1k nodes)
 *    - Spring attraction along edges
 *    - Gravity toward origin so disconnected components don't drift off-screen
 *
 *  Call `step()` on every animation frame until `cooled()` is true.
 */
export interface SimNode {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  pinned?: boolean;
}

export interface SimEdge {
  source: string;
  target: string;
}

export interface SimOptions {
  width: number;
  height: number;
  repulsion?: number;   // higher = nodes push apart more (default 1500)
  spring?: number;      // 0..1 edge stiffness (default 0.06)
  restLength?: number;  // desired edge length in px (default 120)
  gravity?: number;     // center-pull strength (default 0.015)
  damping?: number;     // velocity decay per step (default 0.85)
}

const DEFAULTS: Required<Omit<SimOptions, "width" | "height">> = {
  repulsion: 1500,
  spring: 0.06,
  restLength: 120,
  gravity: 0.015,
  damping: 0.85,
};

export class ForceSimulation {
  readonly nodes: SimNode[];
  readonly edges: SimEdge[];
  private byId: Map<string, SimNode>;
  private readonly opts: Required<SimOptions>;
  private energy = Infinity;

  constructor(
    nodes: Array<{ id: string; pinned?: boolean }>,
    edges: SimEdge[],
    options: SimOptions,
  ) {
    this.opts = { ...DEFAULTS, ...options };
    const cx = this.opts.width / 2;
    const cy = this.opts.height / 2;
    const radius = Math.min(this.opts.width, this.opts.height) / 3;

    this.nodes = nodes.map((n, idx) => {
      const angle = (idx / Math.max(1, nodes.length)) * Math.PI * 2;
      return {
        id: n.id,
        x: cx + Math.cos(angle) * radius + (Math.random() - 0.5) * 10,
        y: cy + Math.sin(angle) * radius + (Math.random() - 0.5) * 10,
        vx: 0,
        vy: 0,
        pinned: n.pinned,
      };
    });

    this.byId = new Map(this.nodes.map((n) => [n.id, n]));
    // Filter edges to nodes that actually exist
    this.edges = edges.filter(
      (e) => this.byId.has(e.source) && this.byId.has(e.target),
    );
  }

  /** Run one integration step. Returns this for chaining. */
  step(): this {
    const { repulsion, spring, restLength, gravity, damping, width, height } = this.opts;
    const cx = width / 2;
    const cy = height / 2;

    // Reset accumulators
    const forces = this.nodes.map(() => ({ fx: 0, fy: 0 }));

    // O(n²) pairwise repulsion
    for (let i = 0; i < this.nodes.length; i++) {
      const a = this.nodes[i];
      for (let j = i + 1; j < this.nodes.length; j++) {
        const b = this.nodes[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let dist2 = dx * dx + dy * dy;
        if (dist2 < 0.01) {
          dx = (Math.random() - 0.5) * 2;
          dy = (Math.random() - 0.5) * 2;
          dist2 = dx * dx + dy * dy;
        }
        const dist = Math.sqrt(dist2);
        const f = repulsion / dist2;
        const fx = (dx / dist) * f;
        const fy = (dy / dist) * f;
        forces[i].fx += fx;
        forces[i].fy += fy;
        forces[j].fx -= fx;
        forces[j].fy -= fy;
      }
    }

    // Spring attraction along edges
    for (const edge of this.edges) {
      const a = this.byId.get(edge.source);
      const b = this.byId.get(edge.target);
      if (!a || !b) continue;
      const i = this.nodes.indexOf(a);
      const j = this.nodes.indexOf(b);
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const delta = dist - restLength;
      const f = spring * delta;
      const fx = (dx / dist) * f;
      const fy = (dy / dist) * f;
      forces[i].fx += fx;
      forces[i].fy += fy;
      forces[j].fx -= fx;
      forces[j].fy -= fy;
    }

    // Gravity toward center + integration
    let totalEnergy = 0;
    for (let i = 0; i < this.nodes.length; i++) {
      const n = this.nodes[i];
      if (n.pinned) {
        n.vx = 0;
        n.vy = 0;
        continue;
      }
      forces[i].fx += (cx - n.x) * gravity;
      forces[i].fy += (cy - n.y) * gravity;

      n.vx = (n.vx + forces[i].fx) * damping;
      n.vy = (n.vy + forces[i].fy) * damping;
      n.x += n.vx;
      n.y += n.vy;

      // Clamp into viewport
      n.x = Math.max(20, Math.min(width - 20, n.x));
      n.y = Math.max(20, Math.min(height - 20, n.y));

      totalEnergy += n.vx * n.vx + n.vy * n.vy;
    }
    this.energy = totalEnergy;
    return this;
  }

  /** True when the simulation has settled. Tune threshold by dataset size. */
  cooled(threshold = 0.5): boolean {
    return this.energy < threshold * this.nodes.length;
  }

  /** Pin a node to a specific screen position (drag support). */
  pin(id: string, x: number, y: number): void {
    const n = this.byId.get(id);
    if (!n) return;
    n.x = x;
    n.y = y;
    n.vx = 0;
    n.vy = 0;
    n.pinned = true;
  }

  unpin(id: string): void {
    const n = this.byId.get(id);
    if (n) n.pinned = false;
  }
}
