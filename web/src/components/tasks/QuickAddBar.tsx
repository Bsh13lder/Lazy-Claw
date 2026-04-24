import { useEffect, useRef, useState } from "react";
import { addTask, parseTask } from "../../api";
import type { TaskDraft } from "../../api";
import {
  DATE_HINT_RE,
  DUE_TONE_CLASS,
  formatDueChip,
} from "./taskHelpers";

/**
 * Quick-add bar with a live parse ghost line.
 *
 * Typing fires the fast regex parser (220 ms debounce). If the regex misses
 * but the text looks date-ish (EN + ES keywords in `DATE_HINT_RE`), we quietly
 * escalate to the AI parser after an extra ~800 ms. That way the user still
 * sees a chip for phrases the regex can't handle ("next Friday at 3pm",
 * "in 2 weeks", "dentro de dos horas") without having to click anything.
 *
 * Variants:
 *   • "full"    — used on the Tasks page. Wide ghost row beneath the input.
 *   • "compact" — used on the Overview widget and in the MyTasksPanel sidebar.
 *                 Single tight row with the ghost summary inline on the right.
 */

const PRIORITY_DOT: Record<NonNullable<TaskDraft["priority"]>, string> = {
  urgent: "bg-red-400",
  high: "bg-amber",
  medium: "bg-accent/60",
  low: "bg-text-muted",
};

const REGEX_DEBOUNCE_MS = 220;
const AUTO_AI_DEBOUNCE_MS = 1_000;

function formatReminderTime(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function QuickAddBar({
  onAdded,
  variant = "full",
  placeholder,
}: {
  onAdded: () => void;
  variant?: "full" | "compact";
  placeholder?: string;
}) {
  const [value, setValue] = useState("");
  const [draft, setDraft] = useState<TaskDraft | null>(null);
  const [parsing, setParsing] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [autoAiRunning, setAutoAiRunning] = useState(false);
  const [draftSource, setDraftSource] = useState<"regex" | "ai" | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const regexTimerRef = useRef<number | null>(null);
  const aiTimerRef = useRef<number | null>(null);
  const activeRequestIdRef = useRef(0);

  // Fast-regex parse on every keystroke change.
  useEffect(() => {
    const trimmed = value.trim();
    if (!trimmed) {
      setDraft(null);
      setDraftSource(null);
      return;
    }

    if (regexTimerRef.current) window.clearTimeout(regexTimerRef.current);
    if (aiTimerRef.current) window.clearTimeout(aiTimerRef.current);

    regexTimerRef.current = window.setTimeout(async () => {
      const requestId = ++activeRequestIdRef.current;
      setParsing(true);
      try {
        const d = await parseTask(trimmed, "fast");
        if (requestId !== activeRequestIdRef.current) return;
        setDraft(d);
        setDraftSource("regex");

        // If regex didn't extract a time but the phrase has date-ish keywords,
        // silently escalate to the AI parser so the user still sees a chip.
        const regexMissedTime = !d.due_date && !d.reminder_at;
        if (regexMissedTime && DATE_HINT_RE.test(trimmed) && trimmed.length >= 6) {
          if (aiTimerRef.current) window.clearTimeout(aiTimerRef.current);
          aiTimerRef.current = window.setTimeout(async () => {
            if (requestId !== activeRequestIdRef.current) return;
            setAutoAiRunning(true);
            try {
              const aiDraft = await parseTask(trimmed, "ai");
              if (requestId !== activeRequestIdRef.current) return;
              // Only accept the AI draft if it actually added time signal or
              // broke the task into steps — otherwise keep the regex draft.
              if (aiDraft.due_date || aiDraft.reminder_at || (aiDraft.steps && aiDraft.steps.length)) {
                setDraft(aiDraft);
                setDraftSource("ai");
              }
            } catch {
              /* keep regex draft */
            } finally {
              if (requestId === activeRequestIdRef.current) setAutoAiRunning(false);
            }
          }, AUTO_AI_DEBOUNCE_MS);
        }
      } catch {
        if (requestId === activeRequestIdRef.current) setDraft(null);
      } finally {
        if (requestId === activeRequestIdRef.current) setParsing(false);
      }
    }, REGEX_DEBOUNCE_MS);

    return () => {
      if (regexTimerRef.current) window.clearTimeout(regexTimerRef.current);
      if (aiTimerRef.current) window.clearTimeout(aiTimerRef.current);
    };
  }, [value]);

  const runAi = async () => {
    if (!value.trim() || aiLoading) return;
    if (aiTimerRef.current) window.clearTimeout(aiTimerRef.current);
    const requestId = ++activeRequestIdRef.current;
    setAiLoading(true);
    try {
      const d = await parseTask(value, "ai");
      if (requestId !== activeRequestIdRef.current) return;
      setDraft(d);
      setDraftSource("ai");
    } catch {
      /* stays on fast draft */
    } finally {
      if (requestId === activeRequestIdRef.current) setAiLoading(false);
    }
  };

  const submit = async () => {
    const raw = value.trim();
    if (!raw || submitting) return;

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
        steps: draft?.steps?.length ? draft.steps.map((s) => ({ title: s })) : undefined,
      });
      setValue("");
      setDraft(null);
      setDraftSource(null);
      onAdded();
    } finally {
      setSubmitting(false);
    }
  };

  const chip = formatDueChip(draft?.due_date ?? null, draft?.reminder_at ?? null);
  const reminderTime = formatReminderTime(draft?.reminder_at ?? null);
  const stepCount = draft?.steps?.length ?? 0;
  const hasDraft = !!draft && (
    !!draft.due_date || !!draft.reminder_at || !!draft.priority ||
    (draft.tags?.length ?? 0) > 0 || stepCount > 0
  );
  const regexMissedButTried = !hasDraft && !parsing && !autoAiRunning &&
    value.trim().length >= 6 && DATE_HINT_RE.test(value);

  const aiButtonClass = [
    "text-[10px] uppercase tracking-wider transition-colors disabled:opacity-40 px-2 py-1 rounded border",
    regexMissedButTried
      ? "border-amber/50 bg-amber/10 text-amber hover:text-amber-dim hover:border-amber"
      : "border-border text-text-muted hover:text-accent hover:border-accent/40",
  ].join(" ");

  const effectivePlaceholder =
    placeholder ?? 'Add a task — "tomorrow at 9 buy milk urgent", "in 2 hours call mom #work"';

  const compact = variant === "compact";

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
          placeholder={effectivePlaceholder}
          disabled={submitting}
          className="flex-1 text-sm bg-transparent text-text-primary placeholder:text-text-placeholder focus:outline-none"
        />
        {value.trim() && (
          <>
            <button
              type="button"
              onClick={runAi}
              disabled={aiLoading || submitting || autoAiRunning}
              title={regexMissedButTried ? "Let the AI read this — regex couldn't find a time" : "Let the AI parse the phrase"}
              className={aiButtonClass}
            >
              {aiLoading || autoAiRunning ? "…" : "✨ AI"}
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

      {value.trim() && (
        <div className={`flex items-center gap-2 px-3 ${compact ? "py-1" : "py-1.5"} border-t border-border/50 bg-bg-tertiary/30 text-[11px] min-h-[26px]`}>
          {parsing && !hasDraft ? (
            <span className="text-text-muted italic">parsing…</span>
          ) : autoAiRunning ? (
            <span className="text-amber italic inline-flex items-center gap-1">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber animate-pulse" />
              ai reading "{value.trim().slice(0, 28)}"…
            </span>
          ) : !hasDraft ? (
            regexMissedButTried ? (
              <span className="text-amber">
                no time detected — ⏎ saves as-is · ✨ AI can read this phrase
              </span>
            ) : (
              <span className="text-text-muted">
                no time detected — press ⏎ to save as-is, or ✨ AI for a deeper read
              </span>
            )
          ) : (
            <>
              {chip.label && (
                <span className={`ticker ${DUE_TONE_CLASS[chip.tone]}`}>
                  🗓 {chip.label}
                  {reminderTime && chip.label !== reminderTime && !chip.label.includes(reminderTime) && (
                    <span className="text-text-muted ml-1">· {reminderTime}</span>
                  )}
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
              {stepCount > 0 && (
                <span
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-accent-soft text-accent"
                  title={draft?.steps?.join(" · ")}
                >
                  ✨ +{stepCount} step{stepCount > 1 ? "s" : ""}
                </span>
              )}
              {draftSource === "ai" && (
                <span className="text-[9px] uppercase tracking-wider text-accent/70" title="Parsed by the AI reader">
                  · ai
                </span>
              )}
              {draft?.title && !compact && (
                <span className="ml-auto text-text-secondary truncate max-w-[55%]" title={draft.title}>
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
