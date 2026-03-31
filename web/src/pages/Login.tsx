import { useState, type FormEvent } from "react";
import { useAuth } from "../context/AuthContext";

export default function Login() {
  const { login, register, error, clearError } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [inviteToken, setInviteToken] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const toggleMode = () => {
    setMode((m) => (m === "login" ? "register" : "login"));
    clearError();
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setSubmitting(true);
    try {
      if (mode === "login") {
        await login(username.trim(), password);
      } else {
        await register(username.trim(), password, inviteToken || undefined);
      }
    } catch {
      // error is set in context
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-bg-primary">
      {/* Full page centered */}
      <div className="flex-1 flex items-center justify-center px-4">
        <div className="w-full max-w-sm">
          {/* Logo */}
          <div className="text-center mb-10">
            <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-accent-soft mb-5">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-accent">
                <rect x="3" y="11" width="18" height="11" rx="2" />
                <path d="M7 11V7a5 5 0 0110 0v4" />
              </svg>
            </div>
            <h1 className="text-xl font-semibold text-text-primary mb-1">
              {mode === "login" ? "Welcome back" : "Create your account"}
            </h1>
            <p className="text-sm text-text-muted">
              {mode === "login"
                ? "Log in to your LazyClaw account"
                : "Get started with E2E encrypted AI"
              }
            </p>
          </div>

          {/* Error */}
          {error && (
            <div className="mb-4 px-4 py-3 rounded-xl bg-error-soft border border-error/15 text-error text-sm animate-fade-in">
              {error}
            </div>
          )}

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1.5 ml-1">
                Username
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
                placeholder="Enter your username"
                className="w-full px-4 py-3 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light transition-colors"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1.5 ml-1">
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                placeholder="Enter your password"
                className="w-full px-4 py-3 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light transition-colors"
              />
            </div>

            {mode === "register" && (
              <div className="animate-fade-in">
                <label className="block text-xs font-medium text-text-secondary mb-1.5 ml-1">
                  Invite token
                </label>
                <input
                  type="text"
                  value={inviteToken}
                  onChange={(e) => setInviteToken(e.target.value)}
                  placeholder="Required for registration"
                  className="w-full px-4 py-3 rounded-xl bg-bg-tertiary border border-border text-sm text-text-primary placeholder:text-text-placeholder focus:outline-none focus:border-border-light transition-colors"
                />
              </div>
            )}

            <button
              type="submit"
              disabled={submitting || !username.trim() || !password}
              className="w-full py-3 mt-2 rounded-xl bg-accent text-bg-primary font-medium text-sm hover:opacity-90 disabled:opacity-30 disabled:cursor-not-allowed transition-opacity"
            >
              {submitting ? (
                <span className="inline-flex items-center gap-2">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="spinner">
                    <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
                  </svg>
                  {mode === "login" ? "Logging in..." : "Creating account..."}
                </span>
              ) : (
                mode === "login" ? "Continue" : "Create account"
              )}
            </button>
          </form>

          {/* Toggle */}
          <p className="mt-5 text-center text-sm text-text-muted">
            {mode === "login" ? "Don\u2019t have an account? " : "Already have an account? "}
            <button
              onClick={toggleMode}
              className="text-accent hover:underline transition-colors"
            >
              {mode === "login" ? "Sign up" : "Log in"}
            </button>
          </p>
        </div>
      </div>

      {/* Footer */}
      <div className="py-4 text-center">
        <p className="text-[11px] text-text-muted">
          All data encrypted with AES-256-GCM &middot; Open source
        </p>
      </div>
    </div>
  );
}
