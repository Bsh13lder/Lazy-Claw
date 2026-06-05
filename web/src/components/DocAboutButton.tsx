import { useEffect, useRef, useState } from "react";

export type DocKind = "sheets" | "docs" | "pdf";

interface AboutContent {
  title: string;
  what: string;
  how: string[];
  important: string[];
}

// Short, accurate explainers. Kept factual: everything here is AES-256-GCM
// encrypted at rest (the server never sees plaintext), and each type is
// editable by natural language through the ✨ AI box or the agent in chat.
const ABOUT: Record<DocKind, AboutContent> = {
  sheets: {
    title: "Sheets",
    what: "A private spreadsheet (like Excel) only you can read — stored fully encrypted; the server never sees its contents.",
    how: [
      "Edit cells directly in the grid, like any spreadsheet.",
      "Or press ✨ AI and just say it — e.g. “add a Total row summing column B”. It writes the formula for you; you don’t need to know formulas.",
      "Formulas recalculate automatically. Export to .xlsx or .csv anytime.",
    ],
    important: [
      "The agent can also create and fill sheets from chat.",
      "Numbers and formulas live in one encrypted blob per sheet.",
    ],
  },
  docs: {
    title: "Documents",
    what: "A private word-processor document (like Word/Google Docs), encrypted at rest — server-blind.",
    how: [
      "Type directly, or press ✨ AI: “add a closing paragraph”, or “add a line and link the word ‘site’ to https://…”.",
      "Real clickable hyperlinks are supported and survive export.",
      "Download as .docx (or PDF when the server has LibreOffice).",
    ],
    important: [
      "To add a link, use the ✨ AI box or write markdown like [label](https://…).",
      "The agent can draft and rewrite docs from chat too.",
    ],
  },
  pdf: {
    title: "PDFs",
    what: "A private, encrypted PDF store + viewer. Upload PDFs to read them; let AI do the edits.",
    how: [
      "Press ✨ AI to fill form fields, stamp text or a signature, merge, split, rotate, or generate a brand-new PDF.",
      "Use “Extract text” to read or search a PDF’s contents.",
      "Download any PDF whenever you want.",
    ],
    important: [
      "PDFs can’t be reflow text-edited (no tool can) — every edit saves a NEW file rather than changing the original.",
      "Scanned/image-only PDFs may have no extractable text.",
    ],
  },
};

/**
 * DocAboutButton — an ⓘ button that opens a short "what is this / how it works"
 * panel for the currently-selected document type. Lives in the Docs sub-tab bar
 * so the content tracks whichever tab (Sheets / Documents / PDFs) is active.
 */
export default function DocAboutButton({ kind }: { kind: DocKind }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const content = ABOUT[kind];

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center justify-center w-7 h-7 rounded-lg transition-colors ${
          open ? "bg-bg-hover text-accent" : "text-text-muted hover:bg-bg-hover hover:text-text-secondary"
        }`}
        title={`About ${content.title}`}
        aria-label={`About ${content.title}`}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" />
          <path d="M12 16v-4M12 8h.01" />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 mt-1.5 z-40 w-80 rounded-xl border border-border bg-bg-secondary shadow-2xl p-3.5 text-left">
          <div className="text-sm font-semibold text-text-primary mb-1">{content.title}</div>
          <p className="text-xs text-text-secondary leading-relaxed mb-2.5">{content.what}</p>

          <div className="text-[11px] font-semibold uppercase tracking-wide text-text-muted mb-1">How it works</div>
          <ul className="space-y-1 mb-2.5">
            {content.how.map((line, i) => (
              <li key={i} className="text-xs text-text-secondary leading-relaxed flex gap-1.5">
                <span className="text-accent shrink-0">•</span>
                <span>{line}</span>
              </li>
            ))}
          </ul>

          <div className="text-[11px] font-semibold uppercase tracking-wide text-text-muted mb-1">Good to know</div>
          <ul className="space-y-1 mb-2.5">
            {content.important.map((line, i) => (
              <li key={i} className="text-xs text-text-secondary leading-relaxed flex gap-1.5">
                <span className="text-text-muted shrink-0">›</span>
                <span>{line}</span>
              </li>
            ))}
          </ul>

          <div className="flex items-center gap-1.5 text-[11px] text-text-muted border-t border-border pt-2">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="11" width="18" height="11" rx="2" />
              <path d="M7 11V7a5 5 0 0 1 10 0v4" />
            </svg>
            End-to-end encrypted — stored as ciphertext, decrypted only for you.
          </div>
        </div>
      )}
    </div>
  );
}
