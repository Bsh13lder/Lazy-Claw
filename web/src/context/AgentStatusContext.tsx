import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import * as api from "../api";
import type { AgentStatus, ActivityEvent, AgentMetrics, EcoUsage, EcoCosts } from "../api";

interface AgentStatusContextValue {
  agentStatus: AgentStatus | null;
  activityFeed: ActivityEvent[];
  metrics: AgentMetrics | null;
  ecoUsage: EcoUsage | null;
  ecoCosts: EcoCosts | null;
  // Force an immediate /api/agents/status + activity feed refresh.
  // Called by chat WS on `task_started` / `task_step` / `task_phase` /
  // `task_completed` / `background_started` / `background_done` /
  // `background_failed` so Activity/Overview update without the 3 s poll lag.
  refreshStatus: () => void;
}

const AgentStatusContext = createContext<AgentStatusContextValue | null>(null);

export function AgentStatusProvider({ children }: { children: ReactNode }) {
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [activityFeed, setActivityFeed] = useState<ActivityEvent[]>([]);
  const [metrics, setMetrics] = useState<AgentMetrics | null>(null);
  const [ecoUsage, setEcoUsage] = useState<EcoUsage | null>(null);
  const [ecoCosts, setEcoCosts] = useState<EcoCosts | null>(null);
  const aliveRef = useRef(true);
  const lastRefreshRef = useRef(0);

  const pollStatusOnce = useCallback(async () => {
    try {
      const data = await api.getAgentStatus();
      if (aliveRef.current) setAgentStatus(data);
    } catch { /* ignore */ }
  }, []);

  const pollFeedOnce = useCallback(async () => {
    try {
      const data = await api.getActivityFeed(30);
      if (aliveRef.current) setActivityFeed(Array.isArray(data) ? data : []);
    } catch { /* ignore */ }
  }, []);

  // Triggered by WS task lifecycle events. Coalesced to ≤1 fire / 250 ms
  // so a burst of `task_step` frames doesn't hammer the gateway.
  const refreshStatus = useCallback(() => {
    const now = Date.now();
    if (now - lastRefreshRef.current < 250) return;
    lastRefreshRef.current = now;
    pollStatusOnce();
    pollFeedOnce();
  }, [pollStatusOnce, pollFeedOnce]);

  useEffect(() => {
    aliveRef.current = true;

    // Metrics + ECO usage — every 10s
    const pollMetrics = async () => {
      try {
        const data = await api.getAgentMetrics();
        if (aliveRef.current) setMetrics(data);
      } catch { /* ignore */ }
      try {
        const usage = await api.getEcoUsage();
        if (aliveRef.current) setEcoUsage(usage);
      } catch { /* ignore */ }
      try {
        const costs = await api.getEcoCosts();
        if (aliveRef.current) setEcoCosts(costs);
      } catch { /* ignore */ }
    };

    // Initial fetch
    pollStatusOnce();
    pollFeedOnce();
    pollMetrics();

    const statusId = setInterval(pollStatusOnce, 3000);
    const feedId = setInterval(pollFeedOnce, 5000);
    const metricsId = setInterval(pollMetrics, 10000);

    return () => {
      aliveRef.current = false;
      clearInterval(statusId);
      clearInterval(feedId);
      clearInterval(metricsId);
    };
  }, [pollStatusOnce, pollFeedOnce]);

  return (
    <AgentStatusContext.Provider
      value={{ agentStatus, activityFeed, metrics, ecoUsage, ecoCosts, refreshStatus }}
    >
      {children}
    </AgentStatusContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAgentStatus(): AgentStatusContextValue {
  const ctx = useContext(AgentStatusContext);
  if (!ctx) throw new Error("useAgentStatus must be used within AgentStatusProvider");
  return ctx;
}
