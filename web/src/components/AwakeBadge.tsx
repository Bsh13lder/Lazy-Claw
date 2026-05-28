import { useEffect, useRef, useState } from "react";
import * as api from "../api";
import type { AwakeStatus } from "../api";

export default function AwakeBadge() {
  const [status, setStatus] = useState<AwakeStatus | null>(null);
  const [toggling, setToggling] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchStatus = async () => {
    try {
      const s = await api.getAwakeStatus();
      setStatus(s);
    } catch {
      // bridge not installed or unreachable — show neutral state
    }
  };

  useEffect(() => {
    fetchStatus();
    intervalRef.current = setInterval(fetchStatus, 30_000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  const handleToggle = async () => {
    if (!status || toggling) return;
    setToggling(true);
    try {
      const next = !status.caffeinate_running;
      await api.toggleAwakeMode(next);
      await fetchStatus();
    } catch {
      // ignore — tooltip will show stale state
    } finally {
      setToggling(false);
    }
  };

  if (!status) return null;

  const isAwake = status.caffeinate_running;
  const bridgeOk = status.bridge_reachable;
  const onBattery = status.on_ac_power === false;
  const pct = status.battery_percent;

  let tooltip = bridgeOk
    ? isAwake
      ? "Awake mode ON — lid-closed sleep is blocked. Click to allow sleep."
      : "Awake mode OFF — machine can sleep. Click to keep awake."
    : "Awake bridge not installed. Run `make awake-bridge` on the host.";

  if (onBattery && isAwake) {
    tooltip += " ⚠ On battery — lid-close still sleeps; plug in.";
  }
  if (status.daily_wake) {
    tooltip += ` Daily wake at ${status.daily_wake}.`;
  }
  if (pct !== null) {
    tooltip += ` Battery: ${pct}%.`;
  }

  return (
    <button
      onClick={handleToggle}
      disabled={!bridgeOk || toggling}
      title={tooltip}
      aria-label={tooltip}
      className={[
        "flex items-center gap-1 px-2 py-1 rounded-lg text-xs transition-colors",
        bridgeOk
          ? "hover:bg-bg-hover cursor-pointer"
          : "opacity-40 cursor-not-allowed",
      ].join(" ")}
    >
      {toggling ? (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner text-text-muted">
          <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
        </svg>
      ) : isAwake ? (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" className="text-green-400">
          <circle cx="12" cy="12" r="5" />
        </svg>
      ) : (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-text-muted">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      )}
      <span className={isAwake ? "text-green-400" : "text-text-muted"}>
        {isAwake ? "awake" : "sleep ok"}
      </span>
    </button>
  );
}
