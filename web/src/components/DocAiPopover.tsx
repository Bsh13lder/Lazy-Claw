import { useEffect, useRef, useState } from "react";
import {
  aiEditDoc,
  aiEditPdf,
  aiEditSheet,
  type UniverSnapshot,
} from "../api";

export type DocKind = "sheets" | "docs" | "pdf";

interface Props {
  kind: DocKind;
  docId: string;
  docName?: string;
  /** Commit pending editor edits before the AI reads the document. */
  beforeSubmit?: () => Promise<void> | void;
  /** Sheets/Docs: the editor should re-fetch + remount (the AI saved server-side). */
  onReload?: (snapshot: UniverSnapshot) => void;
  /** PDF: switch the viewer to the new file the op produced (id may be null). */
  onPdfApplied?: (newPdfId: string | null) => void;
}

const PLACEHOLDER: Record<DocKind, string> = {
  sheets: "e.g. add a Total row that sums column B",
  docs: 'e.g. add a line: visit my site — link "site" to https://…',
  pdf: "e.g. stamp my signature at the bottom-left of page 1",
};

type Status = { kind: "ok" | "err"; msg: string } | null;

/**
 * DocAiPopover — the ✨ in-editor AI box shared by Sheets/Docs/PDF.
 *
 * Opens a small popover with a prompt; one synchronous round-trip to
 * `/api/{kind}/{id}/ai` runs the Document Specialist and the editor reloads in
 * place. No chat, no background task — the edit appears immediately.
 */
export default function DocAiPopover({
  kind,
  docId,
  docName,
  beforeSubmit,
  onReload,
  onPdfApplied,
}: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status>(null);

  const popRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (popRef.current && !popRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    // focus the textarea once the popover is on screen
    const t = setTimeout(() => taRef.current?.focus(), 0);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      clearTimeout(t);
    };
  }, [open]);

  async function submit() {
    const instruction = text.trim();
    if (!instruction || busy) return;
    setBusy(true);
    setStatus(null);
    try {
      // Persist any in-flight keystrokes so the AI reads the latest document.
      await beforeSubmit?.();

      if (kind === "pdf") {
        const r = await aiEditPdf(docId, instruction);
        if (r.ok) {
          setStatus({ kind: "ok", msg: r.summary });
          onPdfApplied?.(r.new_pdf_id ?? null);
          setText("");
        } else {
          setStatus({ kind: "err", msg: r.error || "Couldn't do that." });
        }
      } else {
        const r =
          kind === "sheets"
            ? await aiEditSheet(docId, instruction)
            : await aiEditDoc(docId, instruction);
        if (r.ok && r.snapshot) {
          setStatus({ kind: "ok", msg: r.summary });
          onReload?.(r.snapshot);
          setText("");
        } else {
          setStatus({ kind: "err", msg: r.error || "Couldn't do that." });
        }
      }
    } catch (e) {
      setStatus({
        kind: "err",
        msg: e instanceof Error ? e.message : "Request failed.",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative" ref={popRef}>
      <button
        onClick={() => setOpen((o) => !o)}
        className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-opacity ${
          open
            ? "bg-accent text-bg-primary"
            : "bg-bg-hover text-text-secondary hover:text-accent"
        }`}
        title="Ask AI to edit this document"
        aria-label="Ask AI"
      >
        ✨ AI
      </button>

      {open && (
        <div className="absolute right-0 mt-1.5 z-40 w-80 rounded-xl border border-border bg-bg-secondary shadow-2xl p-3">
          <div className="text-xs font-semibold mb-1.5 truncate">
            Ask AI{docName ? ` · ${docName}` : ""}
          </div>
          <textarea
            ref={taRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                submit();
              }
            }}
            rows={3}
            placeholder={PLACEHOLDER[kind]}
            disabled={busy}
            className="w-full text-sm bg-bg-primary border border-border rounded-lg px-2 py-1.5 outline-none focus:border-accent resize-none disabled:opacity-60"
          />
          <div className="flex items-center justify-between gap-2 mt-2">
            <div className="text-xs min-h-[1rem] flex-1 truncate">
              {busy ? (
                <span className="text-text-muted">Thinking…</span>
              ) : status ? (
                <span
                  className={status.kind === "ok" ? "text-accent" : "text-red-400"}
                  title={status.msg}
                >
                  {status.kind === "ok" ? "✓ " : "⚠ "}
                  {status.msg}
                </span>
              ) : (
                <span className="text-text-muted">⌘/Ctrl+Enter to send</span>
              )}
            </div>
            <button
              onClick={submit}
              disabled={busy || !text.trim()}
              className="px-2.5 py-1 rounded-lg bg-accent text-bg-primary text-xs font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {busy ? "…" : "Send"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
