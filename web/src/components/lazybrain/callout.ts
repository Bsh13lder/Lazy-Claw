/** Obsidian-style callout parsing + kind metadata.
 *
 *  Syntax (same as Obsidian):
 *      > [!info] Optional title
 *      > body line 1
 *      > body line 2
 *
 *  Supported kinds: info, note, tip, hint, success, check, warning, caution,
 *  danger, error, fail, question, faq, quote, cite, todo, bug, example,
 *  abstract, summary. Unknown kind falls back to `info` in the renderer.
 *
 *  Types + the splitter live in this .ts so react-refresh only-exports rule
 *  stays happy for the JSX module. */
import {
  AlertTriangle,
  Bug,
  CheckCircle2,
  FileText,
  Flame,
  HelpCircle,
  Info,
  Lightbulb,
  ListTodo,
  Quote,
  Sparkles,
  XCircle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface MdSegment {
  kind: "md";
  text: string;
}
export interface CalloutSegment {
  kind: "callout";
  type: string;
  title: string;
  body: string;
  foldable: boolean;
  foldedByDefault: boolean;
}
export type Segment = MdSegment | CalloutSegment;

/** Split the raw markdown into markdown + callout segments.
 *  The callout header pattern is `> [!kind][+|-] optional title` followed by
 *  any number of `>` continuation lines (with optional space). A blank line
 *  or a non-`>` line ends the block. */
export function splitCallouts(content: string): Segment[] {
  const lines = content.split("\n");
  const out: Segment[] = [];
  let mdBuf: string[] = [];

  const flushMd = () => {
    if (mdBuf.length) {
      out.push({ kind: "md", text: mdBuf.join("\n") });
      mdBuf = [];
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    const header = /^>\s*\[!(\w+)\]([+-]?)\s*(.*)$/.exec(ln);
    if (header) {
      flushMd();
      const type = header[1].toLowerCase();
      const fold = header[2];
      const title = header[3].trim();
      const body: string[] = [];
      let j = i + 1;
      while (j < lines.length) {
        const cont = /^>\s?(.*)$/.exec(lines[j]);
        if (!cont) break;
        body.push(cont[1]);
        j++;
      }
      out.push({
        kind: "callout",
        type,
        title,
        body: body.join("\n"),
        foldable: fold === "+" || fold === "-",
        foldedByDefault: fold === "-",
      });
      i = j - 1;
    } else {
      mdBuf.push(ln);
    }
  }
  flushMd();
  return out;
}

export const KIND_ICON: Record<string, LucideIcon> = {
  info: Info,
  note: Info,
  tip: Lightbulb,
  hint: Lightbulb,
  success: CheckCircle2,
  check: CheckCircle2,
  warning: AlertTriangle,
  caution: AlertTriangle,
  danger: Flame,
  error: XCircle,
  fail: XCircle,
  question: HelpCircle,
  faq: HelpCircle,
  quote: Quote,
  cite: Quote,
  todo: ListTodo,
  bug: Bug,
  example: Sparkles,
  abstract: FileText,
  summary: FileText,
};

export const KIND_LABEL: Record<string, string> = {
  info: "Info",
  note: "Note",
  tip: "Tip",
  hint: "Hint",
  success: "Success",
  check: "Success",
  warning: "Warning",
  caution: "Caution",
  danger: "Danger",
  error: "Error",
  fail: "Error",
  question: "Question",
  faq: "FAQ",
  quote: "Quote",
  cite: "Quote",
  todo: "To-do",
  bug: "Bug",
  example: "Example",
  abstract: "Abstract",
  summary: "Summary",
};
