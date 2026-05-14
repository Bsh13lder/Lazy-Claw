import { useCallback, useEffect, useState } from "react";
import {
  getStreamingSettings,
  setStreamingSettings,
  type StreamingSettings,
} from "../api";

/** Header pill that toggles live background-task streaming in chat.
 *
 * States:
 *   - bg_streaming=true  → green "📡 Live" badge (verbose — see every bg
 *                          tool call + progress event while bg tasks run)
 *   - bg_streaming=false → muted "🤫 Quiet" badge (only the final
 *                          background_done card appears; chat stays calm
 *                          while bg work runs)
 *
 * Final completion notifications (background_done / background_failed)
 * always surface — the toggle only suppresses live progress chatter.
 */
export default function StreamingToggle() {
  const [settings, setSettings] = useState<StreamingSettings | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await getStreamingSettings();
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
      const next = await setStreamingSettings({
        bg_streaming: !settings.bg_streaming,
      });
      setSettings(next);
    } catch (err) {
      console.error("Streaming toggle failed", err);
    } finally {
      setBusy(false);
    }
  };

  if (!settings) return null;

  const on = settings.bg_streaming;
  const label = on ? "Live" : "Quiet";
  const icon = on ? "📡" : "🤫";
  const pillClass = on
    ? "border-accent/50 bg-accent/15 text-accent"
    : "border-text-muted/40 bg-text-muted/10 text-text-muted";
  const title = on
    ? "Background streaming ON — you'll see live tool calls and progress while background tasks run. Click to silence."
    : "Background streaming OFF — bg tasks run quietly; only the final result appears in chat. Click to enable live streaming.";

  return (
    <button
      onClick={toggle}
      disabled={busy}
      title={title}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-semibold uppercase tracking-wider transition-colors disabled:opacity-60 ${pillClass}`}
    >
      <span className="text-[11px] leading-none">{icon}</span>
      <span>{label}</span>
    </button>
  );
}
