import type { LazyBrainNote } from "../../api";
import {
  KIND_META,
  noteKind,
  noteSource,
  relativeTimeAgo,
  visibleTags,
} from "./noteHelpers";

type Props = {
  note: LazyBrainNote;
  selected: boolean;
  onSelect: (id: string) => void;
};

/**
 * Note row in the list pane. Visual identity:
 *
 *   ●  Title                              ★    12m ago
 *      first body line snippet…
 *      #shopping  #weekend
 *
 * The dot color encodes kind (note/idea/memory). The star marks pinned.
 * Snippet uses Source Serif 4 to signal "reading content".
 */
export function NoteCard({ note, selected, onSelect }: Props) {
  const kind = noteKind(note);
  const meta = KIND_META[kind];
  const tags = visibleTags(note);
  const source = noteSource(note);
  const snippet =
    (note.content || "")
      .replace(/^#\s+.+\n/, "") // skip first H1 if any
      .trim()
      .split(/\r?\n/)
      .find((l) => l.trim().length > 0) || "";

  return (
    <button
      type="button"
      onClick={() => onSelect(note.id)}
      className={[
        "group w-full text-left rounded-lg border px-3 py-2.5 transition-all",
        "flex flex-col gap-1.5",
        selected
          ? "bg-bg-secondary border-accent/40 shadow-[0_0_0_1px_rgba(16,185,129,0.15)]"
          : "bg-bg-primary/40 border-border/50 hover:bg-bg-secondary/60 hover:border-border-light",
      ].join(" ")}
    >
      <div className="flex items-start gap-2.5">
        <span
          className="mt-1.5 inline-block w-1.5 h-1.5 rounded-full flex-none"
          style={{ background: meta.dot, boxShadow: `0 0 6px ${meta.dot}55` }}
          aria-hidden
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2">
            <h3 className="font-medium text-[14px] text-text-primary truncate">
              {note.title || snippet || "(untitled)"}
            </h3>
            {note.pinned ? (
              <span className="text-[12px]" style={{ color: "#f59e0b" }} title="Pinned">
                ★
              </span>
            ) : null}
            <span className="ml-auto text-[10px] text-text-muted whitespace-nowrap">
              {relativeTimeAgo(note.updated_at || note.created_at)}
            </span>
          </div>

          {snippet && note.title && snippet !== note.title ? (
            <p
              className="text-[12.5px] text-text-secondary leading-snug truncate"
              style={{ fontFamily: "'Source Serif 4', Georgia, serif" }}
            >
              {snippet}
            </p>
          ) : null}

          {(tags.length > 0 || source) && (
            <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
              {tags.slice(0, 4).map((t) => (
                <span
                  key={t}
                  className="text-[10px] text-text-muted"
                >
                  #{t}
                </span>
              ))}
              {tags.length > 4 ? (
                <span className="text-[10px] text-text-muted">+{tags.length - 4}</span>
              ) : null}
              {source ? (
                <span
                  className="ml-auto text-[10px] uppercase tracking-wider opacity-70"
                  style={{ color: meta.chipText }}
                >
                  {source}
                </span>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </button>
  );
}
