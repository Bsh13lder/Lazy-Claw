import { useEffect, useState } from "react";
import { useChat } from "../context/ChatContext";

interface BrowserTemplate {
  id: string;
  name: string;
  icon?: string | null;
  system_prompt?: string | null;
  setup_urls?: string[];
  checkpoints?: string[];
  playbook?: string | null;
  page_reader_mode?: string;
  watch_url?: string | null;
  watch_extractor?: string | null;
  watch_condition?: string | null;
  watch_job_id?: string | null;
  created_at?: string;
  updated_at?: string;
}

interface TemplateForm {
  name: string;
  icon: string;
  setup_urls: string;
  checkpoints: string;
  playbook: string;
  page_reader_mode: string;
  watch_url: string;
  watch_extractor: string;
  watch_condition: string;
}

const EMPTY_FORM: TemplateForm = {
  name: "",
  icon: "🌐",
  setup_urls: "",
  checkpoints: "",
  playbook: "",
  page_reader_mode: "auto",
  watch_url: "",
  watch_extractor: "",
  watch_condition: "",
};

function templateToForm(t: BrowserTemplate): TemplateForm {
  return {
    name: t.name,
    icon: t.icon || "🌐",
    setup_urls: (t.setup_urls || []).join("\n"),
    checkpoints: (t.checkpoints || []).join("\n"),
    playbook: t.playbook || "",
    page_reader_mode: t.page_reader_mode || "auto",
    watch_url: t.watch_url || "",
    watch_extractor: t.watch_extractor || "",
    watch_condition: t.watch_condition || "",
  };
}

function formToPayload(f: TemplateForm) {
  return {
    name: f.name.trim(),
    icon: f.icon || null,
    playbook: f.playbook || null,
    page_reader_mode: f.page_reader_mode || "auto",
    setup_urls: f.setup_urls.split("\n").map((s) => s.trim()).filter(Boolean),
    checkpoints: f.checkpoints.split("\n").map((s) => s.trim()).filter(Boolean),
    watch_url: f.watch_url || null,
    watch_extractor: f.watch_extractor || null,
    watch_condition: f.watch_condition || null,
  };
}

export default function BrowserTemplates() {
  const [items, setItems] = useState<BrowserTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | "new" | null>(null);
  const [form, setForm] = useState<TemplateForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const { sendMessage, setChatOpen } = useChat();

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/browser/templates", { credentials: "include" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setItems(data.templates || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
  }, []);

  const startCreate = () => {
    setForm(EMPTY_FORM);
    setEditingId("new");
  };

  const startEdit = (t: BrowserTemplate) => {
    setForm(templateToForm(t));
    setEditingId(t.id);
  };

  const cancel = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
  };

  const save = async () => {
    if (!form.name.trim()) return;
    const payload = formToPayload(form);
    setBusy(true);
    try {
      const url =
        editingId === "new"
          ? "/api/browser/templates"
          : `/api/browser/templates/${editingId}`;
      const method = editingId === "new" ? "POST" : "PATCH";
      const r = await fetch(url, {
        method,
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${r.status}`);
      }
      await reload();
      cancel();
    } catch (e) {
      alert(`Save failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (t: BrowserTemplate) => {
    if (!confirm(`Delete template "${t.name}"?`)) return;
    setBusy(true);
    try {
      const r = await fetch(`/api/browser/templates/${t.id}`, {
        method: "DELETE",
        credentials: "include",
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await reload();
    } catch (e) {
      alert(`Delete failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const run = async (t: BrowserTemplate) => {
    setChatOpen(true);
    sendMessage(`Run my template '${t.name}'`);
  };

  const watch = async (t: BrowserTemplate) => {
    if (!t.watch_url) {
      alert("This template has no watch_url configured. Edit it to add slot polling.");
      return;
    }
    setChatOpen(true);
    sendMessage(`Watch slots for template '${t.name}'`);
  };

  const seed = async () => {
    setBusy(true);
    try {
      const r = await fetch("/api/browser/templates/seed", {
        method: "POST",
        credentials: "include",
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await reload();
    } catch (e) {
      alert(`Seed failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  if (editingId !== null) {
    const isNew = editingId === "new";
    return (
      <div className="h-full overflow-y-auto p-6">
        <div className="max-w-3xl mx-auto flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-semibold text-text-primary">
              {isNew ? "New browser template" : `Edit "${form.name}"`}
            </h1>
            <span className="text-xs text-text-muted">
              Reusable agent recipe — capture once, run by name
            </span>
          </div>
          <FieldRow label="Name" hint="Short unique name e.g. 'DGT cita previa'.">
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="text-sm px-3 py-2 rounded border border-border bg-bg-primary text-text-primary focus:outline-none focus:border-accent"
            />
          </FieldRow>
          <FieldRow label="Icon" hint="Emoji shown in lists.">
            <input
              type="text"
              value={form.icon}
              maxLength={4}
              onChange={(e) => setForm({ ...form, icon: e.target.value })}
              className="text-sm px-3 py-2 rounded border border-border bg-bg-primary text-text-primary w-20 focus:outline-none focus:border-accent"
            />
          </FieldRow>
          <FieldRow label="Setup URLs" hint="One URL per line — opened before the flow starts.">
            <textarea
              rows={3}
              value={form.setup_urls}
              onChange={(e) => setForm({ ...form, setup_urls: e.target.value })}
              className="text-sm px-3 py-2 rounded border border-border bg-bg-primary text-text-primary font-mono focus:outline-none focus:border-accent"
              placeholder="https://example.com/page"
            />
          </FieldRow>
          <FieldRow label="Checkpoints" hint="One per line — names that the agent will pause on with request_user_approval.">
            <textarea
              rows={3}
              value={form.checkpoints}
              onChange={(e) => setForm({ ...form, checkpoints: e.target.value })}
              className="text-sm px-3 py-2 rounded border border-border bg-bg-primary text-text-primary focus:outline-none focus:border-accent"
              placeholder="Pick date&#10;Confirm booking"
            />
          </FieldRow>
          <FieldRow label="Playbook" hint="Free-form instructions: site quirks, vault keys to use, what to skip.">
            <textarea
              rows={8}
              value={form.playbook}
              onChange={(e) => setForm({ ...form, playbook: e.target.value })}
              className="text-sm px-3 py-2 rounded border border-border bg-bg-primary text-text-primary focus:outline-none focus:border-accent"
            />
          </FieldRow>
          <FieldRow label="Watch URL (optional)" hint="If set, the slot watcher hits this URL on a schedule.">
            <input
              type="text"
              value={form.watch_url}
              onChange={(e) => setForm({ ...form, watch_url: e.target.value })}
              className="text-sm px-3 py-2 rounded border border-border bg-bg-primary text-text-primary focus:outline-none focus:border-accent"
            />
          </FieldRow>
          <FieldRow label="Watch extractor JS (optional)" hint="JavaScript that returns a value which changes when the trigger condition is met.">
            <textarea
              rows={4}
              value={form.watch_extractor}
              onChange={(e) => setForm({ ...form, watch_extractor: e.target.value })}
              className="text-sm px-3 py-2 rounded border border-border bg-bg-primary text-text-primary font-mono focus:outline-none focus:border-accent"
              placeholder="(() => document.querySelectorAll('.slot').length)()"
            />
          </FieldRow>
          <FieldRow label="Watch condition (optional)" hint="Plain-language description of the trigger condition.">
            <input
              type="text"
              value={form.watch_condition}
              onChange={(e) => setForm({ ...form, watch_condition: e.target.value })}
              className="text-sm px-3 py-2 rounded border border-border bg-bg-primary text-text-primary focus:outline-none focus:border-accent"
            />
          </FieldRow>
          <div className="flex gap-2 pt-2">
            <button
              onClick={save}
              disabled={busy || !form.name.trim()}
              className="px-3 py-1.5 rounded bg-accent text-bg-primary text-sm font-medium disabled:opacity-40"
            >
              {isNew ? "Create" : "Save changes"}
            </button>
            <button
              onClick={cancel}
              disabled={busy}
              className="px-3 py-1.5 rounded border border-border text-text-secondary text-sm hover:bg-bg-hover"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold text-text-primary flex-1">
            Browser templates
          </h1>
          <button
            onClick={seed}
            disabled={busy}
            className="text-xs px-2 py-1 rounded border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-40"
            title="Install bundled examples (Cita Previa, Doctoralia)"
          >
            ＋ Seed examples
          </button>
          <button
            onClick={startCreate}
            disabled={busy}
            className="text-xs px-2 py-1 rounded bg-accent text-bg-primary font-medium disabled:opacity-40"
          >
            ＋ New template
          </button>
        </div>
        <p className="text-xs text-text-muted">
          Saved recipes the agent can replay by name — perfect for govt appointments,
          doctor bookings, and anything you do more than once.
        </p>

        {loading && <div className="text-sm text-text-muted">Loading...</div>}
        {error && (
          <div className="text-sm text-rose-400 bg-rose-400/10 border border-rose-400/30 rounded p-3">
            {error}
          </div>
        )}
        {!loading && items.length === 0 && (
          <div className="text-sm text-text-muted border border-dashed border-border rounded p-6 text-center">
            No templates yet. Click <b>Seed examples</b> for two starter recipes
            (Cita Previa Spain + Doctoralia), or <b>New template</b> to create one.
          </div>
        )}
        <div className="flex flex-col gap-2">
          {items.map((t) => (
            <div
              key={t.id}
              className="border border-border rounded-md p-3 bg-bg-secondary/40 flex flex-col gap-2"
            >
              <div className="flex items-start gap-3">
                <span className="text-xl shrink-0 mt-0.5">{t.icon || "🌐"}</span>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-text-primary truncate">
                    {t.name}
                  </div>
                  <div className="text-[11px] text-text-muted flex flex-wrap gap-2 mt-0.5">
                    {t.setup_urls?.length
                      ? <span>{t.setup_urls.length} setup URL{t.setup_urls.length === 1 ? "" : "s"}</span>
                      : null}
                    {t.checkpoints?.length
                      ? <span>{t.checkpoints.length} checkpoint{t.checkpoints.length === 1 ? "" : "s"}</span>
                      : null}
                    {t.watch_url
                      ? <span className="text-amber">slot-watch ready</span>
                      : null}
                    {t.watch_job_id
                      ? <span className="text-accent">▶ watching</span>
                      : null}
                  </div>
                </div>
                <div className="flex gap-1.5 shrink-0">
                  <button
                    onClick={() => run(t)}
                    className="text-[11px] px-2 py-1 rounded bg-accent text-bg-primary font-medium"
                    title="Send 'Run template' to chat"
                  >
                    ▶ Run
                  </button>
                  {t.watch_url && (
                    <button
                      onClick={() => watch(t)}
                      className="text-[11px] px-2 py-1 rounded border border-amber/40 bg-amber/10 text-amber hover:bg-amber/20"
                      title="Start zero-token slot polling"
                    >
                      👁 Watch
                    </button>
                  )}
                  <button
                    onClick={() => startEdit(t)}
                    className="text-[11px] px-2 py-1 rounded border border-border text-text-secondary hover:bg-bg-hover"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => remove(t)}
                    className="text-[11px] px-2 py-1 rounded text-rose-400 hover:bg-rose-400/10"
                  >
                    ✕
                  </button>
                </div>
              </div>
              {t.playbook && (
                <div className="text-[11px] text-text-secondary line-clamp-2 whitespace-pre-line opacity-80">
                  {t.playbook}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function FieldRow({
  label, hint, children,
}: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-text-secondary">{label}</span>
      {hint && <span className="text-[11px] text-text-muted">{hint}</span>}
      {children}
    </label>
  );
}
