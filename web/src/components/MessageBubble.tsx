import { useCallback, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { ToolCallInfo } from "../hooks/useChatStream";
import ToolCallCard from "./ToolCallCard";

interface MessageBubbleProps {
  role: "user" | "assistant";
  content: string;
  timestamp?: number;
  toolCalls?: ToolCallInfo[];
  isStreaming?: boolean;
  tokens?: number;
  cost?: number;
  model?: string;
  latency_ms?: number;
  // ECO router fallback surfacing — see agent.py "done" event + chat_ws payload.
  fallbackReason?: string;
  modelUsed?: string;
}

const FALLBACK_REASON_LABELS: Record<string, string> = {
  overloaded: "Sonnet overloaded",
  auth: "auth error",
  cli_failed: "CLI failed",
  local_failed: "local model failed",
  worker_failed: "worker failed",
};

function friendlyModel(raw?: string): string | undefined {
  if (!raw) return undefined;
  const m = raw.toLowerCase();
  if (m.includes("sonnet-4-6") || m.includes("sonnet-4.6")) return "Sonnet 4.6";
  if (m.includes("haiku-4-5") || m.includes("haiku-4.5")) return "Haiku 4.5";
  if (m.includes("opus")) return "Opus";
  if (m.includes("sonnet")) return "Sonnet";
  if (m.includes("haiku")) return "Haiku";
  if (m.includes("gemma") || m.includes("e2b")) return "Gemma E2B";
  if (m === "claude-cli") return "Claude CLI";
  if (m === "unknown" || m === "error") return undefined;
  return raw.replace("claude-", "").split("-2025")[0].slice(0, 24);
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
    }).catch(() => { /* clipboard denied — non-HTTPS or permission blocked */ });
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

function formatCost(cost: number): string {
  if (cost === 0) return "Free";
  if (cost < 0.001) return `$${cost.toFixed(5)}`;
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(3)}`;
}

export default function MessageBubble({
  role,
  content,
  timestamp,
  toolCalls,
  isStreaming,
  tokens,
  cost,
  model,
  latency_ms,
  fallbackReason,
  modelUsed,
}: MessageBubbleProps) {
  const isUser = role === "user";
  const hasMeta = !isUser && !isStreaming && (tokens != null || cost != null || model != null);
  const showFallbackChip = !isUser && !isStreaming && !!fallbackReason;

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
            <div className="mb-2 space-y-0.5">
              {toolCalls.map((tc, i) => (
                <ToolCallCard key={`${tc.name}-${i}`} tool={tc} />
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
                  // All URLs from the agent (e.g. OAuth consent links) must
                  // open in a new tab so the chat session isn't destroyed
                  // when the user clicks through to Google / external site.
                  a({ href, children, ...props }) {
                    return (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        {...props}
                      >
                        {children}
                      </a>
                    );
                  },
                }}
              >
                {content}
              </ReactMarkdown>
              {isStreaming && <span className="typing-cursor" />}
            </div>
          )}

          {/* Fallback chip — always visible when the router swapped models */}
          {showFallbackChip && (
            <div className="mt-2 flex items-center gap-2">
              <span
                className="msg-meta-pill bg-amber-soft text-amber"
                title={
                  "The ECO router had to fall back because your brain model " +
                  "was unavailable. See Settings → ECO to configure."
                }
              >
                ⚠️ fallback → {friendlyModel(modelUsed) || "?"} (
                {FALLBACK_REASON_LABELS[fallbackReason!] ?? fallbackReason})
              </span>
            </div>
          )}

          {/* Token / cost / model metadata */}
          {hasMeta && (
            <div className="msg-meta mt-2 opacity-0 group-hover:opacity-100 transition-opacity">
              {model && (
                <span className="msg-meta-pill bg-bg-tertiary text-text-muted">
                  {model}
                </span>
              )}
              {tokens != null && tokens > 0 && (
                <span className="msg-meta-pill bg-bg-tertiary text-text-muted">
                  {tokens.toLocaleString()} tok
                </span>
              )}
              {cost != null && (
                <span className={`msg-meta-pill ${cost === 0 ? "bg-accent-soft text-accent" : "bg-amber-soft text-amber"}`}>
                  {formatCost(cost)}
                </span>
              )}
              {latency_ms != null && latency_ms > 0 && (
                <span className="msg-meta-pill bg-bg-tertiary text-text-muted">
                  {latency_ms < 1000 ? `${latency_ms}ms` : `${(latency_ms / 1000).toFixed(1)}s`} TTFT
                </span>
              )}
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
