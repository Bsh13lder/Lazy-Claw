import { useEffect, useRef, useState } from "react";
import type { BrowserFramePair, BrowserSession } from "../hooks/useChatStream";
import { browserActionIcon } from "./toolIcons";
import * as api from "./../api";

interface Props {
  session: BrowserSession;
  onDismiss: () => void;
}

function shortHost(url: string | undefined): string {
  if (!url) return "";
  try {
    const u = new URL(url);
    return u.host.replace(/^www\./, "") + (u.pathname.length > 1 ? u.pathname : "");
  } catch {
    return url.slice(0, 60);
  }
}

function relativeTime(ts: number): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ago`;
}

export default function BrowserCanvas({ session, onDismiss }: Props) {
  const [expanded, setExpanded] = useState(true);
  const [thumbUrl, setThumbUrl] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [helpText, setHelpText] = useState("");
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saveResult, setSaveResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [vncUrl, setVncUrl] = useState<string | null>(session.takeoverUrl ?? null);
  const [hostStatus, setHostStatus] = useState<api.HostBrowserStatus | null>(null);
  const [hostSetup, setHostSetup] = useState<
    | null
    | { command: string; warning: string }
  >(null);
  const [liveMode, setLiveMode] = useState<{ active: boolean; remaining: number }>({
    active: false, remaining: 0,
  });
  const [, forceTick] = useState(0);
  const lastBlobUrlRef = useRef<string | null>(null);
  const refreshOnExpandRef = useRef(false);

  // Refresh the relative timestamps every 5s
  useEffect(() => {
    const id = window.setInterval(() => forceTick((n) => n + 1), 5000);
    return () => window.clearInterval(id);
  }, []);

  // Poll the thumbnail when expanded. Live mode → 700ms, default → 2s.
  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    const fetchOnce = async () => {
      try {
        const blob = await api.getBrowserFrame();
        if (cancelled) return;
        if (lastBlobUrlRef.current) {
          URL.revokeObjectURL(lastBlobUrlRef.current);
          lastBlobUrlRef.current = null;
        }
        if (blob) {
          const u = URL.createObjectURL(blob);
          lastBlobUrlRef.current = u;
          setThumbUrl(u);
        } else {
          setThumbUrl(null);
        }
      } catch {
        /* ignore */
      }
    };
    // First open after a long pause? Force a fresh capture so we don't
    // show a stale frame from a previous flow (the actual bug the user hit).
    if (!refreshOnExpandRef.current) {
      refreshOnExpandRef.current = true;
      api.refreshBrowserFrame().catch(() => {/* ok if no browser yet */});
    }
    fetchOnce();
    const id = window.setInterval(fetchOnce, liveMode.active ? 700 : 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      if (lastBlobUrlRef.current) {
        URL.revokeObjectURL(lastBlobUrlRef.current);
        lastBlobUrlRef.current = null;
      }
    };
  }, [expanded, session.thumbnailVersion, liveMode.active]);

  // Pull live-mode status on mount + tick remaining time once a second when active.
  useEffect(() => {
    let cancelled = false;
    api.getBrowserLiveMode().then((s) => {
      if (cancelled) return;
      setLiveMode({ active: s.active, remaining: Math.round(s.remaining_seconds) });
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!liveMode.active) return;
    const id = window.setInterval(() => {
      setLiveMode((prev) => {
        const remaining = Math.max(0, prev.remaining - 1);
        return remaining === 0 ? { active: false, remaining: 0 } : { ...prev, remaining };
      });
    }, 1000);
    return () => window.clearInterval(id);
  }, [liveMode.active]);

  // Sync takeover URL from session events.
  useEffect(() => {
    setVncUrl(session.takeoverUrl ?? null);
  }, [session.takeoverUrl]);

  const startTakeover = async () => {
    setBusy(true);
    try {
      const r = await api.startBrowserRemoteSession();
      setVncUrl(r.url);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      alert(`Could not start takeover:\n${msg}`);
    } finally {
      setBusy(false);
    }
  };

  const stopTakeover = async () => {
    setBusy(true);
    try {
      await api.stopBrowserRemoteSession();
      setVncUrl(null);
    } finally {
      setBusy(false);
    }
  };

  // Poll the host-browser status on mount + every 10s so the badge reflects
  // reality when the user relaunches Brave outside this UI.
  useEffect(() => {
    let cancelled = false;
    const fetchStatus = () => {
      api.getHostBrowserStatus()
        .then((s) => { if (!cancelled) setHostStatus(s); })
        .catch(() => {/* best-effort */});
    };
    fetchStatus();
    const id = window.setInterval(fetchStatus, 10_000);
    return () => { cancelled = true; window.clearInterval(id); };
  }, []);

  const useHostBrowser = async () => {
    setBusy(true);
    try {
      const r = await api.startHostBrowserSession();
      if (r.status === "connected") {
        setHostSetup(null);
        setHostStatus((prev) => prev ? { ...prev, mode: "auto", reachable: true, last_source: "host" } : prev);
      } else {
        setHostSetup({ command: r.command, warning: r.warning });
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      alert(`Could not enable host browser:\n${msg}`);
    } finally {
      setBusy(false);
    }
  };

  const stopHostBrowser = async () => {
    setBusy(true);
    try {
      await api.stopHostBrowserSession();
      setHostStatus((prev) => prev ? { ...prev, mode: "off", last_source: "local" } : prev);
      setHostSetup(null);
    } finally {
      setBusy(false);
    }
  };

  const copySetupCommand = async () => {
    if (!hostSetup) return;
    try {
      await navigator.clipboard.writeText(hostSetup.command);
    } catch {/* clipboard may be unavailable */}
  };

  const sendHelp = () => {
    const text = helpText.trim();
    if (!text) return;
    // Side-note path is the cheapest way to inject mid-task guidance.
    // Reuse what already exists in ChatContext via a CustomEvent so we
    // don't have to thread props for the help button.
    window.dispatchEvent(new CustomEvent("lazyclaw:browser-help", { detail: text }));
    setHelpText("");
    setHelpOpen(false);
  };

  const saveAsTemplate = async () => {
    const name = saveName.trim();
    if (!name) return;
    setBusy(true);
    setSaveResult(null);
    try {
      const r = await api.saveTemplateFromCurrentSession(name);
      setSaveResult(
        `Saved '${r.template.name}' — captured ${r.captured.url_count} URL(s), ${r.captured.checkpoint_count} checkpoint(s).`,
      );
      setSaveName("");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setSaveResult(`Could not save: ${msg}`);
    } finally {
      setBusy(false);
    }
  };

  const refreshNow = async () => {
    setBusy(true);
    try {
      await api.refreshBrowserFrame();
      // Bump thumbnail effect by toggling expanded state imperceptibly is messy;
      // simpler: just fetch frame immediately.
      const blob = await api.getBrowserFrame();
      if (blob) {
        if (lastBlobUrlRef.current) URL.revokeObjectURL(lastBlobUrlRef.current);
        const u = URL.createObjectURL(blob);
        lastBlobUrlRef.current = u;
        setThumbUrl(u);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      alert(`Refresh failed: ${msg}`);
    } finally {
      setBusy(false);
    }
  };

  const toggleLive = async () => {
    setBusy(true);
    try {
      if (liveMode.active) {
        await api.stopBrowserLiveMode();
        setLiveMode({ active: false, remaining: 0 });
      } else {
        const r = await api.startBrowserLiveMode(300);
        setLiveMode({ active: r.active, remaining: Math.round(r.remaining_seconds) });
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      alert(`Live mode toggle failed: ${msg}`);
    } finally {
      setBusy(false);
    }
  };

  const recent = session.events.slice(-6).reverse();

  const host = shortHost(session.url);
  const title = session.title ?? "";

  return (
    <div className="border-b border-border bg-bg-secondary/60">
      {/* Header row */}
      <div className="flex items-center gap-2 px-3 py-2">
        <span className="text-sky-400">
          {browserActionIcon("goto")}
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-medium text-text-primary truncate" title={session.url}>
            {host || "Browser session"}
          </div>
          {title && (
            <div className="text-[10px] text-text-muted truncate" title={title}>
              {title}
            </div>
          )}
        </div>
        {liveMode.active && (
          <span
            className="text-[10px] px-1.5 py-0.5 rounded bg-rose-400/20 text-rose-400 border border-rose-400/40 flex items-center gap-1"
            title={`Live capture every action — ${liveMode.remaining}s left`}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-rose-400 live-pulse" />
            LIVE {liveMode.remaining}s
          </span>
        )}
        {vncUrl && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber/20 text-amber border border-amber/40">
            takeover
          </span>
        )}
        <button
          onClick={() => setExpanded((e) => !e)}
          className="p-1 rounded hover:bg-bg-hover text-text-muted"
          title={expanded ? "Collapse" : "Expand"}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            {expanded ? (
              <polyline points="18 15 12 9 6 15" />
            ) : (
              <polyline points="6 9 12 15 18 9" />
            )}
          </svg>
        </button>
        <button
          onClick={onDismiss}
          className="p-1 rounded hover:bg-bg-hover text-text-muted"
          title="Hide canvas"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="6" y1="6" x2="18" y2="18" />
            <line x1="6" y1="18" x2="18" y2="6" />
          </svg>
        </button>
      </div>

      {expanded && (
        <div className="px-3 pb-3 flex flex-col gap-2">
          {/* Thumbnail */}
          <div className="aspect-[16/9] w-full rounded-md border border-border bg-bg-primary overflow-hidden flex items-center justify-center">
            {thumbUrl ? (
              <img
                src={thumbUrl}
                alt="browser thumbnail"
                className="w-full h-full object-contain"
              />
            ) : (
              <span className="text-[11px] text-text-muted">
                {session.url ? "Loading screenshot…" : "Waiting for browser activity"}
              </span>
            )}
          </div>

          {/* Pre/post flipbook — shown only when Live mode emits frame pairs */}
          {session.lastFramePair && (session.lastFramePair.preB64 || session.lastFramePair.postB64) && (
            <FramePair pair={session.lastFramePair} />
          )}

          {/* Action timeline */}
          {recent.length > 0 && (
            <ul className="flex flex-col gap-1 text-[11px]">
              {recent.map((evt, idx) => (
                <li
                  key={`${evt.ts}-${idx}`}
                  className="flex items-center gap-2 text-text-secondary"
                >
                  <span className="text-sky-400 shrink-0">
                    {browserActionIcon(evt.action)}
                  </span>
                  <span className="truncate flex-1 min-w-0">
                    {evt.detail || `${evt.action ?? evt.kind}${evt.target ? ` → ${evt.target}` : ""}`}
                  </span>
                  <span className="text-text-muted shrink-0">{relativeTime(evt.ts)}</span>
                </li>
              ))}
            </ul>
          )}

          {/* Pending checkpoint banner */}
          {session.pendingCheckpoint && (
            <CheckpointBanner
              name={session.pendingCheckpoint.name}
              detail={session.pendingCheckpoint.detail}
            />
          )}

          {/* Buttons */}
          <div className="flex flex-wrap gap-1.5">
            <button
              onClick={refreshNow}
              disabled={busy}
              className="text-[11px] px-2 py-1 rounded border border-border hover:bg-bg-hover text-text-secondary disabled:opacity-50"
              title="Force a fresh screenshot now"
            >
              🔄 Refresh
            </button>
            <button
              onClick={toggleLive}
              disabled={busy}
              className={
                "text-[11px] px-2 py-1 rounded border disabled:opacity-50 " +
                (liveMode.active
                  ? "border-rose-400/50 bg-rose-400/10 text-rose-400 hover:bg-rose-400/20"
                  : "border-border text-text-secondary hover:bg-bg-hover")
              }
              title={liveMode.active
                ? "Stop capturing every action"
                : "Capture a screenshot after every browser action (5 min)"}
            >
              {liveMode.active ? "⏹ Stop live" : "👁 Live mode"}
            </button>
            <button
              onClick={() => setHelpOpen((o) => !o)}
              disabled={busy}
              className="text-[11px] px-2 py-1 rounded border border-border hover:bg-bg-hover text-text-secondary disabled:opacity-50"
            >
              💬 Help
            </button>
            <button
              onClick={() => { setSaveResult(null); setSaveOpen((o) => !o); }}
              disabled={busy}
              className="text-[11px] px-2 py-1 rounded border border-border hover:bg-bg-hover text-text-secondary disabled:opacity-50"
              title="Save this flow as a reusable template"
            >
              💾 Save as template
            </button>
            {!vncUrl ? (
              <button
                onClick={startTakeover}
                disabled={busy}
                className="text-[11px] px-2 py-1 rounded border border-border hover:bg-bg-hover text-text-secondary disabled:opacity-50"
                title="Watch + take control via VNC. Private: no cookies shared."
              >
                🎮 Take control
              </button>
            ) : (
              <>
                <a
                  href={vncUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[11px] px-2 py-1 rounded border border-amber/40 bg-amber/10 hover:bg-amber/20 text-amber"
                >
                  🔗 Open VNC
                </a>
                <button
                  onClick={stopTakeover}
                  disabled={busy}
                  className="text-[11px] px-2 py-1 rounded border border-border hover:bg-bg-hover text-text-secondary disabled:opacity-50"
                >
                  ⏹ End takeover
                </button>
              </>
            )}
            {hostStatus?.mode === "off" ? (
              <button
                onClick={useHostBrowser}
                disabled={busy}
                className="text-[11px] px-2 py-1 rounded border border-sky-400/50 bg-sky-400/10 hover:bg-sky-400/20 text-sky-400 disabled:opacity-50"
                title="Let the agent drive your real Brave with your cookies + logins."
              >
                🖥️ Use my Brave
              </button>
            ) : hostStatus ? (
              <button
                onClick={stopHostBrowser}
                disabled={busy}
                className="text-[11px] px-2 py-1 rounded border border-sky-400/40 bg-sky-400/10 hover:bg-sky-400/20 text-sky-400 disabled:opacity-50"
                title="Revert to the container Brave. Your host Brave stays open."
              >
                {hostStatus.reachable ? "🖥️ Using your Brave (stop)" : "🖥️ Waiting for Brave (stop)"}
              </button>
            ) : null}
          </div>

          {hostSetup && (
            <HostBrowserSetupCard
              command={hostSetup.command}
              warning={hostSetup.warning}
              busy={busy}
              onCopy={copySetupCommand}
              onRetry={useHostBrowser}
              onDismiss={() => setHostSetup(null)}
            />
          )}

          {helpOpen && (
            <div className="flex flex-col gap-1.5">
              <textarea
                value={helpText}
                onChange={(e) => setHelpText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault();
                    sendHelp();
                  }
                }}
                placeholder="Tell the agent what to do (e.g. 'click the second result', 'use my Madrid address')"
                rows={2}
                className="text-[11px] px-2 py-1.5 rounded border border-border bg-bg-primary text-text-primary resize-none focus:outline-none focus:border-accent"
              />
              <div className="flex justify-end gap-1.5">
                <button
                  onClick={() => setHelpOpen(false)}
                  className="text-[11px] px-2 py-1 rounded text-text-muted hover:text-text-primary"
                >
                  Cancel
                </button>
                <button
                  onClick={sendHelp}
                  disabled={!helpText.trim()}
                  className="text-[11px] px-2 py-1 rounded bg-accent text-bg-primary font-medium disabled:opacity-40"
                >
                  Send (⌘+Enter)
                </button>
              </div>
            </div>
          )}

          {saveOpen && (
            <div className="flex flex-col gap-1.5 rounded-md border border-border bg-bg-primary/60 p-2">
              <div className="text-[11px] text-text-muted">
                LazyClaw captures the recent URLs, checkpoints, and drafts a playbook automatically.
              </div>
              <input
                type="text"
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && saveName.trim() && !busy) {
                    e.preventDefault();
                    void saveAsTemplate();
                  }
                }}
                placeholder="Template name (e.g. 'DGT cita previa')"
                className="text-[11px] px-2 py-1.5 rounded border border-border bg-bg-primary text-text-primary focus:outline-none focus:border-accent"
                autoFocus
              />
              {saveResult && (
                <div className="text-[11px] text-text-secondary">{saveResult}</div>
              )}
              <div className="flex justify-end gap-1.5">
                <button
                  onClick={() => { setSaveOpen(false); setSaveResult(null); }}
                  className="text-[11px] px-2 py-1 rounded text-text-muted hover:text-text-primary"
                  disabled={busy}
                >
                  Close
                </button>
                <button
                  onClick={saveAsTemplate}
                  disabled={busy || !saveName.trim()}
                  className="text-[11px] px-2 py-1 rounded bg-accent text-bg-primary font-medium disabled:opacity-40"
                >
                  {busy ? "Saving…" : "Save"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface HostBrowserSetupCardProps {
  command: string;
  warning: string;
  busy: boolean;
  onCopy: () => void;
  onRetry: () => void;
  onDismiss: () => void;
}

function HostBrowserSetupCard({
  command, warning, busy, onCopy, onRetry, onDismiss,
}: HostBrowserSetupCardProps) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await onCopy();
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };
  return (
    <div className="rounded-md border border-sky-400/40 bg-sky-400/5 p-2 flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-sky-400">
          🖥️ Host Brave setup
        </span>
        <button
          onClick={onDismiss}
          className="text-[10px] text-text-muted hover:text-text-primary"
          title="Hide"
        >
          ✕
        </button>
      </div>
      <p className="text-[11px] text-text-secondary">
        Quit Brave completely (Cmd+Q) first, then paste this into Terminal:
      </p>
      <pre className="text-[10px] leading-tight p-2 rounded bg-bg-primary border border-border text-text-secondary overflow-x-auto whitespace-pre-wrap">
{command}
      </pre>
      <p className="text-[10px] text-amber/80">⚠️ {warning}</p>
      <div className="flex gap-1.5">
        <button
          onClick={copy}
          disabled={busy}
          className="text-[11px] px-2 py-1 rounded border border-border hover:bg-bg-hover text-text-secondary disabled:opacity-50"
        >
          {copied ? "✓ Copied" : "📋 Copy"}
        </button>
        <button
          onClick={onRetry}
          disabled={busy}
          className="text-[11px] px-2 py-1 rounded border border-sky-400/50 bg-sky-400/10 hover:bg-sky-400/20 text-sky-400 disabled:opacity-50"
        >
          🔄 I did it — connect now
        </button>
      </div>
    </div>
  );
}

interface FramePairProps {
  pair: BrowserFramePair;
}

function FramePair({ pair }: FramePairProps) {
  const preSrc = pair.preB64 ? `data:image/webp;base64,${pair.preB64}` : undefined;
  const postSrc = pair.postB64 ? `data:image/webp;base64,${pair.postB64}` : undefined;
  const label = `${pair.action}${pair.target ? ` → ${pair.target.slice(0, 30)}` : ""}`;
  return (
    <div className="rounded-md border border-border/70 bg-bg-primary/60 p-2">
      <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1 flex items-center justify-between">
        <span>Last action · before / after</span>
        <span className="truncate ml-2 text-text-secondary" title={label}>{label}</span>
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        <FrameCell label="before" src={preSrc} />
        <FrameCell label="after" src={postSrc} />
      </div>
    </div>
  );
}

function FrameCell({ label, src }: { label: string; src?: string }) {
  return (
    <div className="aspect-[16/10] rounded border border-border/60 bg-bg-secondary overflow-hidden flex items-center justify-center relative">
      {src ? (
        <img src={src} alt={label} className="w-full h-full object-contain" />
      ) : (
        <span className="text-[10px] text-text-muted">capturing…</span>
      )}
      <span className="absolute bottom-0 left-0 right-0 text-center text-[9px] uppercase tracking-wide text-text-muted bg-bg-primary/70 py-[1px]">
        {label}
      </span>
    </div>
  );
}

interface CheckpointBannerProps {
  name: string;
  detail?: string;
}

function CheckpointBanner({ name, detail }: CheckpointBannerProps) {
  const [busy, setBusy] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");

  const onApprove = async () => {
    setBusy(true);
    try {
      await api.approveCheckpoint(name);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      alert(`Approve failed: ${msg}`);
    } finally {
      setBusy(false);
    }
  };

  const onReject = async () => {
    const reason = rejectReason.trim() || "Rejected by user";
    setBusy(true);
    try {
      await api.rejectCheckpoint(name, reason);
      setRejectOpen(false);
      setRejectReason("");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      alert(`Reject failed: ${msg}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-md border border-amber/50 bg-amber/10 p-2.5 flex flex-col gap-2">
      <div className="flex items-start gap-2">
        <span className="text-amber mt-[1px]">⚑</span>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold text-amber">
            Approval needed: {name}
          </div>
          {detail && (
            <div className="text-[11px] text-text-secondary mt-1">
              {detail}
            </div>
          )}
        </div>
      </div>
      {!rejectOpen ? (
        <div className="flex gap-1.5">
          <button
            onClick={onApprove}
            disabled={busy}
            className="text-[11px] px-2.5 py-1 rounded bg-accent text-bg-primary font-medium disabled:opacity-50"
          >
            ✓ Approve & continue
          </button>
          <button
            onClick={() => setRejectOpen(true)}
            disabled={busy}
            className="text-[11px] px-2.5 py-1 rounded border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-50"
          >
            ✕ Reject
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <input
            type="text"
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            placeholder="Why are you rejecting? (optional)"
            className="text-[11px] px-2 py-1 rounded border border-border bg-bg-primary text-text-primary focus:outline-none focus:border-accent"
            autoFocus
          />
          <div className="flex justify-end gap-1.5">
            <button
              onClick={() => setRejectOpen(false)}
              className="text-[11px] px-2 py-1 rounded text-text-muted hover:text-text-primary"
              disabled={busy}
            >
              Cancel
            </button>
            <button
              onClick={onReject}
              disabled={busy}
              className="text-[11px] px-2 py-1 rounded bg-rose-400 text-bg-primary font-medium disabled:opacity-50"
            >
              Confirm reject
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
