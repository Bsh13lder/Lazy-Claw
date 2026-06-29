/**
 * Pure quick-date math for the fast reschedule chips.
 *
 * The core rule (shared verbatim with mobile — `mobile/lib/core/reschedule_dates.dart`):
 *
 *   • `due_date`    = the target day, **date-only `YYYY-MM-DD`** — keeps the
 *     backend bucket/overdue queries (lexical `due_date <= today_str`) correct.
 *   • `reminder_at` = the target day **at 10:00 local**, ISO datetime
 *     `YYYY-MM-DDT10:00:00` — this carries the "10am" and fires the nag.
 *
 * All math is done in the user's LOCAL time (getFullYear/getMonth/getDate),
 * never UTC, so there is no day-boundary drift. Every function accepts an
 * injectable `now` so the helpers are deterministically unit-testable.
 */

/** Default reschedule hour, local time. */
export const DEFAULT_RESCHEDULE_HOUR = 10;

/** Strip a Date down to local midnight of the same calendar day. */
function atLocalMidnight(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

/** Add `days` calendar days, returning a fresh Date at local midnight. */
function addDays(d: Date, days: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + days);
}

/** Tomorrow = today + 1 day, at local midnight. */
export function nextTomorrow(now: Date = new Date()): Date {
  return addDays(now, 1);
}

/**
 * The coming Saturday (local). If today IS Saturday → today; if Sunday → the
 * next Saturday (6 days out). `getDay()`: Sun=0 … Sat=6.
 */
export function thisWeekend(now: Date = new Date()): Date {
  const today = atLocalMidnight(now);
  const dow = today.getDay();
  const SATURDAY = 6;
  // Days until the coming Saturday: Sat → 0, Sun → 6, else 6 - dow.
  const delta = dow === SATURDAY ? 0 : (SATURDAY - dow + 7) % 7;
  return addDays(today, delta);
}

/**
 * The coming Monday (local). If today IS Monday → next Monday (7 days out);
 * otherwise the next Monday strictly after today. `getDay()`: Sun=0 … Mon=1.
 */
export function nextWeek(now: Date = new Date()): Date {
  const today = atLocalMidnight(now);
  const dow = today.getDay();
  const MONDAY = 1;
  // Strictly-after-today Monday: Mon → 7, else the upcoming Monday.
  const raw = (MONDAY - dow + 7) % 7;
  const delta = raw === 0 ? 7 : raw;
  return addDays(today, delta);
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/** Local date-only `YYYY-MM-DD` for a Date. */
export function toDateString(day: Date): string {
  return `${day.getFullYear()}-${pad2(day.getMonth() + 1)}-${pad2(day.getDate())}`;
}

/**
 * Resolve a target DAY to the deterministic reschedule payload:
 *   { due_date: "YYYY-MM-DD", reminder_at: "YYYY-MM-DDT10:00:00" }
 *
 * `hour` defaults to 10 (the "boom, 10am" behaviour); pass another value to
 * honour a user-chosen time. The reminder is always built from the day's
 * calendar date, never the day's own clock time, so a non-midnight input Date
 * still lands the reminder at the intended hour.
 */
export function toQuickReschedule(
  day: Date,
  hour: number = DEFAULT_RESCHEDULE_HOUR,
  minute: number = 0,
): { due_date: string; reminder_at: string } {
  const dateStr = toDateString(day);
  const reminderAt = `${dateStr}T${pad2(hour)}:${pad2(minute)}:00`;
  return { due_date: dateStr, reminder_at: reminderAt };
}

/** Build a reschedule payload from a `<input type="date">` value (`YYYY-MM-DD`). */
export function toQuickRescheduleFromInput(
  isoDay: string,
  hour: number = DEFAULT_RESCHEDULE_HOUR,
  minute: number = 0,
): { due_date: string; reminder_at: string } {
  // Parse as a LOCAL calendar date (appending the time forces local, not UTC).
  const day = new Date(`${isoDay}T00:00:00`);
  return toQuickReschedule(day, hour, minute);
}

export interface QuickDate {
  label: string;
  fn: (now?: Date) => Date;
}

export const QUICK_DATES: QuickDate[] = [
  { label: "Tomorrow", fn: nextTomorrow },
  { label: "This weekend", fn: thisWeekend },
  { label: "Next week", fn: nextWeek },
];
