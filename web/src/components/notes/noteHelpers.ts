import type { LazyBrainNote } from "../../api";

export type NoteKind = "note" | "idea" | "memory";

/** Tags that mark a note as "personal" — visible in the Notes page. */
export const NOTE_KIND_TAGS = ["kind/note", "kind/idea", "kind/memory"] as const;

/** Visual identity per kind. The dot color is what scans the list. */
export const KIND_META: Record<
  NoteKind,
  { label: string; dot: string; chipBg: string; chipBorder: string; chipText: string }
> = {
  note:   { label: "Note",   dot: "#10b981", chipBg: "rgba(16,185,129,0.12)",  chipBorder: "rgba(16,185,129,0.45)", chipText: "#10b981" },
  idea:   { label: "Idea",   dot: "#06b6d4", chipBg: "rgba(6,182,212,0.12)",   chipBorder: "rgba(6,182,212,0.45)",  chipText: "#22d3ee" },
  memory: { label: "Memory", dot: "#a78bfa", chipBg: "rgba(167,139,250,0.14)", chipBorder: "rgba(167,139,250,0.50)", chipText: "#c4b5fd" },
};

/** Sources are "where did this note come from" — same shape as kind chips. */
export const SOURCE_LABEL: Record<string, string> = {
  telegram: "Telegram",
  web:      "Web UI",
  agent:    "Agent",
  memory:   "Memory mirror",
  task:     "Task mirror",
};

export function noteKind(note: LazyBrainNote): NoteKind {
  const tags = note.tags || [];
  if (tags.includes("kind/idea")) return "idea";
  if (tags.includes("kind/memory") || tags.includes("memory")) return "memory";
  return "note";
}

export function noteSource(note: LazyBrainNote): string | null {
  const t = (note.tags || []).find((x) => x.startsWith("source/"));
  return t ? t.slice("source/".length) : null;
}

/** Tags worth showing on the card — strips the structural ones. */
export function visibleTags(note: LazyBrainNote): string[] {
  return (note.tags || []).filter(
    (t) =>
      !t.startsWith("kind/") &&
      !t.startsWith("owner/") &&
      !t.startsWith("source/") &&
      !t.startsWith("category/") &&
      !t.startsWith("status/") &&
      !t.startsWith("layer/") &&
      !t.startsWith("priority/") &&
      t !== "auto" &&
      t !== "memory" &&
      t !== "task",
  );
}

/** "12m ago" / "yesterday" / "Tue 14:32" / "Apr 22" */
export function relativeTimeAgo(iso: string, now = new Date()): string {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "";
  const ms = now.getTime() - t.getTime();
  const min = Math.floor(ms / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const sameDay =
    t.getDate() === now.getDate() &&
    t.getMonth() === now.getMonth() &&
    t.getFullYear() === now.getFullYear();
  if (sameDay) return t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (
    t.getDate() === yesterday.getDate() &&
    t.getMonth() === yesterday.getMonth() &&
    t.getFullYear() === yesterday.getFullYear()
  ) {
    return `yesterday ${t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  }
  const sevenDays = new Date(now);
  sevenDays.setDate(sevenDays.getDate() - 7);
  if (t > sevenDays) {
    return t.toLocaleDateString([], { weekday: "short" }) +
      ` ${t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  }
  return t.toLocaleDateString([], { month: "short", day: "numeric" });
}

export type RecencyBucket = "today" | "yesterday" | "this_week" | "this_month" | "earlier";

export function recencyBucket(iso: string, now = new Date()): RecencyBucket {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "earlier";
  const dayDiff = Math.floor(
    (new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() -
      new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime()) /
      86_400_000,
  );
  if (dayDiff <= 0) return "today";
  if (dayDiff === 1) return "yesterday";
  if (dayDiff <= 7) return "this_week";
  if (dayDiff <= 31) return "this_month";
  return "earlier";
}

export const RECENCY_LABELS: Record<RecencyBucket, string> = {
  today: "Today",
  yesterday: "Yesterday",
  this_week: "This week",
  this_month: "This month",
  earlier: "Earlier",
};

const TRIGGER_RE = /^\s*(note|idea|remember|memo|nota|recuerda)\s*[:\-]\s*(.+)$/is;

/**
 * Detect a "note: …" / "idea: …" / "remember: …" prefix in raw input.
 * Returns the kind tag + cleaned content, mirrors the server-side parser.
 */
export function detectTriggerPrefix(text: string): { kind: NoteKind; content: string } | null {
  const m = TRIGGER_RE.exec(text || "");
  if (!m) return null;
  const word = m[1].toLowerCase();
  const content = m[2].trim();
  if (!content) return null;
  if (word === "remember" || word === "recuerda") return { kind: "memory", content };
  if (word === "idea") return { kind: "idea", content };
  return { kind: "note", content };
}

/** Pull `#hashtags` out of body so they appear as structured tags. */
export function extractInlineTags(content: string): string[] {
  const matches = content.match(/#([\w-]+)/g) || [];
  const out: string[] = [];
  for (const m of matches) {
    const t = m.slice(1).toLowerCase();
    if (t && !out.includes(t)) out.push(t);
  }
  return out;
}

/**
 * Build first-line title (capped at 80 chars) — matches server save_quick_note.
 * Used as a preview in the quick-add ghost row.
 */
export function deriveTitle(content: string): string {
  const first = (content.split(/\r?\n/)[0] || "").trim();
  return (first || content.trim()).slice(0, 80) || "Quick note";
}
