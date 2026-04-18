/** Outline / TOC for the currently-open note.
 *
 *  Parses `^#{1,6} …` lines from the raw note content, renders as a
 *  click-to-scroll tree. Heading ids are assigned in WikilinkText as
 *  `lb-h-<index>` (indexed by occurrence), so this pane only needs to
 *  know the ordered list of headings to wire click → scrollIntoView.
 *
 *  Respects callout blocks — headings inside `> [!…]` bodies are NOT
 *  included (they're hidden inside an admonition and have no own id). */
import { useEffect, useMemo, useState } from "react";
import { ListTree } from "lucide-react";

interface Heading {
  level: number;
  text: string;
  /** 0-based index across the document — matches the id in WikilinkText. */
  index: number;
}

interface Props {
  content: string;
}

function parseHeadings(content: string): Heading[] {
  const out: Heading[] = [];
  let inCallout = false;
  const lines = content.split("\n");
  let idx = 0;
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    // Track callout blocks — lines within them don't count.
    if (/^>\s*\[!\w+\]/.test(ln)) {
      inCallout = true;
      continue;
    }
    if (inCallout) {
      if (/^>\s?/.test(ln)) continue;
      inCallout = false;
    }
    const m = /^(#{1,6})\s+(.+?)\s*$/.exec(ln);
    if (!m) continue;
    out.push({
      level: m[1].length,
      text: m[2].replace(/\s+#.*$/, "").trim(),
      index: idx,
    });
    idx++;
  }
  return out;
}

export function OutlinePane({ content }: Props) {
  const headings = useMemo(() => parseHeadings(content), [content]);
  const [active, setActive] = useState<number | null>(null);

  // Observe scroll — highlight the deepest heading that's above the fold.
  useEffect(() => {
    if (headings.length === 0) return;
    const handler = () => {
      let current: number | null = null;
      for (const h of headings) {
        const el = document.getElementById(`lb-h-${h.index}`);
        if (!el) continue;
        const r = el.getBoundingClientRect();
        if (r.top < 120) current = h.index;
        else break;
      }
      setActive(current);
    };
    const scrollRoot = document.querySelector<HTMLElement>(
      ".lazybrain-root [data-lb-scroll-root]",
    );
    const target = scrollRoot ?? window;
    handler();
    target.addEventListener("scroll", handler, { passive: true });
    return () => target.removeEventListener("scroll", handler as EventListener);
  }, [headings]);

  if (headings.length === 0) {
    return (
      <div className="px-3 py-3 text-[11px] text-text-muted italic">
        No headings yet — add a <code>#</code> line to see the outline.
      </div>
    );
  }

  // Normalize levels so the tree never jumps too deep visually.
  const minLevel = Math.min(...headings.map((h) => h.level));

  return (
    <div className="flex flex-col gap-0.5 px-2 py-2">
      <div className="flex items-center gap-1.5 px-1.5 py-1 text-[10px] uppercase tracking-wider text-text-muted font-semibold">
        <ListTree size={11} strokeWidth={1.75} />
        <span>Outline</span>
      </div>
      {headings.map((h) => {
        const depth = Math.min(3, h.level - minLevel);
        return (
          <div
            key={`${h.index}-${h.text}`}
            className="lb-outline-row truncate"
            data-active={active === h.index ? "true" : "false"}
            style={{ paddingLeft: 8 + depth * 14 }}
            title={h.text}
            onClick={() => {
              const el = document.getElementById(`lb-h-${h.index}`);
              if (el) {
                el.scrollIntoView({ behavior: "smooth", block: "start" });
                setActive(h.index);
              }
            }}
          >
            {h.text}
          </div>
        );
      })}
    </div>
  );
}
