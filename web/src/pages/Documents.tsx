import { lazy, Suspense, useState } from "react";

// Each type stays its own lazy chunk so opening "Sheets" doesn't pull in
// pdf.js, and opening "PDF" doesn't pull in the ~10 MB Univer engine.
const Sheets = lazy(() => import("./Sheets"));
const Docs = lazy(() => import("./Docs"));
const Pdf = lazy(() => import("./Pdf"));

type DocKind = "sheets" | "docs" | "pdf";

const TABS: { key: DocKind; label: string }[] = [
  { key: "sheets", label: "Sheets" },
  { key: "docs", label: "Documents" },
  { key: "pdf", label: "PDFs" },
];

const STORE_KEY = "lazyclaw:docs-subtab";

function initialKind(): DocKind {
  if (typeof window === "undefined") return "sheets";
  const saved = window.localStorage.getItem(STORE_KEY);
  return saved === "docs" || saved === "pdf" || saved === "sheets" ? saved : "sheets";
}

interface DocumentsProps {
  /** When true the workspace is the standalone fullscreen page (own browser
   *  tab, no app chrome) — the pop-out button becomes an "exit" affordance. */
  fullscreen?: boolean;
}

/**
 * Unified "Docs" workspace — one nav entry hosting Sheets, Documents, and PDFs
 * as sub-tabs. The three remain independent features (separate APIs, stores,
 * agent skills); this is purely the front-of-house consolidation so the user
 * sees a single "Docs" tab instead of three.
 *
 * The ⤢ button pops the whole workspace into a dedicated fullscreen browser
 * tab (`/?page=docs-full`, rendered outside NavShell) for distraction-free
 * document work; in that tab the same button becomes ⤡ "Exit fullscreen".
 */
export default function Documents({ fullscreen = false }: DocumentsProps) {
  const [kind, setKind] = useState<DocKind>(initialKind);

  const select = (k: DocKind) => {
    setKind(k);
    try {
      window.localStorage.setItem(STORE_KEY, k);
    } catch {
      /* storage unavailable — non-fatal */
    }
  };

  return (
    <div className="flex flex-col h-full bg-bg-primary">
      {/* Sub-type switcher */}
      <div className="shrink-0 flex items-center gap-1 px-3 py-2 border-b border-border bg-bg-secondary">
        <span className="text-sm font-semibold text-text-primary mr-2">Docs</span>
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => select(t.key)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              kind === t.key
                ? "bg-accent text-bg-primary"
                : "text-text-muted hover:bg-bg-hover hover:text-text-secondary"
            }`}
          >
            {t.label}
          </button>
        ))}

        {fullscreen ? (
          <button
            onClick={() => {
              window.location.href = "/?page=docs";
            }}
            className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm font-medium text-text-muted hover:bg-bg-hover hover:text-text-secondary transition-colors"
            title="Exit fullscreen — back to the app"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 9H4V4M9 9 4 4M15 9h5V4M15 9l5-5M9 15H4v5M9 15l-5 5M15 15h5v5M15 15l5 5" />
            </svg>
            Exit fullscreen
          </button>
        ) : (
          <button
            onClick={() =>
              window.open("/?page=docs-full", "_blank", "noopener")
            }
            className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm font-medium text-text-muted hover:bg-bg-hover hover:text-accent transition-colors"
            title="Open the Docs workspace fullscreen in a new tab"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M15 3h6v6M21 3l-9 9M10 4H4v16h16v-6" />
            </svg>
            Fullscreen
          </button>
        )}
      </div>

      {/* Active type — remounts on switch (frees Univer/pdf.js resources) */}
      <div className="flex-1 min-h-0">
        <Suspense
          fallback={
            <div className="h-full flex items-center justify-center text-text-muted text-sm">
              Loading…
            </div>
          }
        >
          {kind === "sheets" && <Sheets />}
          {kind === "docs" && <Docs />}
          {kind === "pdf" && <Pdf />}
        </Suspense>
      </div>
    </div>
  );
}
