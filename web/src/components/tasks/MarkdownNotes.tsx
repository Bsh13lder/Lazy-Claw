import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Task description editor with an edit/preview toggle.
 *
 * Starts in preview mode when the current description is non-empty (because
 * reading is the more common action) and in edit mode when it's blank (you
 * were about to type anyway). The caller persists on blur, same as the old
 * plain-textarea flow.
 */
export function MarkdownNotes({
  value,
  onChange,
  onBlur,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  onBlur: () => void;
  placeholder?: string;
}) {
  const [mode, setMode] = useState<"edit" | "preview">(value.trim() ? "preview" : "edit");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // When switching to edit, drop focus into the textarea.
  useEffect(() => {
    if (mode === "edit") {
      textareaRef.current?.focus();
    }
  }, [mode]);

  const empty = !value.trim();

  return (
    <div className="rounded-md border border-border bg-bg-tertiary overflow-hidden">
      <div className="flex items-center gap-1 px-2 py-1 bg-bg-secondary/50 border-b border-border/60">
        <button
          type="button"
          onClick={() => setMode("edit")}
          className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded transition-colors ${
            mode === "edit" ? "bg-accent-soft text-accent" : "text-text-muted hover:text-text-secondary"
          }`}
          aria-pressed={mode === "edit"}
        >
          Write
        </button>
        <button
          type="button"
          onClick={() => setMode("preview")}
          disabled={empty}
          className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
            mode === "preview" ? "bg-accent-soft text-accent" : "text-text-muted hover:text-text-secondary"
          }`}
          aria-pressed={mode === "preview"}
        >
          Preview
        </button>
        <span className="ml-auto text-[9px] uppercase tracking-wider text-text-muted">
          supports **bold** · `code` · - bullets · [links](url)
        </span>
      </div>

      {mode === "edit" ? (
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          placeholder={placeholder ?? "Add context, links, checklists, or reminders for future you…"}
          rows={4}
          className="w-full bg-transparent px-3 py-2 text-[13px] text-text-primary placeholder:text-text-placeholder focus:outline-none resize-y"
        />
      ) : (
        <div
          role="button"
          tabIndex={0}
          onClick={() => setMode("edit")}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setMode("edit");
            }
          }}
          className="markdown-content px-3 py-2 text-[13px] min-h-[64px] cursor-text"
          title="Click to edit"
        >
          {empty ? (
            <span className="text-text-placeholder">
              {placeholder ?? "No notes yet. Click to add…"}
            </span>
          ) : (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ href, children, ...props }) => (
                  <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
                    {children}
                  </a>
                ),
              }}
            >
              {value}
            </ReactMarkdown>
          )}
        </div>
      )}
    </div>
  );
}
