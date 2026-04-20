/** Deterministic color palette by note type — Aurora palette.
 *
 *  Designed as one luminance band so the graph reads as a constellation
 *  against the dark `#0f0f0f` LazyBrain background. Anchored on the
 *  existing violet theme so chrome and content stay cohesive.
 *
 *  All hex values mirror the `--color-lb-cat-*` CSS vars declared in
 *  `globals.css` under `.lazybrain-root`. Keep the two in sync.
 */
export type NoteColor = {
  /** CSS hex used for: graph node fill, MemoCard left border, sidebar dot, chip. */
  ring: string;
  /** Small emoji to represent the category (shown before title). */
  emoji: string;
  /** Human-readable label (for titles / tooltips). */
  label: string;
};

// Aurora v2 — deeper, less neon. Each color sits in the saturated mid-band
// (Tailwind ~500) so white badge text reads clearly against it. Bright
// pastels were burning the eye and washing out the in-dot letters; this
// drops the brightness to "Obsidian-Minimal vibe with a hint of color"
// instead of "Logseq tag salad". Anchored on the existing violet theme.
const PALETTE: Record<string, NoteColor> = {
  task:               { ring: "#e0742a", emoji: "📋", label: "Task" },          // deeper coral
  journal:            { ring: "#2dd4bf", emoji: "📓", label: "Journal" },       // teal-400
  lesson:             { ring: "#d4a015", emoji: "💡", label: "Lesson" },        // muted gold
  til:                { ring: "#22c55e", emoji: "🧠", label: "TIL" },           // green-500
  decision:           { ring: "#9333ea", emoji: "✔️", label: "Decision" },     // violet-600 (distinct from emerald accent)
  price:              { ring: "#0891b2", emoji: "💰", label: "Price" },         // cyan-700
  deadline:           { ring: "#e11d48", emoji: "⏰", label: "Deadline" },      // rose-600
  command:            { ring: "#64748b", emoji: "⚡", label: "Command" },       // slate-500
  recipe:             { ring: "#d97706", emoji: "🍳", label: "Recipe" },        // amber-700
  contact:            { ring: "#0ea5e9", emoji: "📇", label: "Contact" },       // sky-500
  idea:               { ring: "#9333ea", emoji: "💭", label: "Idea" },          // violet-700
  reference:          { ring: "#64748b", emoji: "🔗", label: "Reference" },
  rollup:             { ring: "#c026d3", emoji: "📊", label: "Rollup" },        // fuchsia-600
  layer:              { ring: "#65a30d", emoji: "🗂", label: "Layer" },         // lime-700
  imported:           { ring: "#64748b", emoji: "📥", label: "Imported" },
  pinned:             { ring: "#f59e0b", emoji: "★",  label: "Pinned" },        // amber-500
  auto:               { ring: "#6366f1", emoji: "✨", label: "Auto" },          // indigo-500
  survival:           { ring: "#d97706", emoji: "🛡", label: "Survival" },
  fact:               { ring: "#0d9488", emoji: "💠", label: "Fact" },          // teal-600
  learned_preference: { ring: "#94a3b8", emoji: "🔖", label: "Preference" },    // slate-400
  context:            { ring: "#64748b", emoji: "📎", label: "Context" },
  memory:             { ring: "#0d9488", emoji: "🗃", label: "Memory" },        // teal-600
  "site-memory":      { ring: "#6366f1", emoji: "🌐", label: "Site knowledge" }, // indigo-500
  "daily-log":        { ring: "#0284c7", emoji: "📅", label: "Daily log" },     // sky-600
};

const DEFAULT: NoteColor = {
  ring: "#475569",
  emoji: "📝",
  label: "Note",
};

/** Single source of truth for which category a note belongs to.
 *  Both `colorForTags()` and `GraphView.pickCategoryKey()` walk this list,
 *  so a note's color, badge, icon, and filter row always agree. */
export const CATEGORY_PRIORITY: string[] = [
  "task", "deadline", "journal", "lesson", "til",
  "decision", "price", "command", "recipe", "contact",
  "idea", "rollup", "reference", "layer",
  "survival", "fact", "learned_preference", "context",
  "memory", "site-memory", "daily-log",
  "imported", "auto",
];

/** Resolve a category key from a tag list. Returns null if no match. */
export function categoryKeyFor(
  tags: string[] | null | undefined,
  pinned = false,
): string | null {
  if (pinned) return "pinned";
  if (!tags || tags.length === 0) return null;
  const lower = tags.map((t) => t.toLowerCase());
  for (const key of CATEGORY_PRIORITY) {
    if (lower.includes(key)) return key;
    if (lower.some((t) => t.startsWith(`${key}/`))) return key;
  }
  return null;
}

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
  user:    { emoji: "👤", label: "You",     ring: "#f472b6" },
  agent:   { emoji: "🤖", label: "Agent",   ring: "#38bdf8" },
  unknown: { emoji: "📝", label: "Unknown", ring: "#64748b" },
};

/** Every category chip the filter UI offers. Colors mirror PALETTE 1:1
 *  so a filter row's swatch and its graph node's color always match. */
export const FILTER_CATEGORIES: { key: string; label: string; emoji: string; ring: string }[] = [
  { key: "task",               label: "Tasks",         emoji: "📋", ring: PALETTE.task.ring },
  { key: "journal",            label: "Journal",       emoji: "📓", ring: PALETTE.journal.ring },
  { key: "lesson",             label: "Lessons",       emoji: "💡", ring: PALETTE.lesson.ring },
  { key: "til",                label: "TIL",           emoji: "🧠", ring: PALETTE.til.ring },
  { key: "decision",           label: "Decisions",     emoji: "✔️", ring: PALETTE.decision.ring },
  { key: "deadline",           label: "Deadlines",     emoji: "⏰", ring: PALETTE.deadline.ring },
  { key: "memory",             label: "Facts",         emoji: "🗃", ring: PALETTE.memory.ring },
  { key: "site-memory",        label: "Site knowledge",emoji: "🌐", ring: PALETTE["site-memory"].ring },
  { key: "daily-log",          label: "Daily logs",    emoji: "📅", ring: PALETTE["daily-log"].ring },
  { key: "survival",           label: "Survival",      emoji: "🛡", ring: PALETTE.survival.ring },
  { key: "fact",               label: "Facts (raw)",   emoji: "💠", ring: PALETTE.fact.ring },
  { key: "learned_preference", label: "Preferences",   emoji: "🔖", ring: PALETTE.learned_preference.ring },
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

/** Pick the best color for a note based on its tags. Walks CATEGORY_PRIORITY
 *  so it agrees with `categoryKeyFor()` and the graph's `pickCategoryKey()`. */
export function colorForTags(
  tags: string[] | null | undefined,
  pinned = false,
): NoteColor {
  if (pinned) return { ...PALETTE.pinned };
  const key = categoryKeyFor(tags, false);
  if (!key) return DEFAULT;
  return PALETTE[key] ?? DEFAULT;
}

/** Halo (drop-shadow) color for a category — same hue, ~35% alpha so nodes
 *  glow against the dark bg without smearing into neighbors. */
export function haloForKey(key: string | null | undefined): string {
  const ring = key && PALETTE[key] ? PALETTE[key].ring : DEFAULT.ring;
  return hexToRgba(ring, 0.35);
}

/** Pick a high-contrast text color (white or near-black) for a given
 *  background hex — uses perceived luminance (rec. 709) so the in-dot
 *  badge text is legible against both bright and dark category colors. */
export function readableTextOn(hex: string): string {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) return "#fff";
  const r = parseInt(m[1], 16) / 255;
  const g = parseInt(m[2], 16) / 255;
  const b = parseInt(m[3], 16) / 255;
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.62 ? "#1a1625" : "#ffffff";
}

/** Hex (#rrggbb or #rgb) → rgba string. Defensive — falls back to bg-muted. */
function hexToRgba(hex: string, alpha: number): string {
  const m3 = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(hex);
  const m6 = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  let r = 100, g = 116, b = 139;
  if (m6) {
    r = parseInt(m6[1], 16);
    g = parseInt(m6[2], 16);
    b = parseInt(m6[3], 16);
  } else if (m3) {
    r = parseInt(m3[1] + m3[1], 16);
    g = parseInt(m3[2] + m3[2], 16);
    b = parseInt(m3[3] + m3[3], 16);
  }
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
