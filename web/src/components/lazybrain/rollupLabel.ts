/** Rollup detection + label-formatting helpers.
 *
 *  Single source of truth for "is this note a rollup, and what should
 *  its dot label read on the graph and in the sidebar?" Lifted out of
 *  PageListSidebar.tsx so the GraphView can share the same impl
 *  instead of re-parsing the same `kind/rollup` / `rollup/weekly/*` /
 *  `source-count/N` / `topic/X` tag conventions in two places.
 *
 *  The frontend is the right home for these — even though `rollup.py`
 *  writes the canonical title, older rows (and any future re-tagging)
 *  flow through here, so the display layer stays robust to schema drift.
 */
import type { LazyBrainNote } from "../../api";

/** Minimum subset of a note we need to compute the display label.
 *  Both LazyBrainNote and a stripped graph-node payload (when extended
 *  with tags) match this shape. */
export type LabelableNote = Pick<LazyBrainNote, "title" | "tags">;

/** Lowercase a tag list defensively. Some legacy notes can carry non-string
 *  tag values (partially-decoded JSON) — coerce so the page never crashes
 *  on a single malformed row. */
export function lowerTags(note: LabelableNote | undefined | null): string[] {
  const raw = note?.tags;
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const t of raw) {
    if (typeof t === "string") out.push(t.toLowerCase());
  }
  return out;
}

/** True iff this note is any kind of rollup (topic / weekly / monthly). */
export function isRollupNote(note: LabelableNote | undefined | null): boolean {
  const tags = lowerTags(note);
  return (
    tags.includes("kind/rollup")
    || tags.some((t) => t.startsWith("rollup/weekly") || t.startsWith("rollup/monthly"))
  );
}

/** True for monthly rollups specifically. Used by GraphView to render the
 *  thicker "two-ring planet" treatment. */
export function isMonthlyRollupNote(
  note: LabelableNote | undefined | null,
): boolean {
  return lowerTags(note).some((t) => t.startsWith("rollup/monthly"));
}

/** Pulls "12" out of `source-count/12`. Returns null when absent (older
 *  rollups predate the count tag). */
export function rollupSourceCount(
  note: LabelableNote | undefined | null,
): number | null {
  const tag = lowerTags(note).find((t) => t.startsWith("source-count/"));
  if (!tag) return null;
  const n = Number(tag.split("/", 2)[1]);
  return Number.isFinite(n) ? n : null;
}

/** Returns the period code for a weekly/monthly rollup, e.g. "W23-2026"
 *  or "M2026-05". Empty string when neither tag is present. */
export function rollupPeriodLabel(
  note: LabelableNote | undefined | null,
): string {
  const tags = lowerTags(note);
  for (const t of tags) {
    if (t.startsWith("rollup/weekly/")) return t.slice("rollup/weekly/".length).toUpperCase();
    if (t.startsWith("rollup/monthly/")) return t.slice("rollup/monthly/".length).toUpperCase();
  }
  return "";
}

/** Returns the topic phrase for a topic-rollup note (`topic/X` tag), or
 *  null when not a topic rollup. */
export function rollupTopicTag(
  note: LabelableNote | undefined | null,
): string | null {
  const tags = lowerTags(note);
  const t = tags.find((x) => x.startsWith("topic/"));
  return t ? t.slice("topic/".length) : null;
}

/** Humanise a monthly period code "M2026-05" into "May 2026". */
function humanizeMonth(code: string): string {
  const m = /^M(\d{4})-(\d{2})$/.exec(code);
  if (!m) return code;
  const year = Number(m[1]);
  const month = Math.max(1, Math.min(12, Number(m[2])));
  const date = new Date(Date.UTC(year, month - 1, 1));
  return date.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

/** Strip a legacy "Topic rollup: " / "Rollup — " prefix from a title.
 *  Newer rows (post 2026-05-04 commit) skip the prefix at write time, so
 *  this is a one-time clean-up at display time — no DB migration needed. */
function stripLegacyRollupPrefix(title: string): string {
  // Case-insensitive, swallows the colon/em-dash + any whitespace.
  const m = /^(topic\s+rollup\s*[:—-]|rollup\s*[:—-])\s*(.+)$/i.exec(title);
  return m ? m[2].trim() : title;
}

/** Title-case a topic slug — "mcp_management" → "Mcp Management",
 *  "n8n" → "N8n". Applied to legacy topic rollup titles where the
 *  title_key fallback came in lowercased from the graph endpoint. */
function titleCaseTopic(s: string): string {
  return s
    .split(/[\s_]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Computed display label for a note as rendered in the graph + sidebar.
 *
 *  Returns `{ primary, suffix }` so the consumer can render the topic
 *  phrase prominently and the period/source-count as a muted secondary
 *  line at the end. The user explicitly asked for the topic up front;
 *  rollup metadata moves to the muted suffix or drops entirely. */
export function displayLabelForNote(
  note: LabelableNote | undefined | null,
  fallbackLabel?: string,
): { primary: string; suffix: string | null } {
  const titleRaw = (note?.title ?? "").trim();
  const fallback = (fallbackLabel ?? "").trim();
  const baseTitle = titleRaw || fallback || "(untitled)";

  // Strip legacy "Topic rollup: …" / "Rollup — …" prefixes UNCONDITIONALLY.
  // The graph endpoint sometimes feeds us a node.label fallback (the
  // lowercased title_key) without the full note metadata payload — so
  // gating prefix-strip on rollup tag detection meant the prefix leaked
  // through whenever notesById didn't have the row yet. Treating prefix
  // removal as plain title hygiene fixes that path too.
  const stripped = stripLegacyRollupPrefix(baseTitle);
  // Detect "this looks like a rollup" both ways — by tags AND by the
  // fact we just stripped a known rollup prefix. The latter catches
  // graph nodes that lack tag metadata in the current React tree.
  const looksRollup =
    isRollupNote(note) || stripped !== baseTitle || !!rollupTopicTag(note);

  if (!looksRollup) {
    return { primary: stripped, suffix: null };
  }

  const period = rollupPeriodLabel(note);
  const count = rollupSourceCount(note);

  // Topic rollup: surface the topic phrase. Prefer the `topic/X` tag
  // (canonical) so casing stays consistent even when the title came
  // through as an all-lowercase title_key. Title-case slugs like
  // "mcp_management" → "Mcp Management" so the dot reads as a name,
  // not a code identifier.
  if (!period) {
    const topicFromTag = rollupTopicTag(note);
    const topic = topicFromTag
      ? titleCaseTopic(topicFromTag)
      : stripped || baseTitle;
    return { primary: topic, suffix: null };
  }

  // Weekly: e.g. "W23 · 8 notes"
  // Monthly: e.g. "May 2026 · 4 weeks"
  const isMonthly = period.startsWith("M");
  let suffix: string;
  if (isMonthly) {
    const human = humanizeMonth(period);
    suffix = count !== null ? `${human} · ${count} weeks` : human;
  } else {
    // Weekly periods come back like "W23-2026" — keep the "W23" prefix.
    const short = period.split("-", 2)[0];
    suffix = count !== null ? `${short} · ${count} notes` : short;
  }
  return { primary: stripped, suffix };
}
