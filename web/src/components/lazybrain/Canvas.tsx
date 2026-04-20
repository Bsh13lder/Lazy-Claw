/** Obsidian-Canvas-style free-form spatial board for LazyBrain.
 *
 *  Single-board-at-a-time UI. Parent passes a `boardId` (or null for a
 *  fresh board); we handle load → edit → save autosave. React Flow does
 *  the heavy lifting for pan / zoom / select / edge routing.
 *
 *  Two node kinds:
 *   - **text** — inline editable paragraph on the canvas itself
 *   - **note** — reference to an existing LazyBrainNote; clicking opens it
 *
 *  Keyboard:
 *   - `T` → add text node at viewport center
 *   - `N` → add note node (prompts for a title)
 *   - `Del` / `Backspace` → remove selected
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import * as api from "../../api";
import type { CanvasBoardMeta, LazyBrainNote } from "../../api";
import { FileText, Plus, Save, StickyNote, Trash2 } from "lucide-react";

interface CanvasNodeData {
  kind: "text" | "note";
  label: string;
  /** For kind === "note" — LazyBrainNote id we're referencing. */
  noteId?: string;
}

interface Props {
  onOpenNote: (noteId: string) => void;
  resolveLink?: (page: string) => LazyBrainNote | null;
}

const AUTOSAVE_MS = 2000;

export function Canvas({ onOpenNote, resolveLink }: Props) {
  const [boards, setBoards] = useState<CanvasBoardMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [name, setName] = useState("Untitled canvas");
  const [nodes, setNodes] = useState<Node<CanvasNodeData>[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [dirty, setDirty] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  // ── Board list + initial load ─────────────────────────────────────
  const refreshBoards = useCallback(async () => {
    const list = await api.listLazyBrainCanvases();
    setBoards(list);
    return list;
  }, []);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async fetch
    refreshBoards().then((list) => {
      if (cancelled || activeId) return;
      if (list.length > 0) setActiveId(list[0].id);
    });
    return () => {
      cancelled = true;
    };
  }, [refreshBoards, activeId]);

  // Load selected board
  useEffect(() => {
    if (!activeId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- clean slate on board switch
      setNodes([]);
      setEdges([]);
      setName("Untitled canvas");
      return;
    }
    let cancelled = false;
    void api.getLazyBrainCanvas(activeId).then((board) => {
      if (cancelled) return;
      setName(board.name);
      setNodes(
        (board.payload.nodes as Node<CanvasNodeData>[] | undefined) ?? [],
      );
      setEdges((board.payload.edges as Edge[] | undefined) ?? []);
      setDirty(false);
    });
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  // ── Autosave ──────────────────────────────────────────────────────
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!dirty) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      const meta = await api.saveLazyBrainCanvas({
        id: activeId,
        name,
        payload: { nodes, edges },
      });
      setActiveId(meta.id);
      setSavedAt(meta.updated_at);
      setDirty(false);
      void refreshBoards();
    }, AUTOSAVE_MS);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [dirty, activeId, name, nodes, edges, refreshBoards]);

  // ── Change handlers ───────────────────────────────────────────────
  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      setNodes((n) => applyNodeChanges(changes, n));
      setDirty(true);
    },
    [],
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      setEdges((e) => applyEdgeChanges(changes, e));
      setDirty(true);
    },
    [],
  );
  const onConnect = useCallback(
    (c: Connection) => {
      setEdges((e) => addEdge({ ...c, animated: false }, e));
      setDirty(true);
    },
    [],
  );

  const addTextNode = useCallback(() => {
    const id = `t-${Date.now()}`;
    setNodes((n) => [
      ...n,
      {
        id,
        type: "text",
        position: {
          x: 120 + Math.random() * 240,
          y: 120 + Math.random() * 240,
        },
        data: { kind: "text", label: "New text" },
      },
    ]);
    setDirty(true);
  }, []);

  const addNoteNode = useCallback(
    (title?: string) => {
      const t =
        title ?? window.prompt("Which note? (type its title)")?.trim() ?? "";
      if (!t) return;
      const match = resolveLink?.(t) ?? null;
      const id = `n-${Date.now()}`;
      setNodes((n) => [
        ...n,
        {
          id,
          type: "note",
          position: {
            x: 120 + Math.random() * 240,
            y: 120 + Math.random() * 240,
          },
          data: {
            kind: "note",
            label: match?.title || t,
            noteId: match?.id,
          },
        },
      ]);
      setDirty(true);
    },
    [resolveLink],
  );

  const createNewBoard = useCallback(() => {
    setActiveId(null);
    setName(`Canvas ${new Date().toLocaleDateString()}`);
    setNodes([]);
    setEdges([]);
    setDirty(true);
  }, []);

  const deleteBoard = useCallback(async () => {
    if (!activeId) return;
    if (!window.confirm(`Delete canvas "${name}"?`)) return;
    await api.deleteLazyBrainCanvas(activeId);
    setActiveId(null);
    await refreshBoards();
  }, [activeId, name, refreshBoards]);

  // ── Keyboard shortcuts ────────────────────────────────────────────
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      )
        return;
      if (e.key.toLowerCase() === "t") {
        e.preventDefault();
        addTextNode();
      } else if (e.key.toLowerCase() === "n") {
        e.preventDefault();
        addNoteNode();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [addTextNode, addNoteNode]);

  const updateLabel = useCallback((id: string, label: string) => {
    setNodes((arr) =>
      arr.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, label } } : n,
      ),
    );
    setDirty(true);
  }, []);

  const nodeTypes = useMemo(
    () => ({
      text: (props: NodeProps<CanvasNodeData>) => (
        <TextNode {...props} onLabelChange={updateLabel} />
      ),
      note: (props: NodeProps<CanvasNodeData>) => (
        <NoteNode {...props} onOpenNote={onOpenNote} />
      ),
    }),
    [onOpenNote, updateLabel],
  );

  return (
    <div className="h-full w-full flex flex-col bg-bg-primary">
      {/* Toolbar */}
      <div
        className="shrink-0 px-4 py-2 flex items-center gap-2 border-b border-border"
        style={{ background: "rgba(16,185,129,0.04)" }}
      >
        <select
          value={activeId ?? ""}
          onChange={(e) => setActiveId(e.target.value || null)}
          className="bg-bg-secondary text-xs text-text-primary px-2 py-1 rounded border border-border outline-none"
        >
          <option value="">(new canvas)</option>
          {boards.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
        <input
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setDirty(true);
          }}
          placeholder="Canvas name"
          className="bg-transparent text-sm font-semibold text-text-primary outline-none w-60"
        />
        <div className="w-px h-5 bg-border" />
        <button
          onClick={createNewBoard}
          className="h-7 px-2 text-xs rounded bg-bg-secondary text-text-primary hover:bg-bg-hover flex items-center gap-1"
        >
          <Plus size={12} strokeWidth={2} /> new
        </button>
        <button
          onClick={addTextNode}
          className="h-7 px-2 text-xs rounded bg-bg-secondary text-text-primary hover:bg-bg-hover flex items-center gap-1"
          title="Add text (T)"
        >
          <StickyNote size={12} strokeWidth={1.75} /> text
        </button>
        <button
          onClick={() => addNoteNode()}
          className="h-7 px-2 text-xs rounded bg-bg-secondary text-text-primary hover:bg-bg-hover flex items-center gap-1"
          title="Add note reference (N)"
        >
          <FileText size={12} strokeWidth={1.75} /> note
        </button>
        <div className="w-px h-5 bg-border" />
        {activeId && (
          <button
            onClick={deleteBoard}
            className="h-7 px-2 text-xs rounded text-red-400 hover:bg-red-500/10 flex items-center gap-1"
          >
            <Trash2 size={12} strokeWidth={1.75} /> delete
          </button>
        )}
        <div className="ml-auto text-[11px] text-text-muted flex items-center gap-2">
          {dirty ? (
            <span className="text-accent flex items-center gap-1">
              <Save size={11} /> saving…
            </span>
          ) : savedAt ? (
            <span>saved</span>
          ) : (
            <span>new</span>
          )}
          <span>·</span>
          <span>
            {nodes.length} nodes · {edges.length} edges
          </span>
        </div>
      </div>

      {/* Canvas surface */}
      <div className="flex-1 min-h-0">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          nodeTypes={nodeTypes}
          fitView
          proOptions={{ hideAttribution: true }}
          style={{ background: "#0f0f0f" }}
          defaultEdgeOptions={{
            style: { stroke: "#10b981", strokeWidth: 1.5 },
          }}
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={18}
            size={1}
            color="rgba(16,185,129,0.12)"
          />
          <Controls
            style={{
              background: "rgba(24,24,24,0.92)",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 8,
            }}
            showInteractive={false}
          />
          <MiniMap
            pannable
            zoomable
            style={{
              background: "rgba(24,24,24,0.92)",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 8,
            }}
            nodeStrokeColor={() => "#10b981"}
            nodeColor={() => "rgba(16,185,129,0.4)"}
          />
        </ReactFlow>
      </div>

    </div>
  );
}

function TextNode({
  data,
  id,
  onLabelChange,
}: NodeProps<CanvasNodeData> & {
  onLabelChange: (id: string, label: string) => void;
}) {
  return (
    <div
      className="px-3 py-2 rounded-lg min-w-[160px] max-w-[320px]"
      style={{
        background: "rgba(24,24,24,0.95)",
        border: "1px solid rgba(16,185,129,0.25)",
        color: "#ececec",
        fontSize: 13,
        lineHeight: 1.5,
        whiteSpace: "pre-wrap",
        fontFamily: "Inter, system-ui, sans-serif",
      }}
    >
      <textarea
        value={data.label}
        onChange={(e) => onLabelChange(id, e.target.value)}
        rows={3}
        className="w-full bg-transparent outline-none resize-none"
        style={{
          color: "inherit",
          fontFamily: "inherit",
          fontSize: "inherit",
        }}
      />
    </div>
  );
}

function NoteNode({
  data,
  onOpenNote,
}: NodeProps<CanvasNodeData> & { onOpenNote: (id: string) => void }) {
  return (
    <div
      className="px-3 py-2 rounded-lg min-w-[180px] max-w-[320px] cursor-pointer hover:opacity-90 transition-opacity"
      style={{
        background: "rgba(16,185,129,0.12)",
        border: "1px solid rgba(16,185,129,0.5)",
        color: "#ececec",
        fontFamily: "Inter, system-ui, sans-serif",
        fontSize: 13,
      }}
      onClick={() => {
        if (data.noteId) onOpenNote(data.noteId);
      }}
    >
      <div
        className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider"
        style={{ color: "var(--color-accent)", fontWeight: 600 }}
      >
        <FileText size={10} strokeWidth={1.75} />
        {data.noteId ? "note" : "unresolved"}
      </div>
      <div className="mt-0.5 truncate">{data.label}</div>
    </div>
  );
}
