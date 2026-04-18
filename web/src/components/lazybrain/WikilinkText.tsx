/** Render markdown-ish note body with [[wikilinks]] rendered as clickable
 *  pills and #tags as muted chips. Minimal on purpose — Phase 18.5 can
 *  swap this for a full markdown renderer. */
import { memo } from "react";

interface Props {
  content: string;
  onLinkClick?: (pageName: string) => void;
  onTagClick?: (tag: string) => void;
}

const WIKILINK_RE = /\[\[([^\[\]\n]{1,120})\]\]/g;
const TAG_RE = /(^|\s)#([A-Za-z][A-Za-z0-9_/\-]{0,63})/g;

function splitWikilinks(
  text: string,
  onLinkClick?: (name: string) => void,
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = WIKILINK_RE.exec(text))) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const target = match[1].trim();
    parts.push(
      <button
        key={`lnk-${match.index}`}
        onClick={() => onLinkClick?.(target)}
        className="inline-block px-1.5 py-0.5 mx-0.5 rounded bg-accent-soft text-accent hover:bg-accent hover:text-bg-primary transition-colors text-[0.9em] font-medium"
      >
        {target}
      </button>,
    );
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function splitTags(
  nodes: React.ReactNode[],
  onTagClick?: (tag: string) => void,
): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  nodes.forEach((node, idx) => {
    if (typeof node !== "string") {
      out.push(node);
      return;
    }
    let text = node;
    let last = 0;
    let match: RegExpExecArray | null;
    TAG_RE.lastIndex = 0;
    while ((match = TAG_RE.exec(text))) {
      if (match.index > last) out.push(text.slice(last, match.index));
      out.push(match[1]); // leading whitespace (if any)
      const tag = match[2];
      out.push(
        <button
          key={`tag-${idx}-${match.index}`}
          onClick={() => onTagClick?.(tag)}
          className="inline-block mx-0.5 text-text-muted hover:text-accent text-[0.9em]"
        >
          #{tag}
        </button>,
      );
      last = match.index + match[0].length;
    }
    if (last < text.length) out.push(text.slice(last));
  });
  return out;
}

function WikilinkTextInner({ content, onLinkClick, onTagClick }: Props) {
  const lines = content.split("\n");
  return (
    <div className="whitespace-pre-wrap text-sm text-text-primary leading-relaxed">
      {lines.map((line, idx) => {
        const linkParts = splitWikilinks(line, onLinkClick);
        const rich = splitTags(linkParts, onTagClick);
        return (
          <div key={idx} className="min-h-[1em]">
            {rich.length === 0 ? "\u00a0" : rich}
          </div>
        );
      })}
    </div>
  );
}

export const WikilinkText = memo(WikilinkTextInner);
