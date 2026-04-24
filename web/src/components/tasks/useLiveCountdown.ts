import { useEffect, useState } from "react";
import { COUNTDOWN_REFRESH_MS } from "./taskHelpers";

/**
 * Re-renders the calling component on a cadence tuned to how close the
 * deadline is. Same-day / reminder rows tick every 30–60s; further-out rows
 * tick every 5 min; rows with no deadline don't tick at all.
 *
 * The caller doesn't need the returned value — the point is just to bump
 * React so `formatDueChip()` recomputes "in 3m → in 2m".
 */
export function useLiveCountdown(
  dueDate: string | null,
  reminderAt: string | null,
): void {
  const [, setTick] = useState(0);

  useEffect(() => {
    const interval = COUNTDOWN_REFRESH_MS(dueDate, reminderAt);
    if (!interval) return;
    const id = window.setInterval(() => setTick((n) => (n + 1) % 1_000_000), interval);
    return () => window.clearInterval(id);
  }, [dueDate, reminderAt]);
}
