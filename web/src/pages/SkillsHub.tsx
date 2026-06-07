import { lazy, Suspense, useState } from "react";

// Each panel stays its own lazy chunk so the unified hub doesn't pull the
// Specialists editor, the Skills registry, AND the Discover catalog into one
// bundle — only the active tab's code loads. App.tsx lazy-imports the very
// same modules for the legacy deep-link routes, so Vite dedupes them into a
// single shared chunk per page.
const Specialists = lazy(() => import("./Specialists"));
const Skills = lazy(() => import("./Skills"));
const SkillHub = lazy(() => import("./SkillHub"));

type HubTab = "specialists" | "skills" | "discover";

const TABS: { key: HubTab; label: string }[] = [
  { key: "specialists", label: "Specialists" },
  { key: "skills", label: "Skills" },
  { key: "discover", label: "Discover" },
];

const STORE_KEY = "lazyclaw:skills-hub-subtab";

function isHubTab(value: string | null): value is HubTab {
  return value === "specialists" || value === "skills" || value === "discover";
}

function initialTab(): HubTab {
  if (typeof window === "undefined") return "specialists";
  // Deep-link override: /?page=skills-hub&tab=skills wins over the last
  // remembered tab so links land where they point.
  const fromUrl = new URLSearchParams(window.location.search).get("tab");
  if (isHubTab(fromUrl)) return fromUrl;
  const saved = window.localStorage.getItem(STORE_KEY);
  if (isHubTab(saved)) return saved;
  return "specialists";
}

/**
 * Unified "Skills & Agents" workspace — one nav entry hosting the three
 * skill-related surfaces as sub-tabs:
 *   - Specialists (default): declarative agents the router delegates to
 *   - Skills: manage the agent tool registry
 *   - Discover: browse & install new skills
 *
 * Each remains an independent feature (separate APIs + page components); this
 * is purely the front-of-house consolidation so the user sees a single Tools
 * entry instead of three. The legacy /?page={specialists,skills,hub} routes
 * still resolve to the original components for deep links.
 */
export default function SkillsHub() {
  const [tab, setTab] = useState<HubTab>(initialTab);

  const select = (next: HubTab) => {
    setTab(next);
    try {
      window.localStorage.setItem(STORE_KEY, next);
    } catch {
      /* storage unavailable — non-fatal */
    }
  };

  return (
    <div className="flex flex-col h-full bg-bg-primary">
      {/* Sub-tab switcher */}
      <div className="shrink-0 flex items-center gap-1 px-3 py-2 border-b border-border bg-bg-secondary">
        <span className="text-sm font-semibold text-text-primary mr-2">Skills &amp; Agents</span>
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => select(t.key)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              tab === t.key
                ? "bg-accent text-bg-primary"
                : "text-text-muted hover:bg-bg-hover hover:text-text-secondary"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Active panel — only the selected tab's component mounts/loads */}
      <div className="flex-1 min-h-0 overflow-auto">
        <Suspense
          fallback={
            <div className="h-full flex items-center justify-center text-text-muted text-sm">
              Loading…
            </div>
          }
        >
          {tab === "specialists" && <Specialists />}
          {tab === "skills" && <Skills />}
          {tab === "discover" && <SkillHub />}
        </Suspense>
      </div>
    </div>
  );
}
