/**
 * Tiny cron → human-readable helper. No external dep.
 * Recognises the most common 5-field patterns LazyClaw drafts; falls back
 * to the raw expression when nothing matches so weird crons stay visible.
 */

const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function pad(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

export function cronToHuman(expr: string | null | undefined): string | null {
  if (!expr) return null;
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return null;

  const [min, hour, dom, mon, dow] = parts;

  // Every N minutes: */N * * * *
  const everyMin = /^\*\/(\d+)$/.exec(min);
  if (everyMin && hour === "*" && dom === "*" && mon === "*" && dow === "*") {
    return `Every ${everyMin[1]} min`;
  }

  // Every N hours at minute 0: 0 */N * * *
  const everyHour = /^\*\/(\d+)$/.exec(hour);
  if (min === "0" && everyHour && dom === "*" && mon === "*" && dow === "*") {
    return `Every ${everyHour[1]} hours`;
  }

  // Specific time daily: M H * * *
  const isInt = (s: string) => /^\d+$/.test(s);
  if (isInt(min) && isInt(hour) && dom === "*" && mon === "*") {
    const time = `${pad(parseInt(hour, 10))}:${pad(parseInt(min, 10))}`;
    if (dow === "*") return `Daily at ${time}`;
    if (dow === "1-5") return `Weekdays at ${time}`;
    if (dow === "0,6" || dow === "6,0") return `Weekends at ${time}`;
    if (isInt(dow)) {
      const idx = parseInt(dow, 10) % 7;
      return `${DAY_NAMES[idx]}s at ${time}`;
    }
  }

  return null;
}
