/** Graph mode right-sidebar inspector.
 *
 *  When the user clicks a node on the LazyBrain graph view, this panel
 *  unrolls everything that's interesting about that note WITHOUT taking
 *  them out of the graph: title, category, excerpt, the wikilinks it
 *  emits (out), the backlinks pointing at it (in), connection-graph
 *  stats, and a few quick actions. Each linked title is itself
 *  clickable → re-selects the target note → the panel snaps to it →
 *  you can walk the graph by reading.
 *
 *  Visual language matches the topology layout: very calm, monospace
 *  numerics for stats, category-coloured chips for wikilinks, no
 *  decorative chrome. The graph is the centerpiece; this is a
 *  reading desk pulled up next to it.
 */
import { useMemo } from "react";
import type { LazyBrainNote } from "../../api";
import { categoryKeyFor, ringForKey, ownerOf, OWNER_META } from "./noteColors";
import { iterWikilinks } from "../../lib/wikilink";
import { HtmlCategoryIcon } from "./icons";
import { PhosphorIcon } from "./PhosphorIcon";
import { displayLabelForNote } from "./rollupLabel";

interface Props {
  /** Currently-clicked note. ``null`` → empty state. */
  note: LazyBrainNote | null;
  /** Inbound backlinks (already fetched by parent). */
  backlinks: LazyBrainNote[];
  /** O(1) title→note map for resolving outbound wikilinks. */
  notesByTitle: Map<string, LazyBrainNote>;
  /** Total connection count (= adjacency.size for this note). */
  degree: number;
  /** Connected-component size containing this note (or 1 for orphans). */
  componentSize: number;
  /** Click handler — switches the inspector to a different note id. */
  onSelectId: (id: string) => void;
  /** Optional — open the full editor for this note (leaves graph). */
  onOpenInEditor?: (id: string) => void;
  /** Optional — toggle pin status. */
  onTogglePin?: (note: LazyBrainNote) => void;
}

const EXCERPT_LEN = 240;

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return "";
  const now = Date.now();
  const ms = now - d.getTime();
  if (ms < 0) return d.toLocaleDateString();
  const h = Math.floor(ms / 3_600_000);
  if (h < 1) return "just now";
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.floor(days / 7)}w ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function excerpt(content: string | null | undefined): string {
  if (!content) return "";
  // Strip the YAML frontmatter and the leading `# Title` so the excerpt
  // starts on the first real prose paragraph the reader cares about.
  let body = content;
  if (body.startsWith("---\n")) {
    const close = body.indexOf("\n---", 4);
    if (close >= 0) body = body.slice(close + 4).trimStart();
  }
  body = body.replace(/^#\s+.+?\n/, "").trim();
  // Strip wikilink syntax from the excerpt — `[[Foo|bar]]` → `bar`,
  // `[[Foo]]` → `Foo` — so the snippet reads as natural prose.
  body = body.replace(
    /!?\[\[([^\[\]\n#|]+)(?:#[^\[\]\n|]+)?(?:\|([^\[\]\n]+))?\]\]/g,
    (_m, target, display) => display || target,
  );
  if (body.length <= EXCERPT_LEN) return body;
  return body.slice(0, EXCERPT_LEN).trimEnd() + "…";
}

export function GraphInspector({
  note,
  backlinks,
  notesByTitle,
  degree,
  componentSize,
  onSelectId,
  onOpenInEditor,
  onTogglePin,
}: Props) {
  // Outbound wikilinks — parsed from the note's content. Resolve every
  // target through the title map so we can render category chips +
  // mark broken ones. Deduped on resolved id (or lowercased target for
  // unresolved) so repeated `[[X]]` mentions don't double-count.
  const outbound = useMemo(() => {
    if (!note) return [] as Array<{ title: string; resolved: LazyBrainNote | null }>;
    const seen = new Set<string>();
    const out: Array<{ title: string; resolved: LazyBrainNote | null }> = [];
    for (const m of iterWikilinks(note.content || "")) {
      if (m.kind === "embed") continue; // transclusions count as content, not links
      const key = m.target.toLowerCase();
      const resolved = notesByTitle.get(key) ?? null;
      const id = resolved?.id ?? `__unresolved:${key}`;
      if (seen.has(id)) continue;
      seen.add(id);
      out.push({ title: m.target, resolved });
    }
    return out;
  }, [note, notesByTitle]);

  if (!note) {
    return (
      <div className="flex-1 min-h-0 flex flex-col items-center justify-center px-6 text-center">
        <div className="w-9 h-9 rounded-full border border-border bg-bg-secondary/40 flex items-center justify-center mb-3 text-text-muted">
          <PhosphorIcon name="cursor-click" size={14} />
        </div>
        <div className="text-[12px] text-text-primary mb-1">Inspect a node</div>
        <div className="text-[11px] leading-relaxed text-text-muted">
          Click any dot on the graph to read its title, excerpt, outbound
          wikilinks, and the backlinks pointing at it.
        </div>
      </div>
    );
  }

  const categoryKey = categoryKeyFor(note.tags, note.pinned) ?? "_default";
  const ringColor = ringForKey(categoryKey);
  const owner = ownerOf(note.tags);
  const ownerMeta = OWNER_META[owner];
  const labelParts = displayLabelForNote(note);
  const displayTitle = labelParts.primary || note.title || "(untitled)";
  const displaySuffix = labelParts.suffix;
  const tagCount = note.tags?.length ?? 0;
  const visibleTags = (note.tags ?? []).filter((t) => !t.startsWith("owner/")).slice(0, 8);
  const outboundCount = outbound.length;
  const inboundCount = backlinks.length;

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-y-auto">
      {/* ── Header: kind chip + title ─────────────────────────────────── */}
      <div className="px-4 pt-4 pb-3 border-b border-border">
        <div className="flex items-center gap-2 mb-2">
          <span
            className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-medium"
            style={{
              color: ringColor,
              backgroundColor: ringColor + "1a",
              border: `1px solid ${ringColor}33`,
            }}
          >
            <HtmlCategoryIcon keyName={categoryKey} size={11} />
            {categoryKey === "_default" ? "Note" : categoryKey.replace(/-/g, " ")}
          </span>
          {note.pinned && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] text-amber-500 bg-amber-500/10 border border-amber-500/30">
              <PhosphorIcon name="push-pin-fill" size={10} />
              pinned
            </span>
          )}
          <span className="ml-auto text-[10px] text-text-muted" title={ownerMeta.label}>
            {owner === "user" ? "You" : owner === "agent" ? "Agent" : "—"}
          </span>
        </div>
        <h2 className="text-[14px] font-semibold leading-snug text-text-primary break-words">
          {displayTitle}
          {displaySuffix && (
            <span className="ml-1 text-[10px] font-normal text-text-muted">
              {displaySuffix}
            </span>
          )}
        </h2>
        <div className="flex items-center gap-3 mt-1.5 text-[10px] text-text-muted">
          <span className="inline-flex items-center gap-1">
            <PhosphorIcon name="clock" size={10} />
            {formatDate(note.updated_at || note.created_at)}
          </span>
          {(note.importance ?? 0) >= 7 && (
            <span className="text-amber-500">★ {note.importance}</span>
          )}
        </div>
      </div>

      {/* ── Excerpt ───────────────────────────────────────────────────── */}
      {note.content && (
        <div className="px-4 py-3 border-b border-border">
          <div className="text-[11.5px] leading-relaxed text-text-secondary whitespace-pre-wrap">
            {excerpt(note.content)}
          </div>
          {onOpenInEditor && (
            <button
              type="button"
              onClick={() => onOpenInEditor(note.id)}
              className="mt-2 inline-flex items-center gap-1 text-[10px] text-text-muted hover:text-accent transition-colors"
            >
              <PhosphorIcon name="arrow-square-out" size={10} />
              Open in editor
            </button>
          )}
        </div>
      )}

      {/* ── Stats — degree / component / counts ───────────────────────── */}
      <div className="grid grid-cols-3 gap-px bg-border/40 border-b border-border">
        <Stat label="Connections" value={degree} hint={`${outboundCount} out · ${inboundCount} in`} />
        <Stat label="In system of" value={componentSize} hint={componentSize === 1 ? "orphan" : `${componentSize} notes`} />
        <Stat label="Tags" value={tagCount} hint={tagCount === 0 ? "none" : ""} />
      </div>

      {/* ── Outbound wikilinks ────────────────────────────────────────── */}
      <Section
        icon={<span className="text-text-muted"><PhosphorIcon name="arrow-up-right" size={11} /></span>}
        title="Goes to"
        count={outboundCount}
        empty="No outbound wikilinks."
      >
        {outbound.map((o, i) => {
          if (o.resolved) {
            const k = categoryKeyFor(o.resolved.tags, o.resolved.pinned) ?? "_default";
            const c = ringForKey(k);
            return (
              <button
                key={`out-${i}`}
                type="button"
                onClick={() => onSelectId(o.resolved!.id)}
                className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded hover:bg-bg-hover transition-colors group"
              >
                <span
                  className="w-1.5 h-1.5 rounded-full shrink-0"
                  style={{ backgroundColor: c }}
                />
                <span className="text-[11.5px] text-text-primary truncate flex-1 group-hover:text-accent transition-colors">
                  {o.resolved.title || o.title}
                </span>
                <span className="text-text-muted opacity-0 group-hover:opacity-100 transition-opacity">
                  <PhosphorIcon name="arrow-up-right" size={10} />
                </span>
              </button>
            );
          }
          return (
            <div
              key={`out-${i}`}
              className="flex items-center gap-2 px-2 py-1.5 rounded text-text-muted/70"
              title="Unresolved wikilink — no note with this title yet"
            >
              <span className="w-1.5 h-1.5 rounded-full shrink-0 border border-text-muted/40" />
              <span className="text-[11.5px] truncate flex-1 italic">{o.title}</span>
              <span className="text-[9px] uppercase tracking-wider">broken</span>
            </div>
          );
        })}
      </Section>

      {/* ── Inbound backlinks ─────────────────────────────────────────── */}
      <Section
        icon={<span className="text-text-muted"><PhosphorIcon name="arrow-down-left" size={11} /></span>}
        title="Linked from"
        count={inboundCount}
        empty="No backlinks point here yet."
      >
        {backlinks.map((b) => {
          const k = categoryKeyFor(b.tags, b.pinned) ?? "_default";
          const c = ringForKey(k);
          const snip = excerpt(b.content).slice(0, 90);
          return (
            <button
              key={`in-${b.id}`}
              type="button"
              onClick={() => onSelectId(b.id)}
              className="w-full text-left flex items-start gap-2 px-2 py-1.5 rounded hover:bg-bg-hover transition-colors group"
            >
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0 mt-1.5"
                style={{ backgroundColor: c }}
              />
              <div className="min-w-0 flex-1">
                <div className="text-[11.5px] text-text-primary truncate group-hover:text-accent transition-colors">
                  {b.title || "(untitled)"}
                </div>
                {snip && (
                  <div className="text-[10px] text-text-muted truncate leading-snug mt-0.5">
                    {snip}
                  </div>
                )}
              </div>
            </button>
          );
        })}
      </Section>

      {/* ── Tags ──────────────────────────────────────────────────────── */}
      {visibleTags.length > 0 && (
        <Section
          icon={<span className="text-text-muted"><PhosphorIcon name="hash" size={11} /></span>}
          title="Tags"
          count={tagCount}
        >
          <div className="flex flex-wrap gap-1 px-2 pb-1">
            {visibleTags.map((t) => (
              <span
                key={t}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] text-text-muted bg-bg-hover border border-border"
              >
                {t}
              </span>
            ))}
            {tagCount > visibleTags.length && (
              <span className="text-[10px] text-text-muted self-center">
                +{tagCount - visibleTags.length} more
              </span>
            )}
          </div>
        </Section>
      )}

      {/* ── Quick actions ─────────────────────────────────────────────── */}
      {(onOpenInEditor || onTogglePin) && (
        <div className="mt-auto px-4 py-3 border-t border-border flex gap-2 shrink-0">
          {onOpenInEditor && (
            <button
              type="button"
              onClick={() => onOpenInEditor(note.id)}
              className="flex-1 inline-flex items-center justify-center gap-1.5 px-2 py-1.5 rounded border border-border bg-bg-secondary hover:bg-bg-hover text-[11px] text-text-primary transition-colors"
            >
              <PhosphorIcon name="arrow-square-out" size={11} />
              Edit
            </button>
          )}
          {onTogglePin && (
            <button
              type="button"
              onClick={() => onTogglePin(note)}
              className={`inline-flex items-center justify-center gap-1.5 px-2 py-1.5 rounded border text-[11px] transition-colors ${
                note.pinned
                  ? "border-amber-500/40 bg-amber-500/10 text-amber-500 hover:bg-amber-500/20"
                  : "border-border bg-bg-secondary hover:bg-bg-hover text-text-primary"
              }`}
              title={note.pinned ? "Unpin" : "Pin"}
            >
              <PhosphorIcon
                name={note.pinned ? "push-pin-fill" : "push-pin"}
                size={11}
              />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div className="bg-bg-secondary/60 px-3 py-2.5 flex flex-col gap-0.5">
      <div className="text-[9px] uppercase tracking-wider text-text-muted">{label}</div>
      <div className="text-[18px] font-mono font-semibold text-text-primary leading-none">
        {value}
      </div>
      {hint && (
        <div className="text-[9px] text-text-muted leading-tight mt-0.5">{hint}</div>
      )}
    </div>
  );
}

function Section({
  icon,
  title,
  count,
  empty,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  count: number;
  empty?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-b border-border py-2 px-2">
      <div className="flex items-center gap-1.5 px-2 py-1">
        {icon}
        <span className="text-[10px] uppercase tracking-wider text-text-muted font-medium">
          {title}
        </span>
        <span className="ml-auto text-[10px] font-mono text-text-muted">{count}</span>
      </div>
      {count === 0 && empty ? (
        <div className="px-2 py-1.5 text-[11px] text-text-muted/70 italic">{empty}</div>
      ) : (
        <div className="flex flex-col">{children}</div>
      )}
    </section>
  );
}
