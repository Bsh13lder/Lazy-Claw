import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  deleteLazyBrainNote,
  getLazyBrainNote,
  getNoteCollisions,
  listLazyBrainNotes,
  updateLazyBrainNote,
  type LazyBrainNote,
  type WikilinkCollision,
} from "../../api";
import {
  KIND_META,
  noteKind,
  type NoteKind,
  visibleTags,
} from "./noteHelpers";
import { iterWikilinks, wikilinkLabel, wikilinkHref } from "../../lib/wikilink";

type Props = {
  noteId: string;
  onChange: () => void;
  onClose: () => void;
};

const ALL_KINDS: NoteKind[] = ["note", "idea", "memory"];

/**
 * Detail pane. Two modes:
 *   - "preview" — Source Serif 4, generous line-height, wikilinks are blue
 *   - "edit"    — JetBrains Mono textarea, blur to save
 *
 * No AI buttons live here. The agent can read/write via existing skills,
 * but this surface is silent.
 */
export function NoteDetail({ noteId, onChange, onClose }: Props) {
  const [note, setNote] = useState<LazyBrainNote | null>(null);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  const [draftBody, setDraftBody] = useState("");
  const [draftTitle, setDraftTitle] = useState("");
  const [tagInput, setTagInput] = useState("");
  const titleRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getLazyBrainNote(noteId).then((n) => {
      if (cancelled) return;
      setNote(n);
      setDraftBody(n.content || "");
      setDraftTitle(n.title || "");
      setMode((n.content || "").trim() ? "preview" : "edit");
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [noteId]);

  const kind = note ? noteKind(note) : "note";
  const meta = KIND_META[kind];
  const tags = note ? visibleTags(note) : [];

  const saveBody = async () => {
    if (!note) return;
    if (draftBody === note.content) return;
    const updated = await updateLazyBrainNote(note.id, { content: draftBody });
    setNote(updated);
    onChange();
  };

  const saveTitle = async () => {
    if (!note) return;
    const trimmed = draftTitle.trim();
    if (trimmed === (note.title || "")) return;
    const updated = await updateLazyBrainNote(note.id, { title: trimmed });
    setNote(updated);
    onChange();
  };

  const togglePin = async () => {
    if (!note) return;
    const updated = await updateLazyBrainNote(note.id, { pinned: !note.pinned });
    setNote(updated);
    onChange();
  };

  const switchKind = async (next: NoteKind) => {
    if (!note || next === kind) return;
    const newTags = (note.tags || []).filter((t) => !t.startsWith("kind/"));
    newTags.push(`kind/${next}`);
    const updated = await updateLazyBrainNote(note.id, { tags: newTags });
    setNote(updated);
    onChange();
  };

  const addTag = async (raw: string) => {
    if (!note) return;
    const tag = raw.trim().replace(/^#/, "").toLowerCase();
    if (!tag) return;
    if ((note.tags || []).includes(tag)) {
      setTagInput("");
      return;
    }
    const updated = await updateLazyBrainNote(note.id, {
      tags: [...(note.tags || []), tag],
    });
    setNote(updated);
    setTagInput("");
    onChange();
  };

  const removeTag = async (tag: string) => {
    if (!note) return;
    const updated = await updateLazyBrainNote(note.id, {
      tags: (note.tags || []).filter((t) => t !== tag),
    });
    setNote(updated);
    onChange();
  };

  const remove = async () => {
    if (!note) return;
    if (!window.confirm("Delete this note? This cannot be undone.")) return;
    await deleteLazyBrainNote(note.id);
    onClose();
    onChange();
  };

  // ⌘E / Ctrl+E toggles edit mode.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "e") {
        e.preventDefault();
        setMode((m) => (m === "edit" ? "preview" : "edit"));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ── Phase J: wikilink collision soft warnings ─────────────────────
  // ``[[Madrid]]`` could resolve to two different notes — the resolver
  // picks the most recent silently and writes an audit entry; we surface
  // it inline so the user can rename / alias to disambiguate.
  const [collisions, setCollisions] = useState<WikilinkCollision[]>([]);
  useEffect(() => {
    setCollisions([]);
    if (!note?.id) return;
    let cancelled = false;
    getNoteCollisions(note.id)
      .then((rows) => {
        if (!cancelled) setCollisions(rows);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [note?.id]);

  // ── Phase H: "Folded into [[Rollup]]" banner ──────────────────────
  // When a note carries `rolled-up` it was folded into a weekly/monthly
  // rollup; we look up the rollup's title (via the matching `rollup/<period>`
  // tag) and surface a banner so the user knows where the note now lives.
  const [foldedInto, setFoldedInto] = useState<LazyBrainNote | null>(null);
  useEffect(() => {
    setFoldedInto(null);
    if (!note) return;
    const tags = note.tags || [];
    if (!tags.includes("rolled-up")) return;
    const sourceTag = tags.find((t) => t.startsWith("source-of/"));
    if (!sourceTag) return;
    const period = sourceTag.slice("source-of/".length);
    if (!period) return;
    // Period like "W23-2026" → weekly; "M2026-05" → monthly. Try both.
    const tier = period.startsWith("M") ? "monthly" : "weekly";
    const targetTag = `rollup/${tier}/${period}`;
    let cancelled = false;
    listLazyBrainNotes({ tag: targetTag, limit: 1, include_rolled_up: true })
      .then((notes) => {
        if (!cancelled && notes && notes.length > 0) setFoldedInto(notes[0]);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [note]);

  // Render `[[target#anchor|display]]` inside the body before passing to
  // ReactMarkdown — swap them for a markdown link so they get the same
  // styling as URLs. Uses the shared parser in `lib/wikilink.ts` so syntax
  // stays in lockstep with WikilinkText + the backend.
  const previewBody = useMemo(() => {
    if (!note) return "";
    const src = note.content || "";
    const matches = Array.from(iterWikilinks(src));
    if (matches.length === 0) return src;
    let out = "";
    let last = 0;
    for (const m of matches) {
      if (m.start > last) out += src.slice(last, m.start);
      // Embed (`![[..]]`) is rendered by WikilinkText, not here — keep raw.
      if (m.kind === "embed") {
        out += m.raw;
      } else {
        const label = wikilinkLabel(m);
        out += `[${label}](${wikilinkHref(m.target, m.anchor || undefined)})`;
      }
      last = m.end;
    }
    if (last < src.length) out += src.slice(last);
    return out;
  }, [note]);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-text-muted text-[13px]">
        Loading…
      </div>
    );
  }
  if (!note) {
    return (
      <div className="h-full flex items-center justify-center text-text-muted text-[13px]">
        Note not found.
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header — kind chip, pinned, close, delete */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border/60">
        <div className="flex items-center gap-1.5">
          {ALL_KINDS.map((k) => {
            const m = KIND_META[k];
            const active = k === kind;
            return (
              <button
                key={k}
                onClick={() => switchKind(k)}
                className="px-2 py-0.5 rounded-full text-[10px] uppercase tracking-wider font-medium transition-all border"
                style={{
                  background: active ? m.chipBg : "transparent",
                  borderColor: active ? m.chipBorder : "rgba(255,255,255,0.08)",
                  color: active ? m.chipText : "var(--color-text-muted)",
                  opacity: active ? 1 : 0.6,
                }}
                title={`Mark as ${m.label}`}
              >
                {m.label}
              </button>
            );
          })}
        </div>

        <button
          onClick={togglePin}
          className="ml-2 text-[16px] leading-none transition-colors"
          style={{ color: note.pinned ? "#f59e0b" : "var(--color-text-muted)" }}
          title={note.pinned ? "Unpin" : "Pin to top"}
        >
          {note.pinned ? "★" : "☆"}
        </button>

        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => setMode((m) => (m === "edit" ? "preview" : "edit"))}
            className="px-2 py-1 rounded-md text-[11px] text-text-secondary hover:bg-bg-hover/60 hover:text-text-primary transition-colors"
            title="Toggle edit / preview (⌘E)"
          >
            {mode === "edit" ? "Preview" : "Edit"}
          </button>
          <button
            onClick={remove}
            className="px-2 py-1 rounded-md text-[11px] text-text-muted hover:text-error hover:bg-error/10 transition-colors"
            title="Delete"
          >
            Delete
          </button>
          <button
            onClick={onClose}
            className="px-2 py-1 rounded-md text-[11px] text-text-muted hover:bg-bg-hover/60 transition-colors lg:hidden"
            title="Close"
          >
            Close
          </button>
        </div>
      </div>

      {/* Phase J — wikilink collision warnings. */}
      {collisions.length > 0 && (
        <div
          className="mx-6 mt-3 px-3 py-2 rounded-md text-[12px] flex flex-col gap-1"
          style={{
            background: "rgba(245, 158, 11, 0.08)",
            border: "1px solid rgba(245, 158, 11, 0.3)",
            color: "var(--color-text-secondary)",
          }}
        >
          {collisions.slice(0, 3).map((c) => (
            <div key={c.target} className="flex items-center gap-2">
              <span style={{ fontSize: 14 }}>⚠️</span>
              <span>
                <code style={{ fontWeight: 600 }}>[[{c.target}]]</code>{" "}
                could mean {c.candidates.length} notes — rename or add an
                alias to disambiguate.
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Phase H — "Folded into [[Rollup]]" banner. Pure UI: data already
          comes from existing tags + listLazyBrainNotes. */}
      {foldedInto && (
        <div
          className="mx-6 mt-3 px-3 py-2 rounded-md text-[12px] flex items-center gap-2"
          style={{
            background: "rgba(167, 139, 250, 0.08)",
            border: "1px solid rgba(167, 139, 250, 0.25)",
            color: "var(--color-text-secondary)",
          }}
        >
          <span style={{ fontSize: 14 }}>📎</span>
          <span>
            Folded into{" "}
            <a
              href={`#wikilink/${encodeURIComponent(foldedInto.title || "")}`}
              className="lb-wikilink"
              style={{ fontWeight: 600 }}
            >
              {foldedInto.title || "(untitled rollup)"}
            </a>{" "}
            <span style={{ opacity: 0.7 }}>· sources stay searchable</span>
          </span>
        </div>
      )}

      {/* Title */}
      <div className="px-6 pt-5 pb-2">
        <input
          ref={titleRef}
          value={draftTitle}
          onChange={(e) => setDraftTitle(e.target.value)}
          onBlur={saveTitle}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              titleRef.current?.blur();
            }
          }}
          placeholder="Untitled"
          className="w-full bg-transparent border-0 outline-none text-[22px] font-semibold text-text-primary leading-tight placeholder:text-text-placeholder"
          style={{
            fontFamily: "'Inter', system-ui",
            fontFeatureSettings: '"cv11","ss01","ss03"',
          }}
        />
      </div>

      {/* Tags row */}
      <div className="px-6 pb-3 flex flex-wrap items-center gap-1.5">
        {tags.map((t) => (
          <span
            key={t}
            className="group/tag inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] bg-bg-tertiary border border-border/60 text-text-secondary"
          >
            #{t}
            <button
              onClick={() => removeTag(t)}
              className="opacity-0 group-hover/tag:opacity-100 text-text-muted hover:text-error transition-opacity"
              title="Remove tag"
            >
              ×
            </button>
          </span>
        ))}
        <input
          value={tagInput}
          onChange={(e) => setTagInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              void addTag(tagInput);
            }
            if (e.key === "Backspace" && !tagInput && tags.length > 0) {
              void removeTag(tags[tags.length - 1]);
            }
          }}
          placeholder={tags.length === 0 ? "+ tag" : ""}
          className="bg-transparent border-0 outline-none text-[11px] text-text-secondary placeholder:text-text-muted w-24"
        />
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 overflow-y-auto px-6 pb-6">
        {mode === "edit" ? (
          <textarea
            ref={taRef}
            value={draftBody}
            onChange={(e) => setDraftBody(e.target.value)}
            onBlur={saveBody}
            placeholder="Write here. **bold**, *italic*, `code`, [[wikilinks]], #tags."
            className="w-full min-h-[300px] bg-transparent border-0 outline-none resize-none text-[14px] text-text-primary leading-relaxed"
            style={{
              fontFamily: "'JetBrains Mono', monospace",
              tabSize: 2,
            }}
            autoFocus
          />
        ) : (
          <article
            className="prose prose-invert max-w-none notes-prose"
            style={{
              fontFamily: "'Source Serif 4', Georgia, serif",
            }}
            onClick={(e) => {
              // Click anywhere in the empty preview to enter edit mode.
              if (!previewBody.trim()) setMode("edit");
              // Wikilink click → toggle edit so user can navigate via search.
              const target = e.target as HTMLElement;
              if (target.tagName === "A" && target.getAttribute("href")?.startsWith("#wikilink/")) {
                e.preventDefault();
                // For now, just show in edit mode where user can search.
                setMode("edit");
              }
            }}
          >
            {previewBody.trim() ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {previewBody}
              </ReactMarkdown>
            ) : (
              <p className="text-text-muted italic" style={{ fontFamily: "inherit" }}>
                Empty. Click to start writing.
              </p>
            )}
          </article>
        )}
      </div>

      {/* Footer meta */}
      <div className="px-6 py-2 border-t border-border/40 flex items-center gap-3 text-[10px] text-text-muted">
        <span style={{ color: meta.chipText }}>● {meta.label}</span>
        <span>·</span>
        <span>created {new Date(note.created_at).toLocaleString()}</span>
        {note.updated_at && note.updated_at !== note.created_at ? (
          <>
            <span>·</span>
            <span>edited {new Date(note.updated_at).toLocaleString()}</span>
          </>
        ) : null}
      </div>
    </div>
  );
}
