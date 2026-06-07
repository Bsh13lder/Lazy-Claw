import { useCallback, useEffect, useState } from "react";
import { getGeneralSettings, updateGeneralSettings, type AgentMode } from "../api";

/** Segmented operating-mode switch for the chat header (ADR-0005, Phase 3).
 *
 * Four postures layered OVER the per-skill allow/ask/deny permission system:
 *   - Chat    → conversation only; no tools fire.
 *   - Ask     → acts, but confirms each write/dispatch (one-tap ✅/❌).
 *   - Plan    → read-only research → numbered plan → single Execute tap.
 *   - Execute → fully autonomous; fans agents and works, no asking. (value "auto")
 *
 * Persisted at user settings `general.agent_mode` via the same general-settings
 * PATCH mechanism the ECO-mode / search-provider switches use. Defaults to
 * "ask" when the backend omits the field. Polls every 60s so a change made on
 * Telegram / mobile reflects here too.
 */

interface ModeDef {
  readonly value: AgentMode;
  readonly label: string;
  readonly title: string;
}

const MODES: readonly ModeDef[] = [
  {
    value: "chat",
    label: "Chat",
    title: "Chat — conversation only; no tools fire.",
  },
  {
    value: "ask",
    label: "Ask",
    title: "Ask — acts, but confirms each write/dispatch (one-tap ✅/❌).",
  },
  {
    value: "plan",
    label: "Plan",
    title: "Plan — read-only research → numbered plan → single Execute tap → autonomous run.",
  },
  {
    value: "auto",
    label: "Execute",
    title: "Execute — fully autonomous; fans agents and works, no asking.",
  },
] as const;

export default function ModeSwitch() {
  const [mode, setMode] = useState<AgentMode | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await getGeneralSettings();
      setMode(s.agent_mode ?? "ask");
    } catch {
      // Not authenticated yet, or backend not up — stay silent.
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 60_000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const select = async (next: AgentMode) => {
    if (busy || next === mode) return;
    const prev = mode;
    setMode(next); // optimistic
    setBusy(true);
    try {
      const updated = await updateGeneralSettings({ agent_mode: next });
      setMode(updated.agent_mode ?? next);
    } catch {
      setMode(prev); // rollback on failure
    } finally {
      setBusy(false);
    }
  };

  if (!mode) return null;

  return (
    <div
      role="radiogroup"
      aria-label="Operating mode"
      className="inline-flex items-center gap-0.5 rounded-full border border-border bg-bg-tertiary p-0.5 shrink-0"
    >
      {MODES.map((m) => {
        const active = mode === m.value;
        return (
          <button
            key={m.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => select(m.value)}
            disabled={busy}
            title={m.title}
            className={`px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider transition-colors disabled:opacity-60 ${
              active
                ? "bg-accent text-bg-primary"
                : "text-text-muted hover:text-text-secondary"
            }`}
          >
            {m.label}
          </button>
        );
      })}
    </div>
  );
}
