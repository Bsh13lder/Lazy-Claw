import { useEffect, useState } from "react";

// BeforeInstallPromptEvent isn't in the TS DOM lib yet.
interface BeforeInstallPromptEvent extends Event {
  readonly platforms: ReadonlyArray<string>;
  readonly userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
  prompt(): Promise<void>;
}

const SNOOZE_KEY = "lazyclaw.installPrompt.snoozedUntil";
const SNOOZE_MS = 24 * 60 * 60 * 1000; // 24h

function isSnoozed(): boolean {
  try {
    const raw = window.localStorage.getItem(SNOOZE_KEY);
    if (!raw) return false;
    const until = Number(raw);
    return Number.isFinite(until) && Date.now() < until;
  } catch {
    return false;
  }
}

function snooze() {
  try {
    window.localStorage.setItem(SNOOZE_KEY, String(Date.now() + SNOOZE_MS));
  } catch {
    /* localStorage unavailable — ignore */
  }
}

function isStandalone(): boolean {
  return (
    window.matchMedia?.("(display-mode: standalone)").matches ||
    // Safari fallback
    (window.navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

export default function InstallPrompt() {
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(null);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    if (isStandalone() || isSnoozed()) {
      setHidden(true);
      return;
    }

    const onBeforeInstall = (e: Event) => {
      e.preventDefault();
      setDeferred(e as BeforeInstallPromptEvent);
    };

    const onInstalled = () => {
      setDeferred(null);
      setHidden(true);
    };

    window.addEventListener("beforeinstallprompt", onBeforeInstall);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstall);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (hidden || !deferred) return null;

  const install = async () => {
    try {
      await deferred.prompt();
      const choice = await deferred.userChoice;
      if (choice.outcome === "dismissed") snooze();
    } finally {
      setDeferred(null);
    }
  };

  const dismiss = () => {
    snooze();
    setDeferred(null);
    setHidden(true);
  };

  return (
    <div
      className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 px-4 py-2 rounded-full shadow-lg border border-accent/40 bg-bg-secondary/95 backdrop-blur"
      role="dialog"
      aria-label="Install LazyClaw"
    >
      <span className="text-sm text-text-primary">Install LazyClaw on your phone</span>
      <button
        type="button"
        onClick={install}
        className="text-sm font-semibold px-3 py-1 rounded-full bg-accent text-bg-primary hover:opacity-90"
      >
        Install
      </button>
      <button
        type="button"
        onClick={dismiss}
        className="text-sm text-text-muted hover:text-text-primary"
        aria-label="Dismiss install prompt"
      >
        ✕
      </button>
    </div>
  );
}
