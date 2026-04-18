/** Renders a single Obsidian-style callout block. Header + recursive body.
 *  Kept in its own file so `callout.ts` can export the splitter + kind
 *  metadata without tripping react-refresh's only-export-components rule. */
import type { ReactNode } from "react";
import { Info } from "lucide-react";
import type { CalloutSegment } from "./callout";
import { KIND_ICON, KIND_LABEL } from "./callout";

export function CalloutBlock({
  seg,
  renderBody,
}: {
  seg: CalloutSegment;
  renderBody: (md: string) => ReactNode;
}) {
  const Icon = KIND_ICON[seg.type] ?? Info;
  const label =
    KIND_LABEL[seg.type] ?? seg.type.replace(/^\w/, (c) => c.toUpperCase());
  const title = seg.title || label;

  return (
    <div className="lb-callout" data-kind={seg.type}>
      <div className="lb-callout-head">
        <Icon size={14} strokeWidth={2} />
        <span>{title}</span>
      </div>
      {seg.body.trim().length > 0 && (
        <div className="lb-callout-body">{renderBody(seg.body)}</div>
      )}
    </div>
  );
}
