import { useEffect, useState } from "react";
import type { ConnectionStatus as Status } from "../hooks/useWebSocket";

interface ConnectionStatusProps {
  status: Status;
}

export default function ConnectionStatus({ status }: ConnectionStatusProps) {
  const [showLabel, setShowLabel] = useState(true);

  // Auto-hide "Connected" label after 3s
  useEffect(() => {
    if (status === "connected") {
      setShowLabel(true);
      const timer = setTimeout(() => setShowLabel(false), 3000);
      return () => clearTimeout(timer);
    }
    setShowLabel(true);
  }, [status]);

  const dotColor =
    status === "connected"
      ? "bg-accent"
      : status === "connecting"
        ? "bg-yellow-500"
        : "bg-error";

  const label =
    status === "connected"
      ? "Connected"
      : status === "connecting"
        ? "Connecting..."
        : "Disconnected";

  const shouldPulse = status === "connecting";

  return (
    <div className="flex items-center gap-1.5 px-2 py-1 rounded-full text-[11px] text-text-muted shrink-0">
      <span
        className={`w-1.5 h-1.5 rounded-full ${dotColor} ${shouldPulse ? "status-pulse" : ""}`}
      />
      {(showLabel || status !== "connected") && (
        <span className="animate-fade-in">{label}</span>
      )}
    </div>
  );
}
