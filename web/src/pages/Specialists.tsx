import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "../api";
import type { SpecialistDef } from "../api";
import { useToast } from "../context/ToastContext";
import Modal from "../components/Modal";

// ── Icons ────────────────────────────────────────────────────────────────────

function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
    </svg>
  );
}

function EditIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

function ForkIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="spinner text-accent">
      <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
    </svg>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Lowercase slug for the specialist name/key: a-z 0-9 _ - only. */
function slugify(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

type EditorState =
  | { kind: "closed" }
  | { kind: "create" }
  | { kind: "fork"; source: SpecialistDef }
  | { kind: "edit"; source: SpecialistDef }
  | { kind: "view"; source: SpecialistDef };

// ── Toggle ───────────────────────────────────────────────────────────────────

function Toggle({ on, onChange, disabled }: { on: boolean; onChange: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onChange}
      disabled={disabled}
      className={`relative inline-flex w-9 h-5 rounded-full transition-colors shrink-0 ${on ? "bg-accent" : "bg-bg-hover border border-border"} ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
    >
      <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${on ? "translate-x-4" : "translate-x-0.5"}`} />
    </button>
  );
}

// ── Tool tag input (chips + autocomplete from the skills registry) ───────────

function ToolTagInput({
  tools,
  suggestions,
  disabled,
  onChange,
}: {
  readonly tools: string[];
  readonly suggestions: readonly string[];
  readonly disabled?: boolean;
  readonly onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  const add = (raw: string) => {
    const value = raw.trim();
    if (!value) return;
    if (tools.includes(value)) {
      setDraft("");
      return;
    }
    onChange([...tools, value]);
    setDraft("");
  };

  const remove = (value: string) => onChange(tools.filter((t) => t !== value));

  return (
    <div>
      {tools.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {tools.map((t) => (
            <span
              key={t}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-accent-soft text-accent text-[11px] font-mono"
            >
              {t}
              {!disabled && (
                <button
                  type="button"
                  onClick={() => remove(t)}
                  className="hover:text-text-primary"
                  title={`Remove ${t}`}
                >
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
                    <path d="M18 6L6 18M6 6l12 12" />
                  </svg>
                </button>
              )}
            </span>
          ))}
        </div>
      )}
      {!disabled && (
        <div className="flex gap-2">
          <input
            type="text"
            list="specialist-tool-suggestions"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add(draft);
              }
            }}
            placeholder="Add a tool (autocompletes from skills)…"
            className="flex-1 px-3 py-2 rounded-lg bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light"
          />
          <datalist id="specialist-tool-suggestions">
            {suggestions.map((s) => (
              <option key={s} value={s} />
            ))}
          </datalist>
          <button
            type="button"
            onClick={() => add(draft)}
            disabled={!draft.trim()}
            className="px-3 py-2 text-xs text-accent border border-accent/30 rounded-lg hover:bg-accent-soft disabled:opacity-40 transition-colors"
          >
            Add
          </button>
        </div>
      )}
      {disabled && tools.length === 0 && (
        <p className="text-xs text-text-muted">No tools.</p>
      )}
    </div>
  );
}

// ── Editor (create / fork / edit / view) ─────────────────────────────────────

interface SpecialistEditorProps {
  readonly mode: "create" | "fork" | "edit" | "view";
  readonly source?: SpecialistDef;
  readonly suggestions: readonly string[];
  readonly onClose: () => void;
  readonly onSaved: () => void;
  readonly onFork: (source: SpecialistDef) => void;
}

function SpecialistEditor({ mode, source, suggestions, onClose, onSaved, onFork }: SpecialistEditorProps) {
  const toast = useToast();
  const readOnly = mode === "view";
  const nameLocked = mode === "edit" || mode === "view";

  // Initial values: blank for create; copied from the source otherwise. For a
  // fork we suggest a distinct name so it won't collide with the builtin.
  const [name, setName] = useState(() => {
    if (mode === "create") return "";
    if (mode === "fork" && source) return slugify(`${source.name}-copy`);
    return source?.name ?? "";
  });
  const [displayName, setDisplayName] = useState(source?.display_name ?? "");
  const [systemPrompt, setSystemPrompt] = useState(source?.system_prompt ?? "");
  const [tools, setTools] = useState<string[]>(source?.tools ? [...source.tools] : []);
  const [model, setModel] = useState(source?.model ?? "");
  const [includeScraper, setIncludeScraper] = useState(source?.include_scraper ?? false);
  const [saving, setSaving] = useState(false);

  const canSave =
    !readOnly &&
    displayName.trim().length > 0 &&
    systemPrompt.trim().length > 0 &&
    (nameLocked || name.trim().length > 0);

  const handleSave = async () => {
    if (!canSave || saving) return;
    setSaving(true);
    try {
      const payload = {
        display_name: displayName.trim(),
        system_prompt: systemPrompt.trim(),
        tools,
        model: model.trim() || null,
        include_scraper: includeScraper,
      };
      if (mode === "edit" && source) {
        await api.updateSpecialistDef(source.name, payload);
        toast.success(`Specialist "${source.name}" updated`);
      } else {
        await api.createSpecialistDef({ name: name.trim(), ...payload });
        toast.success(`Specialist "${name.trim()}" created`);
      }
      onSaved();
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save specialist");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Name / slug */}
      <div>
        <label className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">
          Name (ID / slug){nameLocked ? " — immutable" : ""}
        </label>
        <input
          type="text"
          value={name}
          disabled={nameLocked}
          onChange={(e) => setName(slugify(e.target.value))}
          placeholder="e.g. upwork-scout"
          className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light disabled:opacity-60"
        />
      </div>

      {/* Display name */}
      <div>
        <label className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">Display Name</label>
        <input
          type="text"
          value={displayName}
          disabled={readOnly}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="e.g. Upwork Scout"
          className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light disabled:opacity-60"
        />
      </div>

      {/* System prompt */}
      <div>
        <label className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">System Prompt</label>
        <textarea
          value={systemPrompt}
          disabled={readOnly}
          onChange={(e) => setSystemPrompt(e.target.value)}
          placeholder="Instructions that define this specialist's role and behavior…"
          rows={8}
          className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light resize-y disabled:opacity-60"
        />
      </div>

      {/* Tools */}
      <div>
        <label className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">Tools</label>
        <ToolTagInput tools={tools} suggestions={suggestions} disabled={readOnly} onChange={setTools} />
      </div>

      {/* Model */}
      <div>
        <label className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">Model (optional)</label>
        <input
          type="text"
          value={model}
          disabled={readOnly}
          onChange={(e) => setModel(e.target.value)}
          placeholder="Leave empty to use the mode's default brain model"
          className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light disabled:opacity-60"
        />
      </div>

      {/* Include scraper */}
      <div className="flex items-center justify-between py-1">
        <div className="pr-4">
          <p className="text-sm text-text-primary">Include scraper</p>
          <p className="text-xs text-text-muted">Bundle the mcp-scraper toolset into this specialist.</p>
        </div>
        <Toggle on={includeScraper} disabled={readOnly} onChange={() => setIncludeScraper((v) => !v)} />
      </div>

      {/* Footer actions */}
      <div className="flex justify-end gap-2 pt-2">
        <button
          onClick={onClose}
          className="px-4 py-2 text-sm text-text-muted rounded-lg hover:bg-bg-hover transition-colors"
        >
          {readOnly ? "Close" : "Cancel"}
        </button>
        {readOnly && source ? (
          <button
            onClick={() => onFork(source)}
            className="flex items-center gap-1.5 px-4 py-2 text-sm bg-accent text-bg-primary rounded-lg hover:opacity-90 transition-opacity"
          >
            <ForkIcon />
            Fork to custom
          </button>
        ) : (
          <button
            onClick={handleSave}
            disabled={!canSave || saving}
            className="px-4 py-2 text-sm bg-accent text-bg-primary rounded-lg hover:opacity-90 disabled:opacity-30 transition-opacity"
          >
            {saving ? "Saving…" : mode === "edit" ? "Save changes" : "Create"}
          </button>
        )}
      </div>
    </div>
  );
}

// ── Specialist card ──────────────────────────────────────────────────────────

function SpecialistCard({
  spec,
  onView,
  onFork,
  onEdit,
  onDelete,
}: {
  readonly spec: SpecialistDef;
  readonly onView: () => void;
  readonly onFork: () => void;
  readonly onEdit: () => void;
  readonly onDelete: () => void;
}) {
  return (
    <div className="group flex flex-col gap-2 px-4 py-3 rounded-xl border border-border bg-bg-hover card-hover">
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-sm font-medium text-text-primary truncate">{spec.name}</span>
            <span
              className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${spec.is_builtin ? "bg-cyan-soft text-cyan" : "bg-accent-soft text-accent"}`}
            >
              {spec.is_builtin ? "builtin" : "custom"}
            </span>
          </div>
          {spec.display_name && spec.display_name !== spec.name && (
            <p className="text-xs text-text-secondary truncate">{spec.display_name}</p>
          )}
        </div>
      </div>

      {spec.system_prompt && (
        <p className="text-xs text-text-muted line-clamp-2">{spec.system_prompt}</p>
      )}

      <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-text-muted">
        <span className="px-1.5 py-0.5 rounded bg-bg-tertiary">{spec.tools.length} tools</span>
        {spec.model && <span className="px-1.5 py-0.5 rounded bg-bg-tertiary font-mono">{spec.model}</span>}
        {spec.include_scraper && <span className="px-1.5 py-0.5 rounded bg-bg-tertiary">scraper</span>}
      </div>

      <div className="flex items-center gap-1.5 pt-1">
        {spec.is_builtin ? (
          <>
            <button
              onClick={onView}
              className="flex items-center gap-1.5 text-xs text-text-secondary border border-border px-2.5 py-1 rounded-lg hover:bg-bg-tertiary transition-colors"
            >
              View
            </button>
            <button
              onClick={onFork}
              className="flex items-center gap-1.5 text-xs text-accent border border-accent/30 px-2.5 py-1 rounded-lg hover:bg-accent-soft transition-colors"
              title="Create an editable copy"
            >
              <ForkIcon />
              Fork
            </button>
          </>
        ) : (
          <>
            <button
              onClick={onEdit}
              className="flex items-center gap-1.5 text-xs text-accent border border-accent/30 px-2.5 py-1 rounded-lg hover:bg-accent-soft transition-colors"
            >
              <EditIcon />
              Edit
            </button>
            <button
              onClick={onDelete}
              className="flex items-center gap-1.5 text-xs text-error border border-error/30 px-2.5 py-1 rounded-lg hover:bg-error-soft transition-colors"
            >
              <TrashIcon />
              Delete
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function Specialists() {
  const toast = useToast();
  const [specialists, setSpecialists] = useState<SpecialistDef[] | null>(null);
  const [skillNames, setSkillNames] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorState>({ kind: "closed" });
  const [pendingDelete, setPendingDelete] = useState<SpecialistDef | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getSpecialists();
      setSpecialists(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load specialists");
      setSpecialists(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    // Tool-name suggestions for the multi-select — best-effort; the editor
    // still accepts free-typed names (e.g. bridged MCP tools) if this fails.
    api
      .listSkills()
      .then((skills) => setSkillNames(skills.map((s) => s.name).sort()))
      .catch(() => setSkillNames([]));
  }, [load]);

  const handleDelete = async () => {
    if (!pendingDelete || deleting) return;
    setDeleting(true);
    try {
      await api.deleteSpecialistDef(pendingDelete.name);
      toast.success(`Specialist "${pendingDelete.name}" deleted`);
      setPendingDelete(null);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete specialist");
    } finally {
      setDeleting(false);
    }
  };

  const { builtins, customs } = useMemo(() => {
    const list = specialists ?? [];
    return {
      builtins: list.filter((s) => s.is_builtin),
      customs: list.filter((s) => !s.is_builtin),
    };
  }, [specialists]);

  const editorSource = "source" in editor ? editor.source : undefined;
  const editorMode = editor.kind === "closed" ? "create" : editor.kind;
  const editorKey = `${editor.kind}:${editorSource?.name ?? "new"}`;
  const editorTitle =
    editor.kind === "edit"
      ? `Edit ${editorSource?.name ?? "specialist"}`
      : editor.kind === "view"
        ? `${editorSource?.name ?? "Specialist"} (builtin — read only)`
        : editor.kind === "fork"
          ? `Fork ${editorSource?.name ?? "specialist"}`
          : "New specialist";

  return (
    <div className="h-full overflow-y-auto">
      <div className="animate-fade-in max-w-5xl mx-auto px-6 py-8 space-y-6">
        {/* Action bar */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-base font-semibold text-text-primary">Specialists</h1>
            <p className="text-xs text-text-muted mt-0.5">
              Declarative agents the router delegates to. Builtins are read-only — fork one to customize.
            </p>
          </div>
          <button
            onClick={() => setEditor({ kind: "create" })}
            className="flex items-center gap-1.5 text-xs text-accent border border-accent/30 px-3 py-1.5 rounded-lg hover:bg-accent-soft transition-colors shrink-0"
          >
            <PlusIcon />
            New specialist
          </button>
        </div>

        {/* States */}
        {loading ? (
          <div className="h-40 flex items-center justify-center text-text-muted text-sm gap-2">
            <SpinnerIcon />
            Loading specialists…
          </div>
        ) : error ? (
          <div className="bg-error-soft border border-error/20 rounded-xl p-5 text-center">
            <p className="text-sm text-error mb-3">{error}</p>
            <button
              onClick={load}
              className="text-xs text-text-secondary border border-border px-3 py-1.5 rounded-lg hover:bg-bg-hover transition-colors"
            >
              Retry
            </button>
          </div>
        ) : (specialists?.length ?? 0) === 0 ? (
          <div className="bg-bg-secondary border border-border rounded-xl p-10 text-center">
            <p className="text-sm text-text-primary mb-1">No specialists yet</p>
            <p className="text-xs text-text-muted mb-4">
              Create a custom specialist to give the router a focused, context-isolated agent.
            </p>
            <button
              onClick={() => setEditor({ kind: "create" })}
              className="inline-flex items-center gap-1.5 text-xs bg-accent text-bg-primary px-3 py-1.5 rounded-lg hover:opacity-90 transition-opacity"
            >
              <PlusIcon />
              New specialist
            </button>
          </div>
        ) : (
          <div className="space-y-6">
            {/* Custom (editable) */}
            <section>
              <h2 className="text-sm font-semibold text-text-primary mb-3">
                Custom <span className="text-text-muted font-normal">({customs.length})</span>
              </h2>
              {customs.length > 0 ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {customs.map((s) => (
                    <SpecialistCard
                      key={s.name}
                      spec={s}
                      onView={() => setEditor({ kind: "view", source: s })}
                      onFork={() => setEditor({ kind: "fork", source: s })}
                      onEdit={() => setEditor({ kind: "edit", source: s })}
                      onDelete={() => setPendingDelete(s)}
                    />
                  ))}
                </div>
              ) : (
                <p className="text-xs text-text-muted">No custom specialists. Fork a builtin to start.</p>
              )}
            </section>

            {/* Builtins (read-only) */}
            <section>
              <h2 className="text-sm font-semibold text-text-primary mb-3">
                Builtins <span className="text-text-muted font-normal">({builtins.length})</span>
              </h2>
              {builtins.length > 0 ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {builtins.map((s) => (
                    <SpecialistCard
                      key={s.name}
                      spec={s}
                      onView={() => setEditor({ kind: "view", source: s })}
                      onFork={() => setEditor({ kind: "fork", source: s })}
                      onEdit={() => setEditor({ kind: "view", source: s })}
                      onDelete={() => undefined}
                    />
                  ))}
                </div>
              ) : (
                <p className="text-xs text-text-muted">No builtin specialists loaded.</p>
              )}
            </section>
          </div>
        )}
      </div>

      {/* Create / fork / edit / view editor */}
      <Modal open={editor.kind !== "closed"} onClose={() => setEditor({ kind: "closed" })} title={editorTitle}>
        {editor.kind !== "closed" && (
          <SpecialistEditor
            key={editorKey}
            mode={editorMode}
            source={editorSource}
            suggestions={skillNames}
            onClose={() => setEditor({ kind: "closed" })}
            onSaved={load}
            onFork={(source) => setEditor({ kind: "fork", source })}
          />
        )}
      </Modal>

      {/* Delete confirmation */}
      <Modal open={pendingDelete !== null} onClose={() => setPendingDelete(null)} title="Delete specialist">
        <div className="space-y-4">
          <p className="text-sm text-text-secondary">
            Delete <span className="font-mono text-text-primary">{pendingDelete?.name}</span>? This can't be undone.
          </p>
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setPendingDelete(null)}
              className="px-4 py-2 text-sm text-text-muted rounded-lg hover:bg-bg-hover transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="px-4 py-2 text-sm bg-error text-white rounded-lg hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              {deleting ? "Deleting…" : "Delete"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
