/** Markdown renderer with clickable [[wikilinks]] and #tags.
 *  Wraps react-markdown + remark-gfm; intercepts text leaves to turn
 *  [[Page Name]] into pills and #tag into muted chips. Supports an
 *  Obsidian-style hover preview when `resolveLink` is provided — hover a
 *  wikilink and a floating card shows the target page's content. */
import { memo, useCallback, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { LazyBrainNote } from "../../api";
import { WikilinkPreviewCard } from "./WikilinkPreviewCard";
import { splitCallouts } from "./callout";
import { CalloutBlock } from "./CalloutBlock";

interface Props {
  content: string;
  onLinkClick?: (pageName: string) => void;
  onTagClick?: (tag: string) => void;
  /** Optional — when provided, hovering a wikilink shows a floating preview
   *  of the target page. Obsidian-style page preview. */
  resolveLink?: (pageName: string) => LazyBrainNote | null;
}

// `!` prefix = transclusion (![[Note]]), no prefix = plain wikilink.
const WIKILINK_RE = /(!?)\[\[([^\[\]\n]{1,120})\]\]/g;
const TAG_RE = /(^|\s)#([A-Za-z][A-Za-z0-9_/\-]{0,63})/g;

interface HoverState {
  note: LazyBrainNote;
  x: number;
  y: number;
}

function WikilinkTextInner({ content, onLinkClick, onTagClick, resolveLink }: Props) {
  const [hover, setHover] = useState<HoverState | null>(null);

  const handleEnter = useCallback(
    (e: React.MouseEvent<HTMLElement>, name: string) => {
      if (!resolveLink) return;
      const note = resolveLink(name);
      if (!note) return;
      setHover({ note, x: e.clientX, y: e.clientY });
    },
    [resolveLink],
  );

  const handleLeave = useCallback(() => setHover(null), []);

  // Enrich text with wikilink + tag pills + transclusion embeds.
  const splitWikilinks = useCallback(
    (text: string, keyPrefix: string): ReactNode[] => {
      const parts: ReactNode[] = [];
      let last = 0;
      let match: RegExpExecArray | null;
      WIKILINK_RE.lastIndex = 0;
      while ((match = WIKILINK_RE.exec(text))) {
        if (match.index > last) parts.push(text.slice(last, match.index));
        const isEmbed = match[1] === "!";
        const target = match[2].trim();
        if (isEmbed) {
          const embedded = resolveLink?.(target) ?? null;
          parts.push(
            <Transclusion
              key={`${keyPrefix}-emb-${match.index}`}
              title={target}
              note={embedded}
              onLinkClick={onLinkClick}
              onTagClick={onTagClick}
            />,
          );
        } else {
          parts.push(
            <span
              key={`${keyPrefix}-lnk-${match.index}`}
              onClick={(e) => {
                e.stopPropagation();
                setHover(null);
                onLinkClick?.(target);
              }}
              onMouseEnter={(e) => handleEnter(e, target)}
              onMouseLeave={handleLeave}
              className="lb-wikilink"
            >
              {target}
            </span>,
          );
        }
        last = match.index + match[0].length;
      }
      if (last < text.length) parts.push(text.slice(last));
      return parts;
    },
    [onLinkClick, onTagClick, resolveLink, handleEnter, handleLeave],
  );

  const splitTags = useCallback(
    (nodes: ReactNode[], keyPrefix: string): ReactNode[] => {
      const out: ReactNode[] = [];
      nodes.forEach((node, idx) => {
        if (typeof node !== "string") {
          out.push(node);
          return;
        }
        const text = node;
        let last = 0;
        let match: RegExpExecArray | null;
        TAG_RE.lastIndex = 0;
        while ((match = TAG_RE.exec(text))) {
          if (match.index > last) out.push(text.slice(last, match.index));
          if (match[1]) out.push(match[1]);
          const tag = match[2];
          out.push(
            <span
              key={`${keyPrefix}-tag-${idx}-${match.index}`}
              onClick={(e) => {
                e.stopPropagation();
                onTagClick?.(tag);
              }}
              className="lb-tag"
            >
              #{tag}
            </span>,
          );
          last = match.index + match[0].length;
        }
        if (last < text.length) out.push(text.slice(last));
      });
      return out;
    },
    [onTagClick],
  );

  const enrich = useCallback(
    (children: ReactNode, keyPrefix: string): ReactNode => {
      if (children == null || typeof children === "boolean") return children;
      if (typeof children === "string") {
        const linkParts = splitWikilinks(children, keyPrefix);
        return splitTags(linkParts, keyPrefix);
      }
      if (typeof children === "number") return children;
      if (Array.isArray(children)) {
        return children.map((c, i) => enrich(c, `${keyPrefix}-${i}`));
      }
      return children;
    },
    [splitWikilinks, splitTags],
  );

  const enriched = useCallback(
    (k: string) => (props: { children?: ReactNode }) => enrich(props.children, k),
    [enrich],
  );

  // Heading counter — attaches stable ids (lb-h-0, lb-h-1, …) so the
  // outline pane can scroll to them. Reset per-render via useMemo closure.
  const headingIdx = useRef(0);
  headingIdx.current = 0;
  const nextHeadingId = () => {
    const id = `lb-h-${headingIdx.current}`;
    headingIdx.current += 1;
    return id;
  };

  // Dedicated markdown renderer — used both for the top-level note and
  // recursively inside callout bodies. Splits out Obsidian-style callouts
  // (`> [!kind] …`) and renders them as styled admonitions.
  const renderMd = useCallback(
    (md: string, keyNs: string): ReactNode => (
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p:  ({ children }) => <p>{enriched(`${keyNs}-p`)({ children })}</p>,
          li: ({ children }) => <li>{enriched(`${keyNs}-li`)({ children })}</li>,
          strong: ({ children }) => <strong>{enriched(`${keyNs}-strong`)({ children })}</strong>,
          em: ({ children }) => <em>{enriched(`${keyNs}-em`)({ children })}</em>,
          h1: ({ children }) => <h1 id={nextHeadingId()}>{enriched(`${keyNs}-h1`)({ children })}</h1>,
          h2: ({ children }) => <h2 id={nextHeadingId()}>{enriched(`${keyNs}-h2`)({ children })}</h2>,
          h3: ({ children }) => <h3 id={nextHeadingId()}>{enriched(`${keyNs}-h3`)({ children })}</h3>,
          h4: ({ children }) => <h4 id={nextHeadingId()}>{enriched(`${keyNs}-h4`)({ children })}</h4>,
          blockquote: ({ children }) => (
            <blockquote>{enriched(`${keyNs}-bq`)({ children })}</blockquote>
          ),
          td: ({ children }) => <td>{enriched(`${keyNs}-td`)({ children })}</td>,
          th: ({ children }) => <th>{enriched(`${keyNs}-th`)({ children })}</th>,
          a:  ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
        }}
      >
        {md}
      </ReactMarkdown>
    ),
    [enriched],
  );

  const segments = useMemo(() => splitCallouts(content), [content]);

  return (
    <>
      {segments.map((seg, i) => {
        if (seg.kind === "md") {
          if (!seg.text.trim()) return null;
          return (
            <div key={`md-${i}`} data-lb-md>
              {renderMd(seg.text, `s${i}`)}
            </div>
          );
        }
        return (
          <CalloutBlock
            key={`cl-${i}`}
            seg={seg}
            renderBody={(md) => renderMd(md, `cl${i}`)}
          />
        );
      })}
      {hover && <WikilinkPreviewCard note={hover.note} x={hover.x} y={hover.y} />}
    </>
  );
}

export const WikilinkText = memo(WikilinkTextInner);

/** Obsidian-style ![[Note]] transclusion.
 *  Renders a collapsible card embedding the target note's body. If the
 *  target page doesn't exist yet, renders a dotted "Create …" affordance
 *  identical in weight to an unresolved wikilink. */
function Transclusion({
  title,
  note,
  onLinkClick,
  onTagClick,
}: {
  title: string;
  note: LazyBrainNote | null;
  onLinkClick?: (page: string) => void;
  onTagClick?: (tag: string) => void;
}) {
  const [open, setOpen] = useState(true);
  if (!note) {
    return (
      <span
        className="lb-wikilink"
        style={{ borderStyle: "dashed", opacity: 0.8 }}
        onClick={(e) => {
          e.stopPropagation();
          onLinkClick?.(title);
        }}
      >
        ↪ {title} (unresolved)
      </span>
    );
  }
  return (
    <div
      className="lb-transclusion"
      style={{
        margin: "0.8em 0",
        border: "1px solid var(--color-border)",
        borderLeft: "3px solid #10b981",
        borderRadius: 8,
        background: "rgba(16, 185, 129, 0.04)",
        overflow: "hidden",
      }}
    >
      <div
        onClick={() => setOpen((v) => !v)}
        style={{
          padding: "6px 12px",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 12,
          color: "#10b981",
          fontWeight: 600,
          fontFamily: "Inter, system-ui, sans-serif",
          borderBottom: open ? "1px solid var(--color-border)" : "none",
        }}
      >
        <span style={{ opacity: 0.7 }}>{open ? "▾" : "▸"}</span>
        <span>⎘ {note.title || "(untitled)"}</span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onLinkClick?.(title);
          }}
          style={{
            marginLeft: "auto",
            fontSize: 10,
            color: "#767676",
            background: "transparent",
            border: 0,
            cursor: "pointer",
          }}
        >
          open →
        </button>
      </div>
      {open && (
        <div style={{ padding: "10px 14px", fontSize: 14 }}>
          {/* Recursively render — but guard depth by slicing content
              before handing back in. Any nested ![[link]] still works. */}
          <WikilinkText
            content={(note.content || "").slice(0, 4000)}
            onLinkClick={onLinkClick}
            onTagClick={onTagClick}
          />
        </div>
      )}
    </div>
  );
}
