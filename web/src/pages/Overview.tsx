import { useEffect, useState } from "react";
import * as api from "../api";
import { useAuth } from "../context/AuthContext";
import { useChat } from "../context/ChatContext";
import { useAgentStatus } from "../context/AgentStatusContext";
import { LaneColumn } from "../components/LaneColumn";
import { SavedAgentsRail } from "../components/SavedAgentsRail";
import { HistoryPanel } from "../components/HistoryPanel";
import { MyTasksPanel } from "../components/MyTasksPanel";
import type { Page } from "../components/NavShell";

/**
 * Ops Deck — the dashboard is shaped around one question:
 *   "What are my agents doing right now, what's running in the background,
 *    and what's scheduled to run later?"
 *
 * Five regions, top-to-bottom:
 *   1. Command Strip   — gateway status, mode, in-flight count, CTAs
 *   2. The Deck        — three lanes: Foreground · Background · Specialists
 *   3. Saved Agents    — scheduled jobs + watchers, with next-run countdowns
 *   4. History panel   — rich toggles over the activity stream
 *   5. Telemetry fold  — success rate, cost, local-model %  (collapsed by default)
 *
 * Intentionally not here: skill count, memory count, Quick Actions grid,
 * "resources" gauges — those were vanity or belong on their own pages.
 */

/* ── Command Strip ───────────────────────────────────────────── */

function LiveDot({ ok }: { ok: boolean | null }) {
  if (ok === null) return <span className="inline-block w-2 h-2 rounded-full bg-text-muted animate-pulse" />;
  if (!ok) return <span className="inline-block w-2 h-2 rounded-full bg-error" />;
  return <span className="inline-block w-2 h-2 rounded-full bg-accent live-pulse" />;
}

function ModePill({ mode }: { mode: string }) {
  const cls =
    mode === "eco" ? "bg-accent-soft text-accent" :
    mode === "hybrid" ? "bg-cyan-soft text-cyan" :
    mode === "claude" ? "bg-purple-900/30 text-purple-400" :
    "bg-bg-tertiary text-text-muted";
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-full uppercase tracking-[0.12em] font-medium ${cls}`}>
      {mode}
    </span>
  );
}

function CommandStrip({
  username,
  gatewayOk,
  version,
  uptime,
  mode,
  inFlight,
  pendingApprovals,
  onNavigate,
  onNewChat,
}: {
  username: string;
  gatewayOk: boolean | null;
  version: string;
  uptime: string;
  mode: string;
  inFlight: number;
  pendingApprovals: number;
  onNavigate: (p: Page) => void;
  onNewChat: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-3 rounded-xl glass-card">
      <LiveDot ok={gatewayOk} />
      <div className="min-w-0">
        <p className="text-sm text-text-primary truncate">
          <span className="text-text-muted">Hi</span> {username}
          <span className="text-text-muted"> · </span>
          <span className="text-text-secondary">
            {gatewayOk === null ? "connecting…" : gatewayOk ? "online" : "gateway offline"}
          </span>
        </p>
        <p className="text-[10px] text-text-muted tracking-wide">
          v{version} · {uptime} · AES-256
        </p>
      </div>

      <div className="ml-auto flex items-center gap-2 flex-wrap">
        <ModePill mode={mode} />

        {inFlight > 0 && (
          <span className="inline-flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-full border border-accent/40 bg-accent-soft text-accent">
            <span className="w-1.5 h-1.5 rounded-full bg-accent live-pulse" />
            <span className="ticker">{inFlight} in flight</span>
          </span>
        )}

        {pendingApprovals > 0 && (
          <button
            onClick={() => onNavigate("audit")}
            className="inline-flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-full border border-amber/40 bg-amber/10 text-amber hover:bg-amber/20 transition-colors"
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
            {pendingApprovals} awaiting approval
          </button>
        )}

        <button
          onClick={onNewChat}
          className="inline-flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-full border border-accent/40 text-accent hover:bg-accent-soft transition-colors"
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          New chat
        </button>
      </div>
    </div>
  );
}

/* ── Telemetry fold ──────────────────────────────────────────── */

function TelemetryFold({
  metrics,
  ecoUsage,
  ecoCosts,
  onNavigate,
}: {
  metrics: api.AgentMetrics | null;
  ecoUsage: api.EcoUsage | null;
  ecoCosts: api.EcoCosts | null;
  onNavigate: (p: Page) => void;
}) {
  const [open, setOpen] = useState(false);

  const cost = ecoCosts?.total_cost ?? 0;
  const costLabel = cost === 0 ? "Free" : `$${cost.toFixed(4)}`;
  const free = ecoUsage?.free_percentage ?? 0;
  const local = ecoCosts?.local_pct ?? 0;
  const calls = ecoCosts?.total_calls ?? ecoUsage?.total ?? 0;

  const summaryText = metrics
    ? `Success ${metrics.success_rate}% · ${metrics.tool_calls_today} tool calls today · ${costLabel} · ${local}% local`
    : `${costLabel} · ${local}% local · ${free}% free`;

  return (
    <div className="rounded-xl border border-border bg-bg-secondary/60">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-4 py-2.5 text-left"
      >
        <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-muted">
          Telemetry
        </span>
        <span className="text-[11px] text-text-secondary truncate">
          {summaryText}
        </span>
        <svg
          width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          className={`ml-auto text-text-muted transition-transform ${open ? "rotate-180" : ""}`}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div className="px-4 pb-4 pt-1 grid grid-cols-2 md:grid-cols-4 gap-3 animate-fade-in">
          <div>
            <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Success</p>
            <p className={`text-lg font-semibold stat-value ${metrics && metrics.success_rate >= 80 ? "text-green-400" : metrics && metrics.success_rate >= 50 ? "text-amber" : "text-text-primary"}`}>
              {metrics ? `${metrics.success_rate}%` : "—"}
            </p>
            {metrics && (
              <div className="h-1 bg-bg-tertiary rounded-full mt-2 overflow-hidden">
                <div className="h-full bg-green-400 rounded-full" style={{ width: `${metrics.success_rate}%` }} />
              </div>
            )}
          </div>
          <div>
            <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Total cost</p>
            <p className={`text-lg font-semibold stat-value ${cost === 0 ? "text-accent" : "text-amber"}`}>
              {costLabel}
            </p>
            <p className="text-[10px] text-text-muted mt-1 ticker">{calls.toLocaleString()} calls</p>
          </div>
          <div>
            <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Free / Local</p>
            <p className="text-lg font-semibold stat-value text-accent">{free}%</p>
            <div className="h-1 bg-bg-tertiary rounded-full mt-2 overflow-hidden">
              <div className="h-full bg-accent rounded-full" style={{ width: `${free}%` }} />
            </div>
          </div>
          <div>
            <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Local models</p>
            <p className="text-lg font-semibold stat-value text-cyan">{local}%</p>
            <button
              onClick={() => onNavigate("settings")}
              className="mt-2 text-[10px] text-text-muted hover:text-accent transition-colors uppercase tracking-wider"
            >
              tune →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Main component ──────────────────────────────────────────── */

export default function Overview({ onNavigate }: { onNavigate: (page: Page) => void }) {
  const { user } = useAuth();
  const { createSession } = useChat();
  const { agentStatus, activityFeed, metrics, ecoUsage, ecoCosts } = useAgentStatus();

  const [gatewayOk, setGatewayOk] = useState<boolean | null>(null);
  const [version, setVersion] = useState("...");
  const [uptime, setUptime] = useState("—");
  const [mode, setMode] = useState("…");
  const [pendingApprovals, setPendingApprovals] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const [h, eco, approvals] = await Promise.allSettled([
        api.healthCheck(),
        api.getEcoSettings(),
        api.listPendingApprovals(),
      ]);
      if (!alive) return;

      if (h.status === "fulfilled") {
        setGatewayOk(true);
        setVersion(h.value?.version ?? "unknown");
        if (h.value?.started_at) {
          const elapsed = Math.floor(Date.now() / 1000 - h.value.started_at);
          const hours = Math.floor(elapsed / 3600);
          const minutes = Math.floor((elapsed % 3600) / 60);
          setUptime(hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`);
        } else {
          setUptime("online");
        }
      } else {
        setGatewayOk(false);
      }
      if (eco.status === "fulfilled") setMode(eco.value?.mode ?? "—");
      if (approvals.status === "fulfilled") setPendingApprovals(Array.isArray(approvals.value) ? approvals.value.length : 0);
    };
    load();
    const id = setInterval(load, 20_000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // Split the active + background streams into the three lanes.
  const foreground = (agentStatus?.active ?? []).filter((t) => t.lane !== "specialist");
  const background = agentStatus?.background ?? [];
  const specialists = (agentStatus?.active ?? []).filter((t) => t.lane === "specialist");
  const inFlight = foreground.length + background.length + specialists.length;

  const username = user?.display_name ?? user?.username ?? "there";

  return (
    <div className="h-full overflow-y-auto grid-bg">
      <div className="max-w-6xl mx-auto px-6 py-6 space-y-5">

        {/* 1. Command strip */}
        <CommandStrip
          username={username}
          gatewayOk={gatewayOk}
          version={version}
          uptime={uptime}
          mode={mode}
          inFlight={inFlight}
          pendingApprovals={pendingApprovals}
          onNavigate={onNavigate}
          onNewChat={() => createSession()}
        />

        {/* ═══════════════════════════════════════════════════════════
            2. MY TASKS — what the user dictated.
               Left: the encrypted todo list (MyTasksPanel).
               Right: the foreground chat lane (what I'm asking AI right now).
           ═══════════════════════════════════════════════════════════ */}
        <section className="rounded-2xl bg-bg-secondary/40 border border-border/60 p-4 space-y-3">
          <div className="flex items-baseline gap-2">
            <h2 className="text-sm font-semibold text-text-primary flex items-center gap-2">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-accent">
                <path d="M9 11l3 3L22 4" />
                <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
              </svg>
              My tasks
            </h2>
            <span className="text-[10px] text-text-muted">
              Todos I've dictated + what I'm asking AI right now
            </span>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <MyTasksPanel onNavigate={onNavigate} />
            <LaneColumn
              lane="foreground"
              tasks={foreground}
              emptyLabel="Not in an active chat."
              ctaLabel="+ Start one"
              onCta={() => createSession()}
              compact={false}
            />
          </div>
        </section>

        {/* ═══════════════════════════════════════════════════════════
            3. AGENT TASKS — what the agent is doing on its own.
               Background workers, delegated specialists, scheduled
               automations, and browser watchers.
           ═══════════════════════════════════════════════════════════ */}
        <section className="rounded-2xl bg-bg-secondary/40 border border-border/60 p-4 space-y-3">
          <div className="flex items-baseline gap-2">
            <h2 className="text-sm font-semibold text-text-primary flex items-center gap-2">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-cyan">
                <circle cx="12" cy="12" r="9" />
                <path d="M8 12l3 3 5-5" />
                <path d="M12 3v2M12 19v2M3 12h2M19 12h2" />
              </svg>
              Agent tasks
            </h2>
            <span className="text-[10px] text-text-muted">
              Background workers · delegated specialists · scheduled
            </span>
            <button
              onClick={() => onNavigate("activity")}
              className="ml-auto text-[10px] text-text-muted hover:text-accent transition-colors uppercase tracking-wider"
            >
              full activity →
            </button>
          </div>

          {/* Running lanes */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <LaneColumn
              lane="background"
              tasks={background}
              emptyLabel="No background agents running."
              ctaLabel="Browse jobs"
              onCta={() => onNavigate("jobs")}
            />
            <LaneColumn
              lane="specialist"
              tasks={specialists}
              emptyLabel="No specialists delegated right now."
            />
          </div>

          {/* Scheduled + watchers */}
          <SavedAgentsRail onNavigate={onNavigate} />
        </section>

        {/* 4. History + cancel log */}
        <section>
          <div className="flex items-baseline gap-2 mb-3">
            <h2 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary">
              History
            </h2>
            <span className="text-[10px] text-text-muted">
              Filter, search, and review cancels
            </span>
          </div>
          <HistoryPanel
            recent={agentStatus?.recent ?? []}
            events={activityFeed}
            defaultView="cards"
            defaultStatus="all"
          />
        </section>

        {/* 5. Telemetry fold */}
        <TelemetryFold
          metrics={metrics}
          ecoUsage={ecoUsage}
          ecoCosts={ecoCosts}
          onNavigate={onNavigate}
        />

      </div>
    </div>
  );
}
