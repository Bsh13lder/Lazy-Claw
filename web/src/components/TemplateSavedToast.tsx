import type { TemplateSavedToast as TemplateSavedInfo } from "../hooks/useChatStream";

interface Props {
  info: TemplateSavedInfo;
  onDismiss: () => void;
}

export default function TemplateSavedToast({ info, onDismiss }: Props) {
  const isCorrection = info.kind === "correction";
  const verb = isCorrection
    ? "Note added to"
    : info.wasCreated
      ? "Saved"
      : "Updated";
  const icon = isCorrection ? "📝" : info.wasCreated ? "💾" : "✨";
  const runSuffix =
    !isCorrection && !info.wasCreated && info.runCount > 1
      ? ` (run #${info.runCount})`
      : "";
  const subtitle = isCorrection
    ? `${info.correctionSource ?? "user"} • ${info.correctionsPending ?? 1} pending`
    : info.host;

  return (
    <div className="border-b border-border bg-accent/5 px-3 py-2 flex items-center gap-2 animate-in fade-in slide-in-from-top-1">
      <span className="text-base shrink-0">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium text-text-primary truncate">
          {verb} template{" "}
          <span className="text-accent">
            {info.icon ? `${info.icon} ` : ""}
            {info.name}
          </span>
          {runSuffix}
        </div>
        {subtitle && (
          <div className="text-[11px] text-text-muted truncate">
            {subtitle}
          </div>
        )}
      </div>
      <a
        href={`/?page=templates`}
        className="text-[11px] px-2 py-0.5 rounded border border-border text-text-secondary hover:bg-bg-hover"
        title="Open Templates page"
      >
        View
      </a>
      <button
        onClick={onDismiss}
        className="p-1 rounded hover:bg-bg-hover text-text-muted"
        title="Dismiss"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="6" y1="6" x2="18" y2="18" />
          <line x1="6" y1="18" x2="18" y2="6" />
        </svg>
      </button>
    </div>
  );
}
