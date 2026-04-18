/** Force-directed graph view — in-house SVG + Verlet simulation, zero deps.
 *
 *  Simulation runs via requestAnimationFrame until it cools off (or the user
 *  drags a node — then it warms up again). Drag keeps nodes pinned where
 *  released.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LazyBrainGraph, LazyBrainNote } from "../../api";
import { ForceSimulation, type SimNode } from "./ForceSimulation";

interface Props {
  graph: LazyBrainGraph;
  onSelect?: (nodeId: string) => void;
  width?: number;
  height?: number;
  notesById?: Record<string, LazyBrainNote>;
}

interface DragState {
  id: string;
  offsetX: number;
  offsetY: number;
}

export function GraphView({
  graph,
  onSelect,
  width = 720,
  height = 480,
  notesById,
}: Props) {
  const simRef = useRef<ForceSimulation | null>(null);
  const frameRef = useRef<number | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const [, setTick] = useState(0);

  // Rebuild simulation when the graph shape changes
  const sim = useMemo(() => {
    const s = new ForceSimulation(
      graph.nodes.map((n) => ({ id: n.id, pinned: false })),
      graph.edges.map((e) => ({ source: e.source, target: e.target })),
      { width, height },
    );
    simRef.current = s;
    return s;
  }, [graph, width, height]);

  // Animation loop
  useEffect(() => {
    let alive = true;
    let idleFrames = 0;
    const loop = () => {
      if (!alive) return;
      const s = simRef.current;
      if (!s) return;
      s.step();
      setTick((t) => (t + 1) % 1_000_000);
      if (s.cooled()) {
        idleFrames += 1;
        if (idleFrames > 30 && !dragRef.current) {
          return; // fully cooled, stop animating
        }
      } else {
        idleFrames = 0;
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

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<SVGGElement>, id: string) => {
      const node = nodeById.get(id);
      if (!node) return;
      const svg = (e.currentTarget.ownerSVGElement as SVGSVGElement) || null;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      dragRef.current = {
        id,
        offsetX: e.clientX - rect.left - node.x,
        offsetY: e.clientY - rect.top - node.y,
      };
      (e.currentTarget as SVGGElement).setPointerCapture(e.pointerId);
    },
    [nodeById],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<SVGGElement>) => {
      const drag = dragRef.current;
      if (!drag) return;
      const svg = (e.currentTarget.ownerSVGElement as SVGSVGElement) || null;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const x = e.clientX - rect.left - drag.offsetX;
      const y = e.clientY - rect.top - drag.offsetY;
      sim.pin(drag.id, x, y);
      // Keep the sim warm while dragging
      if (frameRef.current === null) {
        const loop = () => {
          sim.step();
          setTick((t) => (t + 1) % 1_000_000);
          frameRef.current = requestAnimationFrame(loop);
        };
        frameRef.current = requestAnimationFrame(loop);
      }
    },
    [sim],
  );

  const handlePointerUp = useCallback(() => {
    dragRef.current = null;
  }, []);

  if (graph.nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-text-muted text-sm">
        No notes to graph yet. Save a note with a
        <code className="mx-1 text-accent">[[wikilink]]</code>
        to start your brain.
      </div>
    );
  }

  return (
    <svg
      width={width}
      height={height}
      className="bg-bg-secondary rounded-lg select-none"
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerLeave={handlePointerUp}
    >
      {/* Edges */}
      {graph.edges.map((edge, idx) => {
        const s = nodeById.get(edge.source);
        const t = nodeById.get(edge.target);
        if (!s || !t) return null;
        return (
          <line
            key={`e-${idx}`}
            x1={s.x}
            y1={s.y}
            x2={t.x}
            y2={t.y}
            stroke="currentColor"
            strokeOpacity={0.2}
            strokeWidth={1}
            className="text-text-muted"
          />
        );
      })}

      {/* Nodes */}
      {graph.nodes.map((node) => {
        const simNode = nodeById.get(node.id);
        if (!simNode) return null;
        const note = notesById?.[node.id];
        const label = (note?.title || node.label || "").trim();
        const r = node.pinned ? 9 : 5 + Math.min(4, node.importance / 3);
        return (
          <g
            key={node.id}
            transform={`translate(${simNode.x},${simNode.y})`}
            onPointerDown={(e) => handlePointerDown(e, node.id)}
            onClick={(e) => {
              if (dragRef.current) return;
              e.stopPropagation();
              onSelect?.(node.id);
            }}
            className="cursor-grab active:cursor-grabbing"
          >
            <circle
              r={r}
              className={
                node.is_root
                  ? "fill-accent"
                  : node.pinned
                  ? "fill-accent"
                  : "fill-text-muted"
              }
              opacity={node.is_root ? 1 : 0.8}
            />
            {label && (
              <text
                x={r + 4}
                y={4}
                className="fill-text-primary pointer-events-none"
                style={{ fontSize: "10px" }}
              >
                {label.length > 22 ? label.slice(0, 22) + "…" : label}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
