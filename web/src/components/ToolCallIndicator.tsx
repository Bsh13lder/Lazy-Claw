interface ToolCallIndicatorProps {
  name: string;
  status: "running" | "done";
  preview?: string;
}

export default function ToolCallIndicator({
  name,
  status,
  preview,
}: ToolCallIndicatorProps) {
  const isTeam = name.startsWith("team:");
  const displayName = isTeam ? name.slice(5) : name;
  const icon = isTeam ? "group" : "tool";

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 my-1 rounded-lg bg-bg-tertiary/60 border border-border/50 text-xs animate-fade-in">
      {status === "running" ? (
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          className="spinner text-cyan shrink-0"
        >
          <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
        </svg>
      ) : (
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          className="text-accent shrink-0"
        >
          <path d="M20 6L9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}

      <span className="text-text-secondary">
        {icon === "group" ? "Delegated to " : "Using "}
        <span className="text-cyan font-medium">{displayName}</span>
      </span>

      {status === "done" && preview && (
        <span className="text-text-muted truncate max-w-[200px]">{preview}</span>
      )}
    </div>
  );
}
