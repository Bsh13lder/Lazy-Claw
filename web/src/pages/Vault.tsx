import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import Modal from "../components/Modal";
import { useToast } from "../context/ToastContext";
import { ListSkeleton } from "../components/Skeleton";

export default function Vault() {
  const [keys, setKeys] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const data = await api.listVaultKeys();
      setKeys(Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load vault");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const handleAdd = async () => {
    if (!newKey.trim() || !newValue) return;
    setSaving(true);
    try {
      await api.setVaultKey(newKey.trim(), newValue);
      setShowAdd(false);
      setNewKey("");
      setNewValue("");
      toast.success("Credential saved");
      load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save credential");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (key: string) => {
    try {
      await api.deleteVaultKey(key);
      setKeys((prev) => prev.filter((k) => k !== key));
      toast.success("Credential deleted");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete credential");
    }
  };

  if (loading) return <ListSkeleton rows={3} />;

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">Credential Vault</h1>
            <p className="text-sm text-text-muted">{keys.length} encrypted credentials</p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => setShowAdd(true)} className="text-xs text-accent hover:text-accent-dim px-3 py-1.5 rounded-lg border border-accent/30 hover:bg-accent-soft transition-colors">
              + Add credential
            </button>
            <button onClick={load} className="text-xs text-text-muted hover:text-text-secondary px-3 py-1.5 rounded-lg border border-border hover:bg-bg-hover transition-colors">
              Refresh
            </button>
          </div>
        </div>

        {keys.length === 0 && (
          <div className="text-center py-12">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-text-muted mx-auto mb-3">
              <rect x="3" y="11" width="18" height="11" rx="2" />
              <path d="M7 11V7a5 5 0 0110 0v4" />
            </svg>
            <p className="text-sm text-text-muted">No credentials stored yet.</p>
            <p className="text-xs text-text-muted mt-1">API keys and secrets are encrypted with AES-256-GCM.</p>
          </div>
        )}

        {keys.length > 0 && (
          <div className="space-y-1">
            {keys.map((key) => (
              <div key={key} className="flex items-center gap-3 px-4 py-3 rounded-xl bg-bg-secondary border border-border hover:border-border-light transition-colors">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent shrink-0">
                  <rect x="3" y="11" width="18" height="11" rx="2" />
                  <path d="M7 11V7a5 5 0 0110 0v4" />
                </svg>
                <span className="text-sm text-text-primary font-mono flex-1 truncate">{key}</span>
                <span className="text-[10px] text-text-muted px-2 py-0.5 rounded-full bg-bg-tertiary">encrypted</span>
                <button onClick={() => handleDelete(key)} className="text-xs text-text-muted hover:text-error px-2 py-1 rounded-lg hover:bg-bg-hover transition-colors">
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Add credential modal */}
      <Modal open={showAdd} onClose={() => setShowAdd(false)} title="Add Credential">
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-1.5">Key name</label>
            <input
              type="text"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder="e.g. OPENAI_API_KEY"
              className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-1.5">Value</label>
            <input
              type="password"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              placeholder="sk-..."
              className="w-full px-4 py-2.5 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary font-mono placeholder:text-text-placeholder focus:outline-none focus:border-border-light"
            />
          </div>
          <p className="text-[11px] text-text-muted">Value will be encrypted with AES-256-GCM before storage.</p>
          <div className="flex justify-end gap-2 pt-2">
            <button onClick={() => setShowAdd(false)} className="px-4 py-2 text-sm text-text-muted rounded-lg hover:bg-bg-hover transition-colors">Cancel</button>
            <button onClick={handleAdd} disabled={saving || !newKey.trim() || !newValue} className="px-4 py-2 text-sm bg-accent text-bg-primary rounded-lg hover:opacity-90 disabled:opacity-30 transition-opacity">
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
