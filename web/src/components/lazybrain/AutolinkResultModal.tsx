/** Structured autolink suggestion modal — per-row Accept / Skip + Accept-all.
 *
 *  Different from AIResultModal because it renders typed data (suggestions
 *  with text + page targets), not free-form markdown. Each accept call
 *  rewrites the current note's content to wrap the matched substring in
 *  `[[ ]]`, then dispatches a save patch through the parent. */
import { useEffect, useMemo, useState } from "react";
import { Sparkles, X, Check, Zap, RotateCcw } from "lucide-react";
import * as api from "../../api";
import type { AutolinkSuggestion } from "../../api";
import { motion, AnimatePresence } from "framer-motion";

interface Props {
  open: boolean;
  noteTitle: string;
  noteContent: string;
  onClose: () => void;
  /** Called whenever a suggestion is accepted. Returns updated content
   *  the parent should persist to the API. */
  onApplyContent: (nextContent: string) => void;
}

type Row = AutolinkSuggestion & {
  /** "pending" while awaiting decision, "accepted" or "skipped" otherwise. */
  state: "pending" | "accepted" | "skipped";
};

export function AutolinkResultModal({
  open,
  noteTitle,
  noteContent,
  onClose,
  onApplyContent,
}: Props) {
  const [phase, setPhase] = useState<
    | { kind: "loading" }
    | { kind: "ok"; rows: Row[]; source: string }
    | { kind: "err"; msg: string }
    | { kind: "empty"; source: string }
  >({ kind: "loading" });

  // Working copy of the note's content — every accept rewrites this in place
  // and the parent gets the final patch.
  const [working, setWorking] = useState(noteContent);

  useEffect(() => {
    if (!open) return;
    // Sync-fetch pattern — legitimate setState on mount + on resolve.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setWorking(noteContent);
    setPhase({ kind: "loading" });
    let cancelled = false;
    api
      .suggestLazyBrainLinks(noteContent || "")
      .then((r) => {
        if (cancelled) return;
        if (r.suggestions.length === 0) {
          setPhase({ kind: "empty", source: r.source });
        } else {
          setPhase({
            kind: "ok",
            source: r.source,
            rows: r.suggestions.map((s) => ({ ...s, state: "pending" })),
          });
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setPhase({ kind: "err", msg: e.message || String(e) });
      });
    return () => {
      cancelled = true;
    };
  }, [open, noteContent]);

  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [open, onClose]);

  const acceptedCount = useMemo(
    () => (phase.kind === "ok" ? phase.rows.filter((r) => r.state === "accepted").length : 0),
    [phase],
  );

  const acceptOne = (idx: number) => {
    if (phase.kind !== "ok") return;
    const row = phase.rows[idx];
    if (row.state !== "pending") return;
    const next = wrapFirstOccurrence(working, row.text, row.page);
    if (next === working) {
      // Substring already linked or not found — mark skipped silently.
      setPhase({
        ...phase,
        rows: phase.rows.map((r, i) => (i === idx ? { ...r, state: "skipped" } : r)),
      });
      return;
    }
    setWorking(next);
    setPhase({
      ...phase,
      rows: phase.rows.map((r, i) => (i === idx ? { ...r, state: "accepted" } : r)),
    });
    onApplyContent(next);
  };

  const skipOne = (idx: number) => {
    if (phase.kind !== "ok") return;
    setPhase({
      ...phase,
      rows: phase.rows.map((r, i) => (i === idx ? { ...r, state: "skipped" } : r)),
    });
  };

  const acceptAll = () => {
    if (phase.kind !== "ok") return;
    let next = working;
    const updatedRows = phase.rows.map((row) => {
      if (row.state !== "pending") return row;
      const after = wrapFirstOccurrence(next, row.text, row.page);
      if (after === next) return { ...row, state: "skipped" as const };
      next = after;
      return { ...row, state: "accepted" as const };
    });
    setWorking(next);
    setPhase({ ...phase, rows: updatedRows });
    if (next !== working) onApplyContent(next);
  };

  return (
    <AnimatePresence>
    {open && (
    <motion.div
      className="fixed inset-0 z-[70] flex items-start justify-center pt-[10vh]"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.14 }}
      style={{
        background: "rgba(10,8,18,0.6)",
        backdropFilter: "blur(6px)",
        WebkitBackdropFilter: "blur(6px)",
      }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <motion.div
        className="w-[min(680px,94vw)] max-h-[80vh] rounded-xl overflow-hidden flex flex-col"
        initial={{ opacity: 0, scale: 0.96, y: -6 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.97, y: -4 }}
        transition={{ type: "spring", stiffness: 420, damping: 32, mass: 0.7 }}
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
            ⚡ Autolink — {noteTitle || "(untitled)"}
          </div>
          {phase.kind === "ok" && (
            <span className="text-[11px] text-text-muted ml-1">
              source <span className="text-accent">{phase.source}</span>
            </span>
          )}
          {phase.kind === "ok" && phase.rows.some((r) => r.state === "pending") && (
            <button
              onClick={acceptAll}
              className="ml-auto h-7 px-2.5 rounded text-[11px] font-medium flex items-center gap-1.5 hover:opacity-90"
              style={{ background: "var(--color-accent)", color: "var(--color-bg-primary)" }}
              title="Wrap every remaining suggestion in [[ ]]"
            >
              <Zap size={12} strokeWidth={2} />
              Accept all ({phase.rows.filter((r) => r.state === "pending").length})
            </button>
          )}
          <button
            onClick={onClose}
            className={`text-text-muted hover:text-text-primary ${
              phase.kind === "ok" && phase.rows.some((r) => r.state === "pending")
                ? "ml-2"
                : "ml-auto"
            }`}
          >
            <X size={16} strokeWidth={1.75} />
          </button>
        </div>

        <div data-lb-scroll-root className="flex-1 overflow-y-auto px-5 py-4">
          {phase.kind === "loading" && (
            <div className="flex items-center gap-3 text-text-muted text-sm">
              <div
                className="w-3 h-3 rounded-full"
                style={{
                  background: "#10b981",
                  animation: "pulse-dot 1.4s ease-in-out infinite",
                }}
              />
              Scanning for wikilink candidates…
            </div>
          )}

          {phase.kind === "err" && (
            <div className="text-red-400 text-sm whitespace-pre-wrap">{phase.msg}</div>
          )}

          {phase.kind === "empty" && (
            <div className="text-text-muted text-sm">
              No autolink candidates found{" "}
              <span className="opacity-70">(source: {phase.source})</span>.
              <div className="text-text-muted text-[12px] mt-2 leading-relaxed">
                Either every name in this note is already linked, or none of them
                match an existing page title. Try creating a few related notes
                first, then run autolink again.
              </div>
            </div>
          )}

          {phase.kind === "ok" && (
            <ul className="space-y-1.5">
              {phase.rows.map((row, idx) => (
                <li
                  key={`${row.text}__${row.page}__${idx}`}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg border"
                  style={{
                    background:
                      row.state === "accepted"
                        ? "rgba(134, 239, 172, 0.08)"
                        : row.state === "skipped"
                        ? "rgba(255, 255, 255, 0.02)"
                        : "rgba(255, 255, 255, 0.03)",
                    borderColor:
                      row.state === "accepted"
                        ? "rgba(134, 239, 172, 0.25)"
                        : "var(--color-lb-border)",
                    opacity: row.state === "skipped" ? 0.55 : 1,
                  }}
                >
                  <div className="flex-1 min-w-0 text-[13px] flex items-center gap-2 flex-wrap">
                    <span className="text-text-muted">"{row.text}"</span>
                    <span className="text-text-muted">→</span>
                    <span className="text-accent font-medium">[[{row.page}]]</span>
                  </div>
                  {row.state === "pending" ? (
                    <>
                      <button
                        onClick={() => skipOne(idx)}
                        className="h-7 px-2 rounded text-[11px] text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors"
                        title="Leave the text unlinked"
                      >
                        Skip
                      </button>
                      <button
                        onClick={() => acceptOne(idx)}
                        className="h-7 px-2.5 rounded text-[11px] font-medium flex items-center gap-1 hover:opacity-90"
                        style={{
                          background: "var(--color-accent-soft)",
                          color: "var(--color-accent)",
                        }}
                        title="Wrap this text in [[ ]]"
                      >
                        <Check size={11} strokeWidth={2.2} />
                        Accept
                      </button>
                    </>
                  ) : (
                    <span
                      className="text-[10px] uppercase tracking-wider font-semibold"
                      style={{
                        color:
                          row.state === "accepted"
                            ? "var(--color-lb-cat-til)"
                            : "var(--color-text-muted)",
                      }}
                    >
                      {row.state === "accepted" ? "linked" : "skipped"}
                      {row.state === "accepted" && (
                        <button
                          onClick={() => {
                            // Undo: unwrap [[page]] back to bare text and reset row.
                            const unwrapped = unwrapFirstLink(working, row.text, row.page);
                            if (unwrapped !== working) {
                              setWorking(unwrapped);
                              onApplyContent(unwrapped);
                            }
                            if (phase.kind === "ok") {
                              setPhase({
                                ...phase,
                                rows: phase.rows.map((r, i) =>
                                  i === idx ? { ...r, state: "pending" as const } : r,
                                ),
                              });
                            }
                          }}
                          className="ml-2 inline-flex items-center gap-0.5 text-text-muted hover:text-text-primary"
                          title="Undo this link"
                        >
                          <RotateCcw size={9} strokeWidth={2} />
                        </button>
                      )}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {phase.kind === "ok" && (
          <div
            className="px-5 py-2 text-[11px] text-text-muted flex items-center gap-3"
            style={{ borderTop: "1px solid rgba(255,255,255,0.05)" }}
          >
            <span>
              {acceptedCount} accepted · {phase.rows.filter((r) => r.state === "skipped").length}{" "}
              skipped · {phase.rows.filter((r) => r.state === "pending").length} pending
            </span>
            <span className="ml-auto opacity-60">Changes save automatically.</span>
          </div>
        )}
      </motion.div>
    </motion.div>
    )}
    </AnimatePresence>
  );
}

/** Wrap the FIRST occurrence of `text` in `content` with `[[page]]`,
 *  but only if that occurrence isn't already inside `[[ ]]`. Returns
 *  the original string when nothing was wrapped. */
function wrapFirstOccurrence(content: string, text: string, page: string): string {
  if (!text || !content) return content;
  const escaped = text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // Word-boundary match (Unicode-friendly via \p{L}\p{N}); skip when already
  // wrapped — `(?<!\[\[)` won't reliably catch every case, so we do an
  // explicit substring scan and check the surrounding context per match.
  const re = new RegExp(`(?<![A-Za-z0-9_])(${escaped})(?![A-Za-z0-9_])`, "i");
  const m = re.exec(content);
  if (!m) return content;
  const start = m.index;
  const end = start + m[0].length;
  // Already wrapped check: is there a `[[` immediately before and `]]` after?
  const before = content.slice(Math.max(0, start - 2), start);
  const after = content.slice(end, end + 2);
  if (before === "[[" && after === "]]") return content;
  return content.slice(0, start) + `[[${page}]]` + content.slice(end);
}

/** Inverse of wrapFirstOccurrence — turns the FIRST `[[page]]` written for
 *  this row back into plain `text`. Used by the per-row Undo button. */
function unwrapFirstLink(content: string, _text: string, page: string): string {
  const escaped = page.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`\\[\\[${escaped}\\]\\]`);
  const m = re.exec(content);
  if (!m) return content;
  return content.slice(0, m.index) + page + content.slice(m.index + m[0].length);
}
