import { useState } from "react";
import type { TemplateSuggest } from "../hooks/useChatStream";
import * as api from "../api";

interface Props {
  suggest: TemplateSuggest;
  onDismiss: () => void;
}

export default function TemplateSuggestBanner({ suggest, onDismiss }: Props) {
  const [name, setName] = useState(suggest.suggestedName);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const onSave = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    setResult(null);
    try {
      const r = await api.saveTemplateFromCurrentSession(trimmed);
      setResult(
        `Saved as '${r.template.name}'. ${r.captured.url_count} URL(s), ${r.captured.checkpoint_count} checkpoint(s) captured.`,
      );
      // Auto-dismiss after the success message has a moment to land.
      window.setTimeout(onDismiss, 2200);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setResult(`Could not save: ${msg}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border-b border-border bg-amber/5 px-3 py-2 flex flex-col gap-2">
      <div className="flex items-start gap-2">
        <span className="text-amber mt-[1px]">💡</span>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-medium text-text-primary">
            Save this as a template?
          </div>
          <div className="text-[11px] text-text-muted">
            {suggest.actionCount} action(s), {suggest.checkpoints.length} checkpoint(s),
            {" "}{suggest.setupUrls.length} URL(s).
          </div>
        </div>
        <button
          onClick={onDismiss}
          disabled={busy}
          className="p-1 rounded hover:bg-bg-hover text-text-muted"
          title="Dismiss"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="6" y1="6" x2="18" y2="18" />
            <line x1="6" y1="18" x2="18" y2="6" />
          </svg>
        </button>
      </div>
      <div className="flex items-center gap-1.5">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && name.trim() && !busy) {
              e.preventDefault();
              void onSave();
            }
          }}
          className="flex-1 text-[11px] px-2 py-1 rounded border border-border bg-bg-primary text-text-primary focus:outline-none focus:border-accent"
        />
        <button
          onClick={onSave}
          disabled={busy || !name.trim()}
          className="text-[11px] px-2.5 py-1 rounded bg-accent text-bg-primary font-medium disabled:opacity-50"
        >
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
      {result && (
        <div className="text-[11px] text-text-secondary">{result}</div>
      )}
    </div>
  );
}
