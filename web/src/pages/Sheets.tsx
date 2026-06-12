import { useEffect, useRef, useState } from "react";
import {
  convertSheetLinks,
  createSheet,
  deleteSheet,
  downloadBlob,
  exportSheetBlob,
  getSheet,
  listSheets,
  saveSheet,
  uploadSheet,
  type SheetMeta,
  type UniverSnapshot,
  ConflictError,
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
import { UniverSheetsHyperLinkPreset } from "@univerjs/preset-sheets-hyper-link";
import UniverPresetSheetsHyperLinkEnUS from "@univerjs/preset-sheets-hyper-link/locales/en-US";
import "@univerjs/preset-sheets-hyper-link/lib/index.css";
import { UniverSheetsFilterPreset } from "@univerjs/preset-sheets-filter";
import UniverPresetSheetsFilterEnUS from "@univerjs/preset-sheets-filter/locales/en-US";
import "@univerjs/preset-sheets-filter/lib/index.css";
import { UniverSheetsSortPreset } from "@univerjs/preset-sheets-sort";
import UniverPresetSheetsSortEnUS from "@univerjs/preset-sheets-sort/locales/en-US";
import "@univerjs/preset-sheets-sort/lib/index.css";
import { UniverSheetsConditionalFormattingPreset } from "@univerjs/preset-sheets-conditional-formatting";
import UniverPresetSheetsConditionalFormattingEnUS from "@univerjs/preset-sheets-conditional-formatting/locales/en-US";
import "@univerjs/preset-sheets-conditional-formatting/lib/index.css";
import { UniverSheetsDataValidationPreset } from "@univerjs/preset-sheets-data-validation";
import UniverPresetSheetsDataValidationEnUS from "@univerjs/preset-sheets-data-validation/locales/en-US";
import "@univerjs/preset-sheets-data-validation/lib/index.css";
import { UniverSheetsFindReplacePreset } from "@univerjs/preset-sheets-find-replace";
import UniverPresetSheetsFindReplaceEnUS from "@univerjs/preset-sheets-find-replace/locales/en-US";
import "@univerjs/preset-sheets-find-replace/lib/index.css";
import { UniverSheetsNotePreset } from "@univerjs/preset-sheets-note";
import UniverPresetSheetsNoteEnUS from "@univerjs/preset-sheets-note/locales/en-US";
import "@univerjs/preset-sheets-note/lib/index.css";
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
  const [exportPassword, setExportPassword] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [activeName, setActiveName] = useState("");
  const [loadingList, setLoadingList] = useState(true);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  // ── Tag filter state ────────────────────────────────────────────────
  const [activeTags, setActiveTags] = useState<Set<string>>(new Set());
  // ── Tag editor state for the open sheet ─────────────────────────────
  const [showTagEditor, setShowTagEditor] = useState(false);
  const [tagInput, setTagInput] = useState("");

  const [reloadToken, setReloadToken] = useState(0);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const univerRef = useRef<{ dispose: () => void } | null>(null);
  const apiRef = useRef<FUniver | null>(null);
  const dirtyRef = useRef(false);
  const nameRef = useRef("");
  const updatedAtRef = useRef<string | null>(null);
  // Lets the ✨ AI popover flush pending edits before the agent reads the sheet.
  const flushHandleRef = useRef<() => Promise<void>>(() => Promise.resolve());

  // Keep the latest name available to the autosave closure without
  // re-initialising Univer on every rename.
  useEffect(() => {
    nameRef.current = activeName;
  }, [activeName]);

  // Derived: all distinct tags across all sheets for the filter bar
  const allTags = Array.from(
    new Set(sheets.flatMap((s) => s.tags ?? []))
  ).sort();

  // Sheets filtered by active tag selection
  const visibleSheets =
    activeTags.size === 0
      ? sheets
      : sheets.filter((s) => (s.tags ?? []).some((t) => activeTags.has(t)));

  // Current active sheet meta (for tag editor)
  const activeMeta = sheets.find((s) => s.id === activeId) ?? null;

  async function refreshList(selectId?: string) {
    const rows = await listSheets();
    setSheets(rows);
    setLoadingList(false);
    if (selectId) {
      const found = rows.find((s) => s.id === selectId);
      if (found) {
        setActiveId(found.id);
        setActiveName(found.name);
        updatedAtRef.current = found.updated_at;
      }
    } else if (rows.length && !activeId) {
      setActiveId(rows[0].id);
      setActiveName(rows[0].name);
      updatedAtRef.current = rows[0].updated_at;
    }
  }

  useEffect(() => {
    refreshList().catch(() => setLoadingList(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Univer lifecycle, keyed on the selected sheet ──────────────────────
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
      return saveSheet(sheetId, nameRef.current, snapshot, updatedAtRef.current)
        .then((row) => {
          updatedAtRef.current = row.updated_at;
          if (!cancelled) setSaveState("saved");
        })
        .catch((err) => {
          if (cancelled) return;
          if (err instanceof ConflictError) {
            const reload = window.confirm(
              "This sheet changed on the server. Reload the latest version? (Cancel keeps your copy and overwrites.)"
            );
            if (reload) {
              setReloadToken((t) => t + 1);
            } else {
              // Re-save with no base — force overwrite
              dirtyRef.current = true;
              return saveSheet(sheetId, nameRef.current, snapshot, null)
                .then((row) => {
                  updatedAtRef.current = row.updated_at;
                  setSaveState("saved");
                })
                .catch(() => setSaveState("error"));
            }
          } else {
            setSaveState("error");
          }
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

      updatedAtRef.current = doc.updated_at;

      const { univer, univerAPI } = createUniver({
        locale: LocaleType.EN_US,
        locales: {
          [LocaleType.EN_US]: mergeLocales(
            UniverPresetSheetsCoreEnUS,
            UniverPresetSheetsHyperLinkEnUS,
            UniverPresetSheetsFilterEnUS,
            UniverPresetSheetsSortEnUS,
            UniverPresetSheetsConditionalFormattingEnUS,
            UniverPresetSheetsDataValidationEnUS,
            UniverPresetSheetsFindReplaceEnUS,
            UniverPresetSheetsNoteEnUS,
          ),
        },
        theme: defaultTheme,
        presets: [
          UniverSheetsCorePreset({ container }),
          UniverSheetsHyperLinkPreset(),
          UniverSheetsFilterPreset(),
          UniverSheetsSortPreset(),
          UniverSheetsConditionalFormattingPreset(),
          UniverSheetsDataValidationPreset(),
          UniverSheetsFindReplacePreset(),
          UniverSheetsNotePreset(),
        ],
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

  async function handleImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-importing the same file
    if (!file) return;
    try {
      const created = await uploadSheet(file);
      await refreshList(created.id);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Import failed");
    }
  }

  async function handleExport(format: "xlsx" | "csv") {
    if (!activeId) return;
    const pw = exportPassword.trim();
    try {
      const blob = await exportSheetBlob(activeId, format, pw || null);
      const ext = pw ? "zip" : format;
      downloadBlob(blob, `${activeName || "sheet"}.${ext}`);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Export failed");
    }
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
      updatedAtRef.current = next ? next.updated_at : null;
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
    saveSheet(id, name, snapshot, updatedAtRef.current)
      .then((row) => {
        updatedAtRef.current = row.updated_at;
        setSaveState("saved");
        setSheets((prev) => prev.map((s) => (s.id === id ? { ...s, name } : s)));
      })
      .catch(() => setSaveState("error"));
  }

  async function handleConvertLinks() {
    if (!activeId) return;
    try {
      const res = await convertSheetLinks(activeId);
      updatedAtRef.current = res.updated_at;
      dirtyRef.current = false;
      setReloadToken((t) => t + 1);
      window.alert(`Converted ${res.converted} URL${res.converted === 1 ? "" : "s"} to hyperlinks.`);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Conversion failed");
    }
  }

  // ── Tag editor helpers ──────────────────────────────────────────────
  function addTag(tag: string) {
    const trimmed = tag.trim();
    if (!trimmed || !activeId) return;
    const current = activeMeta?.tags ?? [];
    if (current.includes(trimmed)) return;
    const newTags = [...current, trimmed];
    applyTags(newTags);
  }

  function removeTag(tag: string) {
    if (!activeId) return;
    const newTags = (activeMeta?.tags ?? []).filter((t) => t !== tag);
    applyTags(newTags);
  }

  function applyTags(newTags: string[]) {
    const id = activeId;
    const api = apiRef.current;
    if (!id) return;

    // Get current snapshot to avoid clobbering edits
    let snapshot: UniverSnapshot | null = null;
    if (api) {
      const wb = api.getActiveWorkbook();
      if (wb) snapshot = wb.save() as unknown as UniverSnapshot;
    }

    if (snapshot) {
      dirtyRef.current = false;
      setSaveState("saving");
      saveSheet(id, nameRef.current, snapshot, updatedAtRef.current, newTags)
        .then((row) => {
          updatedAtRef.current = row.updated_at;
          setSaveState("saved");
          setSheets((prev) =>
            prev.map((s) => (s.id === id ? { ...s, tags: row.tags ?? newTags } : s))
          );
        })
        .catch(() => setSaveState("error"));
    }
  }

  return (
    <div className="flex h-full bg-bg-primary text-text-primary">
      {/* Sheet browser */}
      <aside className="w-60 shrink-0 border-r border-border bg-bg-secondary flex flex-col">
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
          <span className="text-sm font-semibold">Sheets</span>
          <div className="flex items-center gap-1.5">
            <label
              className="px-2 py-1 rounded-lg border border-border text-text-secondary text-xs font-medium hover:bg-bg-hover cursor-pointer transition-colors"
              title="Import a .xlsx spreadsheet"
            >
              Import
              <input
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                className="hidden"
                onChange={handleImportFile}
              />
            </label>
            <button
              onClick={handleNew}
              className="px-2 py-1 rounded-lg bg-accent text-bg-primary text-xs font-medium hover:opacity-90 transition-opacity"
              title="New sheet"
            >
              + New
            </button>
          </div>
        </div>

        {/* Tag filter bar */}
        {allTags.length > 0 && (
          <div className="px-2 py-1.5 border-b border-border flex flex-wrap gap-1">
            {allTags.map((tag) => (
              <button
                key={tag}
                onClick={() =>
                  setActiveTags((prev) => {
                    const next = new Set(prev);
                    if (next.has(tag)) next.delete(tag);
                    else next.add(tag);
                    return next;
                  })
                }
                className={`px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors ${
                  activeTags.has(tag)
                    ? "bg-accent text-bg-primary"
                    : "bg-bg-hover text-text-muted hover:text-text-secondary"
                }`}
              >
                {tag}
              </button>
            ))}
          </div>
        )}

        <div className="flex-1 overflow-y-auto py-1">
          {loadingList ? (
            <div className="px-3 py-2 text-xs text-text-muted">Loading…</div>
          ) : visibleSheets.length === 0 ? (
            <div className="px-3 py-3 text-xs text-text-muted leading-relaxed">
              {sheets.length === 0
                ? <>No sheets yet. Click <span className="text-accent">+ New</span> or ask the agent to create one.</>
                : "No sheets match the selected tags."}
            </div>
          ) : (
            visibleSheets.map((s) => {
              const isActive = s.id === activeId;
              return (
                <div
                  key={s.id}
                  className={`group flex flex-col mx-1 px-2 py-1.5 rounded-lg cursor-pointer transition-colors ${
                    isActive
                      ? "bg-bg-hover text-text-primary"
                      : "text-text-muted hover:bg-bg-hover hover:text-text-secondary"
                  }`}
                  onClick={() => {
                    setActiveId(s.id);
                    setActiveName(s.name);
                    updatedAtRef.current = s.updated_at;
                  }}
                >
                  <div className="flex items-center gap-1">
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
                  {s.tags && s.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1 ml-5">
                      {s.tags.map((tag) => (
                        <span
                          key={tag}
                          className="px-1 py-0 rounded text-[9px] bg-bg-primary text-text-muted border border-border"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
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
              {/* 🏷 Tag editor button */}
              <div className="relative">
                <button
                  onClick={() => {
                    setShowTagEditor((v) => !v);
                    setTagInput("");
                  }}
                  className="p-1 rounded-lg text-text-muted hover:text-text-secondary hover:bg-bg-hover transition-colors"
                  title="Edit tags"
                >
                  🏷
                </button>
                {showTagEditor && (
                  <div className="absolute left-0 top-full mt-1 z-20 w-56 rounded-lg border border-border bg-bg-secondary shadow-lg p-2 space-y-1.5">
                    <div className="flex flex-wrap gap-1">
                      {(activeMeta?.tags ?? []).map((tag) => (
                        <span
                          key={tag}
                          className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-bg-hover text-[10px] text-text-secondary"
                        >
                          {tag}
                          <button
                            onClick={() => removeTag(tag)}
                            className="text-text-muted hover:text-red-400 transition-colors ml-0.5"
                            aria-label={`Remove tag ${tag}`}
                          >
                            ×
                          </button>
                        </span>
                      ))}
                    </div>
                    <input
                      value={tagInput}
                      onChange={(e) => setTagInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          addTag(tagInput);
                          setTagInput("");
                        }
                      }}
                      placeholder="Add tag, press Enter"
                      className="w-full px-2 py-1 rounded-md bg-bg-primary border border-border text-xs outline-none focus:border-accent"
                      autoFocus
                    />
                  </div>
                )}
              </div>
              <SaveBadge state={saveState} />
              <div className="ml-auto flex items-center gap-2">
                <button
                  onClick={handleConvertLinks}
                  className="px-2 py-1 rounded-lg border border-border text-text-secondary text-xs font-medium hover:bg-bg-hover transition-colors"
                  title="Convert plain URLs in cells to hyperlinks"
                >
                  Convert URLs to links
                </button>
                <details className="relative">
                  <summary className="list-none cursor-pointer select-none px-2 py-1 rounded-lg border border-border text-text-secondary text-xs font-medium hover:bg-bg-hover transition-colors">
                    Export ▾
                  </summary>
                  <div className="absolute right-0 mt-1 z-10 w-56 rounded-lg border border-border bg-bg-secondary shadow-lg p-2 space-y-1.5">
                    <input
                      type="password"
                      value={exportPassword}
                      onChange={(e) => setExportPassword(e.target.value)}
                      placeholder="Password (optional)"
                      className="w-full px-2 py-1 rounded-md bg-bg-primary border border-border text-xs outline-none focus:border-accent"
                    />
                    <button
                      onClick={() => handleExport("xlsx")}
                      className="block w-full text-left px-2 py-1.5 rounded-md text-xs text-text-secondary hover:bg-bg-hover"
                    >
                      Excel (.xlsx)
                    </button>
                    <button
                      onClick={() => handleExport("csv")}
                      className="block w-full text-left px-2 py-1.5 rounded-md text-xs text-text-secondary hover:bg-bg-hover"
                    >
                      CSV (.csv)
                    </button>
                    <p className="text-[10px] text-text-muted leading-snug px-1">
                      {exportPassword.trim()
                        ? "🔒 Downloads an AES-256 encrypted .zip."
                        : "Set a password to encrypt the download."}
                    </p>
                  </div>
                </details>
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
