import { useEffect, useRef, useState } from "react";
import type { LazyBrainNote } from "../../api";
import { WikilinkText } from "./WikilinkText";
import { Star, Pencil, Trash2, X, Save, Film, Clock } from "./icons";
import { PropertiesPanel } from "./PropertiesPanel";

interface Props {
  note: LazyBrainNote;
  onSave: (patch: { title?: string; content?: string }) => void;
  onDelete: () => void;
  onTogglePin: () => void;
  onLinkClick: (page: string) => void;
  onTagClick: (tag: string) => void;
  resolveLink?: (pageName: string) => LazyBrainNote | null;
}

export function NoteEditor({
  note,
  onSave,
  onDelete,
  onTogglePin,
  onLinkClick,
  onTagClick,
  resolveLink,
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
      <div className="shrink-0 px-8 py-3 flex items-center gap-2 border-b border-border bg-bg-primary">
        {editing ? (
          <input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Page title"
            className="flex-1 text-2xl font-semibold bg-transparent outline-none text-text-primary tracking-tight"
          />
        ) : (
          <h1
            onDoubleClick={() => setEditing(true)}
            className="flex-1 text-2xl font-semibold text-text-primary truncate cursor-text tracking-tight flex items-center gap-2"
            title="Double-click to edit"
          >
            {note.pinned && <Star size={20} strokeWidth={1.75} color="#fbbf24" fill="#fbbf24" />}
            <span className="truncate">{note.title || "(untitled)"}</span>
          </h1>
        )}

        <button
          onClick={onTogglePin}
          className={`h-8 px-2.5 rounded text-xs transition-colors flex items-center gap-1.5 ${
            note.pinned
              ? "text-[#fbbf24] hover:bg-bg-hover"
              : "text-text-muted hover:text-text-primary hover:bg-bg-hover"
          }`}
          title={note.pinned ? "Unpin" : "Pin"}
        >
          <Star
            size={14}
            strokeWidth={1.75}
            fill={note.pinned ? "#fbbf24" : "none"}
          />
          <span>{note.pinned ? "Pinned" : "Pin"}</span>
        </button>

        {editing ? (
          <>
            <button
              onClick={() => {
                setTitle(note.title || "");
                setContent(note.content);
                setEditing(false);
              }}
              className="h-8 px-2.5 rounded text-xs text-text-muted hover:text-text-primary hover:bg-bg-hover flex items-center gap-1.5"
            >
              <X size={14} strokeWidth={1.75} />
              <span>Cancel</span>
            </button>
            <button
              onClick={() => {
                onSave({ title, content });
                setEditing(false);
              }}
              className="h-8 px-2.5 rounded bg-accent text-bg-primary text-xs font-medium flex items-center gap-1.5 hover:opacity-90"
              title="⌘+S"
            >
              <Save size={14} strokeWidth={2} />
              <span>Save</span>
            </button>
          </>
        ) : (
          <>
            <button
              onClick={() => setEditing(true)}
              className="h-8 px-2.5 rounded text-text-muted hover:text-accent hover:bg-bg-hover text-xs flex items-center gap-1.5"
              title="Edit  (⌘E)"
            >
              <Pencil size={14} strokeWidth={1.75} />
              <span>Edit</span>
            </button>
            <button
              onClick={() => {
                if (window.confirm(`Delete "${note.title || "(untitled)"}"?`)) {
                  onDelete();
                }
              }}
              className="h-8 w-8 rounded text-text-muted hover:text-red-400 hover:bg-red-500/10 flex items-center justify-center"
              title="Delete"
            >
              <Trash2 size={14} strokeWidth={1.75} />
            </button>
          </>
        )}
      </div>

      {/* Body */}
      <div data-lb-scroll-root className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-8 py-6">
          <div className="mb-4">
            {editing ? (
              <PropertiesPanel
                content={content}
                onChange={(next) => setContent(next)}
                showEmpty
              />
            ) : (
              <PropertiesPanel
                content={note.content}
                onChange={(next) => onSave({ content: next })}
              />
            )}
          </div>
          {editing ? (
            <textarea
              ref={bodyRef}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={28}
              placeholder="Markdown — use [[wikilinks]] and #tags freely"
              className="w-full min-h-[60vh] bg-transparent text-text-primary text-[15px] font-mono leading-relaxed resize-none outline-none whitespace-pre-wrap"
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
                resolveLink={resolveLink}
              />
            </article>
          )}
        </div>
      </div>

      {/* Footer metadata */}
      <div className="shrink-0 px-8 py-2 border-t border-border bg-bg-secondary/60 flex items-center gap-4 text-[11px] text-text-muted">
        <span className="flex items-center gap-1.5">
          <Clock size={11} strokeWidth={1.75} />
          <span className="tabular-nums">{created}</span>
        </span>
        <span className="tabular-nums">
          Importance <span className="text-text-secondary">{note.importance}</span>/10
        </span>
        {note.tags.length > 0 && (
          <span className="truncate flex items-center gap-1.5">
            {note.tags.map((t) => (
              <button
                key={t}
                onClick={() => onTagClick(t)}
                className="text-text-muted hover:text-accent transition-colors"
              >
                <span className="opacity-60">#</span>{t}
              </button>
            ))}
          </span>
        )}
        {note.trace_session_id && (
          <a
            href={`/?page=replay&session=${encodeURIComponent(note.trace_session_id)}`}
            className="ml-auto px-2 py-0.5 rounded bg-bg-hover hover:bg-accent hover:text-bg-primary transition-colors flex items-center gap-1"
          >
            <Film size={11} strokeWidth={1.75} />
            <span>replay</span>
          </a>
        )}
      </div>
    </div>
  );
}
