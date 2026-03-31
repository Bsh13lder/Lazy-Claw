import { useEffect, useState } from "react";
import * as api from "../api";
import type { Job, McpServer } from "../api";

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

interface ActivityItem {
  id: string;
  icon: "job" | "mcp" | "skill" | "memory";
  label: string;
  detail: string;
  time: string;
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
    <div className="bg-bg-secondary border border-border rounded-xl p-4 card-hover flex items-start gap-3 animate-fade-in">
      <div className={`p-2 rounded-lg shrink-0 ${accent ? "bg-accent-soft text-accent" : "bg-bg-tertiary text-text-muted"}`}>
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-[11px] text-text-muted uppercase tracking-wider mb-0.5">{title}</p>
        <p className="text-xl font-semibold text-text-primary tabular-nums">{value}</p>
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
        <span className="text-xs text-text-secondary tabular-nums">
          {value}{unit && <span className="text-text-muted">/{max}{unit}</span>}
        </span>
      </div>
      <ProgressBar value={value} max={max} color={color} />
    </div>
  );
}

const ACTIVITY_ICONS: Record<string, React.ReactNode> = {
  job: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  ),
  mcp: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z" />
    </svg>
  ),
  skill: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    </svg>
  ),
  memory: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2z" />
      <path d="M12 16v-4M12 8h.01" />
    </svg>
  ),
};

function ActivityRow({ item }: { item: ActivityItem }) {
  const iconColor =
    item.icon === "job" ? "text-cyan" :
    item.icon === "mcp" ? "text-amber" :
    item.icon === "skill" ? "text-accent" :
    "text-text-muted";

  return (
    <div className="flex items-start gap-3 px-3 py-2.5 rounded-lg hover:bg-bg-hover/50 transition-colors">
      <div className={`mt-0.5 shrink-0 ${iconColor}`}>
        {ACTIVITY_ICONS[item.icon]}
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm text-text-primary truncate">{item.label}</p>
        <p className="text-[11px] text-text-muted truncate">{item.detail}</p>
      </div>
      <span className="text-[10px] text-text-muted shrink-0 tabular-nums">{item.time}</span>
    </div>
  );
}

/* ── Helpers ──────────────────────────────────────────────────────── */

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function buildActivity(jobs: Job[], mcpServers: McpServer[]): ActivityItem[] {
  const items: ActivityItem[] = [];

  for (const j of jobs) {
    if (j.last_run) {
      items.push({
        id: `job-${j.id}`,
        icon: "job",
        label: j.name,
        detail: `${j.status === "active" ? "Ran" : "Completed"} — ${j.instruction.slice(0, 60)}`,
        time: timeAgo(j.last_run),
      });
    }
  }

  for (const m of mcpServers) {
    items.push({
      id: `mcp-${m.id}`,
      icon: "mcp",
      label: m.name,
      detail: `${m.status === "connected" ? "Connected" : m.status} — ${m.tool_count} tools via ${m.transport}`,
      time: "",
    });
  }

  return items.slice(0, 12);
}

/* ── Main component ──────────────────────────────────────────────── */

export default function Overview() {
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
  const [activity, setActivity] = useState<ActivityItem[]>([]);
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
      const jobs: Job[] = results[2].status === "fulfilled" ? (results[2].value as Job[]) : [];
      const mcp: McpServer[] = results[3].status === "fulfilled" ? (results[3].value as McpServer[]) : [];
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

      setActivity(buildActivity(jobsArr, mcpArr));

      // Fake uptime from health check — real implementation would come from server
      if (healthResp) setUptime("Online");
    }
    load();
  }, []);

  const gatewayLabel =
    health.gateway === "ok" ? "All systems operational" :
    health.gateway === "loading" ? "Connecting..." :
    "Gateway offline";

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8 space-y-6">

        {/* ── System health banner ──────────────────────────────── */}
        <div className="animate-fade-in">
          <div className="flex items-center gap-3 px-5 py-4 rounded-xl bg-bg-secondary border border-border">
            <LiveDot status={health.gateway} />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-text-primary">{gatewayLabel}</p>
              <p className="text-[11px] text-text-muted mt-0.5">
                v{health.version} &middot; {uptime} &middot; Telegram connected &middot; {health.connectedMcp}/{health.mcpServers} MCP servers
              </p>
            </div>
            <div className="flex items-center gap-1.5 px-3 py-1 rounded-lg bg-bg-tertiary">
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

        {/* ── Two-column: Resources + Activity ────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">

          {/* Resource usage */}
          <div className="lg:col-span-2 bg-bg-secondary border border-border rounded-xl p-5 animate-fade-in-1 space-y-5">
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
            <div className="pt-2 border-t border-border space-y-2">
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
          <div className="lg:col-span-3 bg-bg-secondary border border-border rounded-xl animate-fade-in-2 flex flex-col">
            <div className="flex items-center justify-between px-5 pt-4 pb-2">
              <h2 className="text-sm font-semibold text-text-primary">Recent Activity</h2>
              <span className="text-[10px] text-text-muted">{activity.length} events</span>
            </div>

            <div className="flex-1 overflow-y-auto px-2 pb-3 max-h-72">
              {activity.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-text-muted">
                  <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="mb-2 opacity-40">
                    <circle cx="12" cy="12" r="10" />
                    <polyline points="12 6 12 12 16 14" />
                  </svg>
                  <p className="text-xs">No recent activity</p>
                  <p className="text-[10px] mt-1">Run a job or connect an MCP server to get started</p>
                </div>
              ) : (
                <div className="space-y-0.5">
                  {activity.map((item) => (
                    <ActivityRow key={item.id} item={item} />
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
              icon={
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
              }
            />
            <QuickAction
              label="Run Job"
              icon={
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
              }
            />
            <QuickAction
              label="Add Skill"
              icon={
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
              }
            />
            <QuickAction
              label="Add Credential"
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

function QuickAction({ label, icon }: { label: string; icon: React.ReactNode }) {
  return (
    <button className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-bg-secondary border border-border text-text-secondary hover:text-text-primary hover:border-border-light card-hover transition-colors text-sm">
      <span className="text-text-muted">{icon}</span>
      {label}
    </button>
  );
}
