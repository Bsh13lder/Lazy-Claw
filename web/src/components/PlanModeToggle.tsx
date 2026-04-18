import { useCallback, useEffect, useState } from "react";
import { getPlanSettings, setPlanSettings, type PlanSettings } from "../api";

/** Small header pill that shows + toggles Plan Mode.
 *
 * States:
 *   - auto_plan=true  → green "🛡 Plan" badge (agent shows plan before acting)
 *   - auto_plan=false → amber "⚡ Auto" badge (agent executes directly)
 *   - session_auto_approve=true → extra "30m" sub-badge w/ click-to-clear
 */
export default function PlanModeToggle() {
  const [settings, setSettings] = useState<PlanSettings | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await getPlanSettings();
      setSettings(s);
    } catch {
      // Not authenticated yet, or backend not up — stay silent.
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 60_000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const toggle = async () => {
    if (busy || !settings) return;
    setBusy(true);
    try {
      const next = await setPlanSettings({ auto_plan: !settings.auto_plan });
      setSettings(next);
    } catch (err) {
      console.error("Plan toggle failed", err);
    } finally {
      setBusy(false);
    }
  };

  const clearTrust = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    try {
      const next = await setPlanSettings({ clear_session_trust: true });
      setSettings(next);
    } catch (err) {
      console.error("Clear trust failed", err);
    } finally {
      setBusy(false);
    }
  };

  if (!settings) return null;

  const on = settings.auto_plan;
  const label = on ? "Plan" : "Auto";
  const icon = on ? "🛡" : "⚡";
  const pillClass = on
    ? "border-accent/50 bg-accent/15 text-accent"
    : "border-amber/50 bg-amber/15 text-amber";
  const title = on
    ? "Plan Mode ON — LazyClaw will show a plan and wait for approval before running tools. Click to switch to Auto."
    : "Auto Mode — LazyClaw executes directly without asking. Click to turn Plan Mode back on.";

  return (
    <div className="flex items-center gap-1">
      <button
        onClick={toggle}
        disabled={busy}
        title={title}
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-semibold uppercase tracking-wider transition-colors disabled:opacity-60 ${pillClass}`}
      >
        <span className="text-[11px] leading-none">{icon}</span>
        <span>{label}</span>
      </button>
      {settings.session_auto_approve && (
        <button
          onClick={clearTrust}
          disabled={busy}
          title="Session trust active — next plans auto-approve. Click to clear."
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full border border-cyan/40 bg-cyan/10 text-cyan text-[10px] font-medium hover:bg-cyan/20 disabled:opacity-60 transition-colors"
        >
          30m ×
        </button>
      )}
    </div>
  );
}
