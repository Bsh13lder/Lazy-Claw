import { useEffect, useMemo, useRef, useState } from "react";
import type { LazyBrainNote } from "../../api";
import { WikilinkText } from "./WikilinkText";
import { Star, Pencil, Trash2, X, Save, Film, Clock } from "./icons";
import { PropertiesPanel } from "./PropertiesPanel";
import { NoteContextStrip } from "./NoteContextStrip";
import { Plus, ChevronRight } from "lucide-react";

interface Props {
  note: LazyBrainNote;
  onSave: (patch: { title?: string; content?: string }) => void;
  onDelete: () => void;
  onTogglePin: () => void;
  onLinkClick: (page: string) => void;
  onTagClick: (tag: string) => void;
  resolveLink?: (pageName: string) => LazyBrainNote | null;
  /** Backlinks for this note — used by the context strip to surface
   *  the most recent referencing page. Optional; strip degrades gracefully. */
  backlinks?: LazyBrainNote[];
  /** Candidate notes for the `[[` typeahead. */
  notes?: LazyBrainNote[];
}

/** State of the wikilink picker — only present while user is typing in `[[...`. */
interface PickerState {
  /** Caret index in the textarea where the matching `[[` begins. */
  bracketStart: number;
  /** Substring between `[[` and the caret — used to filter candidates. */
  query: string;
  /** Pixel position to anchor the dropdown (caret-relative). */
  top: number;
  left: number;
  /** Currently selected row in the dropdown. */
  selected: number;
}

const PICKER_LIMIT = 8;

export function NoteEditor({
  note,
  onSave,
  onDelete,
  onTogglePin,
  onLinkClick,
  onTagClick,
  resolveLink,
  backlinks = [],
  notes = [],
}: Props) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(note.title || "");
  const [content, setContent] = useState(note.content);
  const [picker, setPicker] = useState<PickerState | null>(null);
  const bodyRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    // Reset on note switch — drops stale draft state when navigating pages.
    setTitle(note.title || "");
    setContent(note.content);
    setEditing(false);
    setPicker(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [note.id]);

  useEffect(() => {
    if (!editing) return;
    const handler = (e: KeyboardEvent) => {
      // Save shortcut should not fire while picker is open — Enter is for picker.
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        onSave({ title, content });
        setEditing(false);
      }
      if (e.key === "Escape" && !picker) {
        setTitle(note.title || "");
        setContent(note.content);
        setEditing(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [editing, title, content, note, onSave, picker]);

  // Filtered + ranked picker candidates. Title-prefix matches first, then
  // any-substring; capped at PICKER_LIMIT.
  const pickerMatches = useMemo<LazyBrainNote[]>(() => {
    if (!picker) return [];
    const q = picker.query.trim().toLowerCase();
    if (!q) return notes.slice(0, PICKER_LIMIT);
    const prefix: LazyBrainNote[] = [];
    const contains: LazyBrainNote[] = [];
    for (const n of notes) {
      const t = (n.title || "").toLowerCase();
      if (!t) continue;
      if (t.startsWith(q)) prefix.push(n);
      else if (t.includes(q)) contains.push(n);
      if (prefix.length + contains.length >= PICKER_LIMIT * 3) break;
    }
    return [...prefix, ...contains].slice(0, PICKER_LIMIT);
  }, [picker, notes]);

  const created = new Date(note.created_at + "Z").toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

  const handleContentChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const next = e.target.value;
    setContent(next);
    syncPicker(next, e.target.selectionStart);
  };

  /** Recompute picker visibility from the current textarea state. */
  const syncPicker = (text: string, caret: number) => {
    const ta = bodyRef.current;
    if (!ta) {
      setPicker(null);
      return;
    }
    const trigger = findOpenWikilink(text, caret);
    if (!trigger) {
      if (picker) setPicker(null);
      return;
    }
    const coords = caretCoords(ta, caret);
    setPicker((prev) => ({
      bracketStart: trigger.bracketStart,
      query: trigger.query,
      top: coords.top,
      left: coords.left,
      selected: prev && prev.bracketStart === trigger.bracketStart ? prev.selected : 0,
    }));
  };

  const handleSelectionChange = () => {
    const ta = bodyRef.current;
    if (!ta) return;
    syncPicker(ta.value, ta.selectionStart);
  };

  const insertWikilink = (page: string) => {
    if (!picker) return;
    const ta = bodyRef.current;
    if (!ta) return;
    const before = content.slice(0, picker.bracketStart);
    const caret = ta.selectionStart;
    const after = content.slice(caret);
    const insertion = `[[${page}]]`;
    const next = before + insertion + after;
    setContent(next);
    setPicker(null);
    // Restore caret right after the inserted `]]` on the next tick.
    requestAnimationFrame(() => {
      const pos = (before + insertion).length;
      ta.focus();
      ta.setSelectionRange(pos, pos);
    });
  };

  const handleTextareaKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (!picker || pickerMatches.length === 0) {
      // Allow create-on-Enter when there's a query but no match.
      if (picker && e.key === "Enter" && picker.query.trim()) {
        e.preventDefault();
        insertWikilink(picker.query.trim());
      }
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setPicker((p) =>
        p ? { ...p, selected: (p.selected + 1) % pickerMatches.length } : p,
      );
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setPicker((p) =>
        p
          ? { ...p, selected: (p.selected - 1 + pickerMatches.length) % pickerMatches.length }
          : p,
      );
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      const choice = pickerMatches[picker.selected];
      const title = choice?.title?.trim() || picker.query.trim();
      if (title) insertWikilink(title);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setPicker(null);
    }
  };

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
          <NoteContextStrip
            note={note}
            backlinks={backlinks}
            onLinkClick={onLinkClick}
            onContentPatch={(nextContent) => onSave({ content: nextContent })}
          />
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
            <div className="relative">
              <textarea
                ref={bodyRef}
                value={content}
                onChange={handleContentChange}
                onKeyDown={handleTextareaKeyDown}
                onKeyUp={handleSelectionChange}
                onClick={handleSelectionChange}
                onBlur={() => {
                  // Defer so a click on a picker row can fire first.
                  setTimeout(() => setPicker(null), 120);
                }}
                rows={28}
                placeholder="Markdown — use [[wikilinks]] and #tags freely"
                className="w-full min-h-[60vh] bg-transparent text-text-primary text-[15px] font-mono leading-relaxed resize-none outline-none whitespace-pre-wrap"
              />
              {picker && (
                <WikilinkPicker
                  top={picker.top}
                  left={picker.left}
                  query={picker.query}
                  selected={picker.selected}
                  matches={pickerMatches}
                  onPick={(p) => insertWikilink(p)}
                  onHoverIndex={(i) =>
                    setPicker((prev) => (prev ? { ...prev, selected: i } : prev))
                  }
                />
              )}
            </div>
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

/** Walk back from `caret` to find an unmatched `[[` on the same line.
 *  Returns null if the caret isn't inside an open wikilink. */
function findOpenWikilink(
  text: string,
  caret: number,
): { bracketStart: number; query: string } | null {
  // Search the last 200 chars before caret for performance.
  const start = Math.max(0, caret - 200);
  for (let i = caret - 1; i >= start; i--) {
    const ch = text[i];
    if (ch === "\n") return null;
    if (ch === "]" && text[i - 1] === "]") return null; // closed
    if (ch === "[" && text[i - 1] === "[") {
      const query = text.slice(i + 1, caret);
      // Bail out if the query already contains `]` or a newline — means the
      // user already closed it or moved past.
      if (query.includes("]") || query.includes("\n")) return null;
      // Cap query length so we don't flicker on huge prose.
      if (query.length > 80) return null;
      return { bracketStart: i - 1, query };
    }
  }
  return null;
}

/** Compute the on-screen pixel position of the caret inside a textarea.
 *  Uses the canonical "mirror div" technique: a hidden div with identical
 *  styling renders the text up to the caret + a marker span; the span's
 *  bounding box gives us the caret's pixel coords relative to the textarea. */
function caretCoords(
  textarea: HTMLTextAreaElement,
  position: number,
): { top: number; left: number } {
  const div = document.createElement("div");
  const style = getComputedStyle(textarea);
  // Copy the styles that affect text wrapping/metrics.
  const props = [
    "boxSizing", "width", "height",
    "overflowX", "overflowY",
    "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth",
    "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
    "fontStyle", "fontVariant", "fontWeight", "fontStretch", "fontSize", "fontSizeAdjust",
    "lineHeight", "fontFamily",
    "textAlign", "textTransform", "textIndent", "textDecoration",
    "letterSpacing", "wordSpacing",
    "tabSize", "MozTabSize",
  ] as const;
  for (const p of props) {
    // @ts-expect-error — index style with arbitrary keys
    div.style[p] = style[p];
  }
  div.style.position = "absolute";
  div.style.visibility = "hidden";
  div.style.whiteSpace = "pre-wrap";
  div.style.wordWrap = "break-word";
  div.style.top = "0";
  div.style.left = "0";
  div.textContent = textarea.value.slice(0, position);
  const marker = document.createElement("span");
  marker.textContent = "\u200b";
  div.appendChild(marker);
  document.body.appendChild(div);
  const taRect = textarea.getBoundingClientRect();
  const divRect = div.getBoundingClientRect();
  const markerRect = marker.getBoundingClientRect();
  // Marker position relative to the mirror div, then adjusted to textarea.
  const top =
    markerRect.top - divRect.top - textarea.scrollTop + parseFloat(style.lineHeight || "20");
  const left = markerRect.left - divRect.left - textarea.scrollLeft;
  document.body.removeChild(div);
  // Clamp so the dropdown stays within the textarea's visual area.
  const maxTop = taRect.height - 8;
  const maxLeft = taRect.width - 8;
  return {
    top: Math.max(0, Math.min(maxTop, top)),
    left: Math.max(0, Math.min(maxLeft, left)),
  };
}

interface PickerProps {
  top: number;
  left: number;
  query: string;
  selected: number;
  matches: LazyBrainNote[];
  onPick: (page: string) => void;
  onHoverIndex: (i: number) => void;
}

function WikilinkPicker({ top, left, query, selected, matches, onPick, onHoverIndex }: PickerProps) {
  const trimmed = query.trim();
  return (
    <div
      className="absolute z-30 w-[320px] rounded-lg border shadow-2xl overflow-hidden"
      style={{
        top: top + 4,
        left,
        background: "var(--color-lb-bg-secondary)",
        borderColor: "var(--color-lb-border-strong)",
        boxShadow: "0 18px 32px -12px rgba(0,0,0,0.65)",
      }}
      onMouseDown={(e) => e.preventDefault()}
    >
      <div className="px-3 py-1.5 border-b border-border flex items-center gap-2 text-[11px] text-text-muted">
        <ChevronRight size={11} strokeWidth={1.75} />
        <span className="truncate">
          {trimmed ? <span className="text-text-secondary">[[{trimmed}…]]</span> : "Pick a page to link"}
        </span>
        <span className="ml-auto text-[10px] opacity-70">↑↓ Enter Esc</span>
      </div>
      {matches.length === 0 ? (
        <button
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => trimmed && onPick(trimmed)}
          disabled={!trimmed}
          className="w-full flex items-center gap-2 px-3 py-2 text-[12px] text-text-secondary hover:text-text-primary hover:bg-bg-hover disabled:opacity-50 disabled:hover:bg-transparent"
        >
          <Plus size={12} strokeWidth={1.9} />
          {trimmed
            ? <span>Create new page <span className="text-accent">[[{trimmed}]]</span></span>
            : <span>No notes yet — type a title</span>}
        </button>
      ) : (
        <ul className="max-h-[260px] overflow-y-auto">
          {matches.map((n, i) => {
            const active = i === selected;
            return (
              <li
                key={n.id}
                onMouseEnter={() => onHoverIndex(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  onPick(n.title?.trim() || trimmed);
                }}
                className={`px-3 py-1.5 text-[12.5px] cursor-pointer flex items-center gap-2 ${
                  active ? "bg-accent-soft text-text-primary" : "text-text-secondary hover:bg-bg-hover"
                }`}
              >
                {n.pinned && <Star size={11} strokeWidth={1.75} color="#fbbf24" fill="#fbbf24" />}
                <span className="truncate">{n.title || "(untitled)"}</span>
                {active && (
                  <span className="ml-auto text-[10px] text-accent opacity-80">↵</span>
                )}
              </li>
            );
          })}
          {trimmed && !matches.some((m) => (m.title || "").toLowerCase() === trimmed.toLowerCase()) && (
            <li
              onMouseDown={(e) => {
                e.preventDefault();
                onPick(trimmed);
              }}
              className="px-3 py-1.5 text-[12.5px] cursor-pointer flex items-center gap-2 text-text-muted hover:text-accent hover:bg-bg-hover border-t border-border"
            >
              <Plus size={11} strokeWidth={1.9} />
              <span>Create <span className="text-accent">[[{trimmed}]]</span></span>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
