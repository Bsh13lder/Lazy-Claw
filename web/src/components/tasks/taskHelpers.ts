import type { TaskItem } from "../../api";

export type Priority = TaskItem["priority"];

export const PRIORITY_RANK: Record<Priority, number> = {
  urgent: 0,
  high: 1,
  medium: 2,
  low: 3,
};

export const PRIORITY_GUTTER: Record<Priority, string> = {
  urgent: "bg-red-400",
  high: "bg-amber",
  medium: "bg-accent/60",
  low: "bg-transparent",
};

export const PRIORITY_LABEL: Record<Priority, string> = {
  urgent: "URGENT",
  high: "HIGH",
  medium: "MED",
  low: "LOW",
};

export const PRIORITY_TEXT: Record<Priority, string> = {
  urgent: "text-red-400",
  high: "text-amber",
  medium: "text-text-secondary",
  low: "text-text-muted",
};

/**
 * Format a due_date (YYYY-MM-DD) + optional reminder_at (ISO) into a tight
 * human-readable chip like "in 2h", "today 09:00", "tomorrow", "3d overdue".
 */
export function formatDueChip(
  dueDate: string | null,
  reminderAt: string | null,
): { label: string; tone: "overdue" | "soon" | "future" | "none" } {
  if (!dueDate && !reminderAt) return { label: "", tone: "none" };

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  // If we have a reminder_at with a concrete time, use it for the chip —
  // it's more specific than the bare date.
  if (reminderAt) {
    const when = new Date(reminderAt);
    if (!Number.isNaN(when.getTime())) {
      const diffMs = when.getTime() - Date.now();
      const abs = Math.abs(diffMs);
      if (diffMs < 0) {
        if (abs < 3_600_000) return { label: `${Math.round(abs / 60_000)}m overdue`, tone: "overdue" };
        if (abs < 864e5) return { label: `${Math.round(abs / 3_600_000)}h overdue`, tone: "overdue" };
        return { label: `${Math.round(abs / 864e5)}d overdue`, tone: "overdue" };
      }
      const timeStr = when.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
      if (abs < 3_600_000) return { label: `in ${Math.max(1, Math.round(abs / 60_000))}m`, tone: "soon" };
      if (abs < 24 * 3_600_000) {
        const sameDay = when.toDateString() === new Date().toDateString();
        return { label: sameDay ? `today ${timeStr}` : `in ${Math.round(abs / 3_600_000)}h`, tone: "soon" };
      }
      if (abs < 7 * 864e5) {
        const tomorrow = new Date(today);
        tomorrow.setDate(tomorrow.getDate() + 1);
        if (when.toDateString() === tomorrow.toDateString()) {
          return { label: `tomorrow ${timeStr}`, tone: "soon" };
        }
        return {
          label: when.toLocaleDateString(undefined, { weekday: "short" }) + " " + timeStr,
          tone: "future",
        };
      }
      return {
        label: when.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
        tone: "future",
      };
    }
  }

  if (dueDate) {
    const d = new Date(`${dueDate}T00:00:00`);
    if (Number.isNaN(d.getTime())) return { label: dueDate, tone: "none" };
    const diffDays = Math.round((d.getTime() - today.getTime()) / 864e5);
    if (diffDays < 0) return { label: `${Math.abs(diffDays)}d overdue`, tone: "overdue" };
    if (diffDays === 0) return { label: "today", tone: "soon" };
    if (diffDays === 1) return { label: "tomorrow", tone: "soon" };
    if (diffDays < 7) return { label: `in ${diffDays}d`, tone: "future" };
    return {
      label: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
      tone: "future",
    };
  }

  return { label: "", tone: "none" };
}

export const DUE_TONE_CLASS: Record<"overdue" | "soon" | "future" | "none", string> = {
  overdue: "text-red-400",
  soon: "text-amber",
  future: "text-text-secondary",
  none: "text-text-muted",
};

export function parseTags(raw: string | null | undefined): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}
