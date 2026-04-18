/** Markdown renderer with clickable [[wikilinks]] and #tags.
 *  Wraps react-markdown + remark-gfm; intercepts text leaves to turn
 *  [[Page Name]] into pills and #tag into muted chips. Supports an
 *  Obsidian-style hover preview when `resolveLink` is provided — hover a
 *  wikilink and a floating card shows the target page's content. */
import { memo, useCallback, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { LazyBrainNote } from "../../api";
import { WikilinkPreviewCard } from "./WikilinkPreviewCard";

interface Props {
  content: string;
  onLinkClick?: (pageName: string) => void;
  onTagClick?: (tag: string) => void;
  /** Optional — when provided, hovering a wikilink shows a floating preview
   *  of the target page. Obsidian-style page preview. */
  resolveLink?: (pageName: string) => LazyBrainNote | null;
}

const WIKILINK_RE = /\[\[([^\[\]\n]{1,120})\]\]/g;
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

  // Enrich text with wikilink + tag pills.
  const splitWikilinks = useCallback(
    (text: string, keyPrefix: string): ReactNode[] => {
      const parts: ReactNode[] = [];
      let last = 0;
      let match: RegExpExecArray | null;
      WIKILINK_RE.lastIndex = 0;
      while ((match = WIKILINK_RE.exec(text))) {
        if (match.index > last) parts.push(text.slice(last, match.index));
        const target = match[1].trim();
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
        last = match.index + match[0].length;
      }
      if (last < text.length) parts.push(text.slice(last));
      return parts;
    },
    [onLinkClick, handleEnter, handleLeave],
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

  return (
    <>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p:  ({ children }) => <p>{enriched("p")({ children })}</p>,
          li: ({ children }) => <li>{enriched("li")({ children })}</li>,
          strong: ({ children }) => <strong>{enriched("strong")({ children })}</strong>,
          em: ({ children }) => <em>{enriched("em")({ children })}</em>,
          h1: ({ children }) => <h1>{enriched("h1")({ children })}</h1>,
          h2: ({ children }) => <h2>{enriched("h2")({ children })}</h2>,
          h3: ({ children }) => <h3>{enriched("h3")({ children })}</h3>,
          h4: ({ children }) => <h4>{enriched("h4")({ children })}</h4>,
          blockquote: ({ children }) => <blockquote>{enriched("bq")({ children })}</blockquote>,
          td: ({ children }) => <td>{enriched("td")({ children })}</td>,
          th: ({ children }) => <th>{enriched("th")({ children })}</th>,
          a:  ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
      {hover && <WikilinkPreviewCard note={hover.note} x={hover.x} y={hover.y} />}
    </>
  );
}

export const WikilinkText = memo(WikilinkTextInner);
