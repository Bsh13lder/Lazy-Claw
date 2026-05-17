import { useEffect, useRef, useState } from "react";
import {
  abortGoal,
  getGoal,
  startCodeTask,
  submitGoalAnswers,
  type Goal,
} from "../api";

/**
 * NewCodeTaskModal — the AI-driven intake for a Code Specialist run.
 *
 * Flow:
 *   1. Initial: free-text textarea + optional project_tag → POST start.
 *   2. Drafting: spinner while the brain plans + drafts questions.
 *   3. Questions: render every question_pending with its own textarea
 *      → POST answers → if more pending, re-render with the remainder.
 *   4. Dispatched: terminal — show "Code Specialist started, see the
 *      page below" and close after ~2s so the user sees the new task
 *      card appear on the CodeSpecialist page.
 *
 * The intake polls `/api/goals/{id}` every 1.5s while the goal is in
 * DRAFTING so a slow `build_fix_plan` LLM call doesn't leave the modal
 * stuck on "Thinking…" with no progress signal. Polling stops once we
 * land in any non-DRAFTING state.
 */

interface NewCodeTaskModalProps {
  open: boolean;
  onClose: () => void;
  /** Called when a goal transitions out of intake (EXECUTING /
   * BLOCKED / DONE / ABORTED / FAILED) — the parent can refresh its
   * Code Specialist task list immediately rather than waiting for the
   * 3s status poll. */
  onDispatched?: (goal: Goal) => void;
}

type Stage = "initial" | "drafting" | "questions" | "dispatched" | "error";

const DEFAULT_PROJECT_TAG_HINT =
  "e.g. upwork:job-42, personal, weekend-hack (optional)";

export default function NewCodeTaskModal({
  open,
  onClose,
  onDispatched,
}: NewCodeTaskModalProps) {
  const [stage, setStage] = useState<Stage>("initial");
  const [title, setTitle] = useState("");
  const [projectTag, setProjectTag] = useState("");
  const [goal, setGoal] = useState<Goal | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [errMsg, setErrMsg] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);

  // Drafting poll handle. Cleared whenever the modal closes or the
  // goal leaves DRAFTING — otherwise we'd leak intervals between
  // opens.
  const pollRef = useRef<number | null>(null);

  // Reset state every time the modal opens so a previous failed run
  // doesn't bleed into the next one.
  useEffect(() => {
    if (open) {
      setStage("initial");
      setTitle("");
      setProjectTag("");
      setGoal(null);
      setAnswers({});
      setErrMsg("");
      setSubmitting(false);
    } else if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, [open]);

  // Poll DRAFTING goals so the modal advances as soon as the brain
  // finishes drafting. 1.5s cadence keeps API load tiny — drafting
  // usually finishes in 4–10s.
  useEffect(() => {
    if (!goal || goal.status !== "drafting") {
      if (pollRef.current != null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    pollRef.current = window.setInterval(async () => {
      try {
        const fresh = await getGoal(goal.id);
        setGoal(fresh);
        advanceFromGoal(fresh);
      } catch (e) {
        // Soft fail: keep polling, surface only on N retries.
        // Visible only to console — modal stays on "Thinking…".
        console.warn("goal poll failed", e);
      }
    }, 1500);
    return () => {
      if (pollRef.current != null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [goal?.id, goal?.status]);

  // Single decision point for "where should the modal be right now,
  // given this goal state?" — called from start, answer, and poll
  // tick so behavior is uniform.
  function advanceFromGoal(g: Goal) {
    if (g.status === "awaiting_user_info") {
      setStage("questions");
      // Seed answer textareas with whatever the user already gave
      // (in case they came back after a partial-answer round).
      setAnswers((prev) => {
        const next = { ...prev };
        for (const q of g.questions_pending) {
          if (!(q in next)) next[q] = "";
        }
        return next;
      });
      return;
    }
    if (g.status === "drafting") {
      setStage("drafting");
      return;
    }
    // EXECUTING / BLOCKED / DONE / ABORTED / FAILED — terminal for
    // the intake. Close the modal after a brief "started" beat.
    setStage("dispatched");
    onDispatched?.(g);
    window.setTimeout(() => onClose(), 1500);
  }

  async function handleStart() {
    setErrMsg("");
    if (title.trim().length < 4) {
      setErrMsg("Describe the build in a sentence or two (4+ chars).");
      return;
    }
    setSubmitting(true);
    setStage("drafting");
    try {
      const g = await startCodeTask(
        title.trim(),
        projectTag.trim() || undefined,
      );
      setGoal(g);
      advanceFromGoal(g);
    } catch (e: unknown) {
      setStage("error");
      setErrMsg(e instanceof Error ? e.message : "Failed to start goal.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSubmitAnswers() {
    if (!goal) return;
    // Trim + only send non-empty answers; partial answers are allowed
    // — the goal stays in AWAITING_USER_INFO and re-asks.
    const payload: Record<string, string> = {};
    for (const [q, a] of Object.entries(answers)) {
      const trimmed = a.trim();
      if (trimmed) payload[q] = trimmed;
    }
    if (Object.keys(payload).length === 0) {
      setErrMsg("Answer at least one question to move forward.");
      return;
    }
    setErrMsg("");
    setSubmitting(true);
    try {
      const fresh = await submitGoalAnswers(goal.id, payload);
      setGoal(fresh);
      advanceFromGoal(fresh);
    } catch (e: unknown) {
      setErrMsg(e instanceof Error ? e.message : "Submit failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleAbort() {
    if (!goal) {
      onClose();
      return;
    }
    try {
      await abortGoal(goal.id);
    } catch {
      // Don't block close on abort failure.
    }
    onClose();
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={(e) => {
        // Click on backdrop closes; click inside the dialog doesn't.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-2xl rounded-xl border border-bg-border bg-bg-primary shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="px-5 py-4 border-b border-bg-border flex items-center gap-3">
          <span className="px-2 py-0.5 rounded text-[10px] uppercase tracking-wider bg-accent/20 text-accent border border-accent/30">
            Code Specialist
          </span>
          <h2 className="text-base font-semibold text-text-primary flex-1">
            {stage === "questions"
              ? "A few clarifying questions"
              : stage === "dispatched"
              ? "Started — watch it run"
              : "New Code Task"}
          </h2>
          <button
            type="button"
            onClick={handleAbort}
            className="text-text-muted hover:text-text-primary text-lg leading-none w-6 h-6 flex items-center justify-center"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 overflow-y-auto flex-1 space-y-4">
          {stage === "initial" && (
            <>
              <p className="text-sm text-text-secondary">
                Describe what you want to build. The AI will plan it, ask
                you any clarifying questions in one batch, then hand the
                whole brief to <span className="font-mono">claude-code</span>.
                Files will land on your Desktop at{" "}
                <span className="font-mono text-accent">
                  ~/Desktop/lazyclaw-workspace/
                </span>
                .
              </p>
              <label className="block">
                <span className="text-[11px] uppercase tracking-wide text-text-muted">
                  What should it build?
                </span>
                <textarea
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="e.g. a CLI that converts CSV to JSON with a --pretty flag, in Python, with tests"
                  rows={5}
                  autoFocus
                  className="mt-1 w-full rounded border border-bg-border bg-bg-secondary text-text-primary p-2 text-sm font-mono resize-y focus:outline-none focus:ring-1 focus:ring-accent"
                />
              </label>
              <label className="block">
                <span className="text-[11px] uppercase tracking-wide text-text-muted">
                  Project tag <span className="normal-case text-text-muted/70">(optional — groups it on this page + picks the workspace subfolder)</span>
                </span>
                <input
                  type="text"
                  value={projectTag}
                  onChange={(e) => setProjectTag(e.target.value)}
                  placeholder={DEFAULT_PROJECT_TAG_HINT}
                  className="mt-1 w-full rounded border border-bg-border bg-bg-secondary text-text-primary p-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-accent"
                />
              </label>
              {errMsg && (
                <div className="text-rose-300 text-xs">{errMsg}</div>
              )}
            </>
          )}

          {stage === "drafting" && (
            <div className="py-10 flex flex-col items-center gap-3 text-text-secondary">
              <div className="inline-block w-8 h-8 border-2 border-accent border-t-transparent rounded-full animate-spin" />
              <div className="text-sm">
                Planning your task and drafting questions…
              </div>
              <div className="text-[11px] text-text-muted">
                This usually takes 4–10 seconds.
              </div>
            </div>
          )}

          {stage === "questions" && goal && (
            <>
              {goal.summary && goal.summary !== goal.title && (
                <div className="rounded border border-bg-border bg-bg-secondary p-3">
                  <div className="text-[10px] uppercase tracking-wide text-text-muted">
                    AI understood
                  </div>
                  <div className="text-sm text-text-primary mt-1">
                    {goal.summary}
                  </div>
                </div>
              )}
              <p className="text-sm text-text-secondary">
                Answer what you can — leave anything blank and the AI
                will re-ask only the ones it still needs.
              </p>
              {goal.questions_pending.map((q, i) => (
                <label key={`${i}-${q.slice(0, 12)}`} className="block">
                  <span className="text-[11px] uppercase tracking-wide text-text-muted">
                    Q{i + 1}
                  </span>
                  <div className="text-sm text-text-primary mb-1 mt-0.5">{q}</div>
                  <textarea
                    value={answers[q] || ""}
                    onChange={(e) =>
                      setAnswers((prev) => ({ ...prev, [q]: e.target.value }))
                    }
                    rows={2}
                    className="w-full rounded border border-bg-border bg-bg-secondary text-text-primary p-2 text-sm focus:outline-none focus:ring-1 focus:ring-accent"
                  />
                </label>
              ))}
              {goal.risks.length > 0 && (
                <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2">
                  <div className="text-[10px] uppercase tracking-wide text-amber-300">
                    Heads up
                  </div>
                  <ul className="text-[11px] text-amber-200 mt-1 list-disc pl-4 space-y-0.5">
                    {goal.risks.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                </div>
              )}
              {errMsg && (
                <div className="text-rose-300 text-xs">{errMsg}</div>
              )}
            </>
          )}

          {stage === "dispatched" && goal && (
            <div className="py-8 flex flex-col items-center gap-3 text-text-secondary">
              <div className="text-3xl">🚀</div>
              <div className="text-sm text-text-primary">
                Code Specialist is on it!
              </div>
              <div className="text-[11px] text-text-muted text-center max-w-md">
                Your task has been dispatched to claude-code. Watch it
                run on this page — files will appear at{" "}
                <span className="font-mono text-accent">
                  ~/Desktop/lazyclaw-workspace/
                  {goal.work_type || "untagged"}/
                </span>
              </div>
            </div>
          )}

          {stage === "error" && (
            <div className="space-y-3">
              <div className="text-rose-300 text-sm">
                {errMsg || "Something went wrong."}
              </div>
              <button
                type="button"
                onClick={() => setStage("initial")}
                className="text-xs text-accent hover:underline"
              >
                ← Try again
              </button>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-bg-border flex items-center justify-end gap-2">
          {stage === "initial" && (
            <>
              <button
                type="button"
                onClick={onClose}
                disabled={submitting}
                className="px-3 py-1.5 rounded text-sm text-text-secondary hover:text-text-primary"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleStart}
                disabled={submitting || title.trim().length < 4}
                className="px-4 py-1.5 rounded bg-accent text-bg-primary text-sm font-medium hover:bg-accent/90 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {submitting ? "Starting…" : "Start"}
              </button>
            </>
          )}
          {stage === "questions" && (
            <>
              <button
                type="button"
                onClick={handleAbort}
                disabled={submitting}
                className="px-3 py-1.5 rounded text-sm text-text-secondary hover:text-text-primary"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSubmitAnswers}
                disabled={submitting}
                className="px-4 py-1.5 rounded bg-accent text-bg-primary text-sm font-medium hover:bg-accent/90 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {submitting ? "Submitting…" : "Submit answers"}
              </button>
            </>
          )}
          {stage === "dispatched" && (
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-1.5 rounded bg-accent text-bg-primary text-sm font-medium hover:bg-accent/90"
            >
              Done
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
