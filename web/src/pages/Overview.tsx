import { useEffect, useCallback, useState } from "react";
import * as api from "../api";
import type { AgentStatus, Job, McpServer, ActivityEvent, EcoUsage, EcoCosts } from "../api";
import { useAuth } from "../context/AuthContext";
import { useChat } from "../context/ChatContext";
import { useAgentStatus } from "../context/AgentStatusContext";
import { RecentTaskRow } from "../components/TaskRow";
import type { Page } from "../components/NavShell";

/* ── Types ──────────────────────────────────────────────────────────── */

interface HealthData {
  gateway: "ok" | "error" | "loading";
  version: string;
  skills: number;
  jobs: number;
  activeJobs: number;
  mcpServers: number;
  connectedMcp: number;
  memories: number;
  ecoMode: string;
  pendingApprovals: number;
}

/* ── Sub-components ─────────────────────────────────────────────────── */

function LiveDot({ status }: { status: "ok" | "error" | "loading" }) {
  if (status === "loading") {
    return <span className="inline-block w-2.5 h-2.5 rounded-full bg-text-muted animate-pulse" />;
  }
  if (status === "error") {
    return <span className="inline-block w-2.5 h-2.5 rounded-full bg-error" />;
  }
  return <span className="inline-block w-2.5 h-2.5 rounded-full bg-accent live-pulse" />;
}

function ProgressBar({ value, max, color = "bg-accent" }: { value: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
      <div className={`h-full rounded-full animate-bar-fill ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function StatCard({
  title,
  value,
  sub,
  icon,
  accent = false,
}: {
  title: string;
  value: string | number;
  sub?: string;
  icon: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <div className="glass-card glow-accent rounded-xl p-4 flex items-start gap-3 animate-fade-in">
      <div className={`p-2 rounded-lg shrink-0 ${accent ? "bg-accent-soft text-accent" : "bg-bg-tertiary text-text-muted"}`}>
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-[11px] text-text-muted uppercase tracking-wider mb-0.5">{title}</p>
        <p className="text-xl font-semibold text-text-primary stat-value">{value}</p>
        {sub && <p className="text-[11px] text-text-muted mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

function ResourceGauge({ label, value, max, unit }: { label: string; value: number; max: number; unit?: string }) {
  const color = value / max > 0.8 ? "bg-error" : value / max > 0.5 ? "bg-amber" : "bg-accent";
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs text-text-muted">{label}</span>
        <span className="text-xs text-text-secondary stat-value">
          {value}{unit && <span className="text-text-muted">/{max}{unit}</span>}
        </span>
      </div>
      <ProgressBar value={value} max={max} color={color} />
    </div>
  );
}

const EVENT_TYPE_STYLE: Record<string, { color: string; icon: React.ReactNode }> = {
  task: {
    color: "text-blue-400",
    icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></svg>,
  },
  tool_execution: {
    color: "text-cyan",
    icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" /></svg>,
  },
  specialist: {
    color: "text-orange-400",
    icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /></svg>,
  },
  approval: {
    color: "text-amber",
    icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>,
  },
  error: {
    color: "text-red-400",
    icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><path d="M12 16v-4M12 8h.01" /></svg>,
  },
};

function RealActivityRow({ event }: { event: ActivityEvent }) {
  const style = EVENT_TYPE_STYLE[event.type] ?? EVENT_TYPE_STYLE.task;
  return (
    <div className="flex items-start gap-3 px-3 py-2.5 rounded-lg hover:bg-bg-hover/50 transition-colors">
      <div className={`mt-0.5 shrink-0 ${style.color}`}>{style.icon}</div>
      <div className="min-w-0 flex-1">
        <p className="text-sm text-text-primary truncate">{event.title}</p>
        <p className="text-[11px] text-text-muted truncate">{event.detail}</p>
      </div>
      <span className="text-[10px] text-text-muted shrink-0 tabular-nums">{timeAgo(event.timestamp)}</span>
    </div>
  );
}

/* ── Live Agent Status ─────────────────────────────────────────── */

const LANE_STYLE: Record<string, string> = {
  foreground: "bg-blue-900/30 text-blue-400",
  background: "bg-purple-900/30 text-purple-400",
  specialist: "bg-orange-900/30 text-orange-400",
};

function AgentStatusPanel({ agentStatus }: { agentStatus: AgentStatus | null }) {
  const [expandedTask, setExpandedTask] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<Set<string>>(new Set());

  const handleCancel = useCallback(async (taskId: string) => {
    setCancelling((prev) => new Set(prev).add(taskId));
    try {
      await api.cancelTask(taskId);
    } catch { /* ignore — status poll will update */ }
  }, []);

  const handleCancelAll = useCallback(async () => {
    try {
      await api.cancelAllTasks();
    } catch { /* ignore */ }
  }, []);

  if (!agentStatus) return null;

  const allActive = [...agentStatus.active, ...agentStatus.background];

  return (
    <div className="animate-fade-in">
      {/* Running tasks */}
      {allActive.length > 0 && (
        <div className="mb-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="inline-block w-2 h-2 rounded-full bg-accent live-pulse" />
            <h2 className="text-sm font-semibold text-text-primary">Running Now</h2>
            <span className="px-1.5 py-0.5 rounded-full bg-accent-soft text-accent text-[10px] font-medium">
              {allActive.length}
            </span>
            {allActive.length > 1 && (
              <button
                onClick={handleCancelAll}
                className="ml-auto px-2.5 py-1 text-[10px] rounded-lg border border-red-400/30 text-red-400 hover:bg-red-900/20 transition-colors"
              >
                Cancel All
              </button>
            )}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {allActive.map((t) => (
              <div key={t.task_id} className="glass-card glow-accent rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-sm font-medium text-text-primary truncate">{t.name}</span>
                  <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${LANE_STYLE[t.lane] || "bg-bg-tertiary text-text-muted"}`}>
                    {t.lane}
                  </span>
                  <span className="ml-auto text-xs text-text-muted stat-value">{t.elapsed_s}s</span>
                  <button
                    onClick={() => handleCancel(t.task_id)}
                    disabled={cancelling.has(t.task_id)}
                    className="p-1 rounded-lg text-text-muted hover:text-red-400 hover:bg-red-900/20 transition-colors disabled:opacity-40"
                    title="Cancel task"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="10" />
                      <line x1="15" y1="9" x2="9" y2="15" />
                      <line x1="9" y1="9" x2="15" y2="15" />
                    </svg>
                  </button>
                </div>
                {t.description && <p className="text-xs text-text-muted truncate">{t.description}</p>}
                {t.current_step && (
                  <p className="text-[11px] text-text-secondary mt-1">
                    Step {t.step_count}: <span className="text-accent">{t.current_step}</span>
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent completed — expandable rows */}
      {agentStatus.recent.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
            Agent History
            <span className="text-[10px] text-text-muted font-normal">{agentStatus.recent.length} recent</span>
          </h2>
          <div className="glass-card rounded-xl overflow-hidden">
            {agentStatus.recent.slice(0, 8).map((t) => (
              <RecentTaskRow
                key={t.task_id}
                task={t}
                expanded={expandedTask === t.task_id}
                onToggle={() => setExpandedTask(expandedTask === t.task_id ? null : t.task_id)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Cost & Usage Panel ──────────────────────────────────────── */

function CostUsagePanel({
  ecoUsage,
  ecoCosts,
  ecoMode,
}: {
  ecoUsage: EcoUsage | null;
  ecoCosts: EcoCosts | null;
  ecoMode: string;
}) {
  if (!ecoUsage && !ecoCosts) return null;

  const totalCost = ecoCosts?.total_cost ?? 0;
  const freePct = ecoUsage?.free_percentage ?? 0;
  const totalCalls = ecoCosts?.total_calls ?? ecoUsage?.total ?? 0;
  const localPct = ecoCosts?.local_pct ?? 0;

  return (
    <div className="animate-fade-in">
      <h2 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-amber">
          <line x1="12" y1="1" x2="12" y2="23" /><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
        </svg>
        Cost & Token Usage
        <span className={`text-[10px] px-2 py-0.5 rounded-full uppercase tracking-wider font-medium ml-auto ${
          ecoMode === "eco" ? "bg-accent-soft text-accent" :
          ecoMode === "hybrid" ? "bg-cyan-soft text-cyan" :
          ecoMode === "claude" ? "bg-purple-900/30 text-purple-400" :
          "bg-bg-tertiary text-text-muted"
        }`}>
          {ecoMode}
        </span>
      </h2>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {/* Total Cost */}
        <div className="glass-card glow-accent rounded-xl p-4">
          <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Total Cost</p>
          <p className={`text-lg font-semibold stat-value ${totalCost === 0 ? "text-accent" : "text-amber"}`}>
            {totalCost === 0 ? "Free" : `$${totalCost.toFixed(4)}`}
          </p>
        </div>

        {/* Free/Local % */}
        <div className="glass-card rounded-xl p-4">
          <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Free / Local</p>
          <p className="text-lg font-semibold text-accent stat-value">{freePct}%</p>
          <div className="h-1 bg-bg-tertiary rounded-full mt-2 overflow-hidden">
            <div className="h-full bg-accent rounded-full" style={{ width: `${freePct}%` }} />
          </div>
        </div>

        {/* Total Calls */}
        <div className="glass-card rounded-xl p-4">
          <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Total Calls</p>
          <p className="text-lg font-semibold text-text-primary stat-value">{totalCalls.toLocaleString()}</p>
        </div>

        {/* Local Model % */}
        <div className="glass-card rounded-xl p-4">
          <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Local Models</p>
          <p className="text-lg font-semibold text-cyan stat-value">{localPct}%</p>
          <div className="h-1 bg-bg-tertiary rounded-full mt-2 overflow-hidden">
            <div className="h-full bg-cyan rounded-full" style={{ width: `${localPct}%` }} />
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Helpers ──────────────────────────────────────────────────────── */

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return iso;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

/* ── Main component ──────────────────────────────────────────────── */

export default function Overview({ onNavigate }: { onNavigate: (page: Page) => void }) {
  const { createSession } = useChat();
  const { user } = useAuth();
  const [health, setHealth] = useState<HealthData>({
    gateway: "loading",
    version: "...",
    skills: 0,
    jobs: 0,
    activeJobs: 0,
    mcpServers: 0,
    connectedMcp: 0,
    memories: 0,
    ecoMode: "...",
    pendingApprovals: 0,
  });
  const { agentStatus, activityFeed, metrics, ecoUsage, ecoCosts } = useAgentStatus();
  const [uptime, setUptime] = useState("—");

  useEffect(() => {
    async function load() {
      const results = await Promise.allSettled([
        api.healthCheck(),
        api.listSkills(),
        api.listJobs(),
        api.listMcpServers(),
        api.listMemories(),
        api.getEcoSettings(),
        api.listPendingApprovals(),
      ]);

      const healthResp = results[0].status === "fulfilled" ? results[0].value : null;
      const skills = results[1].status === "fulfilled" ? results[1].value : [];
      const jobs = results[2].status === "fulfilled" ? results[2].value : [];
      const mcp = results[3].status === "fulfilled" ? results[3].value : [];
      const memories = results[4].status === "fulfilled" ? results[4].value : [];
      const eco = results[5].status === "fulfilled" ? results[5].value : null;
      const approvals = results[6].status === "fulfilled" ? results[6].value : [];

      const skillsArr = Array.isArray(skills) ? skills : [];
      const jobsArr = Array.isArray(jobs) ? jobs : [];
      const mcpArr = Array.isArray(mcp) ? mcp : [];
      const memsArr = Array.isArray(memories) ? memories : [];
      const appArr = Array.isArray(approvals) ? approvals : [];

      setHealth({
        gateway: healthResp ? "ok" : "error",
        version: healthResp?.version ?? "unknown",
        skills: skillsArr.length,
        jobs: jobsArr.length,
        activeJobs: jobsArr.filter((j: Job) => j.status === "active").length,
        mcpServers: mcpArr.length,
        connectedMcp: mcpArr.filter((m: McpServer) => m.status === "connected").length,
        memories: memsArr.length,
        ecoMode: eco?.mode ?? "unknown",
        pendingApprovals: appArr.length,
      });

      // Calculate real uptime from server start time
      if (healthResp?.started_at) {
        const elapsed = Math.floor(Date.now() / 1000 - healthResp.started_at);
        const hours = Math.floor(elapsed / 3600);
        const minutes = Math.floor((elapsed % 3600) / 60);
        setUptime(hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`);
      } else if (healthResp) {
        setUptime("Online");
      }
    }
    load();
  }, []);

  const gatewayLabel =
    health.gateway === "ok" ? "All systems operational" :
    health.gateway === "loading" ? "Connecting..." :
    "Gateway offline";

  return (
    <div className="h-full overflow-y-auto grid-bg">
      <div className="max-w-5xl mx-auto px-6 py-8 space-y-6">

        {/* ── Welcome banner ────────────────────────────────────── */}
        <div className="animate-fade-in">
          <h1 className="text-xl font-semibold text-text-primary">
            Welcome back{user?.display_name ?? user?.username ? `, ${user.display_name ?? user.username}` : ""}
          </h1>
          <p className="text-sm text-text-muted mt-0.5">Here's what's happening with your agent.</p>
        </div>

        {/* ── System health banner ──────────────────────────────── */}
        <div className="animate-fade-in">
          <div className="flex items-center gap-3 px-5 py-4 rounded-xl glass-card glow-accent">
            <LiveDot status={health.gateway} />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-text-primary">{gatewayLabel}</p>
              <p className="text-[11px] text-text-muted mt-0.5">
                v{health.version} &middot; {uptime} &middot; {health.connectedMcp}/{health.mcpServers} MCP servers
              </p>
            </div>
            <div className="flex items-center gap-1.5 px-3 py-1 rounded-lg bg-bg-tertiary/60">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
                <rect x="3" y="11" width="18" height="11" rx="2" />
                <path d="M7 11V7a5 5 0 0110 0v4" />
              </svg>
              <span className="text-[10px] text-text-muted">AES-256</span>
            </div>
          </div>
        </div>

        {/* ── Stats cards ───────────────────────────────────────── */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatCard
            title="Skills"
            value={health.skills}
            sub="Registered tools"
            icon={
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
              </svg>
            }
          />
          <StatCard
            title="Jobs"
            value={health.jobs}
            sub={`${health.activeJobs} active`}
            accent={health.activeJobs > 0}
            icon={
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
            }
          />
          <StatCard
            title="MCP Servers"
            value={health.mcpServers}
            sub={`${health.connectedMcp} connected`}
            accent={health.connectedMcp > 0}
            icon={
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z" />
              </svg>
            }
          />
          <StatCard
            title="Memories"
            value={health.memories}
            sub="Personal facts"
            icon={
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2z" />
                <path d="M12 16v-4M12 8h.01" />
              </svg>
            }
          />
        </div>

        {/* ── Agent Performance ──────────────────────────────── */}
        {metrics && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 animate-fade-in">
            <div className="glass-card rounded-xl p-4">
              <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Success Rate</p>
              <p className={`text-lg font-semibold stat-value ${metrics.success_rate >= 80 ? "text-green-400" : metrics.success_rate >= 50 ? "text-amber" : "text-red-400"}`}>
                {metrics.success_rate}%
              </p>
              <div className="h-1 bg-bg-tertiary rounded-full mt-2 overflow-hidden">
                <div className="h-full bg-green-400 rounded-full" style={{ width: `${metrics.success_rate}%` }} />
              </div>
            </div>
            <div className="glass-card rounded-xl p-4">
              <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Avg Duration</p>
              <p className="text-lg font-semibold text-text-primary stat-value">{metrics.avg_duration_s}s</p>
            </div>
            <div className="glass-card rounded-xl p-4">
              <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Tasks / Hour</p>
              <p className="text-lg font-semibold text-text-primary stat-value">{metrics.tasks_last_hour}</p>
            </div>
            <div className="glass-card rounded-xl p-4">
              <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Tool Calls Today</p>
              <p className="text-lg font-semibold text-cyan stat-value">{metrics.tool_calls_today}</p>
            </div>
          </div>
        )}

        {/* ── Cost & Token Usage ──────────────────────────────── */}
        <CostUsagePanel ecoUsage={ecoUsage} ecoCosts={ecoCosts} ecoMode={health.ecoMode} />

        {/* ── Live Agent Status ──────────────────────────────── */}
        <AgentStatusPanel agentStatus={agentStatus} />

        {/* ── Two-column: Resources + Activity ────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">

          {/* Resource usage */}
          <div className="lg:col-span-2 glass-card rounded-xl p-5 animate-fade-in-1 space-y-5">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-text-primary">Resources</h2>
              <span className={`text-[10px] px-2 py-0.5 rounded-full uppercase tracking-wider font-medium ${
                health.ecoMode === "eco" ? "bg-accent-soft text-accent" :
                health.ecoMode === "hybrid" ? "bg-cyan-soft text-cyan" :
                "bg-bg-tertiary text-text-muted"
              }`}>
                {health.ecoMode}
              </span>
            </div>

            <ResourceGauge
              label="Active Jobs"
              value={health.activeJobs}
              max={Math.max(health.jobs, 1)}
              unit=""
            />
            <ResourceGauge
              label="MCP Connections"
              value={health.connectedMcp}
              max={Math.max(health.mcpServers, 1)}
              unit=""
            />
            <ResourceGauge
              label="Pending Approvals"
              value={health.pendingApprovals}
              max={Math.max(health.pendingApprovals + 5, 5)}
              unit=""
            />

            {/* Quick info rows */}
            <div className="pt-2 border-t border-border/50 space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-text-muted">ECO Mode</span>
                <span className="text-text-primary font-medium">{health.ecoMode.toUpperCase()}</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-text-muted">Encryption</span>
                <span className="text-accent font-medium">AES-256-GCM</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-text-muted">Approvals</span>
                <span className={`font-medium ${health.pendingApprovals > 0 ? "text-amber" : "text-text-secondary"}`}>
                  {health.pendingApprovals} pending
                </span>
              </div>
            </div>
          </div>

          {/* Activity feed */}
          <div className="lg:col-span-3 glass-card rounded-xl animate-fade-in-2 flex flex-col">
            <div className="flex items-center justify-between px-5 pt-4 pb-2">
              <h2 className="text-sm font-semibold text-text-primary">Recent Activity</h2>
              <span className="text-[10px] text-text-muted">{activityFeed.length} events</span>
            </div>

            <div className="flex-1 overflow-y-auto px-2 pb-3 max-h-72">
              {activityFeed.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-text-muted">
                  <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="mb-2 opacity-40">
                    <circle cx="12" cy="12" r="10" />
                    <polyline points="12 6 12 12 16 14" />
                  </svg>
                  <p className="text-xs">No recent activity</p>
                  <p className="text-[10px] mt-1">Send a message or run a job to get started</p>
                </div>
              ) : (
                <div className="space-y-0.5">
                  {activityFeed.slice(0, 12).map((ev) => (
                    <RealActivityRow key={ev.id} event={ev} />
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ── Quick actions ──────────────────────────────────── */}
        <div className="animate-fade-in-3">
          <h2 className="text-sm font-semibold text-text-primary mb-3">Quick Actions</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <QuickAction
              label="New Chat"
              onClick={() => createSession()}
              icon={
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
              }
            />
            <QuickAction
              label="Jobs"
              onClick={() => onNavigate("jobs")}
              icon={
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
              }
            />
            <QuickAction
              label="Add Skill"
              onClick={() => onNavigate("hub")}
              icon={
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
              }
            />
            <QuickAction
              label="Add Credential"
              onClick={() => onNavigate("vault")}
              icon={
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <rect x="3" y="11" width="18" height="11" rx="2" />
                  <path d="M7 11V7a5 5 0 0110 0v4" />
                </svg>
              }
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function QuickAction({ label, icon, onClick }: { label: string; icon: React.ReactNode; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-2.5 px-4 py-3 rounded-xl glass-card glow-accent text-text-secondary hover:text-text-primary transition-colors text-sm"
    >
      <span className="text-text-muted">{icon}</span>
      {label}
    </button>
  );
}
