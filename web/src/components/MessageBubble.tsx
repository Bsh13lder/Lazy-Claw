import { useCallback, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { ToolCallInfo } from "../hooks/useChatStream";
import ToolCallIndicator from "./ToolCallIndicator";

interface MessageBubbleProps {
  role: "user" | "assistant";
  content: string;
  timestamp?: number;
  toolCalls?: ToolCallInfo[];
  isStreaming?: boolean;
}

function formatTime(ts: number): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(ts);
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [text]);

  return (
    <button
      onClick={handleCopy}
      className="absolute top-2 right-2 px-2 py-1 rounded-md bg-bg-hover/80 text-text-muted hover:text-text-secondary text-[11px] opacity-0 group-hover/code:opacity-100 transition-opacity"
      title="Copy code"
    >
      {copied ? "Copied!" : "Copy"}
    </button>
  );
}

export default function MessageBubble({
  role,
  content,
  timestamp,
  toolCalls,
  isStreaming,
}: MessageBubbleProps) {
  const isUser = role === "user";

  return (
    <div className="animate-fade-in py-4 group">
      <div className="max-w-3xl mx-auto flex gap-4 px-4">
        {/* Avatar */}
        <div className="shrink-0 mt-0.5">
          {isUser ? (
            <div className="w-7 h-7 rounded-full bg-bg-user flex items-center justify-center">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-text-secondary">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                <circle cx="12" cy="7" r="4" />
              </svg>
            </div>
          ) : (
            <div className="w-7 h-7 rounded-full bg-accent-soft flex items-center justify-center">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
                <rect x="3" y="11" width="18" height="11" rx="2" />
                <path d="M7 11V7a5 5 0 0110 0v4" />
              </svg>
            </div>
          )}
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-medium text-text-muted">
              {isUser ? "You" : "LazyClaw"}
            </span>
            {timestamp && (
              <span className="text-[11px] text-text-muted/60">
                {formatTime(timestamp)}
              </span>
            )}
          </div>

          {/* Tool calls */}
          {toolCalls && toolCalls.length > 0 && (
            <div className="mb-2">
              {toolCalls.map((tc, i) => (
                <ToolCallIndicator
                  key={`${tc.name}-${i}`}
                  name={tc.name}
                  status={tc.status}
                  preview={tc.preview}
                />
              ))}
            </div>
          )}

          {isUser ? (
            <p className="text-[14.5px] leading-7 text-text-primary whitespace-pre-wrap">
              {content}
            </p>
          ) : (
            <div className="markdown-content text-text-primary">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
                components={{
                  pre({ children, ...props }) {
                    // Extract code text for copy button
                    const codeText = extractText(children);
                    return (
                      <div className="relative group/code">
                        <CopyButton text={codeText} />
                        <pre {...props}>{children}</pre>
                      </div>
                    );
                  },
                }}
              >
                {content}
              </ReactMarkdown>
              {isStreaming && <span className="typing-cursor" />}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Recursively extract text content from React children.
 * Used to get the raw code text for the copy button.
 */
function extractText(node: unknown): string {
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (!node) return "";
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (typeof node === "object" && "props" in (node as Record<string, unknown>)) {
    const props = (node as { props?: { children?: unknown } }).props;
    return extractText(props?.children);
  }
  return "";
}
