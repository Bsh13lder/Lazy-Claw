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
  lesson:             { ring: "#d4a015", emoji: "💡", label: "Lesson" },        // muted gold (legacy)
  shape:              { ring: "#22c55e", emoji: "🧩", label: "Skill shape" },   // green-500 — verified replayable
  "shape-pending":    { ring: "#6b7280", emoji: "⌛", label: "Pending shape" }, // gray-500 — awaiting verification
  "shape-failed":     { ring: "#dc2626", emoji: "⚠️", label: "Failed shape" }, // red-600
  "shape-known-bad":  { ring: "#7f1d1d", emoji: "🚫", label: "Known-bad shape" }, // crimson — never replay
  til:                { ring: "#22c55e", emoji: "🧠", label: "TIL" },           // green-500
  decision:           { ring: "#9333ea", emoji: "✔️", label: "Decision" },     // violet-600 (distinct from emerald accent)
  price:              { ring: "#0891b2", emoji: "💰", label: "Price" },         // cyan-700
  deadline:           { ring: "#e11d48", emoji: "⏰", label: "Deadline" },      // rose-600
  command:            { ring: "#64748b", emoji: "⚡", label: "Command" },       // slate-500
  recipe:             { ring: "#d97706", emoji: "🍳", label: "Recipe" },        // amber-700
  contact:            { ring: "#0ea5e9", emoji: "📇", label: "Contact" },       // sky-500
  idea:               { ring: "#9333ea", emoji: "💭", label: "Idea" },          // violet-700
  reference:          { ring: "#64748b", emoji: "🔗", label: "Links" },
  rollup:             { ring: "#c026d3", emoji: "📊", label: "Rollup" },        // fuchsia-600
  layer:              { ring: "#65a30d", emoji: "🗂", label: "Layer" },         // lime-700
  imported:           { ring: "#64748b", emoji: "📥", label: "Imported" },
  pinned:             { ring: "#f59e0b", emoji: "★",  label: "Pinned" },        // amber-500
  auto:               { ring: "#6366f1", emoji: "✨", label: "Auto-captured" },// indigo-500
  survival:           { ring: "#d97706", emoji: "🛡", label: "Survival" },
  fact:               { ring: "#14b8a6", emoji: "💠", label: "Fact" },          // teal-500 — distinct from memory's teal-600
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
 *  so a note's color, badge, icon, and filter row always agree.
 *
 *  Order matters — outcome-flavored shapes win over the generic `shape`
 *  bucket so a `kind/shape outcome/known-bad` note paints crimson, not
 *  green. Generic `shape` only matches when no outcome tag is present
 *  (legacy / mid-write).
 */
export const CATEGORY_PRIORITY: string[] = [
  "task", "deadline", "journal",
  "shape-known-bad", "shape-failed", "shape-pending", "shape",
  "lesson", "til",
  "decision", "price", "command", "recipe", "contact",
  "idea", "rollup", "reference", "layer",
  "survival", "fact", "learned_preference", "context",
  "memory", "site-memory", "daily-log",
  "imported", "auto",
];

/** Upper bound on how many category slices a single graph node renders.
 *  Three slices at 120° each stay legible; anything past that gets a `+N`
 *  overflow chip drawn by the node renderer. */
export const MAX_SLICES = 3;

/** Coerce a tag list to lowercase strings, dropping non-string entries.
 *  A single non-string in the input would otherwise crash `.toLowerCase()`
 *  and abort the surrounding useMemo factory, causing React error #310
 *  (hook count mismatch on next render). */
function safeLowerTags(tags: string[] | null | undefined): string[] {
  if (!Array.isArray(tags)) return [];
  const out: string[] = [];
  for (const t of tags) {
    if (typeof t === "string") out.push(t.toLowerCase());
  }
  return out;
}

/** Walk CATEGORY_PRIORITY and return every matching key (capped at MAX_SLICES).
 *  - pinned short-circuits to ["pinned"] so halos stay a single color.
 *  - callers can compare the returned list length against actual match count
 *    by re-running categoryMatchCount() when the "+N more" indicator matters. */
export function categoryKeysFor(
  tags: string[] | null | undefined,
  pinned = false,
): string[] {
  if (pinned) return ["pinned"];
  const lower = safeLowerTags(tags);
  if (lower.length === 0) return [];
  const hit: string[] = [];
  for (const key of CATEGORY_PRIORITY) {
    if (hit.length >= MAX_SLICES) break;
    if (lower.includes(key) || lower.some((t) => t.startsWith(`${key}/`))) {
      hit.push(key);
    }
  }
  return hit;
}

/** Total number of categories that match the tag list (ignoring MAX_SLICES).
 *  Drives the "+N more" overflow indicator on graph nodes. */
export function categoryMatchCount(
  tags: string[] | null | undefined,
  pinned = false,
): number {
  if (pinned) return 1;
  const lower = safeLowerTags(tags);
  if (lower.length === 0) return 0;
  let n = 0;
  for (const key of CATEGORY_PRIORITY) {
    if (lower.includes(key) || lower.some((t) => t.startsWith(`${key}/`))) n += 1;
  }
  return n;
}

/** Resolve a single (primary) category key from a tag list. Returns null if
 *  no match. Keeps every legacy caller pointing at one winning category. */
export function categoryKeyFor(
  tags: string[] | null | undefined,
  pinned = false,
): string | null {
  const keys = categoryKeysFor(tags, pinned);
  return keys[0] ?? null;
}

/** Who saved this note — derived from `owner/user` or `owner/agent` tags. */
export type Owner = "user" | "agent" | "unknown";

export function ownerOf(tags: string[] | null | undefined): Owner {
  const lower = safeLowerTags(tags);
  if (lower.length === 0) return "unknown";
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

/** Every category chip the filter UI offers. Derived from PALETTE so a
 *  chip's icon/swatch, its filter row, and its graph-node color always
 *  agree — no hand-curated drift. Ordered by narrative grouping:
 *    1. work: task → til
 *    2. artifacts: decision → layer
 *    3. life/system: survival → daily-log
 *    4. ambient: context → auto
 *  `pinned` is excluded because it renders as a halo, not a filter chip. */
const FILTER_ORDER: string[] = [
  "task", "deadline", "journal",
  // Skill-shape cards live behind the "Skills vault" toggle in FilterBar
  // — they're hidden by default to keep the user's vault uncluttered.
  "shape", "shape-pending", "shape-failed", "shape-known-bad",
  "lesson", "til",
  "decision", "idea", "price", "command", "recipe", "contact",
  "reference", "rollup", "layer",
  "survival", "fact", "memory", "learned_preference",
  "site-memory", "daily-log",
  "context", "imported", "auto",
];

/** Filter chips that the "Skills vault" toggle hides by default. The
 *  vault toggle in FilterBar reads this set to set up the initial
 *  hidden-categories state on first load. */
export const SKILLS_VAULT_KEYS: ReadonlySet<string> = new Set([
  "shape", "shape-pending", "shape-failed", "shape-known-bad",
]);

/** User-facing display label per filter key. Falls back to `PALETTE[key].label`
 *  when a key isn't listed — so new kinds inherit the PALETTE label by default
 *  without forcing a second touch-point. Pluralize list-style kinds (Tasks,
 *  Lessons, Decisions) but keep collective nouns (Journal, Survival) singular. */
const FILTER_LABEL_OVERRIDE: Record<string, string> = {
  task:               "Tasks",
  deadline:           "Deadlines",
  lesson:             "Legacy lessons",
  shape:              "Skill shapes",
  "shape-pending":    "Pending shapes",
  "shape-failed":     "Failed shapes",
  "shape-known-bad":  "Known-bad shapes",
  decision:           "Decisions",
  idea:               "Ideas",
  price:              "Prices",
  command:            "Commands",
  recipe:             "Recipes",
  contact:            "Contacts",
  reference:          "Links",
  rollup:             "Rollups",
  layer:              "Layers",
  fact:               "Facts (raw)",
  memory:             "Facts",
  learned_preference: "Preferences",
  "site-memory":      "Site knowledge",
  "daily-log":        "Daily logs",
};

export const FILTER_CATEGORIES: { key: string; label: string; emoji: string; ring: string }[] =
  FILTER_ORDER.map((key) => {
    const p = PALETTE[key];
    return {
      key,
      label: FILTER_LABEL_OVERRIDE[key] ?? p.label,
      emoji: p.emoji,
      ring:  p.ring,
    };
  });

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

/** Return every FILTER_CATEGORIES key a tag list matches, in declared order.
 *  Lowercases the tag list once and reuses simple string ops to avoid the
 *  O(n × FILTER_CATEGORIES) regex re-evaluation that the per-note count
 *  loop in LazyBrain.tsx used to do.
 *
 *  Unlike `categoryKeysFor` (which is capped at MAX_SLICES for graph-node
 *  rendering), this returns the *complete* set so chip count badges stay
 *  accurate for notes with many overlapping tags. */
export function categoryKeysAllFor(
  tags: string[] | null | undefined,
): string[] {
  const lower = safeLowerTags(tags);
  if (lower.length === 0) return [];
  // Pre-compute "has kind/shape" because every shape-* compound key needs it.
  const hasKindShape = lower.includes("kind/shape");
  const out: string[] = [];
  for (const c of FILTER_CATEGORIES) {
    const key = c.key;
    if (key.startsWith("shape-")) {
      if (hasKindShape && lower.includes(`outcome/${key.slice("shape-".length)}`)) {
        out.push(key);
      }
      continue;
    }
    if (
      lower.includes(key)
      || lower.includes(`kind/${key}`)
      || lower.some((t) => t.startsWith(`${key}/`))
    ) {
      out.push(key);
    }
  }
  return out;
}

/** Does a note match the category filter key? (tag prefix match)
 *
 *  Also matches the `kind/<key>` namespace introduced by the Obsidian-borrowed
 *  redesign (`kind/shape`, `kind/fact`, `kind/known-bad`) so chips for new
 *  taxonomy values resolve to the right notes without forcing every emitter
 *  to also stamp a bare top-level tag.
 *
 *  Special handling for `outcome/<state>`: a `shape-<state>` chip key
 *  (e.g. `shape-pending`, `shape-failed`, `shape-known-bad`) requires
 *  both `kind/shape` AND the matching `outcome/<state>` tag. Lets the
 *  filter UI surface "verified shapes vs pending shapes" without two
 *  completely separate code paths.
 */
export function matchesCategory(tags: string[] | null | undefined, key: string): boolean {
  const lower = safeLowerTags(tags);
  if (lower.length === 0) return false;

  // Compound shape-<state> keys.
  if (key.startsWith("shape-")) {
    const state = key.slice("shape-".length);
    return lower.includes("kind/shape") && lower.includes(`outcome/${state}`);
  }

  return (
    lower.includes(key)
    || lower.some((t) => t.startsWith(`${key}/`))
    || lower.includes(`kind/${key}`)
  );
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

// Phase I — golden-ratio hue rotation. Mapping a small int → distinct,
// readable colors uses the same trick d3-scheme-category10 / Stripe / Mapbox
// reach for: multiply by the conjugate of the golden ratio so adjacent
// community ids land far apart on the color wheel.
const GOLDEN_RATIO_CONJUGATE = 0.6180339887498949;

function hslToHex(h: number, s: number, l: number): string {
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    const c = l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    return Math.round(255 * c)
      .toString(16)
      .padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

/** Color for a Leiden community. ``community_id`` is an int; we map it to
 *  hue via golden-ratio rotation so neighbouring ids look distinct.
 *
 *  When ``community_id`` is null/undefined returns null so callers fall
 *  through to ``colorForTags`` (preserves existing tag palette in the
 *  "categories" view of the galaxy graph). */
export function colorForCommunity(
  community_id: number | null | undefined,
): NoteColor | null {
  if (community_id == null || !Number.isFinite(community_id)) return null;
  const hue = ((community_id * GOLDEN_RATIO_CONJUGATE) % 1) * 360;
  const ring = hslToHex(hue, 0.55, 0.62);
  return {
    ring,
    emoji: "✨",
    label: `Cluster ${community_id}`,
  };
}

/** Resolve a graph-node fill: prefer Leiden community color when the
 *  backend stamped one (``neural-link`` mode), else fall back to tag
 *  palette. Pinned always wins (gold) so user emphasis survives both
 *  modes. */
export function colorForNode(
  node: {
    pinned?: boolean;
    community_id?: number | null;
    tags?: string[] | null;
  },
  mode: "category" | "neural-link" = "category",
): NoteColor {
  if (node.pinned) return { ...PALETTE.pinned };
  if (mode === "neural-link") {
    const c = colorForCommunity(node.community_id);
    if (c) return c;
  }
  return colorForTags(node.tags ?? [], false);
}

/** Halo (drop-shadow) color for a category — same hue, ~35% alpha so nodes
 *  glow against the dark bg without smearing into neighbors. */
export function haloForKey(key: string | null | undefined): string {
  const ring = key && PALETTE[key] ? PALETTE[key].ring : DEFAULT.ring;
  return hexToRgba(ring, 0.35);
}

/** Solid ring hex for a category key (or DEFAULT when unknown). Callers that
 *  need the pie-slice fill or the stroke color on a graph node use this. */
export function ringForKey(key: string | null | undefined): string {
  return key && PALETTE[key] ? PALETTE[key].ring : DEFAULT.ring;
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

// ── Stellar atlas: categorical zone anchors ───────────────────────────────
//
// The graph view organises nodes into named "constellations" — one fixed
// anchor per category, laid out on three concentric orbits around the
// canvas centre. Every node feels a STRONG gravitational pull toward
// its category's anchor (see ForceSimulation.CLUSTER_K) so the user
// always sees journals in the journal zone, rollups orbiting the centre,
// daily-logs in their own quadrant, etc. — no more "one big blob".
//
// Layout philosophy:
//   • CENTRE — rollup. Aggregations sit at the gravitational sun
//     because everything cross-references back to them.
//   • INNER ORBIT — the active life: journal, daily-log, task,
//     deadline, idea + the four shape variants. Cardinal directions
//     so the user's eye finds them first.
//   • MIDDLE ORBIT — durable knowledge: lesson, decision, fact,
//     memory, til, learned_preference, context.
//   • OUTER ORBIT — operational + infrastructure: contact, command,
//     recipe, price, reference, layer, survival, site-memory,
//     imported, auto, pinned. These tend to be smaller, quieter
//     populations so the periphery suits them.
//
// Coordinates are normalised: (angle in radians, radius in 0..1 of
// half-canvas). `categoryAnchorAt(key, w, h)` resolves to absolute
// (x, y) given the current canvas size.
const TAU = Math.PI * 2;
const D = (deg: number): number => (deg / 360) * TAU;

interface ZoneAnchor {
  /** Angle measured CCW from +X (east = 0, north = -π/2). */
  angle: number;
  /** Distance from centre, in units of half-min-canvas-dimension. 0 = centre. */
  radius: number;
}

const ZONE_ANCHORS: Record<string, ZoneAnchor> = {
  // CENTRE — gravitational sun
  rollup:               { angle: 0,        radius: 0    },

  // INNER ORBIT — active life (cardinals + shape cluster SE)
  journal:              { angle: D(-90),   radius: 0.34 }, // N
  task:                 { angle: D(-30),   radius: 0.34 }, // NE
  "daily-log":          { angle: D(-150),  radius: 0.34 }, // NW
  deadline:             { angle: D(20),    radius: 0.34 }, // E-of-NE
  idea:                 { angle: D(-70),   radius: 0.34 }, // NNE

  // SHAPE CLUSTER — fans out around SE so 4 variants stay visibly grouped
  shape:                { angle: D(60),    radius: 0.36 },
  "shape-pending":      { angle: D(72),    radius: 0.42 },
  "shape-failed":       { angle: D(48),    radius: 0.42 },
  "shape-known-bad":    { angle: D(80),    radius: 0.48 },

  // MIDDLE ORBIT — knowledge
  lesson:               { angle: D(108),   radius: 0.55 },
  til:                  { angle: D(128),   radius: 0.55 },
  decision:             { angle: D(148),   radius: 0.55 },
  fact:                 { angle: D(180),   radius: 0.50 }, // W
  memory:               { angle: D(202),   radius: 0.55 },
  learned_preference:   { angle: D(222),   radius: 0.55 },
  context:              { angle: D(242),   radius: 0.55 },

  // OUTER ORBIT — operational / infrastructure (NE → N → NW going CCW)
  reference:            { angle: D(-50),   radius: 0.85 },
  command:              { angle: D(-12),   radius: 0.85 },
  recipe:               { angle: D(8),     radius: 0.85 },
  contact:              { angle: D(38),    radius: 0.85 },
  price:                { angle: D(-2),    radius: 0.78 },
  survival:             { angle: D(168),   radius: 0.85 },
  layer:                { angle: D(258),   radius: 0.78 },
  "site-memory":        { angle: D(282),   radius: 0.85 },
  imported:             { angle: D(300),   radius: 0.85 },
  auto:                 { angle: D(322),   radius: 0.85 },
  pinned:               { angle: D(-110),  radius: 0.78 },
  _default:             { angle: D(0),     radius: 0.95 }, // periphery — uncategorised drift outwards
};

/** Resolve a category key to its absolute (x, y) anchor on the current
 *  canvas. Falls back to the canvas centre when the key is unknown. */
export function categoryAnchorAt(
  key: string | null | undefined,
  w: number,
  h: number,
): { x: number; y: number } {
  const cx = w / 2;
  const cy = h / 2;
  const anchor = (key && ZONE_ANCHORS[key]) || ZONE_ANCHORS._default;
  // Half of the SHORTER dimension so anchors sit comfortably inside the
  // canvas regardless of aspect ratio. Soft 0.95 so the outer orbit isn't
  // clipped by the legend / search overlays.
  const half = Math.min(w, h) * 0.5;
  const r = anchor.radius * half * 0.95;
  return {
    x: cx + Math.cos(anchor.angle) * r,
    y: cy + Math.sin(anchor.angle) * r,
  };
}

/** All categories that have a defined anchor — driver for zone-label
 *  rendering on the canvas (skip any zone with zero active members so
 *  the sky stays uncluttered). */
export const ZONE_KEYS: readonly string[] = Object.keys(ZONE_ANCHORS).filter(
  (k) => k !== "_default",
);

/** Human-readable zone title for the canvas label. Reuses the palette's
 *  `label` (e.g. "Daily log", "Failed shape") when available, else falls
 *  back to a Title-Cased version of the key. */
export function zoneTitle(key: string): string {
  const fromPalette = PALETTE[key]?.label;
  if (fromPalette) return fromPalette;
  return key
    .split(/[_-]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
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
