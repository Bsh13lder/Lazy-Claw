import type { LazyBrainNote } from "../../api";

interface Props {
  note: LazyBrainNote | null;
  backlinks: LazyBrainNote[];
  onSelect: (note: LazyBrainNote) => void;
}

export function BacklinksPanel({ note, backlinks, onSelect }: Props) {
  if (!note) {
    return (
      <div className="p-4 text-sm text-text-muted">
        Select a note to see its backlinks.
      </div>
    );
  }
  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b border-border">
        <div className="text-xs text-text-muted mb-1">Backlinks</div>
        <div className="text-sm font-semibold text-text-primary truncate">
          {note.title || "(untitled)"}
        </div>
        <div className="text-xs text-text-muted mt-0.5">
          {backlinks.length} {backlinks.length === 1 ? "link" : "links"}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {backlinks.length === 0 ? (
          <div className="p-3 text-xs text-text-muted italic">
            Nothing links here yet. Add <code className="text-accent">[[{note.title || note.id.slice(0, 8)}]]</code> in another note.
          </div>
        ) : (
          backlinks.map((bl) => (
            <button
              key={bl.id}
              onClick={() => onSelect(bl)}
              className="w-full text-left p-2 rounded hover:bg-bg-hover transition-colors"
            >
              <div className="text-sm font-medium text-text-primary truncate">
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
