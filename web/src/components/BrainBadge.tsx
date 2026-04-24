import { useEffect, useState } from "react";
import { getEcoSettings, getEcoModels } from "../api";

// Show which brain model is currently active for this logged-in user.
// Mirrors the Telegram footer so drift between channels is immediately visible.
// Resolution priority matches eco_router._resolve_models:
//   {mode}_brain_model  >  brain_model  >  mode_defaults[mode].brain
export default function BrainBadge() {
  const [label, setLabel] = useState<string>("");
  const [title, setTitle] = useState<string>("");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [eco, modelsData] = await Promise.all([
          getEcoSettings(),
          getEcoModels(),
        ]);
        if (cancelled) return;

        const mode = eco.mode || "hybrid";
        const perMode = eco[`${mode}_brain_model`] as string | null | undefined;
        const generic = eco.brain_model ?? undefined;
        const defaultBrain = modelsData.mode_defaults[mode]?.brain ?? "?";

        let brain: string;
        let source: string;
        if (perMode) {
          brain = perMode;
          source = `${mode}_brain_model`;
        } else if (generic) {
          brain = generic;
          source = "brain_model (generic)";
        } else {
          brain = defaultBrain;
          source = `mode default (${mode})`;
        }

        // Compact display — strip long vendor prefixes for the badge.
        const short = brain
          .replace(/^claude-/, "")
          .replace(/-\d{8}$/, "");
        setLabel(short);
        setTitle(`Brain: ${brain}\nSource: ${source}\nMode: ${mode}`);
      } catch {
        if (!cancelled) {
          setLabel("");
          setTitle("");
        }
      }
    }

    load();

    // Refresh when the user changes the model in Settings or switches mode.
    const onChange = () => load();
    window.addEventListener("lazyclaw:eco-changed", onChange);
    return () => {
      cancelled = true;
      window.removeEventListener("lazyclaw:eco-changed", onChange);
    };
  }, []);

  if (!label) return null;

  return (
    <span
      title={title}
      className="px-1.5 py-0.5 rounded-md bg-bg-tertiary border border-border text-[10px] font-mono text-text-muted truncate max-w-[120px]"
    >
      {label}
    </span>
  );
}
