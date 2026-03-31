import { useCallback, useRef, useState } from "react";

interface ChatInputProps {
  onSend: (message: string) => void;
  disabled: boolean;
  isStreaming: boolean;
  onCancel: () => void;
}

const MAX_LENGTH = 50_000;

export default function ChatInput({
  onSend,
  disabled,
  isStreaming,
  onCancel,
}: ChatInputProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [value, disabled, onSend]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = () => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 200) + "px";
    }
  };

  const charCount = value.length;
  const showCharCount = charCount > 0;

  return (
    <div className="pb-4 pt-2 px-4 bg-bg-primary">
      <div className="max-w-3xl mx-auto relative">
        <div className="flex items-end bg-bg-tertiary rounded-2xl border border-border focus-within:border-border-light transition-colors">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onInput={handleInput}
            placeholder="Message LazyClaw..."
            disabled={disabled}
            maxLength={MAX_LENGTH}
            rows={1}
            className="flex-1 resize-none bg-transparent px-4 py-3.5 text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none disabled:opacity-50 max-h-[200px]"
          />

          {isStreaming ? (
            <button
              onClick={onCancel}
              className="m-2 p-1.5 rounded-lg bg-error/20 text-error hover:bg-error/30 transition-colors"
              aria-label="Stop generating"
              title="Stop generating"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={disabled || !value.trim()}
              className="m-2 p-1.5 rounded-lg bg-text-primary text-bg-primary disabled:opacity-20 disabled:cursor-not-allowed hover:opacity-80 transition-opacity"
              aria-label="Send"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 4l-1.41 1.41L16.17 11H4v2h12.17l-5.58 5.59L12 20l8-8z" />
              </svg>
            </button>
          )}
        </div>

        <div className="flex items-center justify-between mt-2 px-1">
          <p className="text-[11px] text-text-muted">
            LazyClaw can make mistakes. All messages are E2E encrypted.
          </p>
          {showCharCount && (
            <span
              className={`text-[11px] tabular-nums ${
                charCount > MAX_LENGTH * 0.9 ? "text-error" : "text-text-muted/60"
              }`}
            >
              {charCount.toLocaleString()}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
