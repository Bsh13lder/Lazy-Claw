import { useEffect, useRef, useState } from "react";
import { addTask, parseTask } from "../../api";
import type { TaskDraft } from "../../api";
import { formatDueChip, DUE_TONE_CLASS } from "./taskHelpers";

/**
 * Quick-add bar with a live parse ghost line.
 *
 * As the user types, we debounce-parse via the local regex (fast path). A
 * ghost line under the input shows what the backend will save — time chip,
 * priority, tags — so the user can course-correct before hitting ⏎.
 *
 * If the regex didn't extract a time but the user wants the AI to read
 * the phrase properly, clicking ✨ AI routes the same text through the
 * ECO worker for structured extraction.
 */

const PRIORITY_DOT: Record<NonNullable<TaskDraft["priority"]>, string> = {
  urgent: "bg-red-400",
  high: "bg-amber",
  medium: "bg-accent/60",
  low: "bg-text-muted",
};

export function QuickAddBar({ onAdded }: { onAdded: () => void }) {
  const [value, setValue] = useState("");
  const [draft, setDraft] = useState<TaskDraft | null>(null);
  const [parsing, setParsing] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const debounceRef = useRef<number | null>(null);

  useEffect(() => {
    if (!value.trim()) {
      setDraft(null);
      return;
    }
    if (debounceRef.current) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(async () => {
      setParsing(true);
      try {
        const d = await parseTask(value, "fast");
        setDraft(d);
      } catch {
        setDraft(null);
      } finally {
        setParsing(false);
      }
    }, 220);

    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
  }, [value]);

  const runAi = async () => {
    if (!value.trim() || aiLoading) return;
    setAiLoading(true);
    try {
      const d = await parseTask(value, "ai");
      setDraft(d);
    } catch {
      /* stays on fast draft */
    } finally {
      setAiLoading(false);
    }
  };

  const submit = async () => {
    const raw = value.trim();
    if (!raw || submitting) return;

    // Prefer the parsed draft — falls back to raw title if nothing parsed.
    const title = draft?.title || raw;
    const priority = draft?.priority ?? "medium";

    setSubmitting(true);
    try {
      await addTask({
        title,
        priority,
        due_date: draft?.due_date ?? undefined,
        reminder_at: draft?.reminder_at ?? undefined,
        tags: draft?.tags?.length ? draft.tags : undefined,
        category: draft?.category ?? undefined,
        steps: draft?.steps?.length
          ? draft.steps.map((s) => ({ title: s }))
          : undefined,
      });
      setValue("");
      setDraft(null);
      onAdded();
    } finally {
      setSubmitting(false);
    }
  };

  const chip = formatDueChip(draft?.due_date ?? null, draft?.reminder_at ?? null);
  const hasDraft = !!draft && (
    !!draft.due_date || !!draft.reminder_at || !!draft.priority || (draft.tags?.length ?? 0) > 0
  );

  return (
    <div className="rounded-xl border border-border bg-bg-secondary overflow-hidden">
      <form
        onSubmit={(e) => { e.preventDefault(); void submit(); }}
        className="flex items-center gap-2 px-3 py-2.5"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-accent shrink-0">
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder='Add a task — "tomorrow at 9 buy milk urgent", "in 2 hours call mom #work"'
          disabled={submitting}
          className="flex-1 text-sm bg-transparent text-text-primary placeholder:text-text-placeholder focus:outline-none"
          autoFocus
        />
        {value.trim() && (
          <>
            <button
              type="button"
              onClick={runAi}
              disabled={aiLoading || submitting}
              title="Let the AI parse the phrase"
              className="text-[10px] uppercase tracking-wider text-text-muted hover:text-accent transition-colors disabled:opacity-40 px-2 py-1 rounded border border-border hover:border-accent/40"
            >
              {aiLoading ? "…" : "✨ AI"}
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="text-[10px] uppercase tracking-wider text-accent hover:text-accent-dim transition-colors disabled:opacity-40 px-2.5 py-1 rounded-md bg-accent-soft"
            >
              {submitting ? "…" : "add ⏎"}
            </button>
          </>
        )}
      </form>

      {/* Live parse preview */}
      {value.trim() && (
        <div className="flex items-center gap-2 px-3 py-1.5 border-t border-border/50 bg-bg-tertiary/30 text-[11px] min-h-[28px]">
          {parsing && !hasDraft ? (
            <span className="text-text-muted italic">parsing…</span>
          ) : !hasDraft ? (
            <span className="text-text-muted">
              no time detected — press ⏎ to save as-is, or ✨ AI for a deeper read
            </span>
          ) : (
            <>
              {chip.label && (
                <span className={`ticker ${DUE_TONE_CLASS[chip.tone]}`}>
                  🗓 {chip.label}
                </span>
              )}
              {draft?.priority && (
                <span className="inline-flex items-center gap-1">
                  <span className={`w-1.5 h-1.5 rounded-full ${PRIORITY_DOT[draft.priority]}`} />
                  <span className="uppercase tracking-wider text-text-secondary">
                    {draft.priority}
                  </span>
                </span>
              )}
              {draft?.category && (
                <span className="text-text-muted uppercase tracking-wider">
                  · {draft.category}
                </span>
              )}
              {draft && draft.tags.length > 0 && (
                <span className="flex items-center gap-1">
                  {draft.tags.slice(0, 4).map((t) => (
                    <span key={t} className="px-1 rounded bg-bg-hover text-text-muted">
                      #{t}
                    </span>
                  ))}
                </span>
              )}
              {draft?.title && (
                <span className="ml-auto text-text-secondary truncate max-w-[60%]" title={draft.title}>
                  → "{draft.title}"
                </span>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
