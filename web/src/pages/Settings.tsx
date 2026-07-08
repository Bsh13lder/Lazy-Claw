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
  ModelsData,
  GeneralSettings,
  AboutInfo,
  ClaudeAuthStatus,
} from "../api";
import { useToast } from "../context/ToastContext";
import Modal from "../components/Modal";
import MobileAppTab from "./settings/MobileAppTab";

// ── Tab types ──────────────────────────────────────────────────────────────

type TabId = "models" | "search" | "teams" | "permissions" | "about" | "power" | "mobile";

interface TabDef {
  readonly id: TabId;
  readonly label: string;
  readonly icon: React.ReactNode;
}

// ── SVG Icons ──────────────────────────────────────────────────────────────

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

function SearchIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function InfoIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="16" x2="12" y2="12" />
      <line x1="12" y1="8" x2="12.01" y2="8" />
    </svg>
  );
}

function SlidersIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="4" y1="21" x2="4" y2="14" />
      <line x1="4" y1="10" x2="4" y2="3" />
      <line x1="12" y1="21" x2="12" y2="12" />
      <line x1="12" y1="8" x2="12" y2="3" />
      <line x1="20" y1="21" x2="20" y2="16" />
      <line x1="20" y1="12" x2="20" y2="3" />
      <line x1="1" y1="14" x2="7" y2="14" />
      <line x1="9" y1="8" x2="15" y2="8" />
      <line x1="17" y1="16" x2="23" y2="16" />
    </svg>
  );
}

// ── Tab definitions ────────────────────────────────────────────────────────

function PhoneIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="5" y="2" width="14" height="20" rx="2" ry="2" />
      <line x1="12" y1="18" x2="12.01" y2="18" />
    </svg>
  );
}

function PowerIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18.36 6.64A9 9 0 1 1 5.63 5.63" />
      <line x1="12" y1="2" x2="12" y2="12" />
    </svg>
  );
}

const TABS: readonly TabDef[] = [
  { id: "models", label: "Models", icon: <SlidersIcon /> },
  { id: "search", label: "Search", icon: <SearchIcon /> },
  { id: "teams", label: "Teams", icon: <UsersIcon /> },
  { id: "permissions", label: "Permissions", icon: <ShieldIcon /> },
  { id: "power", label: "Power", icon: <PowerIcon /> },
  { id: "mobile", label: "Mobile App", icon: <PhoneIcon /> },
  { id: "about", label: "About", icon: <InfoIcon /> },
] as const;

// ── Search provider definitions ────────────────────────────────────────────

interface SearchProviderDef {
  readonly id: GeneralSettings["search_provider"];
  readonly label: string;
  readonly blurb: string;
  readonly needsKey: string | null; // env var name, or null (no key)
}

const SEARCH_PROVIDERS: readonly SearchProviderDef[] = [
  { id: "auto", label: "Auto", blurb: "Brave → mcp-scraper → DuckDuckGo. Recommended.", needsKey: null },
  { id: "brave", label: "Brave Search", blurb: "Curated index, 2,000 free queries/month, clean snippets", needsKey: "BRAVE_KEY" },
  { id: "scraper", label: "mcp-scraper", blurb: "Live Google scrape via crawl4ai, free, no key", needsKey: null },
  { id: "duckduckgo", label: "DuckDuckGo", blurb: "No API key, lower quality, rate-limited", needsKey: null },
] as const;

// ── Mode card definitions ──────────────────────────────────────────────────

interface ModeDef {
  readonly mode: string;
  readonly name: string;
  readonly description: string;
}

const MODE_CARDS: readonly ModeDef[] = [
  { mode: "hybrid", name: "HYBRID", description: "Paid brain + free local worker" },
  { mode: "full", name: "FULL", description: "All premium models" },
  { mode: "claude", name: "CLAUDE", description: "Claude CLI ($0 subscription)" },
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

// ── Model Assignment ───────────────────────────────────────────────────────

const ROLES = ["brain", "worker", "fallback"] as const;

const ROLE_INFO: Record<string, { label: string; description: string }> = {
  brain: { label: "Brain", description: "Main agent & team lead" },
  worker: { label: "Worker", description: "Specialists & background tasks" },
  fallback: { label: "Fallback", description: "When primary model fails" },
};

// CLAUDE-mode model + transport picker. Both transports ("sdk" via the
// official claude-agent-sdk and "cli" via raw `claude -p`) share the same
// 3-role model dropdowns — they consume the same subscription, only the
// wire protocol differs. Writes to claude_transport + claude_*_model.
const CLAUDE_CLI_MODELS: readonly { value: string; label: string }[] = [
  { value: "claude-sonnet-5[1m]", label: "Sonnet 5 — 1M context (latest)" },
  { value: "claude-sonnet-5", label: "Sonnet 5 — 200K context" },
  { value: "claude-sonnet-4-6", label: "Sonnet 4.6 — 200K context" },
  { value: "claude-sonnet-4-6[1m]", label: "Sonnet 4.6 — 1M context (beta)" },
  { value: "claude-opus-4-6[1m]", label: "Opus 4.6 — 1M context" },
  { value: "claude-opus-4-7[1m]", label: "Opus 4.7 — 1M context" },
  { value: "claude-opus-4-8[1m]", label: "Opus 4.8 — 1M context (latest)" },
  { value: "claude-haiku-4-5-20251001", label: "Haiku 4.5 — fastest" },
];

const CLAUDE_TRANSPORTS: readonly {
  value: "sdk" | "cli";
  label: string;
  blurb: string;
}[] = [
  {
    value: "sdk",
    label: "SDK",
    blurb: "Native tool_use protocol via the official Claude Agent SDK. Faster, recommended.",
  },
  {
    value: "cli",
    label: "CLI (legacy)",
    blurb: "Raw `claude -p` subprocess with JSON-schema tool injection. Kept as fallback.",
  },
];

const CLAUDE_ROLES = [
  { key: "claude_brain_model" as const, label: "Brain", sub: "Main agent & team lead" },
  { key: "claude_worker_model" as const, label: "Worker", sub: "Specialists & background tasks" },
  { key: "claude_fallback_model" as const, label: "Fallback", sub: "When primary errors" },
];

function ClaudeModePanel({
  eco,
  onSettingsUpdate,
}: {
  readonly eco: EcoSettings;
  readonly onSettingsUpdate: (updates: Partial<EcoSettings>) => Promise<void>;
}) {
  const toast = useToast();
  const transport = eco.claude_transport ?? "sdk";

  const handleTransport = async (value: "sdk" | "cli") => {
    if (value === transport) return;
    try {
      await onSettingsUpdate({ claude_transport: value });
      toast.success(
        value === "sdk"
          ? "Switched to SDK — native tool_use protocol"
          : "Switched to CLI — legacy `claude -p`",
      );
    } catch {
      toast.error("Failed to switch transport");
    }
  };

  // Maps each per-mode CLAUDE role key to its generic "last-picked" mirror so
  // Telegram / CLI fall back to the same choice (matches ModelAssignment).
  const CLAUDE_GENERIC_MIRROR = {
    claude_brain_model: "brain_model",
    claude_worker_model: "worker_model",
    claude_fallback_model: "fallback_model",
  } as const;

  const handleModel = async (key: typeof CLAUDE_ROLES[number]["key"], value: string) => {
    try {
      // Dual-write: per-mode override keeps CLAUDE-mode isolation, generic
      // mirror ensures Telegram / CLI fall back to the same choice.
      await onSettingsUpdate({
        [key]: value,
        [CLAUDE_GENERIC_MIRROR[key]]: value,
      });
      toast.success("Model updated");
    } catch {
      toast.error("Failed to update model");
    }
  };

  // ── Subscription login ($0 CLAUDE via `claude setup-token`) ────────────────
  const [auth, setAuth] = useState<ClaudeAuthStatus | null>(null);
  const [authLoaded, setAuthLoaded] = useState(false);
  const [token, setToken] = useState("");
  const [verifying, setVerifying] = useState(false);

  // One-click "Login with Claude" OAuth flow.
  const [loginId, setLoginId] = useState<string | null>(null);
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginCode, setLoginCode] = useState("");

  const loadAuth = useCallback(async () => {
    try {
      setAuth(await api.getClaudeAuth());
    } catch {
      // Degrade gracefully — show a neutral "unknown" state, never crash.
      setAuth(null);
    } finally {
      setAuthLoaded(true);
    }
  }, []);

  useEffect(() => {
    void loadAuth();
  }, [loadAuth]);

  const handleSaveToken = async () => {
    if (!token.trim()) {
      toast.error("Paste a token first — run `claude setup-token` to get one.");
      return;
    }
    setVerifying(true);
    try {
      const result = await api.setClaudeToken(token.trim());
      toast.success(result.detail);
      setToken("");
      await loadAuth();
    } catch (err) {
      toast.error((err as Error).message ?? "Failed to save token");
    } finally {
      setVerifying(false);
    }
  };

  const handleStartLogin = async () => {
    setLoginBusy(true);
    try {
      const resp = await api.startClaudeLogin();
      if (resp.success && resp.data) {
        window.open(resp.data.auth_url, "_blank", "noopener");
        setAuthUrl(resp.data.auth_url);
        setLoginId(resp.data.login_id);
        toast.success("Opened Claude sign-in — authorize, then paste the code below.");
      } else {
        toast.error(resp.error ?? "Couldn't start login");
      }
    } catch (err) {
      toast.error((err as Error).message ?? "Couldn't start login");
    } finally {
      setLoginBusy(false);
    }
  };

  const handleCompleteLogin = async () => {
    if (!loginId) return;
    const code = loginCode.trim();
    if (!code) {
      toast.error("Paste the code from Claude first.");
      return;
    }
    setLoginBusy(true);
    try {
      const resp = await api.completeClaudeLogin(loginId, code);
      if (resp.success) {
        toast.success(resp.data?.detail ?? "Logged in");
        setLoginId(null);
        setAuthUrl(null);
        setLoginCode("");
        await loadAuth();
      } else {
        toast.error(resp.error ?? "Login failed");
      }
    } catch (err) {
      toast.error((err as Error).message ?? "Login failed");
    } finally {
      setLoginBusy(false);
    }
  };

  return (
    <section className="bg-bg-secondary border border-border rounded-xl p-5">
      <SectionHeading
        title="Claude mode"
        subtitle="$0 via your Claude subscription. Transport picks how we talk to claude; models picks which tier per role."
      />

      {/* Transport radio */}
      <div className="mb-5">
        <p className="text-xs font-medium text-text-secondary mb-2">Transport</p>
        <div className="grid grid-cols-2 gap-2">
          {CLAUDE_TRANSPORTS.map((t) => {
            const isActive = transport === t.value;
            return (
              <button
                key={t.value}
                type="button"
                onClick={() => handleTransport(t.value)}
                className={`relative flex flex-col items-start gap-1 p-3 rounded-lg border text-left transition-colors ${
                  isActive
                    ? "border-accent bg-accent-soft"
                    : "border-border bg-bg-tertiary hover:border-border-light"
                }`}
              >
                <span
                  className={`text-sm font-semibold ${
                    isActive ? "text-accent" : "text-text-primary"
                  }`}
                >
                  {t.label}
                </span>
                <span className="text-[11px] text-text-muted leading-snug">{t.blurb}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* 3-role model dropdowns */}
      <div className="space-y-3">
        {CLAUDE_ROLES.map((role) => {
          const current = (eco[role.key] as string | null | undefined) ?? "claude-sonnet-4-6";
          return (
            <div key={role.key} className="flex items-center gap-4">
              <div className="w-24 shrink-0">
                <p className="text-sm font-medium text-text-primary">{role.label}</p>
                <p className="text-[10px] text-text-muted">{role.sub}</p>
              </div>
              <select
                value={current}
                onChange={(e) => handleModel(role.key, e.target.value)}
                className="flex-1 px-3 py-2 rounded-lg bg-bg-tertiary border border-border text-sm text-text-primary focus:outline-none focus:border-border-light appearance-none cursor-pointer"
              >
                {CLAUDE_CLI_MODELS.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>
          );
        })}
      </div>

      {/* Subscription login — $0 CLAUDE via `claude setup-token` */}
      <div className="mt-5 pt-5 border-t border-border">
        <p className="text-xs font-medium text-text-secondary mb-2">Subscription login</p>

        {/* Status row */}
        <div className="flex items-center gap-2 mb-3">
          <span
            className={`w-2.5 h-2.5 rounded-full shrink-0 ${
              !authLoaded
                ? "bg-text-muted opacity-40"
                : auth?.authenticated
                  ? "bg-accent"
                  : "bg-error"
            }`}
          />
          <span
            className={`text-xs ${
              !authLoaded
                ? "text-text-muted"
                : auth?.authenticated
                  ? "text-accent"
                  : "text-error"
            }`}
          >
            {!authLoaded
              ? "Checking subscription…"
              : auth?.detail ?? "Subscription status unknown"}
          </span>
          {auth?.expires_at && (
            <span className="text-[10px] text-text-muted shrink-0">
              expires {auth.expires_at}
            </span>
          )}
        </div>

        {/* One-click OAuth login */}
        <button
          onClick={handleStartLogin}
          disabled={loginBusy}
          className="w-full px-3 py-2 text-sm font-semibold text-accent border border-accent bg-accent-soft rounded-lg hover:brightness-110 disabled:opacity-40 transition-colors"
        >
          {loginBusy && !loginId ? "Opening sign-in…" : "Login with Claude"}
        </button>
        <p className="text-[10px] text-text-muted mt-2 leading-snug">
          Opens Claude sign-in in a new tab. After you authorize, Anthropic shows a short code —
          paste it back here. One-time; the token lasts ~1 year.
        </p>

        {authUrl && (
          <p className="text-[10px] text-text-muted mt-1">
            Popup blocked?{" "}
            <a
              href={authUrl}
              target="_blank"
              rel="noopener"
              className="text-accent hover:underline"
            >
              open sign-in again
            </a>
          </p>
        )}

        {loginId && (
          <div className="flex gap-1.5 items-center mt-3">
            <input
              type="text"
              placeholder="Paste the code from Claude here"
              value={loginCode}
              onChange={(e) => setLoginCode(e.target.value)}
              className="flex-1 px-2 py-1.5 text-xs rounded bg-bg-tertiary border border-border text-text-primary focus:outline-none focus:border-border-light"
            />
            <button
              onClick={handleCompleteLogin}
              disabled={loginBusy}
              className="px-2.5 py-1.5 text-[10px] text-accent border border-accent/30 rounded hover:bg-accent-soft disabled:opacity-40 shrink-0"
            >
              {loginBusy ? "Finishing…" : "Complete login"}
            </button>
          </div>
        )}

        {/* Manual token fallback */}
        <p className="text-[10px] font-medium text-text-muted mt-4 mb-1.5">
          Or paste a token manually
        </p>
        <div className="flex gap-1.5 items-center">
          <input
            type="password"
            placeholder="sk-ant-oat…"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            className="flex-1 px-2 py-1.5 text-xs rounded bg-bg-tertiary border border-border text-text-primary focus:outline-none focus:border-border-light"
          />
          <button
            onClick={handleSaveToken}
            disabled={verifying}
            className="px-2.5 py-1.5 text-[10px] text-accent border border-accent/30 rounded hover:bg-accent-soft disabled:opacity-40 shrink-0"
          >
            {verifying ? "Verifying…" : "Save & verify"}
          </button>
        </div>
        <p className="text-[10px] text-text-muted mt-2 leading-snug">
          Run <code className="text-text-secondary">claude setup-token</code> on a machine logged
          into your Claude subscription, then paste the token (starts with sk-ant-oat…).
          Long-lived (~1 year) — survives restarts.
        </p>
      </div>

      <p className="text-[10px] text-text-muted mt-3">
        Only affects CLAUDE mode. HYBRID and FULL API model settings are independent.
      </p>
    </section>
  );
}

function ModelAssignment({
  eco,
  modelsData,
  onSettingsUpdate,
}: {
  readonly eco: EcoSettings;
  readonly modelsData: ModelsData;
  readonly onSettingsUpdate: (updates: Partial<EcoSettings>) => Promise<void>;
}) {
  const toast = useToast();
  const mode = eco.mode;
  const defaults = modelsData.mode_defaults[mode] ?? {};

  const modeKey = (role: string) => `${mode}_${role}_model`;

  const handleModelChange = async (role: string, value: string) => {
    const key = modeKey(role);
    const genericKey = `${role}_model`;
    const newVal = value === "" ? null : value;
    try {
      // Dual-write: per-mode override keeps advanced mode isolation,
      // generic mirror ensures Telegram / CLI fall back to the same
      // choice when the active mode has no per-mode key set.
      await onSettingsUpdate({
        [key]: newVal,
        [genericKey]: newVal,
      });
      toast.success(`${ROLE_INFO[role]?.label ?? role} model updated`);
    } catch {
      toast.error("Failed to update model");
    }
  };

  const currentModel = (role: string): string => {
    return (eco[modeKey(role)] as string | null | undefined) ?? "";
  };

  return (
    <section className="bg-bg-secondary border border-border rounded-xl p-5">
      <SectionHeading
        title="Model Assignment"
        subtitle={`Configure which models each role uses in ${mode.toUpperCase()} mode`}
      />
      <div className="space-y-3">
        {ROLES.map((role) => {
          const info = ROLE_INFO[role];
          const active = currentModel(role);
          const defaultModel = defaults[role] ?? "—";
          return (
            <div key={role} className="flex items-center gap-4">
              <div className="w-24 shrink-0">
                <p className="text-sm font-medium text-text-primary">{info.label}</p>
                <p className="text-[10px] text-text-muted">{info.description}</p>
              </div>
              <select
                value={active}
                onChange={(e) => handleModelChange(role, e.target.value)}
                className="flex-1 px-3 py-2 rounded-lg bg-bg-tertiary border border-border text-sm text-text-primary focus:outline-none focus:border-border-light appearance-none cursor-pointer"
              >
                <option value="">Default ({defaultModel})</option>
                {modelsData.models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.display_name} — {m.provider}{m.optimized ? " *" : ""}{m.is_local ? " (local)" : ""}
                  </option>
                ))}
              </select>
            </div>
          );
        })}
      </div>
      <p className="text-[10px] text-text-muted mt-3">* = optimized for this platform. Each mode has independent model settings.</p>
    </section>
  );
}

// ── Providers List ─────────────────────────────────────────────────────────

function ProvidersList({ providers }: { readonly providers: readonly EcoProvider[] }) {
  const toast = useToast();
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [keyValue, setKeyValue] = useState("");
  const [saving, setSaving] = useState(false);

  const handleSaveKey = async (envKey: string) => {
    if (!keyValue.trim()) { toast.error("API key cannot be empty"); return; }
    setSaving(true);
    try {
      await api.setVaultKey(envKey, keyValue.trim());
      toast.success("API key saved to vault");
      setEditingKey(null);
      setKeyValue("");
    } catch { toast.error("Failed to save API key"); }
    finally { setSaving(false); }
  };

  const handleDeleteKey = async (envKey: string) => {
    try {
      await api.deleteVaultKey(envKey);
      toast.success("API key removed");
    } catch { toast.error("Failed to remove API key"); }
  };

  // Sort: paid first, then by name
  const sorted = [...providers].sort((a, b) => {
    if (a.is_paid && !b.is_paid) return -1;
    if (!a.is_paid && b.is_paid) return 1;
    return String(a.name).localeCompare(String(b.name));
  });

  return (
    <section className="bg-bg-secondary border border-border rounded-xl p-5">
      <SectionHeading title="Providers" subtitle="Manage AI provider API keys" />
      <div className="space-y-2">
        {sorted.map((p) => {
          const envKey = String(p.env_key || "");
          const isEditing = editingKey === p.name;
          return (
            <div key={p.name} className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-bg-hover">
              <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${p.configured ? "bg-accent" : "bg-text-muted opacity-40"}`} />
              <div className="flex-1 min-w-0">
                <span className="text-sm text-text-primary truncate block">
                  {String(p.display_name || p.name)}
                </span>
                {Boolean(p.is_paid) && <span className="text-[9px] text-amber uppercase tracking-wider">paid</span>}
              </div>
              {isEditing ? (
                <div className="flex gap-1.5 items-center">
                  <input
                    type="password"
                    placeholder="sk-..."
                    value={keyValue}
                    onChange={(e) => setKeyValue(e.target.value)}
                    className="w-40 px-2 py-1 text-xs rounded bg-bg-tertiary border border-border text-text-primary focus:outline-none focus:border-border-light"
                    autoFocus
                  />
                  <button
                    onClick={() => handleSaveKey(envKey)}
                    disabled={saving}
                    className="px-2 py-1 text-[10px] text-accent border border-accent/30 rounded hover:bg-accent-soft disabled:opacity-40"
                  >
                    {saving ? "..." : "Save"}
                  </button>
                  <button
                    onClick={() => { setEditingKey(null); setKeyValue(""); }}
                    className="px-2 py-1 text-[10px] text-text-muted border border-border rounded hover:bg-bg-hover"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="flex gap-1.5 items-center">
                  <span className={`text-[10px] shrink-0 ${p.configured ? "text-accent" : "text-text-muted"}`}>
                    {p.configured ? "active" : "not configured"}
                  </span>
                  {envKey && (
                    <button
                      onClick={() => { setEditingKey(String(p.name)); setKeyValue(""); }}
                      className="px-2 py-1 text-[10px] text-text-muted border border-border rounded hover:bg-bg-hover hover:text-text-primary"
                    >
                      {p.configured ? "Update" : "Add key"}
                    </button>
                  )}
                  {p.configured && envKey && (
                    <button
                      onClick={() => handleDeleteKey(envKey)}
                      className="px-2 py-1 text-[10px] text-red-400 border border-red-400/30 rounded hover:bg-red-400/10"
                    >
                      Remove
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ── ECO Tab ────────────────────────────────────────────────────────────────

function EcoTab({
  eco,
  usage,
  providers,
  rateLimits,
  modelsData,
  onModeChange,
  onSettingsUpdate,
}: {
  readonly eco: EcoSettings | null;
  readonly usage: EcoUsage | null;
  readonly providers: readonly EcoProvider[];
  readonly rateLimits: RateLimits | null;
  readonly modelsData: ModelsData | null;
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

      {/* Model Assignment — CLAUDE mode gets its own panel with the
          transport radio (SDK / CLI) + brain/worker/fallback model
          dropdowns. HYBRID/FULL keep the existing generic 3-row API
          matrix. No state crosses between modes. */}
      {eco && eco.mode === "claude" && (
        <ClaudeModePanel eco={eco} onSettingsUpdate={onSettingsUpdate} />
      )}
      {eco && modelsData && eco.mode !== "claude" && (
        <ModelAssignment
          eco={eco}
          modelsData={modelsData}
          onSettingsUpdate={onSettingsUpdate}
        />
      )}

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

      {/* Free provider controls */}
      {eco && providers.length > 0 && (
        <section className="bg-bg-secondary border border-border rounded-xl p-5">
          <SectionHeading title="Free Providers" subtitle="Select which free providers to use for worker tasks" />
          <div className="space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {providers.filter((p) => !p.is_paid).map((p) => {
                const selected = eco.free_providers?.includes(String(p.name)) ?? false;
                return (
                  <label key={p.name} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-bg-hover cursor-pointer hover:bg-bg-tertiary transition-colors">
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={async () => {
                        const current = eco.free_providers ?? [];
                        const next = selected
                          ? current.filter((n) => n !== String(p.name))
                          : [...current, String(p.name)];
                        try {
                          await onSettingsUpdate({ free_providers: next } as Partial<EcoSettings>);
                        } catch { /* ignore */ }
                      }}
                      className="rounded"
                    />
                    <span className="text-sm text-text-primary">{String(p.display_name || p.name)}</span>
                    <span className={`ml-auto w-2 h-2 rounded-full shrink-0 ${p.configured ? "bg-accent" : "bg-text-muted opacity-40"}`} />
                  </label>
                );
              })}
            </div>
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1.5">Preferred free model</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  defaultValue={eco.preferred_free_model ?? ""}
                  onBlur={async (e) => {
                    const val = e.target.value.trim() || null;
                    try {
                      await onSettingsUpdate({ preferred_free_model: val } as Partial<EcoSettings>);
                      toast.success("Preferred model updated");
                    } catch { toast.error("Failed to update"); }
                  }}
                  placeholder="e.g. gemma-3-4b"
                  className="flex-1 px-3 py-2 rounded-lg bg-bg-tertiary border border-border text-sm text-text-primary font-mono focus:outline-none focus:border-border-light"
                />
              </div>
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
        <ProvidersList providers={providers} />
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

// ── Team Sessions ─────────────────────────────────────────────────────────

function TeamSessionsSection() {
  const [sessions, setSessions] = useState<api.TeamSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown>[]>([]);
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    api.listTeamSessions()
      .then((data) => setSessions(Array.isArray(data) ? data : []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleExpand = async (sessionId: string) => {
    if (expandedId === sessionId) { setExpandedId(null); return; }
    setExpandedId(sessionId);
    setLoadingDetail(true);
    try {
      const data = await api.getTeamSession(sessionId);
      setDetail(Array.isArray(data) ? data : []);
    } catch { setDetail([]); }
    finally { setLoadingDetail(false); }
  };

  if (loading) return null;
  if (sessions.length === 0) return null;

  return (
    <section className="bg-bg-secondary border border-border rounded-xl p-5">
      <SectionHeading title="Recent Sessions" subtitle="Specialist delegation history" />
      <div className="space-y-1">
        {sessions.slice(0, 10).map((s) => {
          const isExpanded = expandedId === s.session_id;
          return (
            <div key={s.session_id}>
              <div
                className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-bg-hover cursor-pointer transition-colors"
                onClick={() => handleExpand(s.session_id)}
              >
                <span className={`w-2 h-2 rounded-full shrink-0 ${s.status === "done" ? "bg-green-400" : s.status === "running" ? "bg-accent live-pulse" : "bg-text-muted"}`} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-text-primary truncate">{s.specialist}</p>
                  <p className="text-[10px] text-text-muted truncate">{s.task}</p>
                </div>
                <span className="text-[10px] text-text-muted shrink-0">
                  {new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(s.created_at))}
                </span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                  className={`text-text-muted transition-transform ${isExpanded ? "rotate-180" : ""}`}>
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </div>
              {isExpanded && (
                <div className="ml-5 pl-3 border-l border-border mb-2">
                  {loadingDetail ? (
                    <p className="text-[10px] text-text-muted py-2">Loading...</p>
                  ) : detail.length === 0 ? (
                    <p className="text-[10px] text-text-muted py-2">No conversation entries</p>
                  ) : (
                    <div className="space-y-1 py-1">
                      {detail.map((entry, i) => (
                        <div key={i} className="text-xs text-text-secondary px-2 py-1 rounded bg-bg-hover">
                          <span className="text-text-muted font-medium">{String(entry.role ?? "system")}:</span>{" "}
                          <span className="truncate">{String(entry.content ?? "").slice(0, 200)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ── Teams Tab ──────────────────────────────────────────────────────────────

function TeamsTab({
  team,
  specialists,
  modelsData,
  onTeamUpdate,
  onSpecialistDelete,
  onSpecialistCreate,
}: {
  readonly team: TeamSettings | null;
  readonly specialists: readonly Specialist[];
  readonly modelsData: ModelsData | null;
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
  const [specTimeout, setSpecTimeout] = useState(String(team?.specialist_timeout ?? 120));

  useEffect(() => {
    if (team) {
      setMaxParallel(String(team.max_parallel));
      setSpecTimeout(String(team.specialist_timeout));
    }
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

  const handleSpecTimeoutSave = async () => {
    const val = parseInt(specTimeout, 10);
    if (Number.isNaN(val) || val < 10 || val > 600) {
      toast.error("Must be between 10 and 600 seconds");
      return;
    }
    await handleTeamChange({ specialist_timeout: val });
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
              <p className="text-xs text-text-muted">Audit the assistant's reply on HIGH/MAX effort turns and rewrite when it fails the audit (max 3 loops)</p>
            </div>
            <Toggle
              on={team?.critic_mode ?? false}
              onChange={() => handleTeamChange({ critic_mode: !(team?.critic_mode) })}
              disabled={saving === "critic_mode"}
            />
          </div>

          {/* Critic model — picks which LLM does the audit + rewrites */}
          <div className="flex items-center justify-between py-2">
            <div className="pr-4">
              <p className="text-sm text-text-primary">Critic Model</p>
              <p className="text-xs text-text-muted">Which model audits the reply (defaults to the worker model when none picked)</p>
            </div>
            <select
              value={team?.critic_model ?? ""}
              onChange={(e) => handleTeamChange({ critic_model: e.target.value === "" ? null : e.target.value })}
              disabled={saving === "critic_model" || !team?.critic_mode || !modelsData}
              className="w-72 px-3 py-2 rounded-lg bg-bg-tertiary border border-border text-sm text-text-primary focus:outline-none focus:border-border-light appearance-none cursor-pointer disabled:opacity-50"
            >
              <option value="">Default (worker model)</option>
              {modelsData?.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name} — {m.provider}{m.optimized ? " *" : ""}{m.is_local ? " (local)" : ""}
                </option>
              ))}
            </select>
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

          <div className="border-t border-border" />

          {/* Specialist timeout */}
          <div className="flex items-center justify-between py-2">
            <div>
              <p className="text-sm text-text-primary">Specialist Timeout</p>
              <p className="text-xs text-text-muted">Max seconds per specialist task (10-600)</p>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min="10"
                max="600"
                value={specTimeout}
                onChange={(e) => setSpecTimeout(e.target.value)}
                className="w-20 px-2 py-1 text-sm text-center rounded-lg bg-bg-hover border border-border text-text-primary focus:outline-none focus:border-border-light"
              />
              <span className="text-xs text-text-muted">s</span>
              <button
                onClick={handleSpecTimeoutSave}
                disabled={saving === "specialist_timeout"}
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
                    <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${s.is_builtin ? "bg-cyan-soft text-cyan" : "bg-accent-soft text-accent"}`}>
                      {s.is_builtin ? "builtin" : "custom"}
                    </span>
                  </div>
                  {s.display_name && s.display_name !== s.name && (
                    <p className="text-xs text-text-secondary">{s.display_name}</p>
                  )}
                  {s.preferred_model && (
                    <p className="text-[10px] text-text-muted">Model: {s.preferred_model}</p>
                  )}
                </div>
                {!s.is_builtin && (
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

      {/* Team Sessions */}
      <TeamSessionsSection />

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

function PendingApprovals() {
  const [approvals, setApprovals] = useState<Record<string, unknown>[]>([]);
  const toast = useToast();

  useEffect(() => {
    api.listPendingApprovals().then((data) => setApprovals(Array.isArray(data) ? data : [])).catch(() => {});
  }, []);

  if (approvals.length === 0) return null;

  const handleApprove = async (id: string) => {
    try {
      await api.approveRequest(id);
      setApprovals((prev) => prev.filter((a) => a.id !== id));
      toast.success("Approved");
    } catch { toast.error("Failed to approve"); }
  };

  const handleDeny = async (id: string) => {
    try {
      await api.denyRequest(id);
      setApprovals((prev) => prev.filter((a) => a.id !== id));
      toast.success("Denied");
    } catch { toast.error("Failed to deny"); }
  };

  return (
    <section className="bg-amber-900/10 border border-amber-500/20 rounded-xl p-5 mb-6">
      <h2 className="text-sm font-semibold text-amber-400 mb-3 flex items-center gap-2">
        Pending Approvals
        <span className="px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-400 text-[10px] font-medium">
          {approvals.length}
        </span>
      </h2>
      <div className="space-y-2">
        {approvals.map((a) => (
          <div key={a.id as string} className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-bg-secondary border border-border">
            <div className="flex-1 min-w-0">
              <p className="text-sm text-text-primary font-mono truncate">{a.skill_name as string}</p>
              <p className="text-[10px] text-text-muted">Source: {a.source as string}</p>
            </div>
            <button
              onClick={() => handleApprove(a.id as string)}
              className="text-xs text-green-400 border border-green-500/30 px-3 py-1 rounded-lg hover:bg-green-500/10 transition-colors"
            >
              Approve
            </button>
            <button
              onClick={() => handleDeny(a.id as string)}
              className="text-xs text-red-400 border border-red-500/30 px-3 py-1 rounded-lg hover:bg-red-500/10 transition-colors"
            >
              Deny
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

function SkillPermissionsTable() {
  const toast = useToast();
  const [skills, setSkills] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listSkillPermissions()
      .then((data) => setSkills(Array.isArray(data) ? data : []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleLevelChange = async (skillName: string, newLevel: string) => {
    try {
      await api.setSkillPermission(skillName, newLevel);
      setSkills((prev) =>
        prev.map((s) =>
          s.skill_name === skillName ? { ...s, effective_level: newLevel, has_override: true } : s
        )
      );
      toast.success(`${skillName}: ${newLevel}`);
    } catch {
      toast.error("Failed to update permission");
    }
  };

  const handleRemoveOverride = async (skillName: string) => {
    try {
      await api.removeSkillPermission(skillName);
      setSkills((prev) =>
        prev.map((s) =>
          s.skill_name === skillName ? { ...s, has_override: false } : s
        )
      );
      toast.success(`Override removed for ${skillName}`);
    } catch {
      toast.error("Failed to remove override");
    }
  };

  if (loading || skills.length === 0) return null;

  return (
    <section className="bg-bg-secondary border border-border rounded-xl p-5">
      <SectionHeading title="Skill Permissions" subtitle="Resolved permission level for each skill" />
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-text-muted border-b border-border">
              <th className="text-left py-2 pr-4 font-medium">Skill</th>
              <th className="text-left py-2 px-4 font-medium">Category</th>
              <th className="text-center py-2 px-4 font-medium">Level</th>
              <th className="text-right py-2 pl-4 font-medium">Override</th>
            </tr>
          </thead>
          <tbody>
            {skills.map((s) => {
              const name = String(s.skill_name ?? "");
              const cat = String(s.category ?? "—");
              const level = String(s.effective_level ?? s.level ?? "ask");
              const hasOverride = Boolean(s.has_override);
              return (
                <tr key={name} className="border-b border-border/50 hover:bg-bg-hover/50">
                  <td className="py-2 pr-4 text-text-primary font-mono truncate max-w-[200px]">{name}</td>
                  <td className="py-2 px-4 text-text-muted">{cat}</td>
                  <td className="py-2 px-4 text-center">
                    <select
                      value={level}
                      onChange={(e) => handleLevelChange(name, e.target.value)}
                      className="text-[10px] px-2 py-0.5 rounded-full bg-bg-tertiary border border-border text-text-primary cursor-pointer"
                    >
                      {PERMISSION_LEVELS.map((l) => (
                        <option key={l} value={l}>{l}</option>
                      ))}
                    </select>
                  </td>
                  <td className="py-2 pl-4 text-right">
                    {hasOverride && (
                      <button
                        onClick={() => handleRemoveOverride(name)}
                        className="text-[10px] text-red-400 border border-red-400/30 px-2 py-0.5 rounded hover:bg-red-400/10"
                      >
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function PermissionsTab({
  perms,
  onPermsUpdate,
}: {
  readonly perms: PermissionSettings | null;
  readonly onPermsUpdate: (updates: Partial<PermissionSettings>) => Promise<void>;
}) {
  const toast = useToast();
  const [timeoutVal, setTimeoutVal] = useState(perms ? String(perms.auto_approve_timeout) : "0");
  const [savingTimeout, setSavingTimeout] = useState(false);

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
      {/* Pending approvals */}
      <PendingApprovals />

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

      {/* Per-skill permissions */}
      <SkillPermissionsTable />

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

// ── Search Tab ─────────────────────────────────────────────────────────────

function SearchTab({
  general,
  about,
  onUpdate,
}: {
  readonly general: GeneralSettings | null;
  readonly about: AboutInfo | null;
  readonly onUpdate: (updates: Partial<GeneralSettings>) => Promise<void>;
}) {
  const toast = useToast();
  const [saving, setSaving] = useState<string | null>(null);
  const current = general?.search_provider ?? "auto";

  const select = async (id: GeneralSettings["search_provider"]) => {
    if (id === current) return;
    setSaving(id);
    try {
      await onUpdate({ search_provider: id });
      toast.success(`Search engine: ${id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update");
    } finally {
      setSaving(null);
    }
  };

  const quota = about?.search_quota;
  // Authoritative: backend reads the env directly and reports presence.
  // Fall back to a quota-heuristic only on older backends that don't send
  // `search_keys` yet — keeps the UI useful during rolling upgrades.
  const keys = about?.search_keys;
  const keyConfigured = (envKey: string | null): boolean => {
    if (!envKey) return true;
    if (keys) {
      if (envKey === "BRAVE_KEY") return !!keys.brave;
    }
    if (!quota) return false;
    if (envKey === "BRAVE_KEY") return quota.brave_used > 0;
    return false;
  };

  const usedPct = (used: number, limit: number) => (limit > 0 ? Math.min(100, (used / limit) * 100) : 0);

  return (
    <section className="bg-bg-secondary border border-border rounded-xl p-5">
      <SectionHeading
        title="Web Search Engine"
        subtitle="Which provider the agent uses when it needs to search the web. Per-user preference — also respected by Telegram /search."
      />

      <div className="space-y-2">
        {SEARCH_PROVIDERS.map((p) => {
          const active = p.id === current;
          const configured = keyConfigured(p.needsKey);
          return (
            <button
              key={p.id}
              onClick={() => select(p.id)}
              disabled={saving !== null}
              className={`w-full text-left flex items-start gap-3 px-4 py-3 rounded-lg border transition-colors ${
                active
                  ? "bg-accent-soft border-accent/40"
                  : "bg-bg-tertiary border-border hover:border-border-light"
              } ${saving !== null ? "opacity-60 cursor-not-allowed" : "cursor-pointer"}`}
            >
              <div className={`mt-0.5 w-4 h-4 rounded-full border-2 shrink-0 ${active ? "border-accent bg-accent" : "border-border"}`}>
                {active && <div className="w-full h-full rounded-full bg-white scale-[0.4]" />}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-text-primary">{p.label}</span>
                  {p.needsKey ? (
                    <span
                      className={`text-[10px] px-2 py-0.5 rounded-full border ${
                        configured
                          ? "bg-accent-soft text-accent border-accent/20"
                          : "bg-error-soft text-error border-error/20"
                      }`}
                      title={p.needsKey}
                    >
                      {configured ? `${p.needsKey} ✓` : `${p.needsKey} missing`}
                    </span>
                  ) : (
                    <span className="text-[10px] px-2 py-0.5 rounded-full border bg-bg-hover text-text-muted border-border">
                      no API key
                    </span>
                  )}
                  {saving === p.id && <SpinnerIcon />}
                </div>
                <p className="text-xs text-text-muted mt-1">{p.blurb}</p>
              </div>
            </button>
          );
        })}
      </div>

      {/* Show a "how to configure" note only when the Brave key is missing */}
      {(keys && !keys.brave) && (
        <div className="mt-5 p-3 rounded-lg bg-bg-tertiary border border-border text-xs text-text-muted leading-relaxed">
          <div className="text-text-secondary font-medium mb-1">
            Configure Brave Search API key (optional)
          </div>
          Add the key to your <code className="text-accent">.env</code> file
          at the repo root, then restart the container:
          <pre className="mt-2 p-2 rounded bg-bg-primary border border-border overflow-x-auto text-[11px]">
{`# .env
BRAVE_KEY=...        # https://api-dashboard.search.brave.com (2,000 free/mo)

docker compose restart lazyclaw`}
          </pre>
          mcp-scraper and DuckDuckGo work out of the box — no key needed.
          Use <em>Auto</em> to prefer Brave when configured, fall back automatically.
        </div>
      )}

      {quota && (
        <div className="mt-6 pt-5 border-t border-border">
          <SectionHeading
            title="Monthly quota"
            subtitle={quota.reset_month ? `Current month: ${quota.reset_month}` : undefined}
          />
          <div className="space-y-3">
            <QuotaBar label="Brave Search" used={quota.brave_used} limit={quota.brave_limit} pct={usedPct(quota.brave_used, quota.brave_limit)} />
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-text-secondary">mcp-scraper</span>
                <span className="text-text-muted tabular-nums">{quota.scraper_used} (free, unlimited)</span>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="mt-6 pt-5 border-t border-border">
        <SectionHeading
          title="Browser automation"
          subtitle="Save successful multi-step flows so the agent can replay them by name."
        />
        <div className="flex items-center justify-between py-2">
          <div className="pr-4">
            <p className="text-sm text-text-primary">Auto-save successful flows as templates</p>
            <p className="text-xs text-text-muted">
              When a turn completes ≥3 actions and an approved checkpoint without rejects,
              upsert by primary host. Re-runs sharpen the same template — never duplicate.
            </p>
          </div>
          <Toggle
            on={general?.auto_save_browser_templates !== false}
            onChange={async () => {
              try {
                const next = !(general?.auto_save_browser_templates !== false);
                await onUpdate({ auto_save_browser_templates: next });
                toast.success(next ? "Auto-save enabled" : "Auto-save disabled");
              } catch (err) {
                toast.error(err instanceof Error ? err.message : "Failed to update");
              }
            }}
          />
        </div>
      </div>
    </section>
  );
}

function QuotaBar({ label, used, limit, pct }: { readonly label: string; readonly used: number; readonly limit: number; readonly pct: number }) {
  const hot = pct >= 80;
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-text-secondary">{label}</span>
        <span className="text-text-muted tabular-nums">{used} / {limit}</span>
      </div>
      <div className="h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
        <div
          className={`h-full ${hot ? "bg-error" : "bg-accent"} transition-all`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ── About Tab ──────────────────────────────────────────────────────────────

function AboutTab({ about }: { readonly about: AboutInfo | null }) {
  const toast = useToast();

  if (!about) {
    return (
      <section className="bg-bg-secondary border border-border rounded-xl p-5 text-sm text-text-muted">
        System info unavailable.
      </section>
    );
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(about, null, 2));
      toast.success("Diagnostics copied to clipboard");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Copy failed");
    }
  };

  const formatUptime = (s: number): string => {
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ${m % 60}m`;
    const d = Math.floor(h / 24);
    return `${d}d ${h % 24}h`;
  };

  const rows: readonly { readonly label: string; readonly value: string }[] = [
    { label: "LazyClaw version", value: about.version },
    { label: "Uptime", value: formatUptime(about.uptime_seconds) },
    { label: "Python", value: about.python_version },
    { label: "Platform", value: about.platform },
    { label: "Database", value: about.db_path },
    { label: "ECO mode", value: about.eco_mode.toUpperCase() },
    { label: "Search provider", value: about.search_provider },
    { label: "Telegram bot", value: about.telegram_configured ? "configured" : "not configured" },
    { label: "MCP servers", value: String(about.mcp_server_count) },
    { label: "Free LLM providers", value: about.free_providers.length > 0 ? about.free_providers.join(", ") : "none detected" },
  ];

  return (
    <section className="bg-bg-secondary border border-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-semibold text-text-primary">System</h2>
          <p className="text-xs text-text-muted mt-0.5">Read-only diagnostics — useful for bug reports.</p>
        </div>
        <button
          onClick={copy}
          className="px-3 py-1.5 text-xs text-accent border border-accent/30 rounded-lg hover:bg-accent-soft transition-colors"
        >
          Copy diagnostics
        </button>
      </div>

      <div className="divide-y divide-border border border-border rounded-lg overflow-hidden">
        {rows.map((r) => (
          <div key={r.label} className="flex justify-between items-center gap-4 px-4 py-2.5 bg-bg-tertiary/40">
            <span className="text-xs text-text-muted shrink-0">{r.label}</span>
            <span className="text-xs text-text-primary text-right break-all">{r.value}</span>
          </div>
        ))}
      </div>

      <p className="text-[11px] text-text-muted mt-4">
        LazyClaw is MIT-licensed, E2E-encrypted, Python-native. Source &amp; issues: see the repository.
      </p>
    </section>
  );
}

// ── Power tab ─────────────────────────────────────────────────────────────

function PowerTab({
  general,
  onUpdate,
}: {
  readonly general: GeneralSettings | null;
  readonly onUpdate: (updates: Partial<GeneralSettings>) => Promise<void>;
}) {
  const toast = useToast();
  const awake = general?.awake;
  const [saving, setSaving] = useState(false);

  const patch = async (fields: Record<string, unknown>) => {
    setSaving(true);
    try {
      await onUpdate({ awake: fields as never });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6 max-w-xl">
      <SectionHeading
        title="Power & Sleep"
        subtitle="Keep LazyClaw running with the lid closed. Requires the host awake bridge (make awake-bridge)."
      />

      {/* Awake mode toggle */}
      <div className="bg-bg-secondary rounded-xl border border-border p-4 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-text-primary">Stay awake by default</p>
            <p className="text-xs text-text-muted mt-0.5">Hold a no-sleep assertion whenever the agent is running. Works lid-closed when plugged in.</p>
          </div>
          <Toggle
            on={awake?.enabled ?? true}
            disabled={saving}
            onChange={() => patch({ enabled: !(awake?.enabled ?? true) })}
          />
        </div>
      </div>

      {/* Daily auto-wake */}
      <div className="bg-bg-secondary rounded-xl border border-border p-4 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-text-primary">Daily auto-wake</p>
            <p className="text-xs text-text-muted mt-0.5">Tell the hardware to wake itself every day at the set time (pmset). Works even from deep sleep.</p>
          </div>
          <Toggle
            on={awake?.daily_wake_enabled ?? false}
            disabled={saving}
            onChange={() => patch({ daily_wake_enabled: !(awake?.daily_wake_enabled ?? false) })}
          />
        </div>
        {(awake?.daily_wake_enabled) && (
          <div className="flex items-center gap-3">
            <label className="text-xs text-text-muted w-24 shrink-0">Wake time</label>
            <input
              type="time"
              value={awake?.daily_wake_time ?? "07:00"}
              disabled={saving}
              onChange={(e) => patch({ daily_wake_time: e.target.value })}
              className="bg-bg-hover border border-border rounded-lg px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent"
            />
            <span className="text-xs text-text-muted">every day</span>
          </div>
        )}
      </div>

      {/* Install hint */}
      <div className="rounded-xl border border-border bg-bg-secondary/50 p-4">
        <p className="text-xs text-text-muted">
          <strong className="text-text-secondary">First time?</strong> Run{" "}
          <code className="bg-bg-hover px-1.5 py-0.5 rounded text-accent text-[11px]">make awake-bridge</code>{" "}
          on your Mac host (one-time, needs sudo), then restart the container.
          The badge in the header will turn green once it's connected.
        </p>
      </div>
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
  const [modelsData, setModelsData] = useState<ModelsData | null>(null);
  const [team, setTeam] = useState<TeamSettings | null>(null);
  const [specialists, setSpecialists] = useState<Specialist[]>([]);
  const [perms, setPerms] = useState<PermissionSettings | null>(null);
  const [general, setGeneral] = useState<GeneralSettings | null>(null);
  const [about, setAbout] = useState<AboutInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<TabId>("models");

  useEffect(() => {
    let alive = true;
    Promise.allSettled([
      api.getEcoSettings(),
      api.getEcoUsage(),
      api.getEcoProviders(),
      api.getEcoRateLimits(),
      api.getTeamSettings(),
      api.listSpecialists(),
      api.getPermissionSettings(),
      api.getEcoModels(),
      api.getGeneralSettings(),
      api.getAboutInfo(),
    ]).then((results) => {
      if (!alive) return;
      if (results[0].status === "fulfilled") setEco(results[0].value);
      if (results[1].status === "fulfilled") setUsage(results[1].value);
      if (results[2].status === "fulfilled") setProviders(Array.isArray(results[2].value) ? results[2].value : []);
      if (results[3].status === "fulfilled") setRateLimits(results[3].value);
      if (results[4].status === "fulfilled") setTeam(results[4].value);
      if (results[5].status === "fulfilled") setSpecialists(Array.isArray(results[5].value) ? results[5].value : []);
      if (results[6].status === "fulfilled") setPerms(results[6].value);
      if (results[7].status === "fulfilled") setModelsData(results[7].value);
      if (results[8].status === "fulfilled") setGeneral(results[8].value);
      if (results[9].status === "fulfilled") setAbout(results[9].value);
      setLoading(false);
    });
    return () => { alive = false; };
  }, []);

  // Refresh About whenever the user switches onto the About tab,
  // so uptime + quota are current.
  useEffect(() => {
    if (tab !== "about") return;
    let alive = true;
    api.getAboutInfo().then((v) => { if (alive) setAbout(v); }).catch(() => { /* noop */ });
    return () => { alive = false; };
  }, [tab]);

  const handleEcoMode = useCallback(async (mode: string) => {
    try {
      const updated = await api.updateEcoSettings({ mode });
      setEco(updated);
      window.dispatchEvent(new CustomEvent("lazyclaw:eco-changed"));
      toast.success(`Switched to ${mode.toUpperCase()} mode`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update mode");
    }
  }, [toast]);

  const handleEcoSettingsUpdate = useCallback(async (updates: Partial<EcoSettings>) => {
    const updated = await api.updateEcoSettings(updates);
    setEco(updated);
    window.dispatchEvent(new CustomEvent("lazyclaw:eco-changed"));
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

  const handleGeneralUpdate = useCallback(async (updates: Partial<GeneralSettings>) => {
    const updated = await api.updateGeneralSettings(updates);
    setGeneral(updated);
    // `search_provider` shows up on the About tab — keep it in sync cheaply.
    setAbout((prev) => prev ? { ...prev, search_provider: updated.search_provider } : prev);
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
        {tab === "models" && (
          <EcoTab
            eco={eco}
            usage={usage}
            providers={providers}
            rateLimits={rateLimits}
            modelsData={modelsData}
            onModeChange={handleEcoMode}
            onSettingsUpdate={handleEcoSettingsUpdate}
          />
        )}
        {tab === "search" && (
          <SearchTab general={general} about={about} onUpdate={handleGeneralUpdate} />
        )}
        {tab === "teams" && (
          <TeamsTab
            team={team}
            specialists={specialists}
            modelsData={modelsData}
            onTeamUpdate={handleTeamUpdate}
            onSpecialistDelete={handleSpecialistDelete}
            onSpecialistCreate={handleSpecialistCreate}
          />
        )}
        {tab === "permissions" && (
          <PermissionsTab perms={perms} onPermsUpdate={handlePermsUpdate} />
        )}
        {tab === "power" && (
          <PowerTab general={general} onUpdate={handleGeneralUpdate} />
        )}
        {tab === "mobile" && <MobileAppTab />}
        {tab === "about" && <AboutTab about={about} />}
      </div>
    </div>
  );
}
