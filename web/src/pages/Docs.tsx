import { useEffect, useRef, useState } from "react";
import {
  createDoc,
  deleteDoc,
  docExportUrl,
  getDoc,
  listDocs,
  saveDoc,
  type DocMeta,
  type UniverDocSnapshot,
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
  const [loadingList, setLoadingList] = useState(true);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  const [reloadToken, setReloadToken] = useState(0);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const univerRef = useRef<{ dispose: () => void } | null>(null);
  const apiRef = useRef<FUniver | null>(null);
  const dirtyRef = useRef(false);
  const nameRef = useRef("");
  // Lets the ✨ AI popover flush pending edits before the agent reads the doc.
  const flushHandleRef = useRef<() => Promise<void>>(() => Promise.resolve());

  // Keep the latest name available to the autosave closure without
  // re-initialising Univer on every rename.
  useEffect(() => {
    nameRef.current = activeName;
  }, [activeName]);

  async function refreshList(selectId?: string) {
    const rows = await listDocs();
    setDocs(rows);
    setLoadingList(false);
    if (selectId) {
      const found = rows.find((d) => d.id === selectId);
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
      return saveDoc(docId, nameRef.current, snapshot)
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
      const doc = await getDoc(docId);
      if (cancelled || !containerRef.current) return;

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

  async function handleDelete(id: string) {
    if (!window.confirm("Delete this document? This cannot be undone.")) return;
    await deleteDoc(id);
    const remaining = docs.filter((d) => d.id !== id);
    setDocs(remaining);
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
    const snapshot = api.getActiveDocument()?.getSnapshot() as
      | UniverDocSnapshot
      | undefined;
    if (!snapshot) return;
    const name = nameRef.current.trim() || "Untitled doc";
    setSaveState("saving");
    saveDoc(id, name, snapshot)
      .then(() => {
        setSaveState("saved");
        setDocs((prev) => prev.map((d) => (d.id === id ? { ...d, name } : d)));
      })
      .catch(() => setSaveState("error"));
  }

  return (
    <div className="flex h-full bg-bg-primary text-text-primary">
      {/* Doc browser */}
      <aside className="w-60 shrink-0 border-r border-border bg-bg-secondary flex flex-col">
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
          <span className="text-sm font-semibold">Docs</span>
          <button
            onClick={handleNew}
            className="px-2 py-1 rounded-lg bg-accent text-bg-primary text-xs font-medium hover:opacity-90 transition-opacity"
            title="New document"
          >
            + New
          </button>
        </div>

        <div className="flex-1 overflow-y-auto py-1">
          {loadingList ? (
            <div className="px-3 py-2 text-xs text-text-muted">Loading…</div>
          ) : docs.length === 0 ? (
            <div className="px-3 py-3 text-xs text-text-muted leading-relaxed">
              No documents yet. Click <span className="text-accent">+ New</span> or
              ask the agent to create one.
            </div>
          ) : (
            docs.map((d) => {
              const isActive = d.id === activeId;
              return (
                <div
                  key={d.id}
                  className={`group flex items-center gap-1 mx-1 px-2 py-1.5 rounded-lg cursor-pointer transition-colors ${
                    isActive
                      ? "bg-bg-hover text-text-primary"
                      : "text-text-muted hover:bg-bg-hover hover:text-text-secondary"
                  }`}
                  onClick={() => {
                    setActiveId(d.id);
                    setActiveName(d.name);
                  }}
                >
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
                <a
                  href={docExportUrl(activeId, "docx")}
                  className="text-xs text-text-muted hover:text-accent transition-colors"
                  title="Download as .docx"
                >
                  Download .docx
                </a>
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
