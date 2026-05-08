/**
 * Shared wikilink parser — single source of truth for `[[target#anchor|display]]`
 * + `![[target]]` (transclusion) matching across the LazyBrain UI.
 *
 * Mirrors the backend regex in `lazyclaw/lazybrain/wikilinks.py` so both ends
 * extract the same target/anchor/display tuple. If you change either, change
 * both.
 */

export interface WikilinkMatch {
  /** "plain" = `[[..]]`, "embed" = `![[..]]` (transclusion). */
  kind: "plain" | "embed";
  /** Resolved page target (case-insensitive on the backend; pass through here). */
  target: string;
  /** Optional `#heading` after the target. Empty string when absent. */
  anchor: string;
  /** Optional `|display` text. Empty string when absent — UI should fall back to target. */
  display: string;
  /** Original full match text (`[[…]]` / `![[…]]`). Useful for replace passes. */
  raw: string;
  /** 0-based offset of `raw` in the source string. */
  start: number;
  /** Exclusive end offset. */
  end: number;
}

/**
 * Capture order:
 *   1: optional `!` (embed marker)
 *   2: target — disallow `[`, `]`, newline, `#`, `|`
 *   3: optional anchor (after `#`) — disallow `[`, `]`, newline, `|`
 *   4: optional display (after `|`) — disallow `[`, `]`, newline
 *
 * Lengths capped to mirror the original 120-char limit on the whole inner.
 */
export const WIKILINK_RE =
  /(!?)\[\[([^\[\]\n#|]{1,120})(?:#([^\[\]\n|]{1,80}))?(?:\|([^\[\]\n]{1,120}))?\]\]/g;

/** Yield every wikilink match in `text`. Resets the regex's lastIndex itself. */
export function* iterWikilinks(text: string): Generator<WikilinkMatch> {
  WIKILINK_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = WIKILINK_RE.exec(text))) {
    const target = (match[2] ?? "").trim();
    if (!target) continue;
    yield {
      kind: match[1] === "!" ? "embed" : "plain",
      target,
      anchor: (match[3] ?? "").trim(),
      display: (match[4] ?? "").trim(),
      raw: match[0],
      start: match.index,
      end: match.index + match[0].length,
    };
  }
}

/** Materialize iterWikilinks into a list — handy for callers that need length. */
export function parseWikilinks(text: string): WikilinkMatch[] {
  return Array.from(iterWikilinks(text));
}

/** Surface text the renderer should show for a link (display ?? target). */
export function wikilinkLabel(m: Pick<WikilinkMatch, "target" | "display">): string {
  return m.display || m.target;
}

/** Build the routable href used by the markdown-link bridge in NoteDetail. */
export function wikilinkHref(target: string, anchor?: string): string {
  const base = `#wikilink/${encodeURIComponent(target)}`;
  return anchor ? `${base}#${encodeURIComponent(anchor)}` : base;
}
