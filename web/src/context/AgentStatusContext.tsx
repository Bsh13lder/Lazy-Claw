import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import * as api from "../api";
import type { AgentStatus, ActivityEvent, AgentMetrics } from "../api";

interface AgentStatusContextValue {
  agentStatus: AgentStatus | null;
  activityFeed: ActivityEvent[];
  metrics: AgentMetrics | null;
}

const AgentStatusContext = createContext<AgentStatusContextValue | null>(null);

export function AgentStatusProvider({ children }: { children: ReactNode }) {
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [activityFeed, setActivityFeed] = useState<ActivityEvent[]>([]);
  const [metrics, setMetrics] = useState<AgentMetrics | null>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;

    // Agent status — every 3s
    const pollStatus = async () => {
      try {
        const data = await api.getAgentStatus();
        if (aliveRef.current) setAgentStatus(data);
      } catch { /* ignore */ }
    };

    // Activity feed — every 5s
    const pollFeed = async () => {
      try {
        const data = await api.getActivityFeed(30);
        if (aliveRef.current) setActivityFeed(Array.isArray(data) ? data : []);
      } catch { /* ignore */ }
    };

    // Metrics — every 10s
    const pollMetrics = async () => {
      try {
        const data = await api.getAgentMetrics();
        if (aliveRef.current) setMetrics(data);
      } catch { /* ignore */ }
    };

    // Initial fetch
    pollStatus();
    pollFeed();
    pollMetrics();

    const statusId = setInterval(pollStatus, 3000);
    const feedId = setInterval(pollFeed, 5000);
    const metricsId = setInterval(pollMetrics, 10000);

    return () => {
      aliveRef.current = false;
      clearInterval(statusId);
      clearInterval(feedId);
      clearInterval(metricsId);
    };
  }, []);

  return (
    <AgentStatusContext.Provider value={{ agentStatus, activityFeed, metrics }}>
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
