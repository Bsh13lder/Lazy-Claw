/** Deterministic color palette by note type.
 *
 *  Derived from the first tag that matches a known category. Used on:
 *  - MemoCard left border
 *  - PageListSidebar bullet dot
 *  - Chip next to title
 *
 *  Keep this list tight — too many colors becomes noise. Unknown types
 *  fall through to neutral.
 */
export type NoteColor = {
  /** Tailwind-friendly dot / border class (hex for safety against JIT purging). */
  ring: string;
  /** Small emoji to represent the category (shown before title). */
  emoji: string;
  /** Human-readable label (for titles / tooltips). */
  label: string;
};

const PALETTE: Record<string, NoteColor> = {
  task:      { ring: "#f59e0b", emoji: "📋", label: "Task" },
  journal:   { ring: "#3b82f6", emoji: "📓", label: "Journal" },
  lesson:    { ring: "#eab308", emoji: "💡", label: "Lesson" },
  til:       { ring: "#22c55e", emoji: "🧠", label: "TIL" },
  decision:  { ring: "#a855f7", emoji: "✔️", label: "Decision" },
  price:     { ring: "#06b6d4", emoji: "💰", label: "Price" },
  deadline:  { ring: "#ef4444", emoji: "⏰", label: "Deadline" },
  command:   { ring: "#94a3b8", emoji: "⚡", label: "Command" },
  recipe:    { ring: "#f97316", emoji: "🍳", label: "Recipe" },
  contact:   { ring: "#14b8a6", emoji: "📇", label: "Contact" },
  idea:      { ring: "#8b5cf6", emoji: "💭", label: "Idea" },
  reference: { ring: "#64748b", emoji: "🔗", label: "Reference" },
  rollup:    { ring: "#d946ef", emoji: "📊", label: "Rollup" },
  layer:     { ring: "#84cc16", emoji: "🗂", label: "Layer" },
  imported:  { ring: "#64748b", emoji: "📥", label: "Imported" },
  pinned:    { ring: "#fbbf24", emoji: "★",  label: "Pinned" },
  auto:      { ring: "#6366f1", emoji: "✨", label: "Auto" },
  survival:  { ring: "#f97316", emoji: "🛡", label: "Survival" },
  fact:      { ring: "#14b8a6", emoji: "💠", label: "Fact" },
  learned_preference: { ring: "#a3a3a3", emoji: "🔖", label: "Preference" },
  context:   { ring: "#64748b", emoji: "📎", label: "Context" },
};

const DEFAULT: NoteColor = {
  ring: "#475569",
  emoji: "📝",
  label: "Note",
};

/** Who saved this note — derived from `owner/user` or `owner/agent` tags. */
export type Owner = "user" | "agent" | "unknown";

export function ownerOf(tags: string[] | null | undefined): Owner {
  if (!tags) return "unknown";
  const lower = tags.map((t) => t.toLowerCase());
  if (lower.includes("owner/user")) return "user";
  if (lower.includes("owner/agent")) return "agent";
  return "unknown";
}

/** Human label + emoji for the owner badge. */
export const OWNER_META: Record<Owner, { emoji: string; label: string; ring: string }> = {
  user:    { emoji: "👤", label: "You",     ring: "#ec4899" },
  agent:   { emoji: "🤖", label: "Agent",   ring: "#0ea5e9" },
  unknown: { emoji: "📝", label: "Unknown", ring: "#64748b" },
};

/** Every category chip the filter UI offers. Keep ordered by priority. */
export const FILTER_CATEGORIES: { key: string; label: string; emoji: string; ring: string }[] = [
  { key: "task",     label: "Tasks",     emoji: "📋", ring: "#f59e0b" },
  { key: "journal",  label: "Journal",   emoji: "📓", ring: "#3b82f6" },
  { key: "lesson",   label: "Lessons",   emoji: "💡", ring: "#eab308" },
  { key: "til",      label: "TIL",       emoji: "🧠", ring: "#22c55e" },
  { key: "decision", label: "Decisions", emoji: "✔️", ring: "#a855f7" },
  { key: "deadline", label: "Deadlines", emoji: "⏰", ring: "#ef4444" },
  { key: "memory",   label: "Facts",     emoji: "🗃", ring: "#14b8a6" },
  { key: "site-memory", label: "Site knowledge", emoji: "🌐", ring: "#6366f1" },
  { key: "daily-log", label: "Daily logs", emoji: "📅", ring: "#3b82f6" },
  { key: "survival", label: "Survival",  emoji: "🛡", ring: "#f97316" },
  { key: "fact",     label: "Facts (raw)", emoji: "💠", ring: "#14b8a6" },
  { key: "learned_preference", label: "Preferences", emoji: "🔖", ring: "#a3a3a3" },
];

/** Tags the system auto-stamps (owner/*, auto, source/chat, kind/*, layer/*,
 *  imported/*, journal/YYYY-MM-DD, priority/*, category/*, site/*). These are
 *  noise in the user-facing tag cloud — hidden behind a toggle. */
export const SYSTEM_TAG_EXACT = new Set(["auto"]);
export const SYSTEM_TAG_PREFIXES = [
  "owner/",
  "source/",
  "kind/",
  "layer/",
  "imported/",
  "journal/",
  "priority/",
  "category/",
  "site/",
];

export function isSystemTag(tag: string): boolean {
  const t = tag.toLowerCase();
  if (SYSTEM_TAG_EXACT.has(t)) return true;
  return SYSTEM_TAG_PREFIXES.some((p) => t.startsWith(p));
}

/** Does a note match the category filter key? (tag prefix match) */
export function matchesCategory(tags: string[] | null | undefined, key: string): boolean {
  if (!tags) return false;
  const lower = tags.map((t) => t.toLowerCase());
  return lower.includes(key) || lower.some((t) => t.startsWith(`${key}/`));
}

/** Pick the best color for a note based on its tags. Priority: task > deadline > journal > lesson > til > decision > rest. */
export function colorForTags(
  tags: string[] | null | undefined,
  pinned = false,
): NoteColor {
  if (pinned) return { ...PALETTE.pinned };
  if (!tags || tags.length === 0) return DEFAULT;

  // Priority order — more specific categories first
  const priority = [
    "task", "deadline", "journal", "lesson", "til",
    "decision", "price", "command", "recipe", "contact",
    "idea", "rollup", "reference", "layer",
    "survival", "fact", "learned_preference", "context",
    "imported", "auto",
  ];

  const lower = tags.map((t) => t.toLowerCase());

  for (const key of priority) {
    if (lower.includes(key)) return PALETTE[key];
    // Hierarchical tags like "journal/2026-04-18" or "category/foo"
    if (lower.some((t) => t.startsWith(`${key}/`))) return PALETTE[key];
  }
  return DEFAULT;
}
