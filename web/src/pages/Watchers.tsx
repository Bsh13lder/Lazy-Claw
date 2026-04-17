import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "../api";
import type { Watcher, WatcherCheck } from "../api";

// ── helpers ──────────────────────────────────────────────────────────────

function formatInterval(seconds?: number | null): string {
  if (!seconds) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}

function relativeTime(ts: number | string | null | undefined): string {
  if (!ts) return "never";
  const t = typeof ts === "number" ? ts : new Date(ts).getTime() / 1000;
  if (!Number.isFinite(t)) return "never";
  const diff = Math.max(0, Date.now() / 1000 - t);
  if (diff < 45) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

function relativeFuture(ts: number | null | undefined): string {
  if (!ts) return "—";
  const diff = ts - Date.now() / 1000;
  if (diff <= 0) return "due now";
  if (diff < 60) return `in ${Math.round(diff)}s`;
  if (diff < 3600) return `in ${Math.round(diff / 60)}m`;
  return `in ${Math.round(diff / 3600)}h`;
}

function statusBadge(status: string): string {
  if (status === "active")
    return "text-emerald-400 bg-emerald-400/10 border-emerald-400/30";
  if (status === "paused")
    return "text-amber bg-amber/10 border-amber/30";
  return "text-text-muted bg-bg-hover border-border";
}

function shortHost(url?: string | null): string {
  if (!url) return "";
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url.slice(0, 40);
  }
}

// ── page ─────────────────────────────────────────────────────────────────

export default function Watchers() {
  const [items, setItems] = useState<Watcher[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await api.listWatchers();
      setItems(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    // Periodic refresh — cheap; watchers state changes once per poll.
    const id = window.setInterval(() => { void reload(); }, 15000);
    return () => window.clearInterval(id);
  }, [reload]);

  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(id);
  }, [toast]);

  const selected = useMemo(
    () => items.find((w) => w.id === selectedId) ?? null,
    [items, selectedId],
  );

  const onPause = async (w: Watcher) => {
    setBusyId(w.id);
    try { await api.pauseWatcher(w.id); await reload(); setToast(`Paused '${w.name}'`); }
    catch (e) { setToast(`Pause failed: ${e instanceof Error ? e.message : e}`); }
    finally { setBusyId(null); }
  };

  const onResume = async (w: Watcher) => {
    setBusyId(w.id);
    try { await api.resumeWatcher(w.id); await reload(); setToast(`Resumed '${w.name}'`); }
    catch (e) { setToast(`Resume failed: ${e instanceof Error ? e.message : e}`); }
    finally { setBusyId(null); }
  };

  const onDelete = async (w: Watcher) => {
    if (!confirm(`Delete watcher "${w.name}"?`)) return;
    setBusyId(w.id);
    try { await api.deleteWatcher(w.id); await reload(); setToast(`Deleted '${w.name}'`); }
    catch (e) { setToast(`Delete failed: ${e instanceof Error ? e.message : e}`); }
    finally { setBusyId(null); }
  };

  return (
    <div className="h-full flex overflow-hidden">
      <div className="flex-1 overflow-y-auto p-6 min-w-0">
        <div className="max-w-4xl mx-auto flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-semibold text-text-primary flex-1">
              Watchers
            </h1>
            <button
              onClick={() => void reload()}
              disabled={loading}
              className="text-xs px-2 py-1 rounded border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-40"
            >
              {loading ? "Loading…" : "Refresh"}
            </button>
          </div>
          <p className="text-xs text-text-muted">
            Zero-token site polls — JS extractors run on your open tabs and ping
            you when something changes. Edit interval, rewrite the extractor,
            or test-run a watcher to see what it would extract right now.
          </p>

          {error && (
            <div className="text-sm text-rose-400 bg-rose-400/10 border border-rose-400/30 rounded p-3">
              {error}
            </div>
          )}

          {!loading && items.length === 0 && (
            <div className="text-sm text-text-muted border border-dashed border-border rounded p-6 text-center">
              No watchers yet. Ask LazyClaw "watch this page for new slots" or
              start one from a browser template on the Templates page.
            </div>
          )}

          <div className="flex flex-col gap-2">
            {items.map((w) => (
              <button
                key={w.id}
                onClick={() => setSelectedId(w.id)}
                className={`text-left border rounded-md p-3 bg-bg-secondary/40 hover:bg-bg-hover transition-colors ${
                  selectedId === w.id ? "border-accent" : "border-border"
                }`}
              >
                <div className="flex items-start gap-3">
                  <span className="text-xl shrink-0 mt-0.5">
                    {w.template_icon || "👁"}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-text-primary truncate">
                        {w.name}
                      </span>
                      <span
                        className={`text-[10px] px-1.5 py-0.5 rounded border ${statusBadge(w.status)}`}
                      >
                        {w.status}
                      </span>
                      {w.one_shot && (
                        <span className="text-[10px] text-amber">one-shot</span>
                      )}
                    </div>
                    <div className="text-[11px] text-text-muted mt-0.5 truncate">
                      {shortHost(w.url)}
                      {w.what_to_watch
                        ? <> · <span className="text-text-secondary">{w.what_to_watch}</span></>
                        : null}
                    </div>
                    <div className="text-[11px] text-text-muted mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                      <span>every {formatInterval(w.check_interval)}</span>
                      <span>last: {relativeTime(w.last_check)}</span>
                      <span>next: {relativeFuture(w.next_check_ts)}</span>
                      <span>
                        checks: <span className="text-text-secondary">{w.check_count}</span>
                        {" · "}triggers: <span className="text-text-secondary">{w.trigger_count}</span>
                        {w.error_count > 0 && (
                          <> · <span className="text-rose-400">errors: {w.error_count}</span></>
                        )}
                      </span>
                    </div>
                  </div>
                  <div
                    className="flex flex-col gap-1 shrink-0"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div className="flex gap-1">
                      {w.status === "active" ? (
                        <button
                          onClick={() => void onPause(w)}
                          disabled={busyId === w.id}
                          className="text-[11px] px-2 py-1 rounded border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-40"
                        >
                          Pause
                        </button>
                      ) : (
                        <button
                          onClick={() => void onResume(w)}
                          disabled={busyId === w.id}
                          className="text-[11px] px-2 py-1 rounded border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-40"
                        >
                          Resume
                        </button>
                      )}
                      <button
                        onClick={() => void onDelete(w)}
                        disabled={busyId === w.id}
                        className="text-[11px] px-2 py-1 rounded text-rose-400 hover:bg-rose-400/10 disabled:opacity-40"
                      >
                        ✕
                      </button>
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      {selected && (
        <WatcherDetail
          watcher={selected}
          onClose={() => setSelectedId(null)}
          onChanged={async (msg) => { await reload(); if (msg) setToast(msg); }}
        />
      )}

      {toast && (
        <div className="fixed bottom-4 right-4 text-xs bg-bg-secondary border border-border rounded px-3 py-2 shadow-lg text-text-primary">
          {toast}
        </div>
      )}
    </div>
  );
}

// ── detail drawer ────────────────────────────────────────────────────────

interface DetailProps {
  watcher: Watcher;
  onClose: () => void;
  onChanged: (msg?: string) => void | Promise<void>;
}

function WatcherDetail({ watcher, onClose, onChanged }: DetailProps) {
  const [intervalMinutes, setIntervalMinutes] = useState<string>(
    watcher.check_interval ? String(Math.round(watcher.check_interval / 60)) : "5",
  );
  const [condition, setCondition] = useState<string>(watcher.what_to_watch || "");
  const [extractor, setExtractor] = useState<string>(watcher.custom_js || "");
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [history, setHistory] = useState<WatcherCheck[]>([]);
  const [editOpen, setEditOpen] = useState(false);

  useEffect(() => {
    setIntervalMinutes(
      watcher.check_interval ? String(Math.round(watcher.check_interval / 60)) : "5",
    );
    setCondition(watcher.what_to_watch || "");
    setExtractor(watcher.custom_js || "");
    setTestResult(null);
  }, [watcher.id, watcher.check_interval, watcher.what_to_watch, watcher.custom_js]);

  useEffect(() => {
    let alive = true;
    api.getWatcherHistory(watcher.id)
      .then((h) => { if (alive) setHistory(h); })
      .catch(() => { if (alive) setHistory([]); });
    const id = window.setInterval(() => {
      api.getWatcherHistory(watcher.id)
        .then((h) => { if (alive) setHistory(h); })
        .catch(() => {});
    }, 10000);
    return () => { alive = false; window.clearInterval(id); };
  }, [watcher.id]);

  const save = async () => {
    const body: {
      check_interval?: number; custom_js?: string; what_to_watch?: string;
    } = {};
    const secs = Math.max(15, Math.round(Number(intervalMinutes) * 60));
    if (secs !== watcher.check_interval) body.check_interval = secs;
    if (extractor !== (watcher.custom_js || "")) body.custom_js = extractor || "";
    if (condition !== (watcher.what_to_watch || "")) body.what_to_watch = condition || "";
    if (Object.keys(body).length === 0) return;
    setBusy(true);
    try {
      await api.updateWatcher(watcher.id, body);
      await onChanged(`Updated '${watcher.name}'`);
    } catch (e) {
      alert(`Save failed: ${e instanceof Error ? e.message : e}`);
    } finally { setBusy(false); }
  };

  const runTest = async () => {
    setBusy(true);
    setTestResult("Running…");
    try {
      const r = await api.testWatcher(watcher.id);
      const val = r.extracted_value ?? "(empty)";
      setTestResult(val.length > 800 ? `${val.slice(0, 800)}…` : val);
    } catch (e) {
      setTestResult(`Error: ${e instanceof Error ? e.message : e}`);
    } finally { setBusy(false); }
  };

  return (
    <aside className="w-[420px] border-l border-border bg-bg-secondary/40 overflow-y-auto shrink-0">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <span className="text-xl">{watcher.template_icon || "👁"}</span>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-text-primary truncate">{watcher.name}</div>
          <div className="text-[11px] text-text-muted truncate">{shortHost(watcher.url)}</div>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-bg-hover text-text-muted"
          title="Close"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="6" y1="6" x2="18" y2="18" />
            <line x1="6" y1="18" x2="18" y2="6" />
          </svg>
        </button>
      </div>

      <div className="p-4 flex flex-col gap-4 text-xs">
        {/* Summary */}
        <div className="grid grid-cols-2 gap-2">
          <Stat label="Status" value={watcher.status} />
          <Stat label="Interval" value={formatInterval(watcher.check_interval)} />
          <Stat label="Last check" value={relativeTime(watcher.last_check)} />
          <Stat label="Next check" value={relativeFuture(watcher.next_check_ts)} />
          <Stat label="Checks" value={String(watcher.check_count)} />
          <Stat
            label="Triggers"
            value={String(watcher.trigger_count)}
            tone={watcher.trigger_count > 0 ? "accent" : "muted"}
          />
          {watcher.error_count > 0 && (
            <Stat label="Errors" value={String(watcher.error_count)} tone="bad" />
          )}
          {watcher.expires_at && (
            <Stat label="Expires" value={watcher.expires_at.slice(0, 16).replace("T", " ")} />
          )}
        </div>

        {watcher.last_trigger_message && (
          <div className="rounded-md border border-amber/40 bg-amber/10 p-2.5">
            <div className="text-[10px] uppercase tracking-wide text-amber mb-1">
              Last trigger · {relativeTime(watcher.last_trigger_ts)}
            </div>
            <div className="text-text-secondary whitespace-pre-wrap">
              {watcher.last_trigger_message}
            </div>
          </div>
        )}

        {watcher.last_error && (
          <div className="rounded-md border border-rose-400/40 bg-rose-400/10 p-2.5">
            <div className="text-[10px] uppercase tracking-wide text-rose-400 mb-1">
              Last error
            </div>
            <div className="text-text-secondary whitespace-pre-wrap">{watcher.last_error}</div>
          </div>
        )}

        {/* Last extracted value */}
        <div>
          <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">
            Last extracted value
          </div>
          <div className="text-text-secondary bg-bg-primary/50 border border-border rounded p-2 font-mono text-[11px] whitespace-pre-wrap break-all max-h-32 overflow-auto">
            {watcher.last_value || "(nothing captured yet)"}
          </div>
        </div>

        {/* Test */}
        <div className="flex items-center gap-2">
          <button
            onClick={runTest}
            disabled={busy}
            className="text-xs px-3 py-1.5 rounded bg-accent text-bg-primary font-medium disabled:opacity-40"
          >
            🧪 Test extractor now
          </button>
          <span className="text-[11px] text-text-muted">
            Runs once against {shortHost(watcher.url)}
          </span>
        </div>
        {testResult !== null && (
          <div className="text-[11px] text-text-secondary bg-bg-primary/50 border border-border rounded p-2 font-mono whitespace-pre-wrap break-all max-h-40 overflow-auto">
            {testResult}
          </div>
        )}

        {/* Edit toggle */}
        <button
          onClick={() => setEditOpen((o) => !o)}
          className="text-xs text-accent hover:underline self-start"
        >
          {editOpen ? "▼ Close edit" : "▶ Edit interval / extractor / condition"}
        </button>

        {editOpen && (
          <div className="flex flex-col gap-3 border border-border rounded-md p-3 bg-bg-primary/30">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-text-muted">
                Check every (minutes)
              </span>
              <input
                type="number"
                value={intervalMinutes}
                min={0.25}
                step={0.25}
                onChange={(e) => setIntervalMinutes(e.target.value)}
                className="text-xs px-2 py-1.5 rounded border border-border bg-bg-primary text-text-primary focus:outline-none focus:border-accent"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-text-muted">
                What to watch for
              </span>
              <input
                type="text"
                value={condition}
                onChange={(e) => setCondition(e.target.value)}
                placeholder="e.g. appointment slot opens in Madrid"
                className="text-xs px-2 py-1.5 rounded border border-border bg-bg-primary text-text-primary focus:outline-none focus:border-accent"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-wide text-text-muted">
                Extractor JS
              </span>
              <textarea
                value={extractor}
                onChange={(e) => setExtractor(e.target.value)}
                rows={5}
                placeholder="(() => document.querySelectorAll('.slot').length)()"
                className="text-[11px] px-2 py-1.5 rounded border border-border bg-bg-primary text-text-primary font-mono resize-none focus:outline-none focus:border-accent"
              />
            </label>
            <button
              onClick={save}
              disabled={busy}
              className="text-xs px-3 py-1.5 rounded bg-accent text-bg-primary font-medium disabled:opacity-40 self-start"
            >
              {busy ? "Saving…" : "Save changes"}
            </button>
          </div>
        )}

        {/* History */}
        <div>
          <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">
            Recent checks (last {history.length})
          </div>
          {history.length === 0 ? (
            <div className="text-[11px] text-text-muted italic">No checks yet — first poll pending.</div>
          ) : (
            <ul className="flex flex-col gap-1 max-h-80 overflow-auto">
              {[...history].reverse().map((c, idx) => (
                <li
                  key={`${c.ts}-${idx}`}
                  className={`flex items-start gap-2 text-[11px] rounded px-2 py-1 ${
                    c.triggered ? "bg-amber/10" :
                    c.error ? "bg-rose-400/10" :
                    c.changed ? "bg-accent/5" : ""
                  }`}
                >
                  <span className="shrink-0 w-4 text-center">
                    {c.triggered ? "🔔" : c.error ? "⚠️" : c.changed ? "·" : "✓"}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="text-text-muted">
                      {relativeTime(c.ts)}
                      {c.triggered && (<span className="text-amber ml-1">fired</span>)}
                    </div>
                    {c.error ? (
                      <div className="text-rose-400 truncate">{c.error}</div>
                    ) : c.notification ? (
                      <div className="text-text-secondary truncate">{c.notification}</div>
                    ) : c.value_preview ? (
                      <div className="text-text-secondary truncate font-mono">{c.value_preview}</div>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </aside>
  );
}

function Stat({
  label, value, tone = "normal",
}: { label: string; value: string; tone?: "normal" | "muted" | "accent" | "bad" }) {
  const cls =
    tone === "accent" ? "text-accent" :
    tone === "bad" ? "text-rose-400" :
    tone === "muted" ? "text-text-muted" :
    "text-text-primary";
  return (
    <div className="rounded border border-border bg-bg-primary/50 px-2 py-1.5">
      <div className="text-[9px] uppercase tracking-wide text-text-muted">{label}</div>
      <div className={`text-xs font-medium ${cls}`}>{value}</div>
    </div>
  );
}
