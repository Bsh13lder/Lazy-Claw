/** Tiny floating preview shown while hovering a [[wikilink]] in note body.
 *  Pointer-events: none, so the mouse can move freely without losing
 *  the parent link's hover. Obsidian's "Page preview" plugin behavior. */
import { useLayoutEffect, useRef, useState } from "react";
import type { LazyBrainNote } from "../../api";
import { colorForTags, ownerOf, OWNER_META } from "./noteColors";
import { CategoryIcon, Star, Clock } from "./icons";

interface Props {
  note: LazyBrainNote;
  x: number;
  y: number;
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

export function WikilinkPreviewCard({ note, x, y }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  // Flip above / flip left if near viewport edges
  useLayoutEffect(() => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = x + 14;
    let top = y + 14;
    if (left + rect.width > vw - 12) left = Math.max(12, x - rect.width - 14);
    if (top + rect.height > vh - 12) top = Math.max(12, y - rect.height - 14);
    setPos({ left, top });
  }, [x, y]);

  const color = colorForTags(note.tags, note.pinned);
  const owner = ownerOf(note.tags);
  const categoryKey = pickCategoryKey(note.tags, note.pinned);
  const created = new Date(note.created_at + "Z").toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  const snippet = note.content.slice(0, 260);

  return (
    <div
      ref={ref}
      className="fixed z-[60] pointer-events-none w-[320px] rounded-lg border border-border bg-bg-secondary shadow-2xl animate-fade-in"
      style={{
        left: pos?.left ?? x + 14,
        top: pos?.top ?? y + 14,
        visibility: pos ? "visible" : "hidden",
        borderTop: `3px solid ${color.ring}`,
      }}
    >
      <div className="px-3.5 pt-2.5 pb-1.5 flex items-center gap-2">
        <CategoryIcon keyName={categoryKey} size={13} color={color.ring} />
        <div className="text-[13px] font-semibold text-text-primary truncate tracking-tight">
          {note.title || "(untitled)"}
        </div>
        {note.pinned && (
          <Star size={10} strokeWidth={2} fill="#fbbf24" color="#fbbf24" />
        )}
      </div>
      <div className="px-3.5 pb-1 text-[10px] uppercase tracking-wider text-text-muted flex items-center gap-2 tabular-nums">
        <Clock size={9} strokeWidth={1.75} />
        <span>{created}</span>
        <span>·</span>
        <span style={{ color: OWNER_META[owner].ring }}>
          {OWNER_META[owner].label.toLowerCase()}
        </span>
      </div>
      <div className="px-3.5 pb-3 text-[12px] text-text-secondary leading-relaxed line-clamp-6 whitespace-pre-wrap">
        {snippet}
        {note.content.length > 260 ? "…" : ""}
      </div>
      {note.tags.length > 0 && (
        <div className="px-3.5 pb-2.5 flex flex-wrap gap-1 border-t border-border pt-2 bg-bg-primary/30">
          {note.tags.slice(0, 6).map((t) => (
            <span
              key={t}
              className="text-[10px] text-text-muted"
            >
              <span className="opacity-60">#</span>{t}
            </span>
          ))}
          {note.tags.length > 6 && (
            <span className="text-[10px] text-text-muted">+{note.tags.length - 6}</span>
          )}
        </div>
      )}
    </div>
  );
}
