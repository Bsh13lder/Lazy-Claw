/** Phosphor web-font icon wrapper.
 *
 *  Single component that renders any Phosphor icon as an `<i>` text glyph.
 *  Sized via `font-size`, coloured via `color` — both inherit cleanly from
 *  Tailwind utility classes if no explicit values are passed.
 *
 *  Why a font instead of SVG components: each Phosphor glyph is one text
 *  character backed by a shared font file. Hundreds of icon instances
 *  cost a single woff2 download (~30KB) and zero per-instance React
 *  reconcile overhead. Lucide drops a full `<svg>` subtree per call site;
 *  on dense surfaces (sidebars with ~80 icons, filter chips, list rows)
 *  the savings add up in both bundle size and DOM-node count.
 *
 *  This wrapper is HTML-only — Phosphor glyphs cannot render inside an
 *  `<svg>` element. The graph canvas keeps using Lucide components for
 *  its in-canvas node badges (see icons.tsx `CategoryIcon`). */
import type { CSSProperties } from "react";

export interface PhosphorIconProps {
  /** Phosphor icon name without the `ph-` prefix, e.g. "user", "book-open". */
  name: string;
  /** Pixel size — applied as `fontSize`. Defaults to 16. */
  size?: number;
  /** Explicit colour. Omit to inherit from `currentColor`. */
  color?: string;
  /** Extra Tailwind / CSS class names. */
  className?: string;
  /** Inline overrides — merged after the wrapper's own style. */
  style?: CSSProperties;
  /** Accessible title — rendered to a `title` attribute when provided. */
  title?: string;
  /** Accepted for drop-in compatibility with the Lucide API; ignored
   *  (Phosphor glyph stroke weight is fixed at the font level — switch
   *  weights by loading a different Phosphor CSS file). */
  strokeWidth?: number;
  /** Accepted for compatibility — Phosphor's regular weight ignores it.
   *  The graph view's filled-gold Star marker keeps using Lucide. */
  fill?: string;
}

export function PhosphorIcon({
  name,
  size = 16,
  color,
  className,
  style,
  title,
}: PhosphorIconProps) {
  const cls = `ph ph-${name}${className ? ` ${className}` : ""}`;
  return (
    <i
      className={cls}
      style={{
        fontSize: size,
        color,
        lineHeight: 1,
        display: "inline-flex",
        flexShrink: 0,
        ...style,
      }}
      aria-hidden={!title}
      title={title}
    />
  );
}
