import { useEffect, useRef, useState } from "react";
import { rescheduleTask, updateTask } from "../../api";
import {
  QUICK_DATES,
  toQuickReschedule,
  toQuickRescheduleFromInput,
} from "./rescheduleDates";

/**
 * Inline NL reschedule composer.
 *
 *   ┌─ TaskRescheduleInput ─────────────────────────────────────────────┐
 *   │ [+1h] [+1d] [tom] [next wk] │ tomorrow 3pm…       [✨ smart]  ⏎  │
 *   └────────────────────────────────────────────────────────────────────┘
 *
 * Two paths converge on the same backend endpoint:
 *
 *   • "Smart" — POST /api/tasks/{id}/reschedule {mode: "smart"}.
 *     Worker LLM picks a sensible new time given the task's title +
 *     priority + current schedule. No user input needed.
 *
 *   • "NL" — user types a phrase, ⏎ POSTs {mode: "nl", phrase}. Server
 *     runs nl_time.parse, applies reminder_at + due_date.
 *
 * Quick-pick chips ("+1h", "+1d", "tomorrow", "next week") short-circuit
 * the input — clicking one fires the NL path with that phrase verbatim.
 *
 * `compact` shrinks the chip row + drops verbose labels for use inside
 * TaskCard's hover row, where every pixel matters. Default ("full") fits
 * the right-pane Reschedule section.
 */

type Tone = "idle" | "thinking" | "ok" | "error";

type Variant = "compact" | "full";

const QUICK_PICKS: { label: string; phrase: string; hint: string }[] = [
  { label: "+1h", phrase: "in 1 hour", hint: "Push by an hour" },
  { label: "+3h", phrase: "in 3 hours", hint: "Push by three hours" },
  { label: "tom", phrase: "tomorrow 10am", hint: "Tomorrow 10 AM" },
  { label: "+1w", phrase: "next monday 10am", hint: "Next Monday 10 AM" },
];

export function TaskRescheduleInput({
  taskId,
  onChanged,
  variant = "full",
  autoFocus,
}: {
  taskId: string;
  onChanged: () => void;
  variant?: Variant;
  autoFocus?: boolean;
}) {
  const [phrase, setPhrase] = useState("");
  const [tone, setTone] = useState<Tone>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  // Auto-clear the toast line so the strip stays calm at rest.
  useEffect(() => {
    if (tone === "idle" || tone === "thinking") return;
    const id = window.setTimeout(() => {
      setTone("idle");
      setMessage(null);
    }, tone === "error" ? 5000 : 2500);
    return () => window.clearTimeout(id);
  }, [tone]);

  const apply = async (
    rawPhrase: string,
    mode: "nl" | "smart",
  ): Promise<void> => {
    setTone("thinking");
    setMessage(mode === "smart" ? "asking the worker LLM…" : `parsing "${rawPhrase}"…`);
    try {
      const res = await rescheduleTask(taskId, {
        mode,
        ...(mode === "nl" ? { phrase: rawPhrase } : {}),
      });
      setTone("ok");
      setMessage(`✓ ${res.applied}`);
      setPhrase("");
      onChanged();
    } catch (err) {
      setTone("error");
      const detail =
        err instanceof Error && /detail/.test(err.message)
          ? err.message.replace(/^.*?detail":?/i, "").replace(/[}"]+$/g, "")
          : "couldn't parse — try 'tomorrow 3pm', '+2h', 'next Monday'";
      setMessage(detail.slice(0, 120));
    }
  };

  // Deterministic quick-date path: compute the target day client-side and
  // PATCH { due_date, reminder_at } directly (default 10:00 local). Bypasses
  // the NL parser entirely so the semantics match mobile exactly.
  const applyQuickDate = async (
    payload: { due_date: string; reminder_at: string },
    label: string,
  ): Promise<void> => {
    setTone("thinking");
    setMessage(`moving to ${label}…`);
    try {
      await updateTask(taskId, payload);
      setTone("ok");
      setMessage(`✓ ${label} · 10:00 AM`);
      setPhrase("");
      onChanged();
    } catch (err) {
      setTone("error");
      const detail =
        err instanceof Error ? err.message : "couldn't reschedule — try again";
      setMessage(detail.slice(0, 120));
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const v = phrase.trim();
    if (!v) return;
    void apply(v, "nl");
  };

  const onPickDate = (e: React.ChangeEvent<HTMLInputElement>) => {
    const iso = e.target.value; // YYYY-MM-DD (already local-calendar)
    if (!iso) return;
    void applyQuickDate(toQuickRescheduleFromInput(iso), iso);
    e.target.value = "";
  };

  const compact = variant === "compact";

  return (
    <div className="flex flex-col gap-1.5">
      {/* Quick-date chips — one tap drops the task onto that day at 10:00 AM
          via a deterministic PATCH (no NL parsing). */}
      <div className="flex items-center gap-1 flex-wrap">
        {QUICK_DATES.map((q) => (
          <button
            key={q.label}
            type="button"
            title={`${q.label} · 10:00 AM`}
            onClick={() => void applyQuickDate(toQuickReschedule(q.fn()), q.label)}
            disabled={tone === "thinking"}
            className="text-[11px] px-2 py-0.5 rounded-full border border-border/60 text-text-secondary hover:text-accent hover:border-accent/50 hover:bg-accent-soft/30 transition-colors disabled:opacity-40"
          >
            {q.label}
          </button>
        ))}
        {/* "Pick a date…" — native date input; chosen day also lands at 10am. */}
        <label
          title="Pick a date · 10:00 AM"
          className={`relative text-[11px] px-2 py-0.5 rounded-full border border-border/60 text-text-secondary hover:text-accent hover:border-accent/50 hover:bg-accent-soft/30 transition-colors cursor-pointer ${
            tone === "thinking" ? "opacity-40 pointer-events-none" : ""
          }`}
        >
          Pick a date…
          <input
            type="date"
            onChange={onPickDate}
            disabled={tone === "thinking"}
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
            aria-label="Pick a date to reschedule"
          />
        </label>
      </div>

      <div
        className={`flex items-center gap-1.5 rounded-md bg-bg-tertiary/60 border border-border/60 ${
          compact ? "px-1.5 py-1" : "px-2 py-1.5"
        }`}
      >
        {/* Quick-pick chips — single click reschedules. */}
        <div className="flex items-center gap-1 shrink-0">
          {QUICK_PICKS.map((q) => (
            <button
              key={q.label}
              type="button"
              title={q.hint}
              onClick={() => void apply(q.phrase, "nl")}
              disabled={tone === "thinking"}
              className="text-[10px] uppercase tracking-[0.08em] px-1.5 py-0.5 rounded border border-border/50 text-text-muted hover:text-accent hover:border-accent/40 hover:bg-accent-soft/30 transition-colors disabled:opacity-40"
            >
              {q.label}
            </button>
          ))}
        </div>

        <span className="w-px h-4 bg-border/60 shrink-0" aria-hidden="true" />

        {/* Free-form NL input. */}
        <form onSubmit={submit} className="flex items-center gap-1.5 flex-1 min-w-0">
          <input
            ref={inputRef}
            type="text"
            value={phrase}
            onChange={(e) => setPhrase(e.target.value)}
            placeholder={compact ? "tomorrow 3pm…" : "type — tomorrow 3pm, +2h, snooze 30m, next Monday…"}
            disabled={tone === "thinking"}
            className="flex-1 min-w-0 text-[12px] bg-transparent text-text-primary placeholder:text-text-placeholder focus:outline-none"
            onKeyDown={(e) => {
              // Esc clears the input cleanly.
              if (e.key === "Escape") {
                e.currentTarget.blur();
                setPhrase("");
              }
            }}
          />
          {phrase.trim() && (
            <button
              type="submit"
              disabled={tone === "thinking"}
              className="text-[10px] uppercase tracking-wider text-accent hover:text-accent-dim disabled:opacity-40 px-1.5 py-0.5 rounded bg-accent-soft transition-colors"
              title="Apply this phrase"
            >
              {tone === "thinking" ? "…" : "⏎"}
            </button>
          )}
          <button
            type="button"
            onClick={() => void apply("", "smart")}
            disabled={tone === "thinking"}
            className="text-[10px] uppercase tracking-wider text-text-muted hover:text-accent disabled:opacity-40 px-1.5 py-0.5 rounded border border-border/50 hover:border-accent/40 hover:bg-accent-soft/30 transition-colors shrink-0"
            title="Smart reschedule — worker LLM picks a sensible new time"
          >
            ✨ {compact ? "" : "smart"}
          </button>
        </form>
      </div>

      {/* Single-line status row — only renders while non-idle so the
          composer stays calm at rest. */}
      {message && (
        <p
          className={`text-[10.5px] leading-snug px-1 ${
            tone === "error"
              ? "text-red-400"
              : tone === "ok"
                ? "text-accent"
                : "text-text-muted italic"
          }`}
          role="status"
        >
          {tone === "thinking" && (
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent animate-pulse mr-1.5 align-middle" />
          )}
          {message}
        </p>
      )}
    </div>
  );
}
