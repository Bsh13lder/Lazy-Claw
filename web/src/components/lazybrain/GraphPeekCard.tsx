/** Floating preview card shown when a graph node is clicked.
 *  The user can read the full content, pin/unpin, or open the page —
 *  all without leaving graph view. Obsidian-style "page preview". */
import type { LazyBrainNote } from "../../api";
import { WikilinkText } from "./WikilinkText";
import { colorForTags, ownerOf, OWNER_META } from "./noteColors";
import { CategoryIcon, Star, X, Clock, Pencil } from "./icons";
import { Maximize2 } from "lucide-react";

interface Props {
  note: LazyBrainNote;
  onClose: () => void;
  onOpen: () => void;
  onTogglePin: () => void;
  onLinkClick: (page: string) => void;
  onTagClick: (tag: string) => void;
  resolveLink?: (pageName: string) => LazyBrainNote | null;
}

function pickCategoryKey(tags: string[] | null | undefined, pinned: boolean): string {
  if (pinned) return "pinned";
  if (!tags) return "_default";
  const priority = [
    "task", "deadline", "journal", "lesson", "til",
    "decision", "price", "command", "recipe", "contact",
    "idea", "rollup", "reference", "layer", "imported", "auto",
    "memory", "site-memory", "daily-log",
  ];
  const lower = tags.map((t) => t.toLowerCase());
  for (const key of priority) {
    if (lower.includes(key)) return key;
    if (lower.some((t) => t.startsWith(`${key}/`))) return key;
  }
  return "_default";
}

export function GraphPeekCard({
  note,
  onClose,
  onOpen,
  onTogglePin,
  onLinkClick,
  onTagClick,
  resolveLink,
}: Props) {
  const color = colorForTags(note.tags, note.pinned);
  const owner = ownerOf(note.tags);
  const categoryKey = pickCategoryKey(note.tags, note.pinned);
  const created = new Date(note.created_at + "Z").toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

  return (
    <>
      {/* Backdrop — click to close */}
      <div
        className="absolute inset-0 z-30 bg-bg-primary/40 backdrop-blur-sm animate-fade-in"
        onClick={onClose}
      />
      {/* Card */}
      <div
        className="absolute z-40 top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[min(560px,90%)] max-h-[min(70vh,640px)] flex flex-col bg-bg-secondary border border-border rounded-xl shadow-2xl animate-fade-in"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className="shrink-0 px-5 py-3 border-b border-border flex items-start gap-3"
          style={{ borderTop: `3px solid ${color.ring}`, borderTopLeftRadius: 11, borderTopRightRadius: 11 }}
        >
          <CategoryIcon keyName={categoryKey} size={18} color={color.ring} />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-0.5">
              <h2 className="text-lg font-semibold text-text-primary tracking-tight truncate">
                {note.title || "(untitled)"}
              </h2>
              {note.pinned && (
                <Star size={14} strokeWidth={2} fill="#fbbf24" color="#fbbf24" />
              )}
            </div>
            <div className="text-[11px] text-text-muted flex items-center gap-3 tabular-nums">
              <span className="flex items-center gap-1">
                <Clock size={10} strokeWidth={1.75} />
                {created}
              </span>
              <span>· importance {note.importance}/10</span>
              <span
                className="flex items-center gap-1"
                style={{ color: OWNER_META[owner].ring }}
              >
                · {OWNER_META[owner].label.toLowerCase()}
              </span>
            </div>
          </div>
          <button
            onClick={onTogglePin}
            className={`h-7 w-7 rounded flex items-center justify-center transition-colors ${
              note.pinned
                ? "text-[#fbbf24] hover:bg-bg-hover"
                : "text-text-muted hover:text-text-primary hover:bg-bg-hover"
            }`}
            title={note.pinned ? "Unpin" : "Pin"}
          >
            <Star size={14} strokeWidth={1.75} fill={note.pinned ? "#fbbf24" : "none"} />
          </button>
          <button
            onClick={onClose}
            className="h-7 w-7 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-hover"
            title="Close (Esc)"
          >
            <X size={14} strokeWidth={1.75} />
          </button>
        </div>

        {/* Body — full markdown */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          <article className="prose-lazybrain">
            <WikilinkText
              content={note.content}
              onLinkClick={(name) => {
                onClose();
                onLinkClick(name);
              }}
              onTagClick={onTagClick}
              resolveLink={resolveLink}
            />
          </article>
        </div>

        {/* Footer actions */}
        <div className="shrink-0 px-5 py-3 border-t border-border flex items-center justify-between gap-3 bg-bg-primary/40 rounded-b-xl">
          {note.tags.length > 0 ? (
            <div className="flex-1 flex flex-wrap gap-1 items-center">
              {note.tags.slice(0, 8).map((t) => (
                <button
                  key={t}
                  onClick={() => onTagClick(t)}
                  className="text-[11px] text-text-muted hover:text-accent transition-colors"
                >
                  <span className="opacity-60">#</span>{t}
                </button>
              ))}
              {note.tags.length > 8 && (
                <span className="text-[11px] text-text-muted">
                  +{note.tags.length - 8}
                </span>
              )}
            </div>
          ) : (
            <div className="flex-1" />
          )}
          <div className="flex items-center gap-2">
            <button
              onClick={onOpen}
              className="h-8 px-3 rounded bg-accent text-bg-primary text-xs font-medium flex items-center gap-1.5 hover:opacity-90"
              title="Open in editor"
            >
              <Pencil size={12} strokeWidth={1.9} />
              <span>Open page</span>
            </button>
            <button
              onClick={onOpen}
              className="h-8 w-8 rounded bg-bg-hover text-text-muted hover:text-text-primary flex items-center justify-center"
              title="Open full"
            >
              <Maximize2 size={12} strokeWidth={1.75} />
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
