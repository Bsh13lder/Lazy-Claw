import type { LazyBrainNote } from "../../api";
import { Link2 } from "lucide-react";

interface Props {
  note: LazyBrainNote | null;
  backlinks: LazyBrainNote[];
  onSelect: (note: LazyBrainNote) => void;
}

export function BacklinksPanel({ note, backlinks, onSelect }: Props) {
  if (!note) {
    return (
      <div className="p-4 text-sm text-text-muted flex items-center gap-2">
        <Link2 size={14} strokeWidth={1.75} />
        <span>Select a note to see its backlinks.</span>
      </div>
    );
  }
  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b border-border">
        <div className="text-[10px] uppercase tracking-wider text-text-muted mb-1 flex items-center gap-1.5">
          <Link2 size={10} strokeWidth={1.75} />
          <span>Backlinks</span>
        </div>
        <div className="text-sm font-semibold text-text-primary truncate tracking-tight">
          {note.title || "(untitled)"}
        </div>
        <div className="text-[11px] text-text-muted mt-0.5 tabular-nums">
          {backlinks.length} {backlinks.length === 1 ? "link" : "links"}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {backlinks.length === 0 ? (
          <div className="p-3 text-xs text-text-muted italic leading-relaxed">
            Nothing links here yet. Add{" "}
            <code className="text-accent">[[{note.title || note.id.slice(0, 8)}]]</code>{" "}
            in another note.
          </div>
        ) : (
          backlinks.map((bl) => (
            <button
              key={bl.id}
              onClick={() => onSelect(bl)}
              className="w-full text-left p-2 rounded hover:bg-bg-hover transition-colors group"
            >
              <div className="text-sm font-medium text-text-primary truncate group-hover:text-accent transition-colors">
                {bl.title || "(untitled)"}
              </div>
              <div className="text-xs text-text-muted truncate mt-0.5">
                {bl.content.slice(0, 80)}
                {bl.content.length > 80 ? "…" : ""}
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
