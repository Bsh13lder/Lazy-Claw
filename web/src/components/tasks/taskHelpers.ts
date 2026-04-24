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

/**
 * How often a chip bound to this deadline should re-render so the relative
 * label stays accurate. Fine-grained (30s) when the deadline is inside today
 * or in minutes/hours; coarse (5min) for anything further out. 0 means "no
 * ticking needed" — no deadline at all.
 */
export function COUNTDOWN_REFRESH_MS(
  dueDate: string | null,
  reminderAt: string | null,
): number {
  if (!dueDate && !reminderAt) return 0;

  if (reminderAt) {
    const when = new Date(reminderAt).getTime();
    if (!Number.isNaN(when)) {
      const diff = Math.abs(when - Date.now());
      if (diff < 24 * 3_600_000) return 30_000;
      return 300_000;
    }
  }

  if (dueDate) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const d = new Date(`${dueDate}T00:00:00`).getTime();
    if (!Number.isNaN(d)) {
      const diffDays = Math.abs(d - today.getTime()) / 864e5;
      if (diffDays <= 1) return 60_000;
      return 300_000;
    }
  }
  return 0;
}

/** Date-ish keywords (EN + ES) used to decide if we should auto-fire the AI
 * parser when the fast regex misses. Kept here so both QuickAddBar and its
 * tests can import it. */
export const DATE_HINT_RE =
  /\b(tomorrow|today|tonight|morning|evening|afternoon|yesterday|next|in|after|before|at|on|mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december|am|pm|\d+\s*(?:h|hr|hour|hours|m|min|mins|minute|minutes|d|day|days|w|wk|week|weeks|mo|month|months)|manana|ma[nñ]ana|hoy|ayer|proximo|pr[oó]xima|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|hora|horas|d[ií]a|d[ií]as|semana|mes)\b/i;

export function parseTags(raw: string | null | undefined): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}
