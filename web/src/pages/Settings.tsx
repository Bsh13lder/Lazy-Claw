import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { EcoSettings, EcoUsage, EcoProvider, RateLimits, TeamSettings, Specialist, PermissionSettings } from "../api";

export default function Settings() {
  const [eco, setEco] = useState<EcoSettings | null>(null);
  const [usage, setUsage] = useState<EcoUsage | null>(null);
  const [providers, setProviders] = useState<EcoProvider[]>([]);
  const [rateLimits, setRateLimits] = useState<RateLimits | null>(null);
  const [team, setTeam] = useState<TeamSettings | null>(null);
  const [specialists, setSpecialists] = useState<Specialist[]>([]);
  const [perms, setPerms] = useState<PermissionSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"eco" | "teams" | "permissions">("eco");

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

  useEffect(() => { load(); }, [load]);

  const handleEcoMode = async (mode: string) => {
    try { const updated = await api.updateEcoSettings({ mode }); setEco(updated); } catch { /* */ }
  };

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-text-muted text-sm">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner mr-2"><path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" /></svg>
        Loading settings...
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        <div className="mb-6">
          <h1 className="text-lg font-semibold text-text-primary mb-1">Settings</h1>
          <p className="text-sm text-text-muted">Agent configuration, cost control, and permissions</p>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 p-1 bg-bg-secondary rounded-xl border border-border w-fit">
          {(["eco", "teams", "permissions"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`px-4 py-1.5 text-xs rounded-lg transition-colors ${tab === t ? "bg-bg-hover text-text-primary" : "text-text-muted hover:text-text-secondary"}`}>
              {t === "eco" ? "ECO Mode" : t === "teams" ? "Teams" : "Permissions"}
            </button>
          ))}
        </div>

        {/* ECO Tab */}
        {tab === "eco" && (
          <div className="space-y-6">
            {/* Mode selector */}
            <section className="bg-bg-secondary border border-border rounded-xl p-5">
              <h2 className="text-sm font-semibold text-text-primary mb-1">Mode</h2>
              <p className="text-xs text-text-muted mb-3">Controls cost routing between free and paid providers</p>
              <div className="flex gap-2">
                {["eco", "hybrid", "full"].map((mode) => (
                  <button key={mode} onClick={() => handleEcoMode(mode)} className={`px-4 py-2 text-sm rounded-lg border transition-colors ${eco?.mode === mode ? "border-accent bg-accent-soft text-accent" : "border-border text-text-muted hover:text-text-secondary hover:bg-bg-hover"}`}>
                    {mode.toUpperCase()}
                  </button>
                ))}
              </div>
              {eco && <p className="mt-3 text-xs text-text-muted">Budget: ${eco.monthly_paid_budget}/month</p>}
            </section>

            {/* Usage stats */}
            {usage && (
              <section className="bg-bg-secondary border border-border rounded-xl p-5">
                <h2 className="text-sm font-semibold text-text-primary mb-3">Usage</h2>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <div><p className="text-[10px] text-text-muted">Free requests</p><p className="text-lg font-semibold text-text-primary">{usage.free_count}</p></div>
                  <div><p className="text-[10px] text-text-muted">Paid requests</p><p className="text-lg font-semibold text-text-primary">{usage.paid_count}</p></div>
                  <div><p className="text-[10px] text-text-muted">Total</p><p className="text-lg font-semibold text-text-primary">{usage.total}</p></div>
                  <div><p className="text-[10px] text-text-muted">Free %</p><p className="text-lg font-semibold text-accent">{usage.free_percentage}%</p></div>
                </div>
              </section>
            )}

            {/* Providers */}
            {providers.length > 0 && (
              <section className="bg-bg-secondary border border-border rounded-xl p-5">
                <h2 className="text-sm font-semibold text-text-primary mb-3">Providers</h2>
                <div className="space-y-1">
                  {providers.map((p) => (
                    <div key={p.name} className="flex items-center gap-3 px-3 py-2 rounded-lg bg-bg-tertiary">
                      <span className={`w-2 h-2 rounded-full ${p.configured ? "bg-accent" : "bg-text-muted"}`} />
                      <span className="text-sm text-text-primary flex-1">{p.name}</span>
                      <span className="text-[10px] text-text-muted">{p.configured ? "configured" : "not configured"}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Rate limits */}
            {rateLimits && Object.keys(rateLimits).length > 0 && (
              <section className="bg-bg-secondary border border-border rounded-xl p-5">
                <h2 className="text-sm font-semibold text-text-primary mb-3">Rate Limits</h2>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-text-muted border-b border-border">
                        <th className="text-left py-2 pr-4">Provider</th>
                        <th className="text-right py-2 px-4">RPM</th>
                        <th className="text-right py-2 px-4">RPD</th>
                        <th className="text-right py-2 pl-4">TPM</th>
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
        )}

        {/* Teams Tab */}
        {tab === "teams" && (
          <div className="space-y-6">
            <section className="bg-bg-secondary border border-border rounded-xl p-5">
              <h2 className="text-sm font-semibold text-text-primary mb-1">Team Settings</h2>
              <div className="flex gap-6 mt-3 text-xs text-text-muted">
                <span>Mode: <span className="text-text-primary">{team?.mode ?? "—"}</span></span>
                <span>Critic: <span className="text-text-primary">{team?.critic_mode ? "on" : "off"}</span></span>
                <span>Max parallel: <span className="text-text-primary">{team?.max_parallel ?? 0}</span></span>
              </div>
            </section>

            <section className="bg-bg-secondary border border-border rounded-xl p-5">
              <h2 className="text-sm font-semibold text-text-primary mb-3">Specialists ({specialists.length})</h2>
              <div className="space-y-1">
                {specialists.map((s) => (
                  <div key={s.name} className="flex items-center gap-3 px-3 py-2 rounded-lg bg-bg-tertiary">
                    <span className={`w-2 h-2 rounded-full ${s.builtin ? "bg-cyan" : "bg-accent"}`} />
                    <div className="flex-1 min-w-0">
                      <span className="text-sm text-text-primary">{s.name}</span>
                      {s.description && <p className="text-[10px] text-text-muted truncate">{s.description}</p>}
                    </div>
                    <span className="text-[10px] text-text-muted">{s.builtin ? "builtin" : "custom"}</span>
                  </div>
                ))}
                {specialists.length === 0 && <p className="text-xs text-text-muted">No specialists loaded</p>}
              </div>
            </section>
          </div>
        )}

        {/* Permissions Tab */}
        {tab === "permissions" && (
          <div className="space-y-6">
            {perms && (
              <>
                <section className="bg-bg-secondary border border-border rounded-xl p-5">
                  <h2 className="text-sm font-semibold text-text-primary mb-3">Category Defaults</h2>
                  <div className="space-y-1">
                    {Object.entries(perms.category_defaults).map(([cat, level]) => (
                      <div key={cat} className="flex items-center gap-3 px-3 py-2 rounded-lg bg-bg-tertiary">
                        <span className="text-sm text-text-primary flex-1">{cat}</span>
                        <span className={`text-[10px] px-2 py-0.5 rounded-full ${level === "allow" ? "bg-accent-soft text-accent" : level === "deny" ? "bg-error-soft text-error" : "bg-bg-hover text-text-muted"}`}>{level}</span>
                      </div>
                    ))}
                    {Object.keys(perms.category_defaults).length === 0 && <p className="text-xs text-text-muted">No category defaults set</p>}
                  </div>
                </section>

                {Object.keys(perms.skill_overrides).length > 0 && (
                  <section className="bg-bg-secondary border border-border rounded-xl p-5">
                    <h2 className="text-sm font-semibold text-text-primary mb-3">Skill Overrides</h2>
                    <div className="space-y-1">
                      {Object.entries(perms.skill_overrides).map(([skill, level]) => (
                        <div key={skill} className="flex items-center gap-3 px-3 py-2 rounded-lg bg-bg-tertiary">
                          <span className="text-sm text-text-primary font-mono flex-1">{skill}</span>
                          <span className={`text-[10px] px-2 py-0.5 rounded-full ${level === "allow" ? "bg-accent-soft text-accent" : level === "deny" ? "bg-error-soft text-error" : "bg-bg-hover text-text-muted"}`}>{level}</span>
                        </div>
                      ))}
                    </div>
                  </section>
                )}

                <section className="bg-bg-secondary border border-border rounded-xl p-5">
                  <h2 className="text-sm font-semibold text-text-primary mb-1">Auto-approve timeout</h2>
                  <p className="text-sm text-text-secondary">{perms.auto_approve_timeout}s</p>
                </section>
              </>
            )}
            {!perms && <p className="text-sm text-text-muted text-center py-8">Permission settings not available</p>}
          </div>
        )}
      </div>
    </div>
  );
}
