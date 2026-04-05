import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type {
  EcoSettings,
  EcoUsage,
  EcoProvider,
  RateLimits,
  TeamSettings,
  Specialist,
  PermissionSettings,
} from "../api";
import { useToast } from "../context/ToastContext";
import Modal from "../components/Modal";

// ── Tab types ──────────────────────────────────────────────────────────────

type TabId = "eco" | "teams" | "permissions";

interface TabDef {
  readonly id: TabId;
  readonly label: string;
  readonly icon: React.ReactNode;
}

// ── SVG Icons ──────────────────────────────────────────────────────────────

function DollarIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="1" x2="12" y2="23" />
      <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
    </svg>
  );
}

function UsersIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}

function ShieldIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner">
      <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
    </svg>
  );
}

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

// ── Tab definitions ────────────────────────────────────────────────────────

const TABS: readonly TabDef[] = [
  { id: "eco", label: "ECO Mode", icon: <DollarIcon /> },
  { id: "teams", label: "Teams", icon: <UsersIcon /> },
  { id: "permissions", label: "Permissions", icon: <ShieldIcon /> },
] as const;

// ── Mode card definitions ──────────────────────────────────────────────────

interface ModeDef {
  readonly mode: string;
  readonly name: string;
  readonly description: string;
}

const MODE_CARDS: readonly ModeDef[] = [
  { mode: "eco", name: "ECO", description: "Free models only" },
  { mode: "hybrid", name: "HYBRID", description: "Auto-fallback" },
  { mode: "full", name: "FULL", description: "Premium models" },
] as const;

const TEAM_MODES = ["single", "parallel", "sequential"] as const;

const PERMISSION_LEVELS = ["allow", "ask", "deny"] as const;

// ── Permission badge ───────────────────────────────────────────────────────

function PermissionBadge({ level, onClick }: { readonly level: string; readonly onClick?: () => void }) {
  const styles =
    level === "allow"
      ? "bg-accent-soft text-accent border border-accent/20"
      : level === "deny"
        ? "bg-error-soft text-error border border-error/20"
        : "bg-amber-soft text-amber border border-amber/20";

  return (
    <button
      onClick={onClick}
      className={`text-[10px] font-medium px-2.5 py-0.5 rounded-full transition-opacity ${styles} ${onClick ? "hover:opacity-80 cursor-pointer" : "cursor-default"}`}
      title={onClick ? "Click to cycle: allow → ask → deny" : undefined}
    >
      {level}
    </button>
  );
}

// ── Section heading ────────────────────────────────────────────────────────

function SectionHeading({
  title,
  subtitle,
}: {
  readonly title: string;
  readonly subtitle?: string;
}) {
  return (
    <div className="mb-4">
      <h2 className="text-sm font-semibold text-text-primary">{title}</h2>
      {subtitle && <p className="text-xs text-text-muted mt-0.5">{subtitle}</p>}
    </div>
  );
}

// ── Toggle switch ──────────────────────────────────────────────────────────

function Toggle({ on, onChange, disabled }: { on: boolean; onChange: () => void; disabled?: boolean }) {
  return (
    <button
      onClick={onChange}
      disabled={disabled}
      className={`relative inline-flex w-9 h-5 rounded-full transition-colors shrink-0 ${on ? "bg-accent" : "bg-bg-hover border border-border"} ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
    >
      <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${on ? "translate-x-4" : "translate-x-0.5"}`} />
    </button>
  );
}

// ── ECO Tab ────────────────────────────────────────────────────────────────

function EcoTab({
  eco,
  usage,
  providers,
  rateLimits,
  onModeChange,
  onSettingsUpdate,
}: {
  readonly eco: EcoSettings | null;
  readonly usage: EcoUsage | null;
  readonly providers: readonly EcoProvider[];
  readonly rateLimits: RateLimits | null;
  readonly onModeChange: (mode: string) => void;
  readonly onSettingsUpdate: (updates: Partial<EcoSettings>) => Promise<void>;
}) {
  const [budget, setBudget] = useState(String(eco?.monthly_paid_budget ?? "0"));
  const [savingBudget, setSavingBudget] = useState(false);
  const toast = useToast();

  // Keep budget in sync when eco loads
  useEffect(() => {
    if (eco) setBudget(String(eco.monthly_paid_budget));
  }, [eco]);

  const freeRatio = usage && usage.total > 0 ? (usage.free_count / usage.total) * 100 : 0;
  const paidRatio = usage && usage.total > 0 ? (usage.paid_count / usage.total) * 100 : 0;

  const handleBudgetSave = async () => {
    const val = parseFloat(budget);
    if (Number.isNaN(val) || val < 0) {
      toast.error("Invalid budget value");
      return;
    }
    setSavingBudget(true);
    try {
      await onSettingsUpdate({ monthly_paid_budget: val });
      toast.success("Budget updated");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update budget");
    } finally {
      setSavingBudget(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Mode selector cards */}
      <section className="bg-bg-secondary border border-border rounded-xl p-5">
        <SectionHeading
          title="Mode"
          subtitle="Controls cost routing between free and paid providers"
        />
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {MODE_CARDS.map((card) => {
            const isActive = eco?.mode === card.mode;
            return (
              <button
                key={card.mode}
                onClick={() => onModeChange(card.mode)}
                className={`relative flex flex-col items-start gap-1.5 p-4 rounded-xl border-2 transition-all text-left ${
                  isActive
                    ? "border-accent bg-accent-soft"
                    : "border-border hover:border-border hover:bg-bg-hover"
                }`}
              >
                {isActive && (
                  <span className="absolute top-3 right-3 text-accent">
                    <CheckIcon />
                  </span>
                )}
                <span className={`text-sm font-semibold ${isActive ? "text-accent" : "text-text-primary"}`}>
                  {card.name}
                </span>
                <span className="text-xs text-text-muted">{card.description}</span>
              </button>
            );
          })}
        </div>
      </section>

      {/* Budget + badges */}
      {eco && (
        <section className="bg-bg-secondary border border-border rounded-xl p-5">
          <SectionHeading title="Budget & Display" />
          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <div className="flex-1">
                <label className="block text-xs font-medium text-text-secondary mb-1.5">Monthly paid budget ($)</label>
                <div className="flex gap-2">
                  <input
                    type="number"
                    min="0"
                    step="1"
                    value={budget}
                    onChange={(e) => setBudget(e.target.value)}
                    className="w-32 px-3 py-2 rounded-lg bg-bg-tertiary border border-border text-sm text-text-primary focus:outline-none focus:border-border-light"
                  />
                  <button
                    onClick={handleBudgetSave}
                    disabled={savingBudget}
                    className="px-3 py-2 text-xs text-accent border border-accent/30 rounded-lg hover:bg-accent-soft disabled:opacity-40 transition-colors"
                  >
                    {savingBudget ? "Saving..." : "Save"}
                  </button>
                </div>
              </div>
            </div>
            <div className="flex items-center justify-between py-2 border-t border-border">
              <div>
                <p className="text-sm text-text-primary">Show cost badges</p>
                <p className="text-xs text-text-muted">Display free/paid indicators in chat</p>
              </div>
              <Toggle
                on={eco.show_badges}
                onChange={async () => {
                  try {
                    await onSettingsUpdate({ show_badges: !eco.show_badges });
                  } catch {
                    toast.error("Failed to update");
                  }
                }}
              />
            </div>
          </div>
        </section>
      )}

      {/* Usage stats with progress bar */}
      {usage && (
        <section className="bg-bg-secondary border border-border rounded-xl p-5">
          <SectionHeading title="Usage" />
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
            <div>
              <p className="text-[10px] text-text-muted uppercase tracking-wide">Free</p>
              <p className="text-lg font-semibold text-text-primary">{usage.free_count}</p>
            </div>
            <div>
              <p className="text-[10px] text-text-muted uppercase tracking-wide">Paid</p>
              <p className="text-lg font-semibold text-text-primary">{usage.paid_count}</p>
            </div>
            <div>
              <p className="text-[10px] text-text-muted uppercase tracking-wide">Total</p>
              <p className="text-lg font-semibold text-text-primary">{usage.total}</p>
            </div>
            <div>
              <p className="text-[10px] text-text-muted uppercase tracking-wide">Free %</p>
              <p className="text-lg font-semibold text-accent">{usage.free_percentage}%</p>
            </div>
          </div>
          <div className="space-y-1.5">
            <div className="flex justify-between text-[10px] text-text-muted">
              <span>Free ({freeRatio.toFixed(0)}%)</span>
              <span>Paid ({paidRatio.toFixed(0)}%)</span>
            </div>
            <div className="h-2 rounded-full bg-bg-hover overflow-hidden flex">
              <div className="h-full bg-accent rounded-l-full transition-all" style={{ width: `${freeRatio}%` }} />
              <div className="h-full bg-amber transition-all" style={{ width: `${paidRatio}%` }} />
            </div>
          </div>
        </section>
      )}

      {/* Providers */}
      {providers.length > 0 && (
        <section className="bg-bg-secondary border border-border rounded-xl p-5">
          <SectionHeading title="Providers" />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {providers.map((p) => (
              <div key={p.name} className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-bg-hover">
                <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${p.configured ? "bg-accent" : "bg-text-muted opacity-40"}`} />
                <span className="text-sm text-text-primary flex-1 truncate">{p.name}</span>
                <span className={`text-[10px] shrink-0 ${p.configured ? "text-accent" : "text-text-muted"}`}>
                  {p.configured ? "configured" : "not configured"}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Rate limits */}
      {rateLimits && Object.keys(rateLimits).length > 0 && (
        <section className="bg-bg-secondary border border-border rounded-xl p-5">
          <SectionHeading title="Rate Limits" />
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-muted border-b border-border">
                  <th className="text-left py-2 pr-4 font-medium">Provider</th>
                  <th className="text-right py-2 px-4 font-medium">RPM</th>
                  <th className="text-right py-2 px-4 font-medium">RPD</th>
                  <th className="text-right py-2 pl-4 font-medium">TPM</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(rateLimits).map(([name, lim]) => (
                  <tr key={name} className="text-text-secondary border-b border-border/50">
                    <td className="py-2 pr-4 text-text-primary">{name}</td>
                    <td className="text-right py-2 px-4">{lim.requests_per_minute}</td>
                    <td className="text-right py-2 px-4">{lim.requests_per_day.toLocaleString()}</td>
                    <td className="text-right py-2 pl-4">{lim.tokens_per_minute.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

// ── Teams Tab ──────────────────────────────────────────────────────────────

function TeamsTab({
  team,
  specialists,
  onTeamUpdate,
  onSpecialistDelete,
  onSpecialistCreate,
}: {
  readonly team: TeamSettings | null;
  readonly specialists: readonly Specialist[];
  readonly onTeamUpdate: (updates: Partial<TeamSettings>) => Promise<void>;
  readonly onSpecialistDelete: (name: string) => Promise<void>;
  readonly onSpecialistCreate: (body: {
    name: string;
    display_name: string;
    system_prompt: string;
    allowed_skills: string[];
    preferred_model?: string;
  }) => Promise<void>;
}) {
  const toast = useToast();
  const [saving, setSaving] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDisplayName, setNewDisplayName] = useState("");
  const [newPrompt, setNewPrompt] = useState("");
  const [newSkills, setNewSkills] = useState("");
  const [newModel, setNewModel] = useState("");
  const [creating, setCreating] = useState(false);

  const [maxParallel, setMaxParallel] = useState(String(team?.max_parallel ?? 3));

  useEffect(() => {
    if (team) setMaxParallel(String(team.max_parallel));
  }, [team]);

  const handleTeamChange = async (updates: Partial<TeamSettings>) => {
    const key = Object.keys(updates)[0];
    setSaving(key);
    try {
      await onTeamUpdate(updates);
      toast.success("Team settings updated");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update");
    } finally {
      setSaving(null);
    }
  };

  const handleMaxParallelSave = async () => {
    const val = parseInt(maxParallel, 10);
    if (Number.isNaN(val) || val < 1) {
      toast.error("Invalid value");
      return;
    }
    await handleTeamChange({ max_parallel: val });
  };

  const handleDelete = async (name: string) => {
    try {
      await onSpecialistDelete(name);
      toast.success(`Specialist "${name}" removed`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to remove");
    }
  };

  const handleCreate = async () => {
    if (!newName.trim() || !newDisplayName.trim() || !newPrompt.trim()) return;
    setCreating(true);
    try {
      const skills = newSkills.split(",").map((s) => s.trim()).filter(Boolean);
      await onSpecialistCreate({
        name: newName.trim(),
        display_name: newDisplayName.trim(),
        system_prompt: newPrompt.trim(),
        allowed_skills: skills.length > 0 ? skills : ["*"],
        preferred_model: newModel.trim() || undefined,
      });
      setShowCreate(false);
      setNewName(""); setNewDisplayName(""); setNewPrompt(""); setNewSkills(""); setNewModel("");
      toast.success("Specialist created");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Team settings */}
      <section className="bg-bg-secondary border border-border rounded-xl p-5">
        <SectionHeading title="Team Settings" />
        <div className="space-y-3">
          {/* Mode selector */}
          <div className="flex items-center justify-between py-2">
            <div>
              <p className="text-sm text-text-primary">Mode</p>
              <p className="text-xs text-text-muted">Execution strategy for specialist teams</p>
            </div>
            <div className="flex gap-1">
              {TEAM_MODES.map((m) => (
                <button
                  key={m}
                  onClick={() => handleTeamChange({ mode: m })}
                  disabled={saving === "mode"}
                  className={`px-3 py-1 text-xs rounded-lg border transition-colors ${
                    team?.mode === m
                      ? "border-accent bg-accent-soft text-accent"
                      : "border-border text-text-muted hover:bg-bg-hover"
                  } disabled:opacity-50`}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>

          <div className="border-t border-border" />

          {/* Critic mode */}
          <div className="flex items-center justify-between py-2">
            <div>
              <p className="text-sm text-text-primary">Critic Mode</p>
              <p className="text-xs text-text-muted">Review specialist outputs before returning</p>
            </div>
            <Toggle
              on={team?.critic_mode ?? false}
              onChange={() => handleTeamChange({ critic_mode: !(team?.critic_mode) })}
              disabled={saving === "critic_mode"}
            />
          </div>

          <div className="border-t border-border" />

          {/* Max parallel */}
          <div className="flex items-center justify-between py-2">
            <div>
              <p className="text-sm text-text-primary">Max Parallel</p>
              <p className="text-xs text-text-muted">Concurrent specialist limit</p>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min="1"
                max="10"
                value={maxParallel}
                onChange={(e) => setMaxParallel(e.target.value)}
                className="w-16 px-2 py-1 text-sm text-center rounded-lg bg-bg-hover border border-border text-text-primary focus:outline-none focus:border-border-light"
              />
              <button
                onClick={handleMaxParallelSave}
                disabled={saving === "max_parallel"}
                className="text-xs text-accent border border-accent/30 px-2 py-1 rounded-lg hover:bg-accent-soft disabled:opacity-40 transition-colors"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* Specialists */}
      <section className="bg-bg-secondary border border-border rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-sm font-semibold text-text-primary">Specialists ({specialists.length})</h2>
            <p className="text-xs text-text-muted mt-0.5">Available specialist roles for delegation</p>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 text-xs text-accent border border-accent/30 px-3 py-1.5 rounded-lg hover:bg-accent-soft transition-colors"
          >
            <PlusIcon />
            Add
          </button>
        </div>

        {specialists.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {specialists.map((s) => (
              <div
                key={s.name}
                className="group flex items-start gap-3 px-4 py-3 rounded-xl border border-border bg-bg-hover card-hover"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-medium text-text-primary">{s.name}</span>
                    <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${s.builtin ? "bg-cyan-soft text-cyan" : "bg-accent-soft text-accent"}`}>
                      {s.builtin ? "builtin" : "custom"}
                    </span>
                  </div>
                  {s.display_name && s.display_name !== s.name && (
                    <p className="text-xs text-text-secondary">{s.display_name}</p>
                  )}
                  {s.preferred_model && (
                    <p className="text-[10px] text-text-muted">Model: {s.preferred_model}</p>
                  )}
                </div>
                {!s.builtin && (
                  <button
                    onClick={() => handleDelete(s.name)}
                    className="shrink-0 opacity-0 group-hover:opacity-100 p-1.5 rounded-lg text-text-muted hover:text-error hover:bg-bg-hover transition-all"
                    title="Remove specialist"
                  >
                    <TrashIcon />
                  </button>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-text-muted py-4 text-center">No specialists loaded</p>
        )}
      </section>

      {/* Create specialist modal */}
      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="Add Specialist">
        <div className="space-y-3">
          <div>
            <label className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">Name (ID)</label>
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="e.g. researcher"
              className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light"
            />
          </div>
          <div>
            <label className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">Display Name</label>
            <input
              type="text"
              value={newDisplayName}
              onChange={(e) => setNewDisplayName(e.target.value)}
              placeholder="e.g. Research Specialist"
              className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light"
            />
          </div>
          <div>
            <label className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">System Prompt</label>
            <textarea
              value={newPrompt}
              onChange={(e) => setNewPrompt(e.target.value)}
              placeholder="Instructions for this specialist..."
              rows={4}
              className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light resize-y"
            />
          </div>
          <div>
            <label className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">Allowed Skills (comma-separated, * for all)</label>
            <input
              type="text"
              value={newSkills}
              onChange={(e) => setNewSkills(e.target.value)}
              placeholder="web_search, browse_web or * for all"
              className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light"
            />
          </div>
          <div>
            <label className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">Preferred Model (optional)</label>
            <input
              type="text"
              value={newModel}
              onChange={(e) => setNewModel(e.target.value)}
              placeholder="e.g. claude-sonnet-4-6-20250514"
              className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button onClick={() => setShowCreate(false)} className="px-4 py-2 text-sm text-text-muted rounded-lg hover:bg-bg-hover transition-colors">Cancel</button>
            <button
              onClick={handleCreate}
              disabled={creating || !newName.trim() || !newDisplayName.trim() || !newPrompt.trim()}
              className="px-4 py-2 text-sm bg-accent text-bg-primary rounded-lg hover:opacity-90 disabled:opacity-30 transition-opacity"
            >
              {creating ? "Creating..." : "Create"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

// ── Permissions Tab ────────────────────────────────────────────────────────

function PermissionsTab({
  perms,
  onPermsUpdate,
}: {
  readonly perms: PermissionSettings | null;
  readonly onPermsUpdate: (updates: Partial<PermissionSettings>) => Promise<void>;
}) {
  const toast = useToast();

  if (!perms) {
    return (
      <p className="text-sm text-text-muted text-center py-12">
        Permission settings not available
      </p>
    );
  }

  const categoryEntries = Object.entries(perms.category_defaults);
  const overrideEntries = Object.entries(perms.skill_overrides);

  const cycleLevel = (current: string): string => {
    const idx = PERMISSION_LEVELS.indexOf(current as typeof PERMISSION_LEVELS[number]);
    return PERMISSION_LEVELS[(idx + 1) % PERMISSION_LEVELS.length];
  };

  const handleCategoryChange = async (cat: string, current: string) => {
    const next = cycleLevel(current);
    try {
      await onPermsUpdate({
        ...perms,
        category_defaults: { ...perms.category_defaults, [cat]: next },
      });
      toast.success(`${cat}: ${next}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update");
    }
  };

  const handleOverrideChange = async (skill: string, current: string) => {
    const next = cycleLevel(current);
    try {
      await onPermsUpdate({
        ...perms,
        skill_overrides: { ...perms.skill_overrides, [skill]: next },
      });
      toast.success(`${skill}: ${next}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update");
    }
  };

  const [timeoutVal, setTimeoutVal] = useState(String(perms.auto_approve_timeout));
  const [savingTimeout, setSavingTimeout] = useState(false);

  const handleTimeoutSave = async () => {
    const val = parseInt(timeoutVal, 10);
    if (Number.isNaN(val) || val < 0) {
      toast.error("Invalid timeout value");
      return;
    }
    setSavingTimeout(true);
    try {
      await onPermsUpdate({ ...perms, auto_approve_timeout: val });
      toast.success("Timeout updated");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update");
    } finally {
      setSavingTimeout(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Category defaults */}
      <section className="bg-bg-secondary border border-border rounded-xl p-5">
        <SectionHeading
          title="Category Defaults"
          subtitle="Click a badge to cycle: allow → ask → deny"
        />
        {categoryEntries.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {categoryEntries.map(([cat, level]) => (
              <div key={cat} className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-bg-hover">
                <span className="text-sm text-text-primary truncate mr-2">{cat}</span>
                <PermissionBadge level={level} onClick={() => handleCategoryChange(cat, level)} />
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-text-muted py-4 text-center">No category defaults set</p>
        )}
      </section>

      {/* Skill overrides */}
      {overrideEntries.length > 0 && (
        <section className="bg-bg-secondary border border-border rounded-xl p-5">
          <SectionHeading
            title="Skill Overrides"
            subtitle="Per-skill permission overrides — click to change"
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {overrideEntries.map(([skill, level]) => (
              <div key={skill} className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-bg-hover">
                <span className="text-sm text-text-primary font-mono truncate mr-2">{skill}</span>
                <PermissionBadge level={level} onClick={() => handleOverrideChange(skill, level)} />
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Auto-approve timeout */}
      <section className="bg-bg-secondary border border-border rounded-xl p-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-text-primary">Auto-approve Timeout</h2>
            <p className="text-xs text-text-muted mt-0.5">Seconds before pending permissions auto-deny</p>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min="0"
              value={timeoutVal}
              onChange={(e) => setTimeoutVal(e.target.value)}
              className="w-20 px-2 py-1 text-sm text-center rounded-lg bg-bg-hover border border-border text-text-primary focus:outline-none focus:border-border-light"
            />
            <span className="text-xs text-text-muted">s</span>
            <button
              onClick={handleTimeoutSave}
              disabled={savingTimeout}
              className="text-xs text-accent border border-accent/30 px-2 py-1 rounded-lg hover:bg-accent-soft disabled:opacity-40 transition-colors"
            >
              Save
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

// ── Main Settings page ─────────────────────────────────────────────────────

export default function Settings() {
  const toast = useToast();
  const [eco, setEco] = useState<EcoSettings | null>(null);
  const [usage, setUsage] = useState<EcoUsage | null>(null);
  const [providers, setProviders] = useState<EcoProvider[]>([]);
  const [rateLimits, setRateLimits] = useState<RateLimits | null>(null);
  const [team, setTeam] = useState<TeamSettings | null>(null);
  const [specialists, setSpecialists] = useState<Specialist[]>([]);
  const [perms, setPerms] = useState<PermissionSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<TabId>("eco");

  const load = useCallback(async () => {
    setLoading(true);
    const results = await Promise.allSettled([
      api.getEcoSettings(),
      api.getEcoUsage(),
      api.getEcoProviders(),
      api.getEcoRateLimits(),
      api.getTeamSettings(),
      api.listSpecialists(),
      api.getPermissionSettings(),
    ]);

    if (results[0].status === "fulfilled") setEco(results[0].value);
    if (results[1].status === "fulfilled") setUsage(results[1].value);
    if (results[2].status === "fulfilled") setProviders(Array.isArray(results[2].value) ? results[2].value : []);
    if (results[3].status === "fulfilled") setRateLimits(results[3].value);
    if (results[4].status === "fulfilled") setTeam(results[4].value);
    if (results[5].status === "fulfilled") setSpecialists(Array.isArray(results[5].value) ? results[5].value : []);
    if (results[6].status === "fulfilled") setPerms(results[6].value);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleEcoMode = useCallback(async (mode: string) => {
    try {
      const updated = await api.updateEcoSettings({ mode });
      setEco(updated);
      toast.success(`Switched to ${mode.toUpperCase()} mode`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update mode");
    }
  }, [toast]);

  const handleEcoSettingsUpdate = useCallback(async (updates: Partial<EcoSettings>) => {
    const updated = await api.updateEcoSettings(updates);
    setEco(updated);
  }, []);

  const handleTeamUpdate = useCallback(async (updates: Partial<TeamSettings>) => {
    const updated = await api.updateTeamSettings(updates);
    setTeam(updated);
  }, []);

  const handleSpecialistDelete = useCallback(async (name: string) => {
    await api.deleteSpecialist(name);
    setSpecialists((prev) => prev.filter((s) => s.name !== name));
  }, []);

  const handleSpecialistCreate = useCallback(async (body: {
    name: string;
    display_name: string;
    system_prompt: string;
    allowed_skills: string[];
    preferred_model?: string;
  }) => {
    await api.createSpecialist(body);
    const updated = await api.listSpecialists();
    setSpecialists(Array.isArray(updated) ? updated : []);
  }, []);

  const handlePermsUpdate = useCallback(async (updates: Partial<PermissionSettings>) => {
    const updated = await api.updatePermissionSettings(updates);
    setPerms(updated);
  }, []);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-text-muted text-sm">
        <SpinnerIcon />
        <span className="ml-2">Loading settings...</span>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="animate-fade-in max-w-5xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-lg font-semibold text-text-primary mb-1">Settings</h1>
          <p className="text-sm text-text-muted">Agent configuration, cost control, and permissions</p>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 p-1 bg-bg-secondary rounded-xl border border-border w-fit">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`relative flex items-center gap-1.5 px-4 py-2 text-xs rounded-lg transition-colors ${
                tab === t.id
                  ? "bg-bg-hover text-text-primary"
                  : "text-text-muted hover:text-text-secondary"
              }`}
            >
              {t.icon}
              <span>{t.label}</span>
              {tab === t.id && (
                <span className="absolute bottom-0 left-2 right-2 h-0.5 bg-accent rounded-full" />
              )}
            </button>
          ))}
        </div>

        {/* Tab content */}
        {tab === "eco" && (
          <EcoTab
            eco={eco}
            usage={usage}
            providers={providers}
            rateLimits={rateLimits}
            onModeChange={handleEcoMode}
            onSettingsUpdate={handleEcoSettingsUpdate}
          />
        )}
        {tab === "teams" && (
          <TeamsTab
            team={team}
            specialists={specialists}
            onTeamUpdate={handleTeamUpdate}
            onSpecialistDelete={handleSpecialistDelete}
            onSpecialistCreate={handleSpecialistCreate}
          />
        )}
        {tab === "permissions" && (
          <PermissionsTab perms={perms} onPermsUpdate={handlePermsUpdate} />
        )}
      </div>
    </div>
  );
}
