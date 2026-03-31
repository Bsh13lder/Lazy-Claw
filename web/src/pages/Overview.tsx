import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import { useToast } from "../context/ToastContext";
import { useInterval } from "../hooks/useInterval";
import { OverviewSkeleton } from "../components/Skeleton";

interface HealthStatus {
  gateway: "ok" | "error" | "loading";
  version: string;
  skills: number;
  jobs: number;
  mcpServers: number;
  memories: number;
  ecoMode: string;
  pendingApprovals: number;
}

function StatusDot({ status }: { status: "ok" | "error" | "loading" }) {
  const color =
    status === "ok" ? "bg-accent" : status === "error" ? "bg-error" : "bg-text-muted";
  return (
    <span className={`inline-block w-2 h-2 rounded-full ${color} ${status === "loading" ? "animate-pulse" : ""}`} />
  );
}

function Card({ title, value, sub, icon }: { title: string; value: string | number; sub?: string; icon: React.ReactNode }) {
  return (
    <div className="bg-bg-secondary border border-border rounded-xl p-4 flex items-start gap-3">
      <div className="p-2 rounded-lg bg-bg-tertiary text-text-muted shrink-0">{icon}</div>
      <div>
        <p className="text-xs text-text-muted mb-0.5">{title}</p>
        <p className="text-lg font-semibold text-text-primary">{value}</p>
        {sub && <p className="text-xs text-text-muted mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

const INITIAL_STATUS: HealthStatus = {
  gateway: "loading",
  version: "...",
  skills: 0,
  jobs: 0,
  mcpServers: 0,
  memories: 0,
  ecoMode: "...",
  pendingApprovals: 0,
};

export default function Overview() {
  const [status, setStatus] = useState<HealthStatus>(INITIAL_STATUS);
  const [loaded, setLoaded] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    const results = await Promise.allSettled([
      api.healthCheck(),
      api.listSkills(),
      api.listJobs(),
      api.listMcpServers(),
      api.listMemories(),
      api.getEcoSettings(),
      api.listPendingApprovals(),
    ]);

    const health = results[0].status === "fulfilled" ? results[0].value : null;
    const skills = results[1].status === "fulfilled" ? results[1].value : [];
    const jobs = results[2].status === "fulfilled" ? results[2].value : [];
    const mcp = results[3].status === "fulfilled" ? results[3].value : [];
    const memories = results[4].status === "fulfilled" ? results[4].value : [];
    const eco = results[5].status === "fulfilled" ? results[5].value : null;
    const approvals = results[6].status === "fulfilled" ? results[6].value : [];

    // Show toast if gateway went down (only after initial load)
    if (loaded && !health && status.gateway === "ok") {
      toast.error("Gateway connection lost");
    }

    setStatus({
      gateway: health ? "ok" : "error",
      version: health?.version ?? "unknown",
      skills: Array.isArray(skills) ? skills.length : 0,
      jobs: Array.isArray(jobs) ? jobs.length : 0,
      mcpServers: Array.isArray(mcp) ? mcp.length : 0,
      memories: Array.isArray(memories) ? memories.length : 0,
      ecoMode: eco?.mode ?? "unknown",
      pendingApprovals: Array.isArray(approvals) ? approvals.length : 0,
    });
    setLoaded(true);
  }, [loaded, status.gateway, toast]);

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useInterval(load, 30_000);

  if (!loaded) return <OverviewSkeleton />;

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-2">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
              <rect x="3" y="11" width="18" height="11" rx="2" />
              <path d="M7 11V7a5 5 0 0110 0v4" />
            </svg>
            <h1 className="text-xl font-semibold text-text-primary">LazyClaw</h1>
          </div>
          <p className="text-sm text-text-muted">E2E Encrypted AI Agent Platform</p>
        </div>

        {/* Gateway status */}
        <div className="flex items-center gap-2 mb-6 px-4 py-3 rounded-xl bg-bg-secondary border border-border">
          <StatusDot status={status.gateway} />
          <span className="text-sm text-text-secondary">
            Gateway {status.gateway === "ok" ? "online" : status.gateway === "loading" ? "connecting..." : "offline"}
          </span>
          <span className="text-xs text-text-muted ml-auto">v{status.version}</span>
        </div>

        {/* Cards grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <Card
            title="Skills"
            value={status.skills}
            sub="Registered tools"
            icon={
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
              </svg>
            }
          />
          <Card
            title="Jobs"
            value={status.jobs}
            sub="Scheduled & cron"
            icon={
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
            }
          />
          <Card
            title="MCP Servers"
            value={status.mcpServers}
            sub="Connected integrations"
            icon={
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z" />
              </svg>
            }
          />
          <Card
            title="Memories"
            value={status.memories}
            sub="Personal facts"
            icon={
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2z" />
                <path d="M12 16v-4M12 8h.01" />
              </svg>
            }
          />
          <Card
            title="ECO Mode"
            value={status.ecoMode}
            sub="Cost routing"
            icon={
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
              </svg>
            }
          />
          <Card
            title="Pending Approvals"
            value={status.pendingApprovals}
            sub="Awaiting action"
            icon={
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              </svg>
            }
          />
        </div>

        {/* Encryption badge */}
        <div className="mt-8 flex items-center gap-2 text-xs text-text-muted justify-center">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
            <rect x="3" y="11" width="18" height="11" rx="2" />
            <path d="M7 11V7a5 5 0 0110 0v4" />
          </svg>
          All data AES-256-GCM encrypted at rest
        </div>
      </div>
    </div>
  );
}
