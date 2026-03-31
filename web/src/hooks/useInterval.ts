import { useEffect, useRef } from "react";

/**
 * Calls `callback` every `delayMs` milliseconds.
 * Pass `null` for delayMs to pause.
 * The callback is NOT called immediately — only after the first interval.
 */
export function useInterval(callback: () => void, delayMs: number | null) {
  const savedCallback = useRef(callback);

  // Always keep latest callback ref
  useEffect(() => {
    savedCallback.current = callback;
  }, [callback]);

  useEffect(() => {
    if (delayMs === null) return;
    const id = setInterval(() => savedCallback.current(), delayMs);
    return () => clearInterval(id);
  }, [delayMs]);
}
