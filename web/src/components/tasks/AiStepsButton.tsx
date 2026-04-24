import { useState } from "react";
import type { TaskItem, TaskStep } from "../../api";
import { parseSteps, parseTask, setTaskSteps } from "../../api";

/**
 * ✨ Break into steps — single-click subtask generation.
 *
 * Sends the task's title + description through the backend's AI parser
 * (`/api/tasks/parse` with `mode="ai"`), reads the `draft.steps[]` array, and
 * appends any new ones to the task's existing steps (de-duped by
 * title-lowercase). Works even if the task already has steps — the agent
 * treats the existing list as context and usually continues from there.
 *
 * No-op when the task has no title. Hard-failures surface a 3-second red
 * hint next to the button; soft-failures (backend returns empty `steps[]`)
 * show "no sub-steps needed" in the same spot.
 */
export function AiStepsButton({
  task,
  onAdded,
}: {
  task: TaskItem;
  onAdded: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState<{ tone: "ok" | "warn" | "err"; text: string } | null>(null);

  const clearHint = () => setTimeout(() => setHint(null), 3_000);

  const onClick = async () => {
    if (busy || !task.title.trim()) return;
    setBusy(true);
    setHint(null);

    const prompt = task.description?.trim()
      ? `${task.title}. ${task.description.trim()}`
      : task.title;

    try {
      const draft = await parseTask(prompt, "ai");
      const suggested = (draft.steps ?? []).map((s) => s.trim()).filter(Boolean);

      if (suggested.length === 0) {
        setHint({ tone: "warn", text: "no sub-steps needed" });
        clearHint();
        return;
      }

      const existing = parseSteps(task.steps);
      const existingLower = new Set(existing.map((s) => s.title.toLowerCase().trim()));
      const fresh: TaskStep[] = suggested
        .filter((title) => !existingLower.has(title.toLowerCase()))
        .map((title, i) => ({
          id: `ai-${Date.now()}-${i}`,
          title,
          done: false,
        }));

      if (fresh.length === 0) {
        setHint({ tone: "warn", text: "already covered" });
        clearHint();
        return;
      }

      await setTaskSteps(task.id, [...existing, ...fresh]);
      setHint({ tone: "ok", text: `+${fresh.length} step${fresh.length > 1 ? "s" : ""}` });
      clearHint();
      onAdded();
    } catch {
      setHint({ tone: "err", text: "couldn't break it down" });
      clearHint();
    } finally {
      setBusy(false);
    }
  };

  const hintColor =
    hint?.tone === "err"
      ? "text-red-400"
      : hint?.tone === "warn"
        ? "text-text-muted"
        : "text-accent";

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        title="Let the AI break this task into sub-steps"
        className="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded border border-border text-text-muted hover:text-accent hover:border-accent/40 transition-colors disabled:opacity-40 inline-flex items-center gap-1"
      >
        {busy ? (
          <>
            <span className="inline-block w-[8px] h-[8px] rounded-full bg-accent/50 animate-pulse" />
            breaking…
          </>
        ) : (
          <>✨ break into steps</>
        )}
      </button>
      {hint && (
        <span className={`text-[10px] ${hintColor}`} role="status">
          {hint.text}
        </span>
      )}
    </div>
  );
}
