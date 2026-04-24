import { useMemo, useState } from "react";
import { useAgentStatus } from "../context/AgentStatusContext";
import type { AgentTask } from "../api";
import { cancelTask } from "../api";
import { LaneColumn } from "../components/LaneColumn";
import { HistoryPanel } from "../components/HistoryPanel";
import { formatElapsed } from "../components/TaskRow";
import { iconFor, colorFor } from "../components/toolIcons";

/**
 * Activity — the full telemetry twin of the Overview dashboard.
 *
 *   ┌─── Deck (same three lanes as Overview) ──────────────────┐
 *   │                                                            │
 *   ├─── Master / detail split ──────────────────────────────────┤
 *   │   Left: live + recent task list                            │
 *   │   Right: full trace for the selected task                  │
 *   │                                                            │
 *   ├─── History panel (shared with Overview) ───────────────────┤
 *   └────────────────────────────────────────────────────────────┘
 */

function TaskSummaryRow({
  task,
  selected,
  onSelect,
}: {
  task: AgentTask;
  selected: boolean;
  onSelect: () => void;
}) {
  const running = task.status === "running";
  const statusColor =
    task.status === "done" ? "bg-green-400" :
    task.status === "failed" ? "bg-red-400" :
    task.status === "cancelled" ? "bg-yellow-400" :
    running ? "bg-accent live-pulse" :
    "bg-text-muted";
  const duration = task.duration_s ?? task.elapsed_s ?? 0;

  return (
    <button
      onClick={onSelect}
      className={`w-full text-left flex items-center gap-2.5 px-3 py-2 rounded-lg border transition-colors ${
        selected
          ? "bg-accent-soft border-accent/40"
          : "bg-bg-secondary border-border hover:border-border-light"
      }`}
    >
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusColor}`} />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] text-text-primary truncate">{task.name}</p>
        <p className="text-[10px] text-text-muted truncate">
          <span className="uppercase tracking-wider">{task.lane}</span>
          {task.current_tool ? <> · {task.current_tool}</> : null}
        </p>
      </div>
      <span className="ticker text-[10px] text-text-muted shrink-0">
        {formatElapsed(duration)}
      </span>
    </button>
  );
}

function DetailPane({ task }: { task: AgentTask | null }) {
  if (!task) {
    return (
      <div className="rounded-xl bg-bg-secondary border border-border p-6 text-center text-sm text-text-muted min-h-[320px] flex flex-col items-center justify-center">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.3" className="opacity-30 mb-2">
          <rect x="4" y="4" width="16" height="16" rx="2" />
          <path d="M4 10h16" />
        </svg>
        Select a task on the left to see its full trace.
      </div>
    );
  }

  const running = task.status === "running";
  const request = task.instruction || task.description || "";
  const result = task.result || task.result_preview || "";
  const recent = task.recent_tools ?? [];

  return (
    <div className="rounded-xl bg-bg-secondary border border-border p-4 space-y-3 animate-fade-in">
      {/* Head */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`w-1.5 h-1.5 rounded-full ${running ? "bg-accent live-pulse" : "bg-text-muted"}`} />
        <h3 className="text-sm font-semibold text-text-primary truncate flex-1 min-w-0" title={task.name}>
          {task.name}
        </h3>
        <span className="text-[10px] text-text-muted uppercase tracking-wider">{task.lane}</span>
        <span className="text-[10px] text-text-muted uppercase tracking-wider">{task.status}</span>
        {running && (
          <button
            onClick={() => cancelTask(task.task_id).catch(() => {})}
            className="text-[10px] px-2 py-0.5 rounded-full bg-red-900/30 text-red-400 hover:bg-red-900/50 transition-colors uppercase tracking-wider"
          >
            cancel
          </button>
        )}
      </div>

      {/* Meta row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
        <div>
          <p className="text-text-muted uppercase tracking-wider text-[10px]">Duration</p>
          <p className="ticker text-text-primary">
            {formatElapsed(task.duration_s ?? task.elapsed_s ?? 0)}
          </p>
        </div>
        <div>
          <p className="text-text-muted uppercase tracking-wider text-[10px]">Steps</p>
          <p className="ticker text-text-primary">{task.step_count ?? 0}</p>
        </div>
        <div>
          <p className="text-text-muted uppercase tracking-wider text-[10px]">Phase</p>
          <p className="text-text-primary">{task.phase ?? "—"}</p>
        </div>
        <div>
          <p className="text-text-muted uppercase tracking-wider text-[10px]">ID</p>
          <p className="font-mono text-text-primary truncate">{task.task_id.slice(0, 8)}</p>
        </div>
      </div>

      {/* Request */}
      {request && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-text-muted mb-1">Request</p>
          <div className="text-xs text-text-secondary bg-bg-tertiary rounded-lg px-3 py-2 max-h-[140px] overflow-y-auto whitespace-pre-wrap">
            {request}
          </div>
        </div>
      )}

      {/* Tool trace */}
      {recent.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-text-muted mb-1">
            Tool sequence {recent.length > 0 && <span className="ticker text-text-secondary">· {recent.length}</span>}
          </p>
          <div className="flex items-center gap-1 flex-wrap">
            {recent.map((rt, i) => (
              <span
                key={`${rt}-${i}`}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] border border-border bg-bg-tertiary/60"
                title={rt}
              >
                <span className={colorFor(rt)}>{iconFor(rt)}</span>
                <span className="text-text-secondary truncate max-w-[110px]">{rt}</span>
                {i < recent.length - 1 && <span className="text-text-muted">→</span>}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Result */}
      {result && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-text-muted mb-1">Result</p>
          <div className="text-xs text-text-secondary bg-bg-tertiary rounded-lg px-3 py-2 max-h-[260px] overflow-y-auto whitespace-pre-wrap font-mono">
            {result}
          </div>
        </div>
      )}

      {/* Error */}
      {task.error && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-red-400 mb-1">Error</p>
          <div className="text-xs text-red-300 bg-red-900/20 rounded-lg px-3 py-2 whitespace-pre-wrap font-mono">
            {task.error}
          </div>
        </div>
      )}

      {!request && !result && recent.length === 0 && !task.error && (
        <p className="text-xs text-text-muted italic">
          {running ? "No trace yet — the task just started." : "No trace available for this task."}
        </p>
      )}
    </div>
  );
}

export default function Activity() {
  const { agentStatus, activityFeed, metrics } = useAgentStatus();

  const foreground = useMemo(
    () => (agentStatus?.active ?? []).filter((t) => t.lane !== "specialist"),
    [agentStatus],
  );
  const background = useMemo(() => agentStatus?.background ?? [], [agentStatus]);
  const specialists = useMemo(
    () => (agentStatus?.active ?? []).filter((t) => t.lane === "specialist"),
    [agentStatus],
  );
  const recent = useMemo(() => agentStatus?.recent ?? [], [agentStatus]);
  const inFlight = foreground.length + background.length + specialists.length;

  const allTasks = useMemo<AgentTask[]>(() => {
    return [...foreground, ...background, ...specialists, ...recent];
  }, [foreground, background, specialists, recent]);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectedTask = selectedId ? allTasks.find((t) => t.task_id === selectedId) ?? null : null;

  const cancelledCount = recent.filter((t) => t.status === "cancelled").length;

  return (
    <div className="h-full overflow-y-auto grid-bg">
      <div className="max-w-6xl mx-auto px-6 py-6 space-y-5">

        {/* Header strip */}
        <header className="flex flex-wrap items-baseline gap-3">
          <h1 className="text-lg font-semibold text-text-primary">Activity</h1>
          <p className="text-[11px] text-text-muted">
            Live agent & task monitor
          </p>
          <div className="ml-auto flex items-center gap-3 text-[11px]">
            {inFlight > 0 && (
              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border border-accent/40 bg-accent-soft text-accent">
                <span className="w-1.5 h-1.5 rounded-full bg-accent live-pulse" />
                <span className="ticker">{inFlight}</span> running
              </span>
            )}
            {cancelledCount > 0 && (
              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border border-yellow-400/30 bg-yellow-900/20 text-yellow-400">
                <span className="ticker">{cancelledCount}</span> cancelled
              </span>
            )}
            {metrics && (
              <>
                <span className="text-text-muted">
                  Success <span className={`ticker ${metrics.success_rate >= 80 ? "text-green-400" : metrics.success_rate >= 50 ? "text-amber" : "text-red-400"}`}>{metrics.success_rate}%</span>
                </span>
                <span className="text-text-muted">
                  Avg <span className="ticker text-text-primary">{metrics.avg_duration_s}s</span>
                </span>
                <span className="text-text-muted">
                  Tools today <span className="ticker text-cyan">{metrics.tool_calls_today}</span>
                </span>
              </>
            )}
          </div>
        </header>

        {/* Deck — three lanes */}
        <section>
          <div className="flex items-baseline gap-2 mb-3">
            <h2 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary">
              Running now
            </h2>
            <span className="text-[10px] text-text-muted">
              Click a card to inspect its full trace below
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <LaneColumn
              lane="foreground"
              tasks={foreground}
              emptyLabel="No foreground tasks."
              compact={false}
            />
            <LaneColumn
              lane="background"
              tasks={background}
              emptyLabel="No background agents running."
              compact={false}
            />
            <LaneColumn
              lane="specialist"
              tasks={specialists}
              emptyLabel="No delegated specialists."
              compact={false}
            />
          </div>
        </section>

        {/* Master / Detail split */}
        <section>
          <div className="flex items-baseline gap-2 mb-3">
            <h2 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary">
              Task inspector
            </h2>
            <span className="text-[10px] text-text-muted">
              Pick a task to see its request, tool sequence, and result
            </span>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-3">
            {/* Master list */}
            <div className="space-y-1.5 max-h-[440px] overflow-y-auto pr-1">
              {inFlight === 0 && recent.length === 0 ? (
                <p className="text-xs text-text-muted text-center py-8">No tasks yet.</p>
              ) : (
                <>
                  {[...foreground, ...background, ...specialists].map((t) => (
                    <TaskSummaryRow
                      key={t.task_id}
                      task={t}
                      selected={selectedId === t.task_id}
                      onSelect={() => setSelectedId(t.task_id)}
                    />
                  ))}
                  {recent.slice(0, 20).map((t) => (
                    <TaskSummaryRow
                      key={t.task_id}
                      task={t}
                      selected={selectedId === t.task_id}
                      onSelect={() => setSelectedId(t.task_id)}
                    />
                  ))}
                </>
              )}
            </div>

            {/* Detail pane */}
            <DetailPane task={selectedTask} />
          </div>
        </section>

        {/* History with rich toggles */}
        <section>
          <div className="flex items-baseline gap-2 mb-3">
            <h2 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-secondary">
              History & cancels
            </h2>
            <span className="text-[10px] text-text-muted">
              Filter by lane, status, kind, or range — flip Cards / Timeline / Table
            </span>
          </div>
          <HistoryPanel
            recent={recent}
            events={activityFeed}
            defaultView="cards"
            defaultStatus="all"
          />
        </section>

        {/* Last refresh stamp — bottom marker */}
        {agentStatus && (
          <p className="text-[10px] text-text-muted text-center pt-2">
            Live poll · 3s status · 5s feed · 10s metrics
          </p>
        )}

      </div>
    </div>
  );
}

