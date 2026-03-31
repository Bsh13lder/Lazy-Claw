import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import * as api from "../api";
import Modal from "../components/Modal";

/* ------------------------------------------------------------------ */
/*  Key type classification                                           */
/* ------------------------------------------------------------------ */

type KeyType = "api" | "token" | "secret" | "default";

function classifyKey(name: string): KeyType {
  const upper = name.toUpperCase();
  if (upper.includes("API") || upper.includes("KEY")) return "api";
  if (upper.includes("TOKEN")) return "token";
  if (upper.includes("SECRET") || upper.includes("PASSWORD")) return "secret";
  return "default";
}

/* ------------------------------------------------------------------ */
/*  Icons                                                             */
/* ------------------------------------------------------------------ */

function KeyIcon({ className }: { className?: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
    </svg>
  );
}

function ShieldIcon({ className }: { className?: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

function LockIcon({ size = 18, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <rect x="3" y="11" width="18" height="11" rx="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}

function ClipboardIcon({ className }: { className?: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <rect x="9" y="2" width="6" height="4" rx="1" />
      <path d="M9 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2h-3" />
    </svg>
  );
}

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function TrashIcon({ className }: { className?: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  );
}

function SpinnerIcon({ className }: { className?: string }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className={className}>
      <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/*  Key type icon resolver                                            */
/* ------------------------------------------------------------------ */

function keyTypeIcon(keyType: KeyType): ReactNode {
  switch (keyType) {
    case "api":
      return <KeyIcon className="text-cyan" />;
    case "token":
      return <ShieldIcon className="text-amber" />;
    case "secret":
      return <LockIcon className="text-accent" />;
    default:
      return <LockIcon className="text-text-muted" />;
  }
}

function keyTypeBgClass(keyType: KeyType): string {
  switch (keyType) {
    case "api":
      return "bg-cyan-soft";
    case "token":
      return "bg-amber-soft";
    case "secret":
      return "bg-accent-soft";
    default:
      return "bg-bg-hover";
  }
}

/* ------------------------------------------------------------------ */
/*  Credential card                                                   */
/* ------------------------------------------------------------------ */

function CredentialCard({
  name,
  onDelete,
}: {
  name: string;
  onDelete: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const keyType = classifyKey(name);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(name);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  return (
    <div className="group card-hover flex items-center gap-3 px-4 py-3 rounded-xl bg-bg-secondary border border-border transition-all duration-200">
      {/* Icon */}
      <div className={`shrink-0 w-8 h-8 rounded-lg flex items-center justify-center ${keyTypeBgClass(keyType)}`}>
        {keyTypeIcon(keyType)}
      </div>

      {/* Name */}
      <span className="text-sm text-text-primary font-mono flex-1 truncate">
        {name}
      </span>

      {/* Encrypted badge */}
      <span className="text-[10px] text-text-muted px-2 py-0.5 rounded-full bg-bg-hover border border-border shrink-0">
        Encrypted
      </span>

      {/* Copy button */}
      <div className="relative">
        <button
          onClick={handleCopy}
          className="p-1.5 rounded-lg text-text-muted hover:text-text-secondary hover:bg-bg-hover transition-colors"
          aria-label="Copy key name"
        >
          {copied ? <CheckIcon className="text-accent" /> : <ClipboardIcon />}
        </button>
        {copied && (
          <span className="absolute -top-7 left-1/2 -translate-x-1/2 text-[10px] text-accent bg-bg-secondary border border-border px-2 py-0.5 rounded-md whitespace-nowrap animate-fade-in">
            Copied!
          </span>
        )}
      </div>

      {/* Delete button — visible on hover */}
      <button
        onClick={onDelete}
        className="p-1.5 rounded-lg text-text-muted opacity-0 group-hover:opacity-100 hover:text-error hover:bg-bg-hover transition-all duration-200"
        aria-label="Delete credential"
      >
        <TrashIcon />
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main page                                                         */
/* ------------------------------------------------------------------ */

export default function Vault() {
  const [keys, setKeys] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listVaultKeys();
      setKeys(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load vault");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleAdd = async () => {
    if (!newKey.trim() || !newValue) return;
    setSaving(true);
    try {
      await api.setVaultKey(newKey.trim(), newValue);
      setShowAdd(false);
      setNewKey("");
      setNewValue("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save credential");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (key: string) => {
    try {
      await api.deleteVaultKey(key);
      setKeys((prev) => prev.filter((k) => k !== key));
    } catch {
      /* silent */
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8 animate-fade-in">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">Credential Vault</h1>
            <p className="text-sm text-text-muted">{keys.length} encrypted credentials</p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setShowAdd(true)}
              className="text-xs text-accent hover:text-accent-dim px-3 py-1.5 rounded-lg border border-accent/30 hover:bg-accent-soft transition-colors"
            >
              + Add credential
            </button>
            <button
              onClick={load}
              className="text-xs text-text-muted hover:text-text-secondary px-3 py-1.5 rounded-lg border border-border hover:bg-bg-hover transition-colors"
            >
              Refresh
            </button>
          </div>
        </div>

        {/* Security info bar */}
        <div className="flex items-center justify-between px-4 py-3 rounded-xl bg-bg-secondary border border-border mb-6">
          <div className="flex items-center gap-2 text-text-muted">
            <LockIcon size={14} className="text-accent shrink-0" />
            <span className="text-xs">All credentials encrypted with AES-256-GCM</span>
          </div>
          <div className="flex items-center gap-1.5 text-xs text-text-muted px-2.5 py-1 rounded-full bg-bg-hover border border-border">
            <ShieldIcon className="w-3 h-3" />
            <span>{keys.length}</span>
          </div>
        </div>

        {/* Loading */}
        {loading && (
          <div className="flex items-center gap-2 text-text-muted text-sm py-8 justify-center">
            <SpinnerIcon className="spinner" />
            Loading vault...
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="px-4 py-3 rounded-xl bg-error-soft border border-error/15 text-error text-sm mb-4">
            {error}
          </div>
        )}

        {/* Empty state */}
        {!loading && !error && keys.length === 0 && (
          <div className="text-center py-16">
            <div className="w-16 h-16 rounded-2xl bg-bg-secondary border border-border flex items-center justify-center mx-auto mb-4">
              <LockIcon size={28} className="text-text-muted" />
            </div>
            <h2 className="text-base font-semibold text-text-primary mb-2">Vault is empty</h2>
            <p className="text-sm text-text-muted max-w-sm mx-auto mb-6">
              Store API keys and secrets — encrypted with AES-256-GCM before storage
            </p>
            <button
              onClick={() => setShowAdd(true)}
              className="px-5 py-2.5 text-sm bg-accent text-bg-primary rounded-lg hover:opacity-90 transition-opacity"
            >
              Add first credential
            </button>
          </div>
        )}

        {/* Credential list */}
        {!loading && !error && keys.length > 0 && (
          <div className="space-y-2">
            {keys.map((key) => (
              <CredentialCard
                key={key}
                name={key}
                onDelete={() => handleDelete(key)}
              />
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
            <button
              onClick={() => setShowAdd(false)}
              className="px-4 py-2 text-sm text-text-muted rounded-lg hover:bg-bg-hover transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleAdd}
              disabled={saving || !newKey.trim() || !newValue}
              className="px-4 py-2 text-sm bg-accent text-bg-primary rounded-lg hover:opacity-90 disabled:opacity-30 transition-opacity"
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
