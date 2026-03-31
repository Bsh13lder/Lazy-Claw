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

// ── Permission badge ───────────────────────────────────────────────────────

function PermissionBadge({ level }: { readonly level: string }) {
  const styles =
    level === "allow"
      ? "bg-accent-soft text-accent"
      : level === "deny"
        ? "bg-error-soft text-error"
        : "bg-amber-soft text-amber";

  return (
    <span className={`text-[10px] font-medium px-2.5 py-0.5 rounded-full ${styles}`}>
      {level}
    </span>
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

// ── ECO Tab ────────────────────────────────────────────────────────────────

function EcoTab({
  eco,
  usage,
  providers,
  rateLimits,
  onModeChange,
}: {
  readonly eco: EcoSettings | null;
  readonly usage: EcoUsage | null;
  readonly providers: readonly EcoProvider[];
  readonly rateLimits: RateLimits | null;
  readonly onModeChange: (mode: string) => void;
}) {
  const freeRatio = usage && usage.total > 0 ? (usage.free_count / usage.total) * 100 : 0;
  const paidRatio = usage && usage.total > 0 ? (usage.paid_count / usage.total) * 100 : 0;

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
                <span
                  className={`text-sm font-semibold ${
                    isActive ? "text-accent" : "text-text-primary"
                  }`}
                >
                  {card.name}
                </span>
                <span className="text-xs text-text-muted">{card.description}</span>
              </button>
            );
          })}
        </div>
        {eco && (
          <p className="mt-4 text-xs text-text-muted">
            Monthly budget: <span className="text-text-secondary font-medium">${eco.monthly_paid_budget}</span>
          </p>
        )}
      </section>

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
          {/* Progress bar */}
          <div className="space-y-1.5">
            <div className="flex justify-between text-[10px] text-text-muted">
              <span>Free ({freeRatio.toFixed(0)}%)</span>
              <span>Paid ({paidRatio.toFixed(0)}%)</span>
            </div>
            <div className="h-2 rounded-full bg-bg-hover overflow-hidden flex">
              <div
                className="h-full bg-accent rounded-l-full transition-all"
                style={{ width: `${freeRatio}%` }}
              />
              <div
                className="h-full bg-amber transition-all"
                style={{ width: `${paidRatio}%` }}
              />
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
              <div
                key={p.name}
                className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-bg-hover"
              >
                <span
                  className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                    p.configured ? "bg-accent" : "bg-text-muted opacity-40"
                  }`}
                />
                <span className="text-sm text-text-primary flex-1 truncate">{p.name}</span>
                <span
                  className={`text-[10px] shrink-0 ${
                    p.configured ? "text-accent" : "text-text-muted"
                  }`}
                >
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
}: {
  readonly team: TeamSettings | null;
  readonly specialists: readonly Specialist[];
}) {
  return (
    <div className="space-y-6">
      {/* Team settings */}
      <section className="bg-bg-secondary border border-border rounded-xl p-5">
        <SectionHeading title="Team Settings" />
        <div className="space-y-3">
          <div className="flex items-center justify-between py-2">
            <div>
              <p className="text-sm text-text-primary">Mode</p>
              <p className="text-xs text-text-muted">Execution strategy for specialist teams</p>
            </div>
            <span className="text-sm text-text-secondary font-medium px-3 py-1 bg-bg-hover rounded-lg">
              {team?.mode ?? "---"}
            </span>
          </div>

          <div className="border-t border-border" />

          <div className="flex items-center justify-between py-2">
            <div>
              <p className="text-sm text-text-primary">Critic Mode</p>
              <p className="text-xs text-text-muted">Review specialist outputs before returning</p>
            </div>
            <div className={`toggle-switch ${team?.critic_mode ? "active" : ""}`}>
              <div className="toggle-knob" />
            </div>
          </div>

          <div className="border-t border-border" />

          <div className="flex items-center justify-between py-2">
            <div>
              <p className="text-sm text-text-primary">Max Parallel</p>
              <p className="text-xs text-text-muted">Concurrent specialist limit</p>
            </div>
            <span className="text-sm text-text-secondary font-medium px-3 py-1 bg-bg-hover rounded-lg">
              {team?.max_parallel ?? 0}
            </span>
          </div>
        </div>
      </section>

      {/* Specialists */}
      <section className="bg-bg-secondary border border-border rounded-xl p-5">
        <SectionHeading
          title={`Specialists (${specialists.length})`}
          subtitle="Available specialist roles for delegation"
        />
        {specialists.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {specialists.map((s) => (
              <div
                key={s.name}
                className="card-hover flex items-start gap-3 px-4 py-3 rounded-xl border border-border bg-bg-hover"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-medium text-text-primary">{s.name}</span>
                    <span
                      className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${
                        s.builtin
                          ? "bg-cyan-soft text-cyan"
                          : "bg-accent-soft text-accent"
                      }`}
                    >
                      {s.builtin ? "builtin" : "custom"}
                    </span>
                  </div>
                  {s.description && (
                    <p className="text-xs text-text-muted line-clamp-2">{s.description}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-text-muted py-4 text-center">No specialists loaded</p>
        )}
      </section>
    </div>
  );
}

// ── Permissions Tab ────────────────────────────────────────────────────────

function PermissionsTab({
  perms,
}: {
  readonly perms: PermissionSettings | null;
}) {
  if (!perms) {
    return (
      <p className="text-sm text-text-muted text-center py-12">
        Permission settings not available
      </p>
    );
  }

  const categoryEntries = Object.entries(perms.category_defaults);
  const overrideEntries = Object.entries(perms.skill_overrides);

  return (
    <div className="space-y-6">
      {/* Category defaults */}
      <section className="bg-bg-secondary border border-border rounded-xl p-5">
        <SectionHeading
          title="Category Defaults"
          subtitle="Default permission level per skill category"
        />
        {categoryEntries.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {categoryEntries.map(([cat, level]) => (
              <div
                key={cat}
                className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-bg-hover"
              >
                <span className="text-sm text-text-primary truncate mr-2">{cat}</span>
                <PermissionBadge level={level} />
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
            subtitle="Per-skill permission overrides"
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {overrideEntries.map(([skill, level]) => (
              <div
                key={skill}
                className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-bg-hover"
              >
                <span className="text-sm text-text-primary font-mono truncate mr-2">{skill}</span>
                <PermissionBadge level={level} />
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
            <p className="text-xs text-text-muted mt-0.5">
              Seconds before pending permissions auto-deny
            </p>
          </div>
          <span className="text-sm text-text-secondary font-medium px-3 py-1 bg-bg-hover rounded-lg">
            {perms.auto_approve_timeout}s
          </span>
        </div>
      </section>
    </div>
  );
}

// ── Main Settings page ─────────────────────────────────────────────────────

export default function Settings() {
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
    } catch {
      /* network error — eco state unchanged */
    }
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
          <p className="text-sm text-text-muted">
            Agent configuration, cost control, and permissions
          </p>
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
          />
        )}
        {tab === "teams" && <TeamsTab team={team} specialists={specialists} />}
        {tab === "permissions" && <PermissionsTab perms={perms} />}
      </div>
    </div>
  );
}
