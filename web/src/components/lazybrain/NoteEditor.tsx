import { useEffect, useRef, useState } from "react";
import type { LazyBrainNote } from "../../api";
import { WikilinkText } from "./WikilinkText";

interface Props {
  note: LazyBrainNote;
  onSave: (patch: { title?: string; content?: string }) => void;
  onDelete: () => void;
  onTogglePin: () => void;
  onLinkClick: (page: string) => void;
  onTagClick: (tag: string) => void;
}

export function NoteEditor({
  note,
  onSave,
  onDelete,
  onTogglePin,
  onLinkClick,
  onTagClick,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(note.title || "");
  const [content, setContent] = useState(note.content);
  const bodyRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    setTitle(note.title || "");
    setContent(note.content);
    setEditing(false);
  }, [note.id]);

  useEffect(() => {
    if (!editing) return;
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        onSave({ title, content });
        setEditing(false);
      }
      if (e.key === "Escape") {
        setTitle(note.title || "");
        setContent(note.content);
        setEditing(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [editing, title, content, note, onSave]);

  const created = new Date(note.created_at + "Z").toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Top bar */}
      <div className="shrink-0 px-8 py-3 flex items-center gap-3 border-b border-border bg-bg-primary">
        {editing ? (
          <input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Page title"
            className="flex-1 text-2xl font-semibold bg-transparent outline-none text-text-primary"
          />
        ) : (
          <h1
            onDoubleClick={() => setEditing(true)}
            className="flex-1 text-2xl font-semibold text-text-primary truncate cursor-text"
            title="Double-click to edit"
          >
            {note.pinned && <span className="text-accent mr-2">📌</span>}
            {note.title || "(untitled)"}
          </h1>
        )}

        <button
          onClick={onTogglePin}
          className={`px-2 py-1 rounded text-xs transition-colors ${
            note.pinned ? "bg-accent-soft text-accent" : "hover:bg-bg-hover text-text-muted"
          }`}
          title={note.pinned ? "Unpin" : "Pin"}
        >
          {note.pinned ? "📌 pinned" : "📎 pin"}
        </button>

        {editing ? (
          <>
            <button
              onClick={() => {
                setTitle(note.title || "");
                setContent(note.content);
                setEditing(false);
              }}
              className="px-3 py-1 rounded text-xs text-text-muted hover:text-text-primary"
            >
              Cancel
            </button>
            <button
              onClick={() => {
                onSave({ title, content });
                setEditing(false);
              }}
              className="px-3 py-1 rounded bg-accent text-bg-primary text-xs font-medium"
              title="⌘+S"
            >
              Save
            </button>
          </>
        ) : (
          <>
            <button
              onClick={() => setEditing(true)}
              className="px-2 py-1 rounded hover:bg-bg-hover text-text-muted hover:text-accent text-xs"
              title="Edit  (⌘E)"
            >
              ✏️ edit
            </button>
            <button
              onClick={() => {
                if (window.confirm(`Delete "${note.title || "(untitled)"}"?`)) {
                  onDelete();
                }
              }}
              className="px-2 py-1 rounded hover:bg-red-500/10 text-text-muted hover:text-red-400 text-xs"
              title="Delete"
            >
              🗑
            </button>
          </>
        )}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-8 py-6">
          {editing ? (
            <textarea
              ref={bodyRef}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={28}
              placeholder="Markdown — use [[wikilinks]] and #tags freely"
              className="w-full min-h-[60vh] bg-transparent text-text-primary text-base font-mono leading-relaxed resize-none outline-none"
            />
          ) : (
            <article
              onDoubleClick={() => setEditing(true)}
              className="prose-lazybrain cursor-text"
              title="Double-click to edit"
            >
              <WikilinkText
                content={note.content}
                onLinkClick={onLinkClick}
                onTagClick={onTagClick}
              />
            </article>
          )}
        </div>
      </div>

      {/* Footer metadata */}
      <div className="shrink-0 px-8 py-2 border-t border-border bg-bg-secondary/60 flex items-center gap-4 text-[11px] text-text-muted">
        <span>Created {created}</span>
        <span>Importance {note.importance}/10</span>
        {note.tags.length > 0 && (
          <span className="truncate">
            Tags:{" "}
            {note.tags.map((t) => (
              <button
                key={t}
                onClick={() => onTagClick(t)}
                className="text-text-muted hover:text-accent mx-0.5"
              >
                #{t}
              </button>
            ))}
          </span>
        )}
        {note.trace_session_id && (
          <a
            href={`/?page=replay&session=${encodeURIComponent(note.trace_session_id)}`}
            className="ml-auto px-2 py-0.5 rounded bg-bg-hover hover:bg-accent hover:text-bg-primary transition-colors"
          >
            📼 open replay
          </a>
        )}
      </div>
    </div>
  );
}
