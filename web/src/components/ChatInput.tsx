import { useCallback, useEffect, useMemo, useRef, useState } from "react";

interface ChatInputProps {
  onSend: (message: string) => void;
  disabled: boolean;
  isStreaming: boolean;
  onCancel: () => void;
}

const MAX_LENGTH = 50_000;

/* ── Slash command definitions ─────────────────────────────────────── */

interface SlashCommand {
  cmd: string;
  args?: string;
  desc: string;
  category: "general" | "ai" | "tools" | "channels" | "admin";
}

const SLASH_COMMANDS: SlashCommand[] = [
  // General
  { cmd: "/help", desc: "Show all available commands", category: "general" },
  { cmd: "/status", desc: "Live status of running tasks", category: "general" },
  { cmd: "/tasks", desc: "List background tasks with cancel", category: "general" },
  { cmd: "/cancel", args: "<name>", desc: "Cancel a running task", category: "general" },
  { cmd: "/history", desc: "Show recent messages", category: "general" },
  { cmd: "/wipe", desc: "Clear conversation history", category: "general" },
  { cmd: "/usage", desc: "Token costs & API usage stats", category: "general" },
  { cmd: "/logs", desc: "Show activity logs", category: "general" },
  { cmd: "/doctor", desc: "System diagnostics", category: "general" },
  { cmd: "/ram", desc: "Show RAM usage", category: "general" },
  { cmd: "/recovery", desc: "Recovery phrase management", category: "general" },

  // AI / Model
  { cmd: "/mode", args: "<hybrid|full|claude>", desc: "Set AI routing mode", category: "ai" },
  { cmd: "/model", args: "[brain|worker|fallback]", desc: "Show or change AI models", category: "ai" },
  { cmd: "/key", args: "<set|list|delete>", desc: "Manage API keys", category: "ai" },
  { cmd: "/local", args: "<on|off|restart|status>", desc: "Local AI server control", category: "ai" },
  { cmd: "/search", args: "<provider>", desc: "Configure search provider", category: "ai" },

  // Tools
  { cmd: "/mcp", args: "[list|connect|status]", desc: "Manage MCP servers", category: "tools" },
  { cmd: "/browser", desc: "Browser automation control", category: "tools" },
  { cmd: "/screen", desc: "Desktop screenshot", category: "tools" },
  { cmd: "/watch", args: "[create|list|stop]", desc: "Create/manage watchers", category: "tools" },
  { cmd: "/survival", desc: "Toggle job hunting mode", category: "tools" },
  { cmd: "/profile", desc: "Manage freelance profile", category: "tools" },

  // Channels
  { cmd: "/whatsapp", desc: "WhatsApp setup & status", category: "channels" },
  { cmd: "/instagram", desc: "Instagram setup & status", category: "channels" },
  { cmd: "/email", desc: "Email setup & status", category: "channels" },
  { cmd: "/qr", desc: "WhatsApp QR re-link", category: "channels" },

  // Admin
  { cmd: "/resetpass", desc: "Reset web UI password", category: "admin" },
  { cmd: "/addadmin", args: "<username>", desc: "Add admin user", category: "admin" },
  { cmd: "/removeadmin", args: "<username>", desc: "Remove admin user", category: "admin" },
  { cmd: "/nuke", desc: "Complete data wipe (irreversible!)", category: "admin" },
];

const CATEGORY_LABELS: Record<string, { label: string; color: string }> = {
  general: { label: "General", color: "text-accent" },
  ai: { label: "AI & Models", color: "text-cyan" },
  tools: { label: "Tools", color: "text-blue-400" },
  channels: { label: "Channels", color: "text-purple-400" },
  admin: { label: "Admin", color: "text-amber" },
};

/* ── Slash Command Palette ─────────────────────────────────────────── */

function CommandPalette({
  filter,
  onSelect,
  selectedIndex,
}: {
  filter: string;
  onSelect: (cmd: string) => void;
  selectedIndex: number;
}) {
  const listRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    const q = filter.toLowerCase();
    return SLASH_COMMANDS.filter(
      (c) => c.cmd.includes(q) || c.desc.toLowerCase().includes(q),
    );
  }, [filter]);

  // Scroll selected item into view
  useEffect(() => {
    const el = listRef.current?.children[selectedIndex] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  if (filtered.length === 0) return null;

  // Group by category
  const grouped = new Map<string, SlashCommand[]>();
  for (const cmd of filtered) {
    const list = grouped.get(cmd.category) ?? [];
    list.push(cmd);
    grouped.set(cmd.category, list);
  }

  let flatIndex = -1;

  return (
    <div className="absolute bottom-full left-0 right-0 mb-2 bg-bg-secondary border border-border rounded-xl shadow-lg max-h-[280px] overflow-y-auto z-50 animate-fade-in" ref={listRef}>
      {[...grouped.entries()].map(([category, cmds]) => {
        const cat = CATEGORY_LABELS[category] ?? { label: category, color: "text-text-muted" };
        return (
          <div key={category}>
            <div className="px-3 pt-2 pb-1">
              <span className={`text-[9px] uppercase tracking-wider font-medium ${cat.color}`}>
                {cat.label}
              </span>
            </div>
            {cmds.map((c) => {
              flatIndex++;
              const idx = flatIndex;
              const isSelected = idx === selectedIndex;
              return (
                <button
                  key={c.cmd}
                  onClick={() => onSelect(c.cmd)}
                  className={`w-full flex items-center gap-3 px-3 py-1.5 text-left transition-colors ${
                    isSelected ? "bg-bg-hover" : "hover:bg-bg-hover/50"
                  }`}
                >
                  <span className="text-xs font-mono text-text-primary shrink-0">{c.cmd}</span>
                  {c.args && (
                    <span className="text-[10px] text-text-muted font-mono shrink-0">{c.args}</span>
                  )}
                  <span className="text-[11px] text-text-muted truncate">{c.desc}</span>
                </button>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

/* ── Chat Input ────────────────────────────────────────────────────── */

export default function ChatInput({
  onSend,
  disabled,
  isStreaming,
  onCancel,
}: ChatInputProps) {
  const [value, setValue] = useState("");
  const [showCommands, setShowCommands] = useState(false);
  const [selectedCmd, setSelectedCmd] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Filter for command palette
  const cmdFilter = useMemo(() => {
    if (!showCommands) return "";
    return value.startsWith("/") ? value : "";
  }, [showCommands, value]);

  const filteredCount = useMemo(() => {
    const q = cmdFilter.toLowerCase();
    return SLASH_COMMANDS.filter(
      (c) => c.cmd.includes(q) || c.desc.toLowerCase().includes(q),
    ).length;
  }, [cmdFilter]);

  // Show palette when typing /
  useEffect(() => {
    if (value.startsWith("/") && !value.includes(" ")) {
      setShowCommands(true);
      setSelectedCmd(0);
    } else {
      setShowCommands(false);
    }
  }, [value]);

  const selectCommand = useCallback(
    (cmd: string) => {
      setValue(cmd + " ");
      setShowCommands(false);
      textareaRef.current?.focus();
    },
    [],
  );

  const handleSend = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
    setShowCommands(false);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [value, disabled, onSend]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (showCommands && filteredCount > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedCmd((i) => (i + 1) % filteredCount);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedCmd((i) => (i - 1 + filteredCount) % filteredCount);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        // Find the selected command
        const q = cmdFilter.toLowerCase();
        const filtered = SLASH_COMMANDS.filter(
          (c) => c.cmd.includes(q) || c.desc.toLowerCase().includes(q),
        );
        if (filtered[selectedCmd]) {
          selectCommand(filtered[selectedCmd].cmd);
          return;
        }
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setShowCommands(false);
        return;
      }
    }

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
        {/* Command palette */}
        {showCommands && (
          <CommandPalette
            filter={cmdFilter}
            onSelect={selectCommand}
            selectedIndex={selectedCmd}
          />
        )}

        <div
          className={`flex items-end bg-bg-tertiary rounded-2xl border transition-colors focus-within:border-border-light ${
            isStreaming ? "border-cyan/40" : "border-border"
          }`}
        >
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onInput={handleInput}
            placeholder={
              isStreaming
                ? "Agent is working — type to send a side-note (ask status, pivot, add info)"
                : "Message LazyClaw... (type / for commands)"
            }
            disabled={disabled}
            maxLength={MAX_LENGTH}
            rows={1}
            className="flex-1 resize-none bg-transparent px-4 py-3.5 text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none disabled:opacity-50 max-h-[200px]"
          />

          <div className="flex items-center gap-1 m-2">
            {isStreaming && (
              <button
                onClick={onCancel}
                className="p-1.5 rounded-lg bg-error/20 text-error hover:bg-error/30 transition-colors"
                aria-label="Stop agent"
                title="Cancel the running turn"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
              </button>
            )}
            <button
              onClick={handleSend}
              disabled={disabled || !value.trim()}
              className={`p-1.5 rounded-lg disabled:opacity-20 disabled:cursor-not-allowed hover:opacity-80 transition-opacity ${
                isStreaming
                  ? "bg-cyan/20 text-cyan"
                  : "bg-text-primary text-bg-primary"
              }`}
              aria-label={isStreaming ? "Send side-note" : "Send"}
              title={
                isStreaming
                  ? "Send as side-note to the running agent (no new turn)"
                  : "Send"
              }
            >
              {isStreaming ? (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
                </svg>
              ) : (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 4l-1.41 1.41L16.17 11H4v2h12.17l-5.58 5.59L12 20l8-8z" />
                </svg>
              )}
            </button>
          </div>
        </div>

        <div className="flex items-center justify-between mt-2 px-1">
          <p className="text-[11px] text-text-muted">
            {isStreaming ? (
              <>
                <span className="text-cyan">● Side-note mode</span>
                <span className="mx-1.5">—</span>
                your input appends to the running turn instead of starting a new one.
              </>
            ) : (
              <>
                Type <span className="font-mono text-text-secondary">/</span> for commands. All messages E2E encrypted.
              </>
            )}
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
