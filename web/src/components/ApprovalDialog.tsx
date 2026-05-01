import { useEffect } from "react";
import Modal from "./Modal";
import type { ApprovalRequest } from "../hooks/useChatStream";

/**
 * Modal prompting the user to approve / deny a gated skill execution.
 *
 * The server (`WebSocketCallback.on_approval_request`) sent an
 * `approval_request` frame and is now blocking the agent loop on a
 * matching `approval_response`. If the user dismisses without choosing
 * (or 2 minutes pass) the server defaults to **deny** — never silently
 * approves. Set `LAZYCLAW_AUTO_APPROVE=1` to bypass dialogs entirely
 * for trusted self-host setups.
 */
interface Props {
  request: ApprovalRequest | null;
  onDecide: (requestId: string, approved: boolean) => void;
}

export default function ApprovalDialog({ request, onDecide }: Props) {
  // Keyboard shortcuts: Y = approve, N = deny. Defensive — only mount
  // listener when there's a live request so we don't intercept normal
  // typing in chat input.
  useEffect(() => {
    if (!request) return;
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement) {
        const tag = e.target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA") return;
      }
      if (e.key === "y" || e.key === "Y") {
        e.preventDefault();
        onDecide(request.requestId, true);
      } else if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        onDecide(request.requestId, false);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [request, onDecide]);

  return (
    <Modal
      open={!!request}
      onClose={() => {
        if (request) onDecide(request.requestId, false);
      }}
      title="Approval required"
    >
      {request && (
        <div className="space-y-4 text-sm">
          <p className="text-text-primary">
            The agent wants to run a permission-gated skill:{" "}
            <code className="px-1.5 py-0.5 rounded bg-bg-hover text-accent font-mono text-[12px]">
              {request.skill}
            </code>
          </p>
          {Object.keys(request.args).length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-[0.2em] text-text-muted mb-1">
                Arguments
              </div>
              <pre className="text-[12px] font-mono whitespace-pre-wrap break-words bg-bg-primary border border-border rounded-md p-3 max-h-[280px] overflow-auto">
                {JSON.stringify(request.args, null, 2)}
              </pre>
            </div>
          )}
          <p className="text-[12px] text-text-muted">
            Press <kbd className="px-1 rounded bg-bg-hover">Y</kbd> to approve,{" "}
            <kbd className="px-1 rounded bg-bg-hover">N</kbd> to deny. The agent
            will time out and deny automatically if you don't decide within 2
            minutes.
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <button
              onClick={() => onDecide(request.requestId, false)}
              className="px-4 py-2 rounded-lg border border-border text-text-secondary hover:bg-bg-hover transition-colors text-sm"
            >
              Deny
            </button>
            <button
              onClick={() => onDecide(request.requestId, true)}
              className="px-4 py-2 rounded-lg bg-accent text-bg-primary font-semibold hover:bg-accent/90 transition-colors text-sm"
            >
              Approve
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
