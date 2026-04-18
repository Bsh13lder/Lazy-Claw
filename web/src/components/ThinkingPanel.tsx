import { useState } from "react";

interface ThinkingPanelProps {
  content: string;
  done: boolean;
}

/** Collapsible reasoning panel — shows the model's internal thinking
 *  tokens (`<think>...</think>`) in a dim, styled container so the user
 *  can expand it for transparency without it cluttering the chat.
 *
 *  Auto-hides when there's nothing to show.
 */
export default function ThinkingPanel({ content, done }: ThinkingPanelProps) {
  const [expanded, setExpanded] = useState(false);

  const trimmed = content.trim();
  if (!trimmed) return null;

  const charCount = trimmed.length;
  const preview = trimmed.slice(0, 70).replace(/\s+/g, " ");

  return (
    <div className="rounded-md border border-purple-400/30 bg-purple-400/5 overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-2 py-1.5 text-[10px] text-purple-300 hover:bg-purple-400/10 transition-colors"
        title={expanded ? "Hide reasoning" : "Show reasoning"}
      >
        <span className="shrink-0">💭</span>
        <span className="font-medium">
          {done ? "Thinking" : "Thinking…"}
        </span>
        <span className="text-purple-300/60 tabular-nums shrink-0">
          {charCount} chars
        </span>
        {!expanded && (
          <span className="truncate text-text-muted italic flex-1 text-left min-w-0">
            {preview}
            {trimmed.length > 70 ? "…" : ""}
          </span>
        )}
        <svg
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          className={`shrink-0 transition-transform ${
            expanded ? "rotate-90" : ""
          }`}
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>
      {expanded && (
        <div className="px-3 py-2 border-t border-purple-400/20 max-h-64 overflow-y-auto">
          <pre className="text-[11px] leading-relaxed text-text-muted italic whitespace-pre-wrap font-sans">
            {trimmed}
          </pre>
        </div>
      )}
    </div>
  );
}
