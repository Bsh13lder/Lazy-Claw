import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { McpServer, McpTool } from "../api";
import Modal from "../components/Modal";

// ── Transport options ─────────────────────────────────────────────────────

const TRANSPORTS = [
  { value: "stdio" as const, label: "stdio", desc: "Local process (command + args)" },
  { value: "sse" as const, label: "SSE", desc: "Server-Sent Events (URL)" },
  { value: "streamable_http" as const, label: "Streamable HTTP", desc: "HTTP streaming (URL)" },
];

// ── Status helpers ────────────────────────────────────────────────────────

function statusDot(s: string) {
  if (s === "connected") return "bg-emerald-500";
  if (s === "connecting") return "bg-yellow-500 animate-pulse";
  if (s === "error") return "bg-red-500";
  return "bg-gray-500";
}

function statusLabel(s: string) {
  if (s === "connected") return "text-emerald-400";
  if (s === "error") return "text-red-400";
  if (s === "connecting") return "text-yellow-400";
  return "text-text-muted";
}

// ── Main component ────────────────────────────────────────────────────────

export default function Mcp() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [reconnectingAll, setReconnectingAll] = useState(false);
  const [actionIds, setActionIds] = useState<Set<string>>(new Set());

  // Tools cache per server
  const [toolsCache, setToolsCache] = useState<Record<string, McpTool[]>>({});
  const [toolsLoading, setToolsLoading] = useState<Set<string>>(new Set());

  // Add server modal
  const [showAdd, setShowAdd] = useState(false);
  const [addName, setAddName] = useState("");
  const [addTransport, setAddTransport] = useState<"stdio" | "sse" | "streamable_http">("stdio");
  const [addCommand, setAddCommand] = useState("");
  const [addArgs, setAddArgs] = useState("");
  const [addUrl, setAddUrl] = useState("");
  const [addSaving, setAddSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listMcpServers();
      setServers(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load MCP servers");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // ── Actions ───────────────────────────────────────────────────────────

  const markAction = (id: string, active: boolean) => {
    setActionIds((prev) => {
      const next = new Set(prev);
      if (active) next.add(id); else next.delete(id);
      return next;
    });
  };

  const handleExpand = async (serverId: string) => {
    if (expandedId === serverId) {
      setExpandedId(null);
      return;
    }
    setExpandedId(serverId);
    // Fetch real tools if not cached
    if (!toolsCache[serverId]) {
      setToolsLoading((prev) => new Set([...prev, serverId]));
      try {
        const tools = await api.getMcpServerTools(serverId);
        setToolsCache((prev) => ({ ...prev, [serverId]: tools }));
      } catch {
        setToolsCache((prev) => ({ ...prev, [serverId]: [] }));
      } finally {
        setToolsLoading((prev) => { const next = new Set(prev); next.delete(serverId); return next; });
      }
    }
  };

  const handleReconnect = async (id: string) => {
    markAction(id, true);
    try { await api.reconnectMcp(id); await load(); } catch { /* ignore */ }
    finally { markAction(id, false); }
  };

  const handleDisconnect = async (id: string) => {
    markAction(id, true);
    try { await api.disconnectMcp(id); await load(); } catch { /* ignore */ }
    finally { markAction(id, false); }
  };

  const handleRemove = async (id: string) => {
    markAction(id, true);
    try {
      await api.removeMcpServer(id);
      setServers((prev) => prev.filter((s) => s.id !== id));
      if (expandedId === id) setExpandedId(null);
    } catch { /* ignore */ }
    finally { markAction(id, false); }
  };

  const handleReconnectAll = async () => {
    setReconnectingAll(true);
    try {
      await Promise.allSettled(
        servers.filter((s) => s.status !== "connected").map((s) => api.reconnectMcp(s.id)),
      );
      await load();
    } catch { /* ignore */ }
    finally { setReconnectingAll(false); }
  };

  const handleAdd = async () => {
    if (!addName.trim()) return;
    setAddSaving(true);
    try {
      const config: Record<string, unknown> = {};
      if (addTransport === "stdio") {
        config.command = addCommand.trim();
        if (addArgs.trim()) {
          config.args = addArgs.split(/\s+/).filter(Boolean);
        }
      } else {
        config.url = addUrl.trim();
      }
      await api.addMcpServer({
        name: addName.trim(),
        transport: addTransport,
        config,
      });
      setShowAdd(false);
      setAddName("");
      setAddCommand("");
      setAddArgs("");
      setAddUrl("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add server");
    } finally {
      setAddSaving(false);
    }
  };

  // ── Stats ─────────────────────────────────────────────────────────────

  const connectedCount = servers.filter((s) => s.status === "connected").length;
  const errorCount = servers.filter((s) => s.status === "error").length;
  const totalTools = servers.reduce((sum, s) => sum + s.tool_count, 0);

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">MCP Servers</h1>
            <p className="text-sm text-text-muted">{servers.length} configured</p>
          </div>
          <div className="flex gap-2">
            {servers.some((s) => s.status !== "connected") && (
              <button
                onClick={handleReconnectAll}
                disabled={reconnectingAll}
                className="flex items-center gap-1.5 text-xs text-emerald-400 hover:text-emerald-300 px-3 py-1.5 rounded-lg border border-emerald-500/30 hover:bg-emerald-500/10 transition-colors disabled:opacity-40"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className={reconnectingAll ? "spinner" : ""}>
                  <path d="M23 4v6h-6" />
                  <path d="M1 20v-6h6" />
                  <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
                </svg>
                {reconnectingAll ? "Reconnecting..." : "Reconnect All"}
              </button>
            )}
            <button
              onClick={() => setShowAdd(true)}
              className="flex items-center gap-1.5 text-xs text-cyan-400 hover:text-cyan-300 px-3 py-1.5 rounded-lg border border-cyan-500/30 hover:bg-cyan-500/10 transition-colors"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              Add Server
            </button>
            <button
              onClick={load}
              className="text-xs text-text-muted hover:text-text-secondary px-3 py-1.5 rounded-lg border border-border hover:bg-bg-hover transition-colors"
            >
              Refresh
            </button>
          </div>
        </div>

        {/* Stats bar */}
        {!loading && servers.length > 0 && (
          <div className="grid grid-cols-3 gap-3 mb-6">
            <div className="px-4 py-3 rounded-xl bg-bg-secondary border border-border">
              <p className="text-[10px] text-text-muted uppercase tracking-wider mb-0.5">Connected</p>
              <p className="text-lg font-semibold text-emerald-400">{connectedCount}<span className="text-sm text-text-muted font-normal">/{servers.length}</span></p>
            </div>
            <div className="px-4 py-3 rounded-xl bg-bg-secondary border border-border">
              <p className="text-[10px] text-text-muted uppercase tracking-wider mb-0.5">Total Tools</p>
              <p className="text-lg font-semibold text-cyan-400">{totalTools}</p>
            </div>
            <div className="px-4 py-3 rounded-xl bg-bg-secondary border border-border">
              <p className="text-[10px] text-text-muted uppercase tracking-wider mb-0.5">Errors</p>
              <p className={`text-lg font-semibold ${errorCount > 0 ? "text-red-400" : "text-text-muted"}`}>{errorCount}</p>
            </div>
          </div>
        )}

        {loading && (
          <div className="flex items-center gap-2 text-text-muted text-sm py-8 justify-center">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner">
              <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
            </svg>
            Loading MCP servers...
          </div>
        )}

        {error && (
          <div className="px-4 py-3 rounded-xl bg-error-soft border border-error/15 text-error text-sm mb-4">
            {error}
          </div>
        )}

        {!loading && !error && servers.length === 0 && (
          <div className="text-center py-16">
            <div className="w-16 h-16 rounded-2xl bg-bg-secondary border border-border flex items-center justify-center mx-auto mb-4">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-text-muted" strokeLinecap="round">
                <path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z" />
              </svg>
            </div>
            <p className="text-text-muted text-sm mb-2">No MCP servers configured</p>
            <p className="text-text-muted text-xs mb-4">Add your first MCP server to extend your agent's capabilities</p>
            <button
              onClick={() => setShowAdd(true)}
              className="text-xs text-cyan-400 hover:text-cyan-300 px-4 py-2 rounded-xl border border-cyan-500/30 hover:bg-cyan-500/10 transition-colors"
            >
              + Add MCP Server
            </button>
          </div>
        )}

        {/* Server list */}
        {!loading && !error && servers.length > 0 && (
          <div className="space-y-2">
            {servers.map((server) => {
              const expanded = expandedId === server.id;
              const busy = actionIds.has(server.id);

              return (
                <div
                  key={server.id}
                  className={`rounded-xl bg-bg-secondary border transition-colors ${
                    expanded ? "border-border-light" : "border-border hover:border-border-light"
                  }`}
                >
                  {/* Server header */}
                  <div
                    className="px-4 py-4 cursor-pointer"
                    onClick={() => handleExpand(server.id)}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 mb-1">
                          <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${statusDot(server.status)}`} />
                          <p className="text-sm font-medium text-text-primary">{server.name}</p>
                          <span className={`text-[10px] font-medium ${statusLabel(server.status)}`}>
                            {server.status}
                          </span>
                        </div>
                        <div className="flex items-center gap-4 text-[11px] text-text-muted">
                          <span className="flex items-center gap-1">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                              <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
                            </svg>
                            {server.transport}
                          </span>
                          <span className="flex items-center gap-1">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                              <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
                            </svg>
                            {server.tool_count} tools
                          </span>
                          {server.command && (
                            <span className="font-mono truncate max-w-[200px]">{server.command}</span>
                          )}
                          {server.url && (
                            <span className="font-mono truncate max-w-[200px]">{server.url}</span>
                          )}
                        </div>
                      </div>

                      {/* Actions */}
                      <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                        {server.status === "connected" ? (
                          <button
                            onClick={() => handleDisconnect(server.id)}
                            disabled={busy}
                            className="text-xs text-text-muted hover:text-yellow-400 px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors disabled:opacity-40"
                          >
                            Disconnect
                          </button>
                        ) : (
                          <button
                            onClick={() => handleReconnect(server.id)}
                            disabled={busy}
                            className="text-xs text-text-muted hover:text-emerald-400 px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors disabled:opacity-40"
                          >
                            Reconnect
                          </button>
                        )}
                        <button
                          onClick={() => handleRemove(server.id)}
                          disabled={busy}
                          className="text-xs text-text-muted hover:text-red-400 px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors disabled:opacity-40"
                        >
                          Remove
                        </button>
                        {/* Expand chevron */}
                        <svg
                          width="16"
                          height="16"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          className={`text-text-muted transition-transform ${expanded ? "rotate-180" : ""}`}
                        >
                          <polyline points="6 9 12 15 18 9" />
                        </svg>
                      </div>
                    </div>
                  </div>

                  {/* Expanded tool browser */}
                  {expanded && (
                    <div className="px-4 pb-4 border-t border-border">
                      <div className="pt-3">
                        <p className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-2">
                          Tools ({server.tool_count})
                        </p>
                        {toolsLoading.has(server.id) ? (
                          <div className="flex items-center gap-2 text-text-muted text-xs py-2">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner">
                              <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
                            </svg>
                            Loading tools...
                          </div>
                        ) : (toolsCache[server.id] ?? []).length === 0 ? (
                          <p className="text-xs text-text-muted py-2">No tools registered</p>
                        ) : (
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                            {(toolsCache[server.id] ?? []).map((tool) => (
                              <div
                                key={tool.name}
                                className="flex items-start gap-2 px-3 py-2 rounded-lg bg-bg-tertiary"
                              >
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-cyan-400 shrink-0 mt-0.5">
                                  <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
                                </svg>
                                <div className="min-w-0">
                                  <span className="text-xs text-text-secondary font-mono truncate block">
                                    {tool.name}
                                  </span>
                                  {tool.description && (
                                    <span className="text-[10px] text-text-muted line-clamp-1">
                                      {tool.description}
                                    </span>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Add MCP Server modal */}
      <Modal open={showAdd} onClose={() => setShowAdd(false)} title="Add MCP Server">
        <div className="space-y-4">
          <div>
            <label className="text-xs text-text-muted uppercase tracking-wider mb-1 block">Server Name</label>
            <input
              type="text"
              value={addName}
              onChange={(e) => setAddName(e.target.value)}
              placeholder="e.g. my-mcp-server"
              className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light"
            />
          </div>

          <div>
            <label className="text-xs text-text-muted uppercase tracking-wider mb-2 block">Transport</label>
            <div className="flex gap-2">
              {TRANSPORTS.map((t) => (
                <button
                  key={t.value}
                  onClick={() => setAddTransport(t.value)}
                  className={`flex-1 px-3 py-2.5 rounded-xl border text-xs transition-colors ${
                    addTransport === t.value
                      ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-400"
                      : "border-border text-text-muted hover:bg-bg-hover"
                  }`}
                >
                  <p className="font-medium">{t.label}</p>
                  <p className="text-[10px] mt-0.5 opacity-60">{t.desc}</p>
                </button>
              ))}
            </div>
          </div>

          {addTransport === "stdio" ? (
            <>
              <div>
                <label className="text-xs text-text-muted uppercase tracking-wider mb-1 block">Command</label>
                <input
                  type="text"
                  value={addCommand}
                  onChange={(e) => setAddCommand(e.target.value)}
                  placeholder="e.g. python -m my_mcp_server"
                  className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light"
                />
              </div>
              <div>
                <label className="text-xs text-text-muted uppercase tracking-wider mb-1 block">Arguments (space-separated)</label>
                <input
                  type="text"
                  value={addArgs}
                  onChange={(e) => setAddArgs(e.target.value)}
                  placeholder="e.g. --port 8080 --verbose"
                  className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light"
                />
              </div>
            </>
          ) : (
            <div>
              <label className="text-xs text-text-muted uppercase tracking-wider mb-1 block">URL</label>
              <input
                type="text"
                value={addUrl}
                onChange={(e) => setAddUrl(e.target.value)}
                placeholder="e.g. http://localhost:8080/mcp"
                className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light"
              />
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              onClick={() => setShowAdd(false)}
              className="px-4 py-2 text-sm text-text-muted rounded-lg hover:bg-bg-hover transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleAdd}
              disabled={addSaving || !addName.trim() || (addTransport === "stdio" ? !addCommand.trim() : !addUrl.trim())}
              className="px-4 py-2 text-sm bg-cyan-500 text-white rounded-lg hover:opacity-90 disabled:opacity-30 transition-opacity"
            >
              {addSaving ? "Adding..." : "Add Server"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
