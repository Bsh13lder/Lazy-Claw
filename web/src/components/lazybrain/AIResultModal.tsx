/** Modal used to show the result of an AI skill call — Ask / Topic Rollup
 *  / Morning Briefing / Autolink preview.
 *
 *  Dumb container: the parent runs the skill, sets either an async promise
 *  or a pre-computed body, and we render markdown with full wikilink
 *  support. No business logic here beyond loading + error display. */
import { useEffect, useState } from "react";
import { Sparkles, X } from "lucide-react";
import type { LazyBrainNote } from "../../api";
import { WikilinkText } from "./WikilinkText";

interface Props {
  open: boolean;
  title: string;
  /** Async thunk — we call it on open and render its result. */
  run: () => Promise<string>;
  onClose: () => void;
  onLinkClick?: (page: string) => void;
  onTagClick?: (tag: string) => void;
  resolveLink?: (page: string) => LazyBrainNote | null;
  /** Optional: prefix icon / emoji to give the modal a personality. */
  hint?: string;
}

export function AIResultModal({
  open,
  title,
  run,
  onClose,
  onLinkClick,
  onTagClick,
  resolveLink,
  hint,
}: Props) {
  const [state, setState] = useState<
    { kind: "loading" } | { kind: "ok"; body: string } | { kind: "err"; msg: string }
  >({ kind: "loading" });

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    // Async-fetch pattern — legitimate setState on mount + on resolve.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState({ kind: "loading" });
    run()
      .then((body) => {
        if (!cancelled) setState({ kind: "ok", body });
      })
      .catch((e: Error) => {
        if (!cancelled) setState({ kind: "err", msg: e.message || String(e) });
      });
    return () => {
      cancelled = true;
    };
  }, [open, run]);

  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center pt-[10vh]"
      style={{
        background: "rgba(10,8,18,0.6)",
        backdropFilter: "blur(6px)",
      }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="w-[min(720px,94vw)] max-h-[80vh] rounded-xl overflow-hidden flex flex-col"
        style={{
          background: "rgba(30,27,43,0.98)",
          border: "1px solid rgba(16, 185, 129, 0.22)",
          boxShadow: "0 32px 64px -12px rgba(0,0,0,0.65)",
          fontFamily: "Inter, system-ui, sans-serif",
        }}
      >
        <div
          className="flex items-center gap-2 px-5 py-3"
          style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}
        >
          <Sparkles size={16} strokeWidth={1.75} color="#10b981" />
          <div className="text-sm font-semibold text-text-primary truncate">
            {hint ? `${hint} · ` : ""}
            {title}
          </div>
          <button
            onClick={onClose}
            className="ml-auto text-text-muted hover:text-text-primary"
          >
            <X size={16} strokeWidth={1.75} />
          </button>
        </div>
        <div
          data-lb-scroll-root
          className="flex-1 overflow-y-auto px-6 py-5"
        >
          {state.kind === "loading" && (
            <div className="flex items-center gap-3 text-text-muted text-sm">
              <div
                className="w-3 h-3 rounded-full"
                style={{
                  background: "#10b981",
                  animation: "pulse-dot 1.4s ease-in-out infinite",
                }}
              />
              Thinking through your notes…
            </div>
          )}
          {state.kind === "err" && (
            <div className="text-red-400 text-sm whitespace-pre-wrap">
              {state.msg}
            </div>
          )}
          {state.kind === "ok" && (
            <article className="prose-lazybrain">
              <WikilinkText
                content={state.body || "_(empty response)_"}
                onLinkClick={onLinkClick}
                onTagClick={onTagClick}
                resolveLink={resolveLink}
              />
            </article>
          )}
        </div>
      </div>
    </div>
  );
}
