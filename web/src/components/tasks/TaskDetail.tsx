import { useEffect, useState } from "react";
import type { TaskItem, TaskStep } from "../../api";
import {
  completeTask,
  deleteTask,
  parseSteps,
  setTaskSteps,
  toggleTaskStep,
  updateTask,
} from "../../api";
import { PRIORITY_GUTTER, parseTags } from "./taskHelpers";

/**
 * Right pane — full control over a single task.
 *
 * Inline edits: title (click to edit), priority (pill dropdown), due date
 * (native date input), description (textarea), tags (chip list with +/×).
 * Sub-task steps render as a checkbox list with an add-step input.
 *
 * All changes persist via PATCH / PUT on keystroke-debounced or blur.
 */

type Priority = TaskItem["priority"];
const PRIORITIES: Priority[] = ["low", "medium", "high", "urgent"];

const PRIORITY_ACCENT: Record<Priority, string> = {
  urgent: "bg-red-400/10 text-red-400 border-red-400/30",
  high: "bg-amber/10 text-amber border-amber/30",
  medium: "bg-accent-soft text-accent border-accent/30",
  low: "bg-bg-tertiary text-text-muted border-border",
};

export function TaskDetail({
  task,
  onChanged,
}: {
  task: TaskItem;
  onChanged: () => void;
}) {
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description ?? "");
  const [dueDate, setDueDate] = useState(task.due_date ?? "");
  const [reminderAt, setReminderAt] = useState(task.reminder_at ?? "");
  const [priority, setPriority] = useState<Priority>(task.priority);
  const [tags, setTags] = useState<string[]>(parseTags(task.tags));
  const [newTag, setNewTag] = useState("");
  const [steps, setSteps] = useState<TaskStep[]>(parseSteps(task.steps));
  const [newStep, setNewStep] = useState("");
  const [savingTitle, setSavingTitle] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Reset local state when the selected task changes.
  useEffect(() => {
    setTitle(task.title);
    setDescription(task.description ?? "");
    setDueDate(task.due_date ?? "");
    setReminderAt(task.reminder_at ?? "");
    setPriority(task.priority);
    setTags(parseTags(task.tags));
    setSteps(parseSteps(task.steps));
    setConfirmDelete(false);
  }, [task.id, task.title, task.description, task.due_date, task.reminder_at, task.priority, task.tags, task.steps]);

  type PatchFields = Parameters<typeof updateTask>[1];
  const patch = async (fields: PatchFields) => {
    try {
      await updateTask(task.id, fields);
      onChanged();
    } catch {
      /* swallow — UI keeps local state so the user can retry */
    }
  };

  const saveTitleIfChanged = async () => {
    const next = title.trim();
    if (!next || next === task.title) return;
    setSavingTitle(true);
    try {
      await patch({ title: next });
    } finally {
      setSavingTitle(false);
    }
  };

  const addTag = async () => {
    const t = newTag.trim().toLowerCase().replace(/^#/, "");
    if (!t || tags.includes(t)) {
      setNewTag("");
      return;
    }
    const next = [...tags, t];
    setTags(next);
    setNewTag("");
    await patch({ tags: next });
  };

  const removeTag = async (tag: string) => {
    const next = tags.filter((t) => t !== tag);
    setTags(next);
    await patch({ tags: next });
  };

  const toggleStep = async (stepId: string) => {
    // Optimistic flip.
    setSteps((prev) => prev.map((s) => s.id === stepId ? { ...s, done: !s.done } : s));
    try {
      await toggleTaskStep(task.id, stepId);
      onChanged();
    } catch {
      // Revert on failure.
      setSteps((prev) => prev.map((s) => s.id === stepId ? { ...s, done: !s.done } : s));
    }
  };

  const addStep = async () => {
    const t = newStep.trim();
    if (!t) return;
    const draft: TaskStep = { id: `temp-${Date.now()}`, title: t, done: false };
    const next = [...steps, draft];
    setSteps(next);
    setNewStep("");
    try {
      const saved = await setTaskSteps(task.id, next);
      setSteps(saved);
      onChanged();
    } catch {
      // roll back
      setSteps(steps);
    }
  };

  const removeStep = async (stepId: string) => {
    const next = steps.filter((s) => s.id !== stepId);
    setSteps(next);
    try {
      await setTaskSteps(task.id, next);
      onChanged();
    } catch {
      setSteps(steps);
    }
  };

  const doDelete = async () => {
    try {
      await deleteTask(task.id);
      onChanged();
    } catch { /* ignore */ }
  };

  const doComplete = async () => {
    try {
      await completeTask(task.id);
      onChanged();
    } catch { /* ignore */ }
  };

  const done = task.status === "done";

  return (
    <div className="relative rounded-xl bg-bg-secondary border border-border overflow-hidden">
      {/* Priority gutter (full-height) */}
      <span
        className={`absolute left-0 top-0 bottom-0 w-[3px] ${PRIORITY_GUTTER[priority]}`}
        aria-hidden="true"
      />

      <div className="pl-4 pr-4 py-4 space-y-4">
        {/* Title (inline editable) */}
        <div className="flex items-start gap-3">
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={saveTitleIfChanged}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); (e.currentTarget as HTMLInputElement).blur(); } }}
            className={`flex-1 bg-transparent text-lg font-semibold focus:outline-none focus:ring-1 focus:ring-accent/40 rounded px-1 -mx-1 ${
              done ? "line-through text-text-muted" : "text-text-primary"
            }`}
          />
          {savingTitle && <span className="text-[10px] text-text-muted">saving…</span>}
          {task.owner === "agent" && (
            <span className="text-[9px] uppercase tracking-[0.1em] px-1.5 py-0.5 rounded bg-cyan-soft text-cyan">
              AI task
            </span>
          )}
        </div>

        {/* Meta row — priority chips, due date, reminder */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1">
            {PRIORITIES.map((p) => (
              <button
                key={p}
                onClick={async () => { setPriority(p); await patch({ priority: p }); }}
                className={`px-2 py-0.5 text-[10px] uppercase tracking-wider rounded-md border transition-colors ${
                  priority === p
                    ? PRIORITY_ACCENT[p]
                    : "border-border text-text-muted hover:text-text-secondary"
                }`}
              >
                {p}
              </button>
            ))}
          </div>

          <div className="ml-auto flex items-center gap-2 text-[11px]">
            <label className="flex items-center gap-1 text-text-muted">
              <span className="uppercase tracking-wider text-[10px]">Due</span>
              <input
                type="date"
                value={dueDate || ""}
                onChange={(e) => setDueDate(e.target.value)}
                onBlur={async () => {
                  if (dueDate !== (task.due_date ?? "")) {
                    await patch({ due_date: dueDate || null });
                  }
                }}
                className="bg-bg-tertiary border border-border rounded px-1.5 py-0.5 text-text-primary focus:outline-none focus:border-accent/40"
              />
            </label>
            <label className="flex items-center gap-1 text-text-muted">
              <span className="uppercase tracking-wider text-[10px]">Remind</span>
              <input
                type="datetime-local"
                value={reminderAt ? reminderAt.slice(0, 16) : ""}
                onChange={(e) => setReminderAt(e.target.value ? new Date(e.target.value).toISOString() : "")}
                onBlur={async () => {
                  if (reminderAt !== (task.reminder_at ?? "")) {
                    await patch({ reminder_at: reminderAt || null });
                  }
                }}
                className="bg-bg-tertiary border border-border rounded px-1.5 py-0.5 text-text-primary focus:outline-none focus:border-accent/40"
              />
            </label>
          </div>
        </div>

        {/* Steps */}
        <div>
          <div className="flex items-center gap-2 mb-2">
            <h3 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary">
              Steps
            </h3>
            {steps.length > 0 && (
              <span className="text-[10px] ticker text-text-muted">
                {steps.filter((s) => s.done).length}/{steps.length}
              </span>
            )}
          </div>
          <div className="space-y-1">
            {steps.map((s) => (
              <div
                key={s.id}
                className="group flex items-center gap-2 px-2 py-1 rounded hover:bg-bg-hover/40"
              >
                <button
                  onClick={() => toggleStep(s.id)}
                  className={`w-[13px] h-[13px] rounded border shrink-0 flex items-center justify-center transition-all ${
                    s.done ? "bg-accent border-accent" : "border-border bg-bg-tertiary hover:border-accent"
                  }`}
                  aria-label={s.done ? "Uncheck step" : "Check step"}
                >
                  {s.done && (
                    <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  )}
                </button>
                <span className={`text-[13px] flex-1 ${s.done ? "line-through text-text-muted" : "text-text-primary"}`}>
                  {s.title}
                </span>
                <button
                  onClick={() => removeStep(s.id)}
                  className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-[10px] text-text-muted hover:text-red-400 transition-all px-1"
                  aria-label="Remove step"
                >
                  ×
                </button>
              </div>
            ))}

            <form
              onSubmit={(e) => { e.preventDefault(); void addStep(); }}
              className="flex items-center gap-2 px-2 py-1 border-t border-border/50 pt-2"
            >
              <span className="w-[13px] h-[13px] rounded border border-dashed border-border/80 shrink-0" />
              <input
                type="text"
                value={newStep}
                onChange={(e) => setNewStep(e.target.value)}
                placeholder="+ add step"
                className="flex-1 bg-transparent text-[13px] text-text-primary placeholder:text-text-placeholder focus:outline-none"
              />
              {newStep.trim() && (
                <button
                  type="submit"
                  className="text-[10px] uppercase tracking-wider text-accent hover:text-accent-dim"
                >
                  add
                </button>
              )}
            </form>
          </div>
        </div>

        {/* Description */}
        <div>
          <h3 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary mb-1.5">
            Notes
          </h3>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            onBlur={async () => {
              if (description !== (task.description ?? "")) {
                await patch({ description: description || null });
              }
            }}
            placeholder="Add context, links, reminders for future you…"
            rows={3}
            className="w-full bg-bg-tertiary border border-border rounded-md px-3 py-2 text-[13px] text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-accent/40 resize-y"
          />
        </div>

        {/* Tags */}
        <div>
          <h3 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary mb-1.5">
            Tags
          </h3>
          <div className="flex items-center gap-1.5 flex-wrap">
            {tags.map((t) => (
              <span
                key={t}
                className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-md bg-bg-tertiary border border-border text-text-secondary"
              >
                #{t}
                <button
                  onClick={() => removeTag(t)}
                  className="text-text-muted hover:text-red-400"
                  aria-label={`Remove ${t}`}
                >
                  ×
                </button>
              </span>
            ))}
            <form onSubmit={(e) => { e.preventDefault(); void addTag(); }}>
              <input
                type="text"
                value={newTag}
                onChange={(e) => setNewTag(e.target.value)}
                placeholder="+ tag"
                className="text-[11px] bg-transparent border border-dashed border-border/60 rounded-md px-2 py-0.5 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/40 w-20"
              />
            </form>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 pt-2 border-t border-border/50">
          {!done && (
            <button
              onClick={doComplete}
              className="text-[10px] uppercase tracking-wider px-3 py-1.5 rounded-md bg-accent-soft text-accent hover:bg-accent hover:text-white transition-colors"
            >
              ✓ Mark done
            </button>
          )}
          <div className="ml-auto">
            {confirmDelete ? (
              <div className="flex items-center gap-1 text-[10px]">
                <span className="uppercase tracking-wider text-red-400">delete?</span>
                <button
                  onClick={doDelete}
                  className="uppercase tracking-wider px-2 py-1 rounded bg-red-900/30 text-red-400 hover:bg-red-900/60"
                >
                  yes
                </button>
                <button
                  onClick={() => setConfirmDelete(false)}
                  className="uppercase tracking-wider px-2 py-1 rounded text-text-muted hover:text-text-primary"
                >
                  no
                </button>
              </div>
            ) : (
              <button
                onClick={() => setConfirmDelete(true)}
                className="text-[10px] uppercase tracking-wider text-text-muted hover:text-red-400 transition-colors"
              >
                delete
              </button>
            )}
          </div>
        </div>

        {/* Metadata footer */}
        <div className="text-[10px] text-text-muted space-x-3">
          <span>ID <span className="font-mono">{task.id.slice(0, 8)}</span></span>
          <span>· created {new Date(task.created_at).toLocaleDateString()}</span>
          {task.recurring && <span>· recurring {task.recurring}</span>}
          {task.reminder_job_id && <span>· reminder scheduled</span>}
        </div>
      </div>
    </div>
  );
}
