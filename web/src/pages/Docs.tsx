import { useEffect, useRef, useState } from "react";
import {
  createDoc,
  deleteDoc,
  downloadBlob,
  exportDocBlob,
  getDoc,
  listDocs,
  saveDoc,
  uploadDoc,
  type DocMeta,
  type UniverDocSnapshot,
  ConflictError,
} from "../api";
import {
  createUniver,
  defaultTheme,
  LocaleType,
  mergeLocales,
  type FUniver,
} from "@univerjs/presets";
import { UniverDocsCorePreset } from "@univerjs/preset-docs-core";
import UniverPresetDocsCoreEnUS from "@univerjs/preset-docs-core/locales/en-US";
import "@univerjs/preset-docs-core/lib/index.css";
import { UniverDocsHyperLinkPreset } from "@univerjs/preset-docs-hyper-link";
import UniverPresetDocsHyperLinkEnUS from "@univerjs/preset-docs-hyper-link/locales/en-US";
import "@univerjs/preset-docs-hyper-link/lib/index.css";
import DocAiPopover from "../components/DocAiPopover";

type SaveState = "idle" | "saving" | "saved" | "error";

const AUTOSAVE_MS = 800;

/**
 * Docs — private encrypted word-processor documents backed by the embedded
 * Univer Docs editor.
 *
 * The canonical document is Univer's `IDocumentData` snapshot, persisted
 * verbatim (AES-256-GCM) via `/api/docs`. We mount a fresh Univer instance per
 * selected doc, autosave the snapshot on edits (debounced), and dispose on
 * switch/unmount — flushing any pending save first so no edit is lost crossing
 * the debounce window.
 *
 * This is the Docs twin of Sheets.tsx. The Univer differences are isolated:
 *   - mount with `UniverDocsCorePreset` (vs sheets-core);
 *   - create the doc with `univerAPI.createUniverDoc(...)` (vs createWorkbook);
 *   - read the snapshot with `getActiveDocument()?.getSnapshot()` (vs wb.save()).
 *
 * Autosave event: the installed `@univerjs/presets` typings expose no
 * `Event.DocChanged` member (only DocCreated/DocDisposed live on the Docs
 * facade). We therefore subscribe to `Event.CommandExecuted`, which fires on
 * every editing command — the same generic command channel Univer uses for doc
 * mutations — and debounce a save off it.
 */
export default function Docs() {
  const [docs, setDocs] = useState<DocMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [activeName, setActiveName] = useState("");
  const [exportPassword, setExportPassword] = useState("");
  const [loadingList, setLoadingList] = useState(true);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  // ── Tag filter state ────────────────────────────────────────────────
  const [activeTags, setActiveTags] = useState<Set<string>>(new Set());
  // ── Tag editor state for the open doc ───────────────────────────────
  const [showTagEditor, setShowTagEditor] = useState(false);
  const [tagInput, setTagInput] = useState("");

  const [reloadToken, setReloadToken] = useState(0);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const univerRef = useRef<{ dispose: () => void } | null>(null);
  const apiRef = useRef<FUniver | null>(null);
  const dirtyRef = useRef(false);
  const nameRef = useRef("");
  const updatedAtRef = useRef<string | null>(null);
  // Lets the ✨ AI popover flush pending edits before the agent reads the doc.
  const flushHandleRef = useRef<() => Promise<void>>(() => Promise.resolve());

  // Keep the latest name available to the autosave closure without
  // re-initialising Univer on every rename.
  useEffect(() => {
    nameRef.current = activeName;
  }, [activeName]);

  // Derived: all distinct tags across all docs for the filter bar
  const allTags = Array.from(
    new Set(docs.flatMap((d) => d.tags ?? []))
  ).sort();

  // Docs filtered by active tag selection
  const visibleDocs =
    activeTags.size === 0
      ? docs
      : docs.filter((d) => (d.tags ?? []).some((t) => activeTags.has(t)));

  // Current active doc meta (for tag editor)
  const activeMeta = docs.find((d) => d.id === activeId) ?? null;

  async function refreshList(selectId?: string) {
    const rows = await listDocs();
    setDocs(rows);
    setLoadingList(false);
    if (selectId) {
      const found = rows.find((d) => d.id === selectId);
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

  // ── Univer lifecycle, keyed on the selected doc ──────────────────────
  useEffect(() => {
    const container = containerRef.current;
    if (!activeId || !container) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const docId = activeId; // capture for the closure / cleanup

    const flush = (): Promise<void> => {
      const api = apiRef.current;
      if (!api || !dirtyRef.current) return Promise.resolve();
      const snapshot = api.getActiveDocument()?.getSnapshot() as
        | UniverDocSnapshot
        | undefined;
      if (!snapshot) return Promise.resolve();
      dirtyRef.current = false;
      setSaveState("saving");
      return saveDoc(docId, nameRef.current, snapshot, updatedAtRef.current)
        .then((row) => {
          updatedAtRef.current = row.updated_at;
          if (!cancelled) setSaveState("saved");
        })
        .catch((err) => {
          if (cancelled) return;
          if (err instanceof ConflictError) {
            const reload = window.confirm(
              "This document changed on the server. Reload the latest version? (Cancel keeps your copy and overwrites.)"
            );
            if (reload) {
              setReloadToken((t) => t + 1);
            } else {
              dirtyRef.current = true;
              return saveDoc(docId, nameRef.current, snapshot, null)
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
      const doc = await getDoc(docId);
      if (cancelled || !containerRef.current) return;

      updatedAtRef.current = doc.updated_at;

      const { univer, univerAPI } = createUniver({
        locale: LocaleType.EN_US,
        locales: {
          [LocaleType.EN_US]: mergeLocales(
            UniverPresetDocsCoreEnUS,
            UniverPresetDocsHyperLinkEnUS,
          ),
        },
        theme: defaultTheme,
        presets: [
          UniverDocsCorePreset({ container }),
          UniverDocsHyperLinkPreset(),
        ],
      });
      univerRef.current = univer;
      apiRef.current = univerAPI;
      dirtyRef.current = false;

      univerAPI.createUniverDoc(
        doc.payload as unknown as Parameters<FUniver["createUniverDoc"]>[0],
      );
      // No Event.DocChanged in the installed typings — CommandExecuted is the
      // generic editing-command channel and fires on every doc mutation.
      univerAPI.addEvent(univerAPI.Event.CommandExecuted, scheduleSave);
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
    const created = await createDoc("Untitled doc");
    await refreshList(created.id);
  }

  async function handleImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-importing the same file
    if (!file) return;
    try {
      const created = await uploadDoc(file);
      await refreshList(created.id);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Import failed");
    }
  }

  async function handleExport(format: "docx" | "pdf") {
    if (!activeId) return;
    const pw = exportPassword.trim();
    try {
      const blob = await exportDocBlob(activeId, format, pw || null);
      const ext = pw ? "zip" : format;
      downloadBlob(blob, `${activeName || "document"}.${ext}`);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Export failed");
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm("Delete this document? This cannot be undone.")) return;
    await deleteDoc(id);
    const remaining = docs.filter((d) => d.id !== id);
    setDocs(remaining);
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
    const snapshot = api.getActiveDocument()?.getSnapshot() as
      | UniverDocSnapshot
      | undefined;
    if (!snapshot) return;
    const name = nameRef.current.trim() || "Untitled doc";
    setSaveState("saving");
    saveDoc(id, name, snapshot, updatedAtRef.current)
      .then((row) => {
        updatedAtRef.current = row.updated_at;
        setSaveState("saved");
        setDocs((prev) => prev.map((d) => (d.id === id ? { ...d, name } : d)));
      })
      .catch(() => setSaveState("error"));
  }

  // ── Tag editor helpers ──────────────────────────────────────────────
  function addTag(tag: string) {
    const trimmed = tag.trim();
    if (!trimmed || !activeId) return;
    const current = activeMeta?.tags ?? [];
    if (current.includes(trimmed)) return;
    applyTags([...current, trimmed]);
  }

  function removeTag(tag: string) {
    if (!activeId) return;
    applyTags((activeMeta?.tags ?? []).filter((t) => t !== tag));
  }

  function applyTags(newTags: string[]) {
    const id = activeId;
    const api = apiRef.current;
    if (!id) return;

    let snapshot: UniverDocSnapshot | null = null;
    if (api) {
      snapshot = (api.getActiveDocument()?.getSnapshot() as UniverDocSnapshot | undefined) ?? null;
    }

    if (snapshot) {
      dirtyRef.current = false;
      setSaveState("saving");
      saveDoc(id, nameRef.current, snapshot, updatedAtRef.current, newTags)
        .then((row) => {
          updatedAtRef.current = row.updated_at;
          setSaveState("saved");
          setDocs((prev) =>
            prev.map((d) => (d.id === id ? { ...d, tags: row.tags ?? newTags } : d))
          );
        })
        .catch(() => setSaveState("error"));
    }
  }

  return (
    <div className="flex h-full bg-bg-primary text-text-primary">
      {/* Doc browser */}
      <aside className="w-60 shrink-0 border-r border-border bg-bg-secondary flex flex-col">
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
          <span className="text-sm font-semibold">Docs</span>
          <div className="flex items-center gap-1.5">
            <label
              className="px-2 py-1 rounded-lg border border-border text-text-secondary text-xs font-medium hover:bg-bg-hover cursor-pointer transition-colors"
              title="Import a .docx document"
            >
              Import
              <input
                type="file"
                accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                className="hidden"
                onChange={handleImportFile}
              />
            </label>
            <button
              onClick={handleNew}
              className="px-2 py-1 rounded-lg bg-accent text-bg-primary text-xs font-medium hover:opacity-90 transition-opacity"
              title="New document"
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
          ) : visibleDocs.length === 0 ? (
            <div className="px-3 py-3 text-xs text-text-muted leading-relaxed">
              {docs.length === 0
                ? <>No documents yet. Click <span className="text-accent">+ New</span> or ask the agent to create one.</>
                : "No documents match the selected tags."}
            </div>
          ) : (
            visibleDocs.map((d) => {
              const isActive = d.id === activeId;
              return (
                <div
                  key={d.id}
                  className={`group flex flex-col mx-1 px-2 py-1.5 rounded-lg cursor-pointer transition-colors ${
                    isActive
                      ? "bg-bg-hover text-text-primary"
                      : "text-text-muted hover:bg-bg-hover hover:text-text-secondary"
                  }`}
                  onClick={() => {
                    setActiveId(d.id);
                    setActiveName(d.name);
                    updatedAtRef.current = d.updated_at;
                  }}
                >
                  <div className="flex items-center gap-1">
                    <svg
                      width="16" height="16" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"
                      className="shrink-0"
                    >
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                      <path d="M14 2v6h6" />
                      <line x1="8" y1="13" x2="16" y2="13" />
                      <line x1="8" y1="17" x2="16" y2="17" />
                    </svg>
                    <span className="text-sm truncate flex-1">{d.name}</span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(d.id);
                      }}
                      className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-red-400 transition-opacity"
                      title="Delete document"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                        <path d="M18 6 6 18M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                  {d.tags && d.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1 ml-5">
                      {d.tags.map((tag) => (
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
                aria-label="Document name"
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
              <div className="ml-auto flex items-center gap-3">
                <DocAiPopover
                  kind="docs"
                  docId={activeId}
                  docName={activeName}
                  beforeSubmit={() => flushHandleRef.current()}
                  onReload={() => {
                    // The AI saved the doc server-side; discard any local dirty
                    // state and remount from the fresh snapshot.
                    dirtyRef.current = false;
                    setReloadToken((t) => t + 1);
                  }}
                />
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
                      onClick={() => handleExport("docx")}
                      className="block w-full text-left px-2 py-1.5 rounded-md text-xs text-text-secondary hover:bg-bg-hover"
                    >
                      Word (.docx)
                    </button>
                    <button
                      onClick={() => handleExport("pdf")}
                      className="block w-full text-left px-2 py-1.5 rounded-md text-xs text-text-secondary hover:bg-bg-hover"
                    >
                      PDF (.pdf)
                    </button>
                    <p className="text-[10px] text-text-muted leading-snug px-1">
                      {exportPassword.trim()
                        ? "🔒 Downloads an AES-256 encrypted .zip."
                        : "Set a password to encrypt the download."}
                    </p>
                  </div>
                </details>
              </div>
            </div>
            {/* Univer mounts here — needs a real pixel height */}
            <div ref={containerRef} className="flex-1 min-h-0" />
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
            {loadingList ? "Loading…" : "Select a document or create a new one."}
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
