import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { McpServer } from "../api";
import { useToast } from "../context/ToastContext";
import { useInterval } from "../hooks/useInterval";
import { ListSkeleton } from "../components/Skeleton";

export default function Mcp() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const data = await api.listMcpServers();
      setServers(Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load MCP servers");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);
  useInterval(load, 60_000);

  const handleReconnect = async (id: string) => {
    try { await api.reconnectMcp(id); toast.success("Reconnecting..."); load(); } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to reconnect");
    }
  };

  const handleDisconnect = async (id: string) => {
    try { await api.disconnectMcp(id); toast.success("Disconnected"); load(); } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to disconnect");
    }
  };

  const statusColor = (s: string) => {
    if (s === "connected") return "bg-accent";
    if (s === "connecting") return "bg-yellow-500 animate-pulse";
    if (s === "error") return "bg-error";
    return "bg-text-muted";
  };

  if (loading) return <ListSkeleton rows={3} />;

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">MCP Servers</h1>
            <p className="text-sm text-text-muted">{servers.length} configured</p>
          </div>
          <button onClick={load} className="text-xs text-text-muted hover:text-text-secondary px-3 py-1.5 rounded-lg border border-border hover:bg-bg-hover transition-colors">
            Refresh
          </button>
        </div>

        {servers.length === 0 && (
          <div className="text-center py-12 text-text-muted text-sm">
            No MCP servers configured. Add servers via chat or the API.
          </div>
        )}

        {servers.length > 0 && (
          <div className="space-y-2">
            {servers.map((server) => (
              <div
                key={server.id}
                className="px-4 py-4 rounded-xl bg-bg-secondary border border-border hover:border-border-light transition-colors"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`w-2 h-2 rounded-full shrink-0 ${statusColor(server.status)}`} />
                      <p className="text-sm font-medium text-text-primary">{server.name}</p>
                    </div>
                    <div className="flex items-center gap-4 text-[11px] text-text-muted">
                      <span>Transport: {server.transport}</span>
                      <span>Tools: {server.tool_count}</span>
                      <span>Status: {server.status}</span>
                    </div>
                    {server.command && (
                      <p className="text-[11px] text-text-muted mt-1 font-mono truncate">{server.command}</p>
                    )}
                    {server.url && (
                      <p className="text-[11px] text-text-muted mt-1 font-mono truncate">{server.url}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    {server.status === "connected" ? (
                      <button onClick={() => handleDisconnect(server.id)} className="text-xs text-text-muted hover:text-yellow-400 px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors">
                        Disconnect
                      </button>
                    ) : (
                      <button onClick={() => handleReconnect(server.id)} className="text-xs text-text-muted hover:text-accent px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors">
                        Reconnect
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
