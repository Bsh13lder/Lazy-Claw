import { useEffect, useRef, useState } from "react";
import {
  createSheet,
  deleteSheet,
  getSheet,
  listSheets,
  saveSheet,
  type SheetMeta,
  type UniverSnapshot,
} from "../api";
import {
  createUniver,
  defaultTheme,
  LocaleType,
  mergeLocales,
  type FUniver,
} from "@univerjs/presets";
import { UniverSheetsCorePreset } from "@univerjs/preset-sheets-core";
import UniverPresetSheetsCoreEnUS from "@univerjs/preset-sheets-core/locales/en-US";
import "@univerjs/preset-sheets-core/lib/index.css";
import DocAiPopover from "../components/DocAiPopover";

type SaveState = "idle" | "saving" | "saved" | "error";

const AUTOSAVE_MS = 800;

/**
 * Sheets — private encrypted spreadsheets backed by the embedded Univer editor.
 *
 * The canonical document is Univer's `IWorkbookData` snapshot, persisted
 * verbatim (AES-256-GCM) via `/api/sheets`. We mount a fresh Univer instance
 * per selected sheet, autosave the snapshot on cell changes (debounced), and
 * dispose on switch/unmount — flushing any pending save first so no edit is
 * lost crossing the debounce window.
 */
export default function Sheets() {
  const [sheets, setSheets] = useState<SheetMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [activeName, setActiveName] = useState("");
  const [loadingList, setLoadingList] = useState(true);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  const [reloadToken, setReloadToken] = useState(0);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const univerRef = useRef<{ dispose: () => void } | null>(null);
  const apiRef = useRef<FUniver | null>(null);
  const dirtyRef = useRef(false);
  const nameRef = useRef("");
  // Lets the ✨ AI popover flush pending edits before the agent reads the sheet.
  const flushHandleRef = useRef<() => Promise<void>>(() => Promise.resolve());

  // Keep the latest name available to the autosave closure without
  // re-initialising Univer on every rename.
  useEffect(() => {
    nameRef.current = activeName;
  }, [activeName]);

  async function refreshList(selectId?: string) {
    const rows = await listSheets();
    setSheets(rows);
    setLoadingList(false);
    if (selectId) {
      const found = rows.find((s) => s.id === selectId);
      if (found) {
        setActiveId(found.id);
        setActiveName(found.name);
      }
    } else if (rows.length && !activeId) {
      setActiveId(rows[0].id);
      setActiveName(rows[0].name);
    }
  }

  useEffect(() => {
    refreshList().catch(() => setLoadingList(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Univer lifecycle, keyed on the selected sheet ──────────────────
  useEffect(() => {
    const container = containerRef.current;
    if (!activeId || !container) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const sheetId = activeId; // capture for the closure / cleanup

    const flush = (): Promise<void> => {
      const api = apiRef.current;
      if (!api || !dirtyRef.current) return Promise.resolve();
      const wb = api.getActiveWorkbook();
      if (!wb) return Promise.resolve();
      const snapshot = wb.save() as unknown as UniverSnapshot;
      dirtyRef.current = false;
      setSaveState("saving");
      return saveSheet(sheetId, nameRef.current, snapshot)
        .then(() => {
          if (!cancelled) setSaveState("saved");
        })
        .catch(() => {
          if (!cancelled) setSaveState("error");
        });
    };
    flushHandleRef.current = flush;

    const scheduleSave = () => {
      dirtyRef.current = true;
      if (timer) clearTimeout(timer);
      timer = setTimeout(flush, AUTOSAVE_MS);
    };

    (async () => {
      const doc = await getSheet(sheetId);
      if (cancelled || !containerRef.current) return;

      const { univer, univerAPI } = createUniver({
        locale: LocaleType.EN_US,
        locales: { [LocaleType.EN_US]: mergeLocales(UniverPresetSheetsCoreEnUS) },
        theme: defaultTheme,
        presets: [UniverSheetsCorePreset({ container })],
      });
      univerRef.current = univer;
      apiRef.current = univerAPI;
      dirtyRef.current = false;

      univerAPI.createWorkbook(
        doc.payload as unknown as Parameters<FUniver["createWorkbook"]>[0],
      );
      univerAPI.addEvent(univerAPI.Event.SheetValueChanged, scheduleSave);
    })();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      flush(); // persist anything edited inside the debounce window
      try {
        univerRef.current?.dispose();
      } catch {
        /* already torn down */
      }
      univerRef.current = null;
      apiRef.current = null;
    };
  }, [activeId, reloadToken]);

  async function handleNew() {
    const created = await createSheet("Untitled sheet");
    await refreshList(created.id);
  }

  async function handleDelete(id: string) {
    if (!window.confirm("Delete this sheet? This cannot be undone.")) return;
    await deleteSheet(id);
    const remaining = sheets.filter((s) => s.id !== id);
    setSheets(remaining);
    if (activeId === id) {
      const next = remaining[0];
      setActiveId(next ? next.id : null);
      setActiveName(next ? next.name : "");
    }
  }

  function commitRename() {
    const id = activeId;
    const api = apiRef.current;
    if (!id || !api) return;
    const wb = api.getActiveWorkbook();
    if (!wb) return;
    const snapshot = wb.save() as unknown as UniverSnapshot;
    const name = nameRef.current.trim() || "Untitled sheet";
    setSaveState("saving");
    saveSheet(id, name, snapshot)
      .then(() => {
        setSaveState("saved");
        setSheets((prev) => prev.map((s) => (s.id === id ? { ...s, name } : s)));
      })
      .catch(() => setSaveState("error"));
  }

  return (
    <div className="flex h-full bg-bg-primary text-text-primary">
      {/* Sheet browser */}
      <aside className="w-60 shrink-0 border-r border-border bg-bg-secondary flex flex-col">
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
          <span className="text-sm font-semibold">Sheets</span>
          <button
            onClick={handleNew}
            className="px-2 py-1 rounded-lg bg-accent text-bg-primary text-xs font-medium hover:opacity-90 transition-opacity"
            title="New sheet"
          >
            + New
          </button>
        </div>

        <div className="flex-1 overflow-y-auto py-1">
          {loadingList ? (
            <div className="px-3 py-2 text-xs text-text-muted">Loading…</div>
          ) : sheets.length === 0 ? (
            <div className="px-3 py-3 text-xs text-text-muted leading-relaxed">
              No sheets yet. Click <span className="text-accent">+ New</span> or ask
              the agent to create one.
            </div>
          ) : (
            sheets.map((s) => {
              const isActive = s.id === activeId;
              return (
                <div
                  key={s.id}
                  className={`group flex items-center gap-1 mx-1 px-2 py-1.5 rounded-lg cursor-pointer transition-colors ${
                    isActive
                      ? "bg-bg-hover text-text-primary"
                      : "text-text-muted hover:bg-bg-hover hover:text-text-secondary"
                  }`}
                  onClick={() => {
                    setActiveId(s.id);
                    setActiveName(s.name);
                  }}
                >
                  <svg
                    width="16" height="16" viewBox="0 0 24 24" fill="none"
                    stroke="currentColor" strokeWidth="1.8" className="shrink-0"
                  >
                    <rect x="3" y="3" width="18" height="18" rx="1" />
                    <line x1="3" y1="9" x2="21" y2="9" />
                    <line x1="9" y1="3" x2="9" y2="21" />
                  </svg>
                  <span className="text-sm truncate flex-1">{s.name}</span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDelete(s.id);
                    }}
                    className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-red-400 transition-opacity"
                    title="Delete sheet"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                      <path d="M18 6 6 18M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              );
            })
          )}
        </div>
      </aside>

      {/* Editor */}
      <main className="flex-1 min-w-0 flex flex-col">
        {activeId ? (
          <>
            <div className="shrink-0 flex items-center gap-3 px-4 py-2 border-b border-border">
              <input
                value={activeName}
                onChange={(e) => setActiveName(e.target.value)}
                onBlur={commitRename}
                onKeyDown={(e) => {
                  if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                }}
                className="text-sm font-medium bg-transparent outline-none border-b border-transparent focus:border-border-light px-1 py-0.5 min-w-0"
                aria-label="Sheet name"
              />
              <SaveBadge state={saveState} />
              <div className="ml-auto">
                <DocAiPopover
                  kind="sheets"
                  docId={activeId}
                  docName={activeName}
                  beforeSubmit={() => flushHandleRef.current()}
                  onReload={() => {
                    dirtyRef.current = false;
                    setReloadToken((t) => t + 1);
                  }}
                />
              </div>
            </div>
            {/* Univer mounts here — needs a real pixel height */}
            <div ref={containerRef} className="flex-1 min-h-0" />
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
            {loadingList ? "Loading…" : "Select a sheet or create a new one."}
          </div>
        )}
      </main>
    </div>
  );
}

function SaveBadge({ state }: { state: SaveState }) {
  if (state === "idle") return null;
  const map: Record<Exclude<SaveState, "idle">, { text: string; cls: string }> = {
    saving: { text: "Saving…", cls: "text-text-muted" },
    saved: { text: "Saved", cls: "text-accent" },
    error: { text: "Save failed", cls: "text-red-400" },
  };
  const { text, cls } = map[state];
  return <span className={`text-xs ${cls}`}>{text}</span>;
}
