import type { LazyBrainNote } from "../../api";
import { colorForTags } from "./noteColors";
import { WikilinkText } from "./WikilinkText";
import { Trash2 } from "./icons";
import { parseFrontmatter } from "./frontmatter";

interface Props {
  note: LazyBrainNote;
  selected?: boolean;
  onSelect?: (note: LazyBrainNote) => void;
  onPin?: (note: LazyBrainNote) => void;
  onDelete?: (note: LazyBrainNote) => void;
  onLinkClick?: (pageName: string) => void;
  onTagClick?: (tag: string) => void;
}

export function MemoCard({
  note,
  selected,
  onSelect,
  onPin,
  onDelete,
  onLinkClick,
  onTagClick,
}: Props) {
  const date = new Date(note.created_at + "Z");
  const stamp = isNaN(date.getTime())
    ? note.created_at
    : date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });

  const color = colorForTags(note.tags, note.pinned);

  return (
    <div
      onClick={() => onSelect?.(note)}
      style={{ borderLeftColor: color.ring, borderLeftWidth: 3 }}
      className={`rounded-lg border p-3 bg-bg-secondary cursor-pointer transition-all ${
        selected ? "border-accent" : "border-border hover:border-border-hover"
      }`}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span
              className="text-xs"
              style={{ color: color.ring }}
              title={color.label}
            >
              {color.emoji}
            </span>
            <h3 className="font-semibold text-text-primary truncate">
              {note.title || "(untitled)"}
            </h3>
          </div>
          <div className="text-xs text-text-muted mt-0.5 flex items-center gap-2">
            <span>
              {stamp} · importance {note.importance}/10
            </span>
            {note.trace_session_id && (
              <a
                href={`/?page=replay&session=${encodeURIComponent(note.trace_session_id)}`}
                onClick={(e) => {
                  e.stopPropagation();
                }}
                className="px-1.5 py-0.5 rounded bg-bg-hover text-accent hover:bg-accent hover:text-bg-primary transition-colors"
                title="Jump to the chat session that created this note"
              >
                📼 replay
              </a>
            )}
          </div>
        </div>
        <div className="flex gap-1 shrink-0">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onPin?.(note);
            }}
            className="p-1 rounded hover:bg-bg-hover text-text-muted hover:text-accent"
            title={note.pinned ? "Unpin" : "Pin"}
          >
            {note.pinned ? "📌" : "📎"}
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onDelete?.(note);
            }}
            className="p-1 rounded hover:bg-bg-hover text-text-muted hover:text-red-500"
            title="Delete"
          >
            <Trash2 size={16} />
          </button>
        </div>
      </div>
      {/* Strip leading YAML frontmatter (`---\nkey: value\n---\n`) before
          rendering. Plan-mirror notes (kind/plan, source/claude-plan)
          carry an ingest metadata block that's meant for
          PropertiesPanel, not the markdown view — leaving it raw made
          every plan card open with a wall of `kind: plan source: …`. */}
      <WikilinkText
        content={parseFrontmatter(note.content).body}
        onLinkClick={onLinkClick}
        onTagClick={onTagClick}
      />
    </div>
  );
}
