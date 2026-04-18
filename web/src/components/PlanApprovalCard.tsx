import { useState } from "react";
import { approvePlan, rejectPlan } from "../api";
import type { PendingPlanInfo } from "../hooks/useChatStream";

interface PlanApprovalCardProps {
  plan: PendingPlanInfo;
  onResolved: () => void;
}

export default function PlanApprovalCard({
  plan,
  onResolved,
}: PlanApprovalCardProps) {
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState("");

  const handleApprove = async (trust: boolean) => {
    if (busy) return;
    setBusy(true);
    try {
      await approvePlan({ auto_approve_session: trust });
      onResolved();
    } catch (err) {
      console.error("Plan approve failed", err);
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await rejectPlan(rejectReason.trim() || undefined);
      onResolved();
    } catch (err) {
      console.error("Plan reject failed", err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="py-3 animate-fade-in">
      <div className="max-w-3xl mx-auto px-4">
        <div className="border border-amber/50 bg-amber/10 rounded-lg p-4 space-y-3">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border border-amber/60 bg-amber/20 text-amber text-[10px] font-semibold uppercase tracking-wider">
              <span className="w-1.5 h-1.5 rounded-full bg-amber pulse-dot" />
              Plan — review before I start
            </span>
          </div>

          {plan.steps.length > 0 ? (
            <ol className="list-decimal list-inside space-y-1 text-sm text-text-primary">
              {plan.steps.map((step, i) => (
                <li key={i} className="leading-snug">
                  {step}
                </li>
              ))}
            </ol>
          ) : (
            <pre className="text-xs text-text-secondary whitespace-pre-wrap font-sans">
              {plan.plan}
            </pre>
          )}

          {!rejecting ? (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <button
                onClick={() => handleApprove(false)}
                disabled={busy}
                className="px-3 py-1.5 rounded-md bg-accent text-bg-primary text-xs font-semibold hover:bg-accent/90 disabled:opacity-50 transition-colors"
              >
                ✅ Approve
              </button>
              <button
                onClick={() => handleApprove(true)}
                disabled={busy}
                className="px-3 py-1.5 rounded-md border border-accent/60 text-accent text-xs font-semibold hover:bg-accent/10 disabled:opacity-50 transition-colors"
                title="Auto-approve all plans for the next 30 minutes"
              >
                ⚡ Approve & trust 30min
              </button>
              <button
                onClick={() => setRejecting(true)}
                disabled={busy}
                className="px-3 py-1.5 rounded-md border border-border text-text-secondary text-xs hover:bg-bg-tertiary disabled:opacity-50 transition-colors ml-auto"
              >
                ❌ Reject
              </button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <input
                type="text"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Why? (optional)"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleReject();
                  if (e.key === "Escape") setRejecting(false);
                }}
                className="flex-1 min-w-[160px] px-2 py-1.5 rounded-md bg-bg-tertiary border border-border text-xs text-text-primary placeholder-text-muted"
              />
              <button
                onClick={handleReject}
                disabled={busy}
                className="px-3 py-1.5 rounded-md bg-red-500 text-white text-xs font-semibold hover:bg-red-500/90 disabled:opacity-50 transition-colors"
              >
                Reject
              </button>
              <button
                onClick={() => {
                  setRejecting(false);
                  setRejectReason("");
                }}
                disabled={busy}
                className="px-2 py-1.5 rounded-md text-text-muted text-xs hover:bg-bg-tertiary disabled:opacity-50 transition-colors"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
