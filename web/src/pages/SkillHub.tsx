import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "../api";
import type { Skill } from "../api";
import Modal from "../components/Modal";

// ── Category mapping ──────────────────────────────────────────────────────

const CATEGORY_MAP: Record<string, string> = {
  browser: "Browser",
  automation: "Automation",
  data: "Data",
  communication: "Communication",
  development: "Development",
  code: "Development",
  instruction: "Automation",
  builtin: "Core",
  mcp: "Integration",
  plugin: "Plugin",
};

const ALL_CATEGORIES = ["All", "Core", "Browser", "Automation", "Data", "Communication", "Development", "Integration", "Plugin"];

function resolveCategory(skill: Skill): string {
  if (skill.category) {
    const mapped = CATEGORY_MAP[skill.category.toLowerCase()];
    if (mapped) return mapped;
    return skill.category.charAt(0).toUpperCase() + skill.category.slice(1);
  }
  return CATEGORY_MAP[skill.skill_type?.toLowerCase()] ?? "Other";
}

// ── Skill type icon ───────────────────────────────────────────────────────

function SkillIcon({ type }: { type: string }) {
  const base = "w-10 h-10 rounded-xl flex items-center justify-center shrink-0";
  switch (type) {
    case "builtin":
      return (
        <div className={`${base} bg-emerald-500/15`}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#10b981" strokeWidth="1.8" strokeLinecap="round">
            <path d="M12 2L2 7l10 5 10-5-10-5z" />
            <path d="M2 17l10 5 10-5" />
            <path d="M2 12l10 5 10-5" />
          </svg>
        </div>
      );
    case "code":
      return (
        <div className={`${base} bg-cyan-500/15`}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#06b6d4" strokeWidth="1.8" strokeLinecap="round">
            <polyline points="16 18 22 12 16 6" />
            <polyline points="8 6 2 12 8 18" />
          </svg>
        </div>
      );
    case "mcp":
      return (
        <div className={`${base} bg-purple-500/15`}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#a855f7" strokeWidth="1.8" strokeLinecap="round">
            <path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z" />
          </svg>
        </div>
      );
    case "instruction":
      return (
        <div className={`${base} bg-amber-500/15`}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" strokeWidth="1.8" strokeLinecap="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" y1="13" x2="8" y2="13" />
            <line x1="16" y1="17" x2="8" y2="17" />
          </svg>
        </div>
      );
    default:
      return (
        <div className={`${base} bg-gray-500/15`}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="1.8" strokeLinecap="round">
            <circle cx="12" cy="12" r="10" />
            <path d="M12 16v-4M12 8h.01" />
          </svg>
        </div>
      );
  }
}

// ── Type badge color ──────────────────────────────────────────────────────

function typeBadgeClass(type: string): string {
  switch (type) {
    case "builtin": return "bg-emerald-500/15 text-emerald-400 border-emerald-500/25";
    case "code": return "bg-cyan-500/15 text-cyan-400 border-cyan-500/25";
    case "mcp": return "bg-purple-500/15 text-purple-400 border-purple-500/25";
    case "instruction": return "bg-amber-500/15 text-amber-400 border-amber-500/25";
    default: return "bg-gray-500/15 text-gray-400 border-gray-500/25";
  }
}

// ── Main component ────────────────────────────────────────────────────────

export default function SkillHub() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [activeCategory, setActiveCategory] = useState("All");
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [showGenerate, setShowGenerate] = useState(false);
  const [togglingIds, setTogglingIds] = useState<Set<string>>(new Set());

  // Generate form
  const [gDesc, setGDesc] = useState("");
  const [gName, setGName] = useState("");
  const [generating, setGenerating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listSkills();
      setSkills(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load skills");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // ── Filtering ─────────────────────────────────────────────────────────

  const filtered = useMemo(() => {
    let list = skills;
    if (activeCategory !== "All") {
      list = list.filter((s) => resolveCategory(s) === activeCategory);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.description?.toLowerCase().includes(q) ||
          s.skill_type?.toLowerCase().includes(q),
      );
    }
    return list;
  }, [skills, activeCategory, search]);

  // Split into featured (builtin enabled) and the rest
  const featured = useMemo(
    () => filtered.filter((s) => s.skill_type === "builtin" && s.enabled).slice(0, 6),
    [filtered],
  );

  const categoryCount = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const s of skills) {
      const cat = resolveCategory(s);
      counts[cat] = (counts[cat] ?? 0) + 1;
    }
    return counts;
  }, [skills]);

  // ── Handlers ──────────────────────────────────────────────────────────

  const handleToggle = async (skill: Skill) => {
    if (skill.skill_type === "builtin" || skill.skill_type === "mcp") return;
    setTogglingIds((prev) => new Set(prev).add(skill.id));
    try {
      await api.updateSkill(skill.id, {});
      setSkills((prev) =>
        prev.map((s) => (s.id === skill.id ? { ...s, enabled: !s.enabled } : s)),
      );
    } catch {
      // silent
    } finally {
      setTogglingIds((prev) => {
        const next = new Set(prev);
        next.delete(skill.id);
        return next;
      });
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteSkill(id);
      setSkills((prev) => prev.filter((s) => s.id !== id));
      setSelectedSkill(null);
    } catch {
      // silent
    }
  };

  const handleGenerate = async () => {
    if (!gDesc.trim()) return;
    setGenerating(true);
    try {
      await api.generateSkill({ description: gDesc.trim(), name: gName.trim() || undefined });
      setShowGenerate(false);
      setGDesc("");
      setGName("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate skill");
    } finally {
      setGenerating(false);
    }
  };

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <div className="h-full overflow-y-auto">
      {/* Hero section */}
      <div className="relative overflow-hidden">
        {/* Gradient background */}
        <div className="absolute inset-0 bg-gradient-to-br from-emerald-500/8 via-transparent to-cyan-500/8" />
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[300px] bg-emerald-500/5 rounded-full blur-3xl" />

        <div className="relative max-w-6xl mx-auto px-6 pt-10 pb-8">
          <div className="text-center mb-8">
            <h1 className="text-3xl font-bold text-text-primary mb-2">
              Discover Skills
            </h1>
            <p className="text-text-muted text-sm">
              {skills.length} skills available — browse, install, or create your own
            </p>
          </div>

          {/* Search bar */}
          <div className="max-w-xl mx-auto relative">
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              className="absolute left-4 top-1/2 -translate-y-1/2 text-text-muted"
            >
              <circle cx="11" cy="11" r="8" />
              <path d="M21 21l-4.35-4.35" />
            </svg>
            <input
              type="text"
              placeholder="Search skills by name, description, or type..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-12 pr-4 py-3.5 rounded-2xl bg-bg-secondary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-all"
            />
          </div>

          {/* Action buttons */}
          <div className="flex justify-center gap-3 mt-5">
            <button
              onClick={() => setShowGenerate(true)}
              className="flex items-center gap-2 text-xs px-4 py-2 rounded-xl bg-gradient-to-r from-cyan-500/15 to-cyan-500/5 border border-cyan-500/25 text-cyan-400 hover:border-cyan-500/40 transition-colors"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
              </svg>
              AI Generate
            </button>
            <button
              onClick={load}
              className="flex items-center gap-2 text-xs px-4 py-2 rounded-xl border border-border text-text-muted hover:bg-bg-hover hover:text-text-secondary transition-colors"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M23 4v6h-6" />
                <path d="M1 20v-6h6" />
                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
              </svg>
              Refresh
            </button>
          </div>
        </div>
      </div>

      {/* Category chips */}
      <div className="max-w-6xl mx-auto px-6 mb-6">
        <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-none">
          {ALL_CATEGORIES.map((cat) => {
            const count = cat === "All" ? skills.length : (categoryCount[cat] ?? 0);
            if (cat !== "All" && count === 0) return null;
            return (
              <button
                key={cat}
                onClick={() => setActiveCategory(cat)}
                className={`shrink-0 px-4 py-2 rounded-xl text-xs font-medium border transition-all ${
                  activeCategory === cat
                    ? "bg-emerald-500/15 border-emerald-500/30 text-emerald-400"
                    : "bg-bg-secondary border-border text-text-muted hover:border-border-light hover:text-text-secondary"
                }`}
              >
                {cat}
                <span className="ml-1.5 text-[10px] opacity-60">{count}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Content */}
      <div className="max-w-6xl mx-auto px-6 pb-12">
        {loading && (
          <div className="flex items-center gap-2 text-text-muted text-sm py-16 justify-center">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner">
              <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
            </svg>
            Loading skills...
          </div>
        )}

        {error && (
          <div className="px-4 py-3 rounded-xl bg-error-soft border border-error/15 text-error text-sm mb-6">
            {error}
          </div>
        )}

        {!loading && !error && (
          <>
            {/* Featured row */}
            {activeCategory === "All" && search === "" && featured.length > 0 && (
              <div className="mb-8">
                <h2 className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-3 flex items-center gap-2">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10b981" strokeWidth="2" strokeLinecap="round">
                    <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
                  </svg>
                  Featured Skills
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {featured.map((skill) => (
                    <SkillCard
                      key={skill.id}
                      skill={skill}
                      featured
                      toggling={togglingIds.has(skill.id)}
                      onToggle={() => handleToggle(skill)}
                      onClick={() => setSelectedSkill(skill)}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* All skills grid */}
            {filtered.length === 0 ? (
              <div className="text-center py-16 text-text-muted text-sm">
                No skills match your search.
              </div>
            ) : (
              <>
                <h2 className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-3">
                  {activeCategory === "All" ? "All Skills" : activeCategory} ({filtered.length})
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {filtered.map((skill) => (
                    <SkillCard
                      key={skill.id}
                      skill={skill}
                      toggling={togglingIds.has(skill.id)}
                      onToggle={() => handleToggle(skill)}
                      onClick={() => setSelectedSkill(skill)}
                    />
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </div>

      {/* Skill detail modal */}
      <Modal open={selectedSkill !== null} onClose={() => setSelectedSkill(null)} title="Skill Details">
        {selectedSkill && (
          <div className="space-y-4">
            <div className="flex items-start gap-3">
              <SkillIcon type={selectedSkill.skill_type} />
              <div className="min-w-0 flex-1">
                <h3 className="text-base font-semibold text-text-primary">{selectedSkill.name}</h3>
                <span className={`inline-block mt-1 text-[10px] px-2 py-0.5 rounded-full border ${typeBadgeClass(selectedSkill.skill_type)}`}>
                  {selectedSkill.skill_type}
                </span>
              </div>
              <div className={`w-3 h-3 rounded-full shrink-0 mt-1 ${selectedSkill.enabled ? "bg-emerald-500" : "bg-gray-500"}`} />
            </div>

            {selectedSkill.description && (
              <div>
                <p className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-1">Description</p>
                <p className="text-sm text-text-secondary leading-relaxed">{selectedSkill.description}</p>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div className="px-3 py-2.5 rounded-xl bg-bg-tertiary">
                <p className="text-[10px] text-text-muted uppercase tracking-wider mb-0.5">Category</p>
                <p className="text-sm text-text-primary">{resolveCategory(selectedSkill)}</p>
              </div>
              <div className="px-3 py-2.5 rounded-xl bg-bg-tertiary">
                <p className="text-[10px] text-text-muted uppercase tracking-wider mb-0.5">Status</p>
                <p className="text-sm text-text-primary">{selectedSkill.enabled ? "Enabled" : "Disabled"}</p>
              </div>
            </div>

            {selectedSkill.instruction && (
              <div>
                <p className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-1">Instruction</p>
                <pre className="text-xs text-text-secondary bg-bg-tertiary rounded-xl px-4 py-3 overflow-x-auto whitespace-pre-wrap font-mono max-h-48">
                  {selectedSkill.instruction}
                </pre>
              </div>
            )}

            {selectedSkill.code && (
              <div>
                <p className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-1">Code</p>
                <pre className="text-xs text-text-secondary bg-bg-tertiary rounded-xl px-4 py-3 overflow-x-auto whitespace-pre-wrap font-mono max-h-48">
                  {selectedSkill.code}
                </pre>
              </div>
            )}

            {/* Actions */}
            <div className="flex justify-end gap-2 pt-2 border-t border-border">
              {selectedSkill.skill_type !== "builtin" && selectedSkill.skill_type !== "mcp" && (
                <button
                  onClick={() => handleDelete(selectedSkill.id)}
                  className="px-4 py-2 text-sm text-error hover:bg-error-soft rounded-lg transition-colors"
                >
                  Delete
                </button>
              )}
              <button
                onClick={() => setSelectedSkill(null)}
                className="px-4 py-2 text-sm text-text-muted rounded-lg hover:bg-bg-hover transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        )}
      </Modal>

      {/* AI Generate modal */}
      <Modal open={showGenerate} onClose={() => setShowGenerate(false)} title="AI Generate Skill">
        <div className="space-y-3">
          <p className="text-xs text-text-muted">Describe what the skill should do and the AI will generate it for you.</p>
          <input
            type="text"
            value={gName}
            onChange={(e) => setGName(e.target.value)}
            placeholder="Skill name (optional)"
            className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light"
          />
          <textarea
            value={gDesc}
            onChange={(e) => setGDesc(e.target.value)}
            placeholder="Describe what the skill should do..."
            rows={4}
            className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light resize-y"
          />
          <div className="flex justify-end gap-2 pt-2">
            <button
              onClick={() => setShowGenerate(false)}
              className="px-4 py-2 text-sm text-text-muted rounded-lg hover:bg-bg-hover transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleGenerate}
              disabled={generating || !gDesc.trim()}
              className="px-4 py-2 text-sm bg-gradient-to-r from-cyan-500 to-emerald-500 text-white rounded-lg hover:opacity-90 disabled:opacity-30 transition-opacity"
            >
              {generating ? "Generating..." : "Generate"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

// ── Skill card component ──────────────────────────────────────────────────

function SkillCard({
  skill,
  featured,
  toggling,
  onToggle,
  onClick,
}: {
  skill: Skill;
  featured?: boolean;
  toggling: boolean;
  onToggle: () => void;
  onClick: () => void;
}) {
  const isToggleable = skill.skill_type !== "builtin" && skill.skill_type !== "mcp";

  return (
    <div
      onClick={onClick}
      className={`group relative px-4 py-4 rounded-2xl border cursor-pointer transition-all hover:scale-[1.01] ${
        featured
          ? "bg-gradient-to-br from-emerald-500/8 to-transparent border-emerald-500/20 hover:border-emerald-500/40"
          : "bg-bg-secondary border-border hover:border-border-light"
      }`}
    >
      <div className="flex items-start gap-3">
        <SkillIcon type={skill.skill_type} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-text-primary truncate">{skill.name}</p>
          {skill.description && (
            <p className="text-xs text-text-muted mt-0.5 line-clamp-2 leading-relaxed">
              {skill.description}
            </p>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between mt-3 pt-3 border-t border-border/50">
        <span className={`text-[10px] px-2 py-0.5 rounded-full border ${typeBadgeClass(skill.skill_type)}`}>
          {skill.skill_type}
        </span>

        {isToggleable ? (
          <button
            onClick={(e) => { e.stopPropagation(); onToggle(); }}
            disabled={toggling}
            className={`text-[11px] px-3 py-1 rounded-lg border transition-colors ${
              skill.enabled
                ? "border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/10"
                : "border-border text-text-muted hover:bg-bg-hover"
            } ${toggling ? "opacity-50" : ""}`}
          >
            {skill.enabled ? "Enabled" : "Disabled"}
          </button>
        ) : (
          <span className="text-[11px] text-emerald-400/60 flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
            Built-in
          </span>
        )}
      </div>
    </div>
  );
}
