import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { Skill } from "../api";
import Modal from "../components/Modal";

export default function Skills() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [showGenerate, setShowGenerate] = useState(false);

  // Create form state
  const [cType, setCType] = useState<"instruction" | "code">("instruction");
  const [cName, setCName] = useState("");
  const [cDesc, setCDesc] = useState("");
  const [cBody, setCBody] = useState("");
  const [saving, setSaving] = useState(false);

  // Generate form state
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

  const handleCreate = async () => {
    if (!cName.trim() || !cDesc.trim() || !cBody.trim()) return;
    setSaving(true);
    try {
      const body: Parameters<typeof api.createSkill>[0] = {
        skill_type: cType,
        name: cName.trim(),
        description: cDesc.trim(),
      };
      if (cType === "instruction") body.instruction = cBody;
      else body.code = cBody;
      await api.createSkill(body);
      setShowCreate(false);
      setCName(""); setCDesc(""); setCBody("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create skill");
    } finally {
      setSaving(false);
    }
  };

  const handleGenerate = async () => {
    if (!gDesc.trim()) return;
    setGenerating(true);
    try {
      await api.generateSkill({ description: gDesc.trim(), name: gName.trim() || undefined });
      setShowGenerate(false);
      setGDesc(""); setGName("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate skill");
    } finally {
      setGenerating(false);
    }
  };

  const handleDelete = async (id: string) => {
    try { await api.deleteSkill(id); setSkills((prev) => prev.filter((s) => s.id !== id)); } catch { /* */ }
  };

  const filtered = skills.filter(
    (s) =>
      s.name.toLowerCase().includes(filter.toLowerCase()) ||
      s.description?.toLowerCase().includes(filter.toLowerCase()),
  );

  const byType: Record<string, Skill[]> = {};
  for (const s of filtered) {
    const t = s.skill_type || "other";
    if (!byType[t]) byType[t] = [];
    byType[t].push(s);
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">Skills</h1>
            <p className="text-sm text-text-muted">{skills.length} registered</p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => setShowGenerate(true)} className="text-xs text-cyan hover:text-cyan-dim px-3 py-1.5 rounded-lg border border-cyan/30 hover:bg-cyan/10 transition-colors">
              AI Generate
            </button>
            <button onClick={() => setShowCreate(true)} className="text-xs text-accent hover:text-accent-dim px-3 py-1.5 rounded-lg border border-accent/30 hover:bg-accent-soft transition-colors">
              + Create
            </button>
            <button onClick={load} className="text-xs text-text-muted hover:text-text-secondary px-3 py-1.5 rounded-lg border border-border hover:bg-bg-hover transition-colors">
              Refresh
            </button>
          </div>
        </div>

        <input
          type="text"
          placeholder="Filter skills..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full mb-6 px-4 py-2.5 rounded-xl bg-bg-secondary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light transition-colors"
        />

        {loading && (
          <div className="flex items-center gap-2 text-text-muted text-sm py-8 justify-center">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner"><path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" /></svg>
            Loading skills...
          </div>
        )}

        {error && (
          <div className="px-4 py-3 rounded-xl bg-error-soft border border-error/15 text-error text-sm mb-4">{error}</div>
        )}

        {!loading && !error && (
          <div className="space-y-6">
            {Object.entries(byType).map(([type, items]) => (
              <div key={type}>
                <h2 className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-2">
                  {type} ({items.length})
                </h2>
                <div className="space-y-1">
                  {items.map((skill) => (
                    <div key={skill.id} className="flex items-center gap-3 px-4 py-3 rounded-xl bg-bg-secondary border border-border hover:border-border-light transition-colors group">
                      <div className={`w-2 h-2 rounded-full shrink-0 ${skill.enabled ? "bg-accent" : "bg-text-muted"}`} />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium text-text-primary truncate">{skill.name}</p>
                        {skill.description && <p className="text-xs text-text-muted truncate mt-0.5">{skill.description}</p>}
                      </div>
                      <span className="text-[10px] text-text-muted px-2 py-0.5 rounded-full bg-bg-tertiary shrink-0">{skill.category ?? type}</span>
                      {skill.skill_type !== "builtin" && skill.skill_type !== "mcp" && (
                        <button onClick={() => handleDelete(skill.id)} className="text-xs text-text-muted hover:text-error px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors opacity-0 group-hover:opacity-100">
                          Delete
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Create skill modal */}
      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="Create Skill">
        <div className="space-y-3">
          <div className="flex gap-2">
            {(["instruction", "code"] as const).map((t) => (
              <button key={t} onClick={() => setCType(t)} className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${cType === t ? "border-accent bg-accent-soft text-accent" : "border-border text-text-muted hover:bg-bg-hover"}`}>
                {t.charAt(0).toUpperCase() + t.slice(1)}
              </button>
            ))}
          </div>
          <input type="text" value={cName} onChange={(e) => setCName(e.target.value)} placeholder="Skill name" className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light" />
          <input type="text" value={cDesc} onChange={(e) => setCDesc(e.target.value)} placeholder="Description" className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light" />
          <textarea value={cBody} onChange={(e) => setCBody(e.target.value)} placeholder={cType === "instruction" ? "Instruction text..." : "Python code..."} rows={6} className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light resize-y" />
          <div className="flex justify-end gap-2 pt-2">
            <button onClick={() => setShowCreate(false)} className="px-4 py-2 text-sm text-text-muted rounded-lg hover:bg-bg-hover transition-colors">Cancel</button>
            <button onClick={handleCreate} disabled={saving || !cName.trim() || !cDesc.trim() || !cBody.trim()} className="px-4 py-2 text-sm bg-accent text-bg-primary rounded-lg hover:opacity-90 disabled:opacity-30 transition-opacity">
              {saving ? "Creating..." : "Create"}
            </button>
          </div>
        </div>
      </Modal>

      {/* AI Generate modal */}
      <Modal open={showGenerate} onClose={() => setShowGenerate(false)} title="AI Generate Skill">
        <div className="space-y-3">
          <p className="text-xs text-text-muted">Describe what the skill should do and the AI will generate the code.</p>
          <input type="text" value={gName} onChange={(e) => setGName(e.target.value)} placeholder="Skill name (optional)" className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light" />
          <textarea value={gDesc} onChange={(e) => setGDesc(e.target.value)} placeholder="Describe the skill..." rows={4} className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light resize-y" />
          <div className="flex justify-end gap-2 pt-2">
            <button onClick={() => setShowGenerate(false)} className="px-4 py-2 text-sm text-text-muted rounded-lg hover:bg-bg-hover transition-colors">Cancel</button>
            <button onClick={handleGenerate} disabled={generating || !gDesc.trim()} className="px-4 py-2 text-sm bg-cyan text-bg-primary rounded-lg hover:opacity-90 disabled:opacity-30 transition-opacity">
              {generating ? "Generating..." : "Generate"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
