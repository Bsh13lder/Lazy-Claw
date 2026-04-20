/** Quiet info strip rendered above every note body.
 *
 *  Surfaces what a brainstorming view should show at a glance:
 *  - WHO wrote it (You / Agent badge)
 *  - WHAT state it's in (status pill — only for task-tagged notes)
 *  - WHEN it last moved (relative edited time + last backlink source)
 *  - WHAT'S NEXT (first 3 unchecked `- [ ]` items, click toggles)
 *
 *  Pure derivation from data already on the note + backlinks list — no
 *  LLM, no extra API call. Stays visually quiet so it never competes
 *  with the note body. */
import { useMemo } from "react";
import type { LazyBrainNote } from "../../api";
import { OWNER_META, ownerOf } from "./noteColors";
import { OWNER_ICONS } from "./icons";
import { AlarmClock, CheckSquare, Clock, ListTodo, Link2 } from "lucide-react";
import { Checkbox } from "./Checkbox";

interface Props {
  note: LazyBrainNote;
  backlinks: LazyBrainNote[];
  onLinkClick: (page: string) => void;
  /** Persists a content patch — used by checkbox-toggle. */
  onContentPatch?: (nextContent: string) => void;
}

type TaskStatus = "overdue" | "today" | "scheduled" | "done" | null;

const ISO_DATE = /(\d{4})-(\d{2})-(\d{2})/;

function relativeTime(iso: string): string {
  const then = new Date(iso.endsWith("Z") ? iso : iso + "Z").getTime();
  if (Number.isNaN(then)) return "—";
  const diffSec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (diffSec < 60) return "just now";
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  const diffMo = Math.round(diffDay / 30);
  if (diffMo < 12) return `${diffMo}mo ago`;
  return `${Math.round(diffMo / 12)}y ago`;
}

function isTaskNote(tags: string[]): boolean {
  return tags.some(
    (t) => t.toLowerCase() === "task" || t.toLowerCase().startsWith("task/"),
  );
}

function deriveStatus(tags: string[]): TaskStatus {
  if (!isTaskNote(tags)) return null;
  const lower = tags.map((t) => t.toLowerCase());
  if (lower.includes("status/done") || lower.includes("done")) return "done";
  // Find a `due/YYYY-MM-DD` tag and compare to today.
  const due = lower
    .map((t) => (t.startsWith("due/") ? t.slice(4) : null))
    .find((v): v is string => !!v && ISO_DATE.test(v));
  if (due) {
    const today = new Date().toISOString().slice(0, 10);
    if (due < today) return "overdue";
    if (due === today) return "today";
    return "scheduled";
  }
  if (lower.includes("today") || lower.includes("priority/high")) return "today";
  return "scheduled";
}

const STATUS_META: Record<NonNullable<TaskStatus>, { label: string; color: string; bg: string }> = {
  overdue:   { label: "Overdue",   color: "var(--color-lb-cat-deadline)", bg: "rgba(251, 113, 133, 0.12)" },
  today:     { label: "Today",     color: "var(--color-lb-cat-task)",     bg: "rgba(255, 138, 76, 0.12)" },
  scheduled: { label: "Scheduled", color: "var(--color-lb-cat-daily-log)", bg: "rgba(125, 211, 252, 0.12)" },
  done:      { label: "Done",      color: "var(--color-lb-cat-til)",      bg: "rgba(134, 239, 172, 0.12)" },
};

interface CheckboxItem {
  /** Position of the `[ ]` (or `[x]`) marker in the source so we can flip it. */
  bracketIndex: number;
  text: string;
  checked: boolean;
}

const CHECKBOX_RE = /^(\s*)[-*+]\s+\[( |x|X)\]\s+(.+)$/gm;

function findCheckboxes(content: string, limit: number): CheckboxItem[] {
  const out: CheckboxItem[] = [];
  CHECKBOX_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = CHECKBOX_RE.exec(content)) !== null) {
    const fullStart = m.index;
    const bracketIndex = content.indexOf("[", fullStart);
    out.push({
      bracketIndex,
      text: m[3].trim(),
      checked: m[2].toLowerCase() === "x",
    });
    if (out.filter((x) => !x.checked).length >= limit && out.length >= limit) break;
  }
  return out;
}

function toggleCheckbox(content: string, bracketIndex: number): string {
  if (bracketIndex < 0 || bracketIndex + 2 >= content.length) return content;
  const cur = content[bracketIndex + 1];
  const next = cur === " " ? "x" : " ";
  return content.slice(0, bracketIndex + 1) + next + content.slice(bracketIndex + 2);
}

export function NoteContextStrip({ note, backlinks, onLinkClick, onContentPatch }: Props) {
  const owner = ownerOf(note.tags);
  const ownerMeta = OWNER_META[owner];
  const OwnerIcon = OWNER_ICONS[owner];
  const status = deriveStatus(note.tags);
  const statusMeta = status ? STATUS_META[status] : null;
  const editedRel = relativeTime(note.updated_at);
  const lastBacklink = backlinks[0];

  const checkboxes = useMemo(() => findCheckboxes(note.content, 6), [note.content]);
  const openItems = checkboxes.filter((c) => !c.checked).slice(0, 3);

  // Hide entirely when there's truly nothing to surface (very fresh note,
  // no backlinks, no checkboxes, no task tag, owner=unknown).
  const isEmpty =
    !statusMeta &&
    !lastBacklink &&
    openItems.length === 0 &&
    owner === "unknown";
  if (isEmpty) return null;

  const handleToggle = (bracketIndex: number) => {
    if (!onContentPatch) return;
    onContentPatch(toggleCheckbox(note.content, bracketIndex));
  };

  return (
    <div
      className="mb-4 rounded-lg border border-border px-3 py-2.5 flex flex-col gap-2 text-[12px] leading-snug"
      style={{ background: "var(--color-lb-bg-panel)" }}
    >
      {/* Top row — badges + last-activity */}
      <div className="flex flex-wrap items-center gap-2 text-text-secondary">
        {owner !== "unknown" && (
          <Pill color={ownerMeta.ring} bg="rgba(255, 255, 255, 0.04)">
            <OwnerIcon size={11} strokeWidth={1.9} />
            <span>{ownerMeta.label}</span>
          </Pill>
        )}
        {statusMeta && (
          <Pill color={statusMeta.color} bg={statusMeta.bg}>
            <AlarmClock size={11} strokeWidth={1.9} />
            <span>{statusMeta.label}</span>
          </Pill>
        )}
        <span className="flex items-center gap-1 text-text-muted">
          <Clock size={11} strokeWidth={1.75} />
          <span>edited {editedRel}</span>
        </span>
        {lastBacklink && (
          <span className="flex items-center gap-1 text-text-muted">
            <Link2 size={11} strokeWidth={1.75} />
            <span>last referenced in</span>
            <button
              onClick={() => onLinkClick(lastBacklink.title || "")}
              className="text-accent hover:underline truncate max-w-[260px]"
              title={lastBacklink.title || "(untitled)"}
            >
              [[{lastBacklink.title || "untitled"}]]
            </button>
          </span>
        )}
      </div>

      {/* Next steps — only when there's at least one open checkbox */}
      {openItems.length > 0 && (
        <div className="flex items-start gap-3 pt-2 border-t border-border">
          <span className="flex items-center gap-1.5 text-text-muted shrink-0 pt-1">
            <ListTodo size={12} strokeWidth={1.9} />
            <span className="uppercase tracking-wider text-[10px] font-semibold">Next</span>
          </span>
          <ul className="flex-1 min-w-0 space-y-1">
            {openItems.map((item) => (
              <li
                key={item.bracketIndex}
                className="flex items-center gap-2 text-text-primary text-[13px]"
              >
                <Checkbox
                  checked={false}
                  size={14}
                  color="var(--color-lb-cat-task)"
                  title={onContentPatch ? "Mark done" : "Open editor to toggle"}
                  disabled={!onContentPatch}
                  onClick={() => handleToggle(item.bracketIndex)}
                />
                <span className="truncate">{item.text}</span>
              </li>
            ))}
            {checkboxes.filter((c) => !c.checked).length > openItems.length && (
              <li className="text-text-muted text-[11px] italic pl-[22px]">
                +{checkboxes.filter((c) => !c.checked).length - openItems.length} more
              </li>
            )}
          </ul>
          {checkboxes.length > 0 && (
            <span
              className="shrink-0 flex items-center gap-1 text-text-muted text-[11px] pt-1"
              title="Completed / total in this note"
            >
              <CheckSquare size={11} strokeWidth={1.75} />
              <span className="tabular-nums">
                {checkboxes.filter((c) => c.checked).length}/{checkboxes.length}
              </span>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function Pill({
  color,
  bg,
  children,
}: {
  color: string;
  bg: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium"
      style={{ color, background: bg, border: `1px solid ${color}33` }}
    >
      {children}
    </span>
  );
}
