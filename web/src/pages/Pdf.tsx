import { useEffect, useMemo, useRef, useState } from "react";
import {
  deletePdf,
  downloadBlob,
  downloadPdfBlob,
  extractPdfText,
  listPdfs,
  patchPdf,
  pdfRawUrl,
  uploadPdf,
  type PdfMeta,
} from "../api";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import DocAiPopover from "../components/DocAiPopover";

// pdf.js worker (Vite + react-pdf 10 + pdfjs-dist 5). The `.mjs` worker exists
// under node_modules/pdfjs-dist/build/ — resolved as a module URL so Vite
// bundles it as an asset and serves it with the right MIME type.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

type ExtractState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "done"; text: string; pages: number }
  | { phase: "error"; message: string };

/**
 * Pdf — view, upload, manage, extract & download PDFs.
 *
 * Honest scope: this is a viewer + file manager. There is NO in-browser PDF
 * editing here — fill / sign / merge are performed by the agent via skills.
 * The layout mirrors Sheets.tsx (left list + main pane, `flex h-full`,
 * `flex-1 min-h-0 overflow-auto`).
 */
export default function Pdf() {
  const [files, setFiles] = useState<PdfMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [numPages, setNumPages] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [extract, setExtract] = useState<ExtractState>({ phase: "idle" });
  const [exportPassword, setExportPassword] = useState("");

  // ── Tag filter state ────────────────────────────────────────────────
  const [activeTags, setActiveTags] = useState<Set<string>>(new Set());
  // ── Tag editor state for the open PDF ───────────────────────────────
  const [showTagEditor, setShowTagEditor] = useState(false);
  const [tagInput, setTagInput] = useState("");

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const activeFile = files.find((f) => f.id === activeId) ?? null;

  // Derived: all distinct tags across all PDFs for the filter bar
  const allTags = Array.from(
    new Set(files.flatMap((f) => f.tags ?? []))
  ).sort();

  // PDFs filtered by active tag selection
  const visibleFiles =
    activeTags.size === 0
      ? files
      : files.filter((f) => (f.tags ?? []).some((t) => activeTags.has(t)));

  // Memoize the `file` source — react-pdf uses `===` to detect changes, so a
  // fresh string each render would re-fetch the whole document every time.
  const fileSource = useMemo(
    () => (activeId ? pdfRawUrl(activeId) : null),
    [activeId],
  );

  async function refreshList(selectId?: string) {
    const rows = await listPdfs();
    setFiles(rows);
    setLoadingList(false);
    if (selectId) {
      const found = rows.find((f) => f.id === selectId);
      if (found) setActiveId(found.id);
    } else if (rows.length && !activeId) {
      setActiveId(rows[0].id);
    }
  }

  useEffect(() => {
    refreshList().catch(() => setLoadingList(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reset per-document viewer state whenever the selection changes.
  useEffect(() => {
    setNumPages(null);
    setLoadError(null);
    setExtract({ phase: "idle" });
  }, [activeId]);

  async function handleUploadClick() {
    fileInputRef.current?.click();
  }

  async function handleFileChosen(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-uploading the same file later
    if (!file) return;
    setUploading(true);
    try {
      const meta = await uploadPdf(file);
      await refreshList(meta.id);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm("Delete this PDF? This cannot be undone.")) return;
    await deletePdf(id);
    const remaining = files.filter((f) => f.id !== id);
    setFiles(remaining);
    if (activeId === id) setActiveId(remaining[0]?.id ?? null);
  }

  async function handleDownload() {
    if (!activeId) return;
    const pw = exportPassword.trim();
    try {
      const blob = await downloadPdfBlob(activeId, pw || null);
      const stem = (activeFile?.name || "document").replace(/\.pdf$/i, "");
      downloadBlob(blob, pw ? `${stem}.zip` : `${stem}.pdf`);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Download failed");
    }
  }

  async function handleExtract() {
    if (!activeId) return;
    setExtract({ phase: "loading" });
    try {
      const res = await extractPdfText(activeId);
      setExtract({ phase: "done", text: res.text, pages: res.pages });
    } catch (err) {
      setExtract({
        phase: "error",
        message: err instanceof Error ? err.message : "Extraction failed",
      });
    }
  }

  // ── Tag editor helpers ──────────────────────────────────────────────
  function addTag(tag: string) {
    const trimmed = tag.trim();
    if (!trimmed || !activeId) return;
    const current = activeFile?.tags ?? [];
    if (current.includes(trimmed)) return;
    applyTags(activeId, [...current, trimmed]);
  }

  function removeTag(tag: string) {
    if (!activeId) return;
    applyTags(activeId, (activeFile?.tags ?? []).filter((t) => t !== tag));
  }

  function applyTags(id: string, newTags: string[]) {
    patchPdf(id, { tags: newTags })
      .then((updated) => {
        setFiles((prev) =>
          prev.map((f) => (f.id === id ? { ...f, tags: updated.tags ?? newTags } : f))
        );
      })
      .catch((err) =>
        window.alert(err instanceof Error ? err.message : "Failed to update tags")
      );
  }

  return (
    <div className="flex h-full bg-bg-primary text-text-primary">
      {/* PDF browser */}
      <aside className="w-60 shrink-0 border-r border-border bg-bg-secondary flex flex-col">
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
          <span className="text-sm font-semibold">PDFs</span>
          <button
            onClick={handleUploadClick}
            disabled={uploading}
            className="px-2 py-1 rounded-lg bg-accent text-bg-primary text-xs font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
            title="Upload a PDF"
          >
            {uploading ? "Uploading…" : "↑ Upload"}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf"
            className="hidden"
            onChange={handleFileChosen}
          />
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
          ) : visibleFiles.length === 0 ? (
            <div className="px-3 py-3 text-xs text-text-muted leading-relaxed">
              {files.length === 0
                ? <>No PDFs yet. Click <span className="text-accent">↑ Upload</span> or ask the agent to create one.</>
                : "No PDFs match the selected tags."}
            </div>
          ) : (
            visibleFiles.map((f) => {
              const isActive = f.id === activeId;
              return (
                <div
                  key={f.id}
                  className={`group flex flex-col mx-1 px-2 py-1.5 rounded-lg cursor-pointer transition-colors ${
                    isActive
                      ? "bg-bg-hover text-text-primary"
                      : "text-text-muted hover:bg-bg-hover hover:text-text-secondary"
                  }`}
                  onClick={() => setActiveId(f.id)}
                >
                  <div className="flex items-center gap-1">
                    <svg
                      width="16" height="16" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
                      strokeLinejoin="round" className="shrink-0"
                    >
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                      <path d="M14 2v6h6" />
                    </svg>
                    <span className="text-sm truncate flex-1">{f.name}</span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(f.id);
                      }}
                      className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-red-400 transition-opacity"
                      title="Delete PDF"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                        <path d="M18 6 6 18M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                  {f.tags && f.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1 ml-5">
                      {f.tags.map((tag) => (
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

      {/* Viewer */}
      <main className="flex-1 min-w-0 flex flex-col">
        {activeId && activeFile ? (
          <>
            {/* Top strip: filename, page count, tags, download, extract */}
            <div className="shrink-0 flex items-center gap-3 px-4 py-2 border-b border-border">
              <span className="text-sm font-medium truncate min-w-0">
                {activeFile.name}
              </span>
              <span className="text-xs text-text-muted shrink-0">
                {numPages
                  ? `${numPages} page${numPages === 1 ? "" : "s"}`
                  : activeFile.pages
                    ? `${activeFile.pages} pages`
                    : ""}
              </span>
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
                      {(activeFile.tags ?? []).map((tag) => (
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
              <div className="ml-auto flex items-center gap-3 shrink-0">
                <DocAiPopover
                  kind="pdf"
                  docId={activeId}
                  docName={activeFile.name}
                  onPdfApplied={(newId) => {
                    // PDF ops create a new file — switch the viewer to it.
                    refreshList(newId ?? activeId).catch(() => {});
                  }}
                />
                <button
                  onClick={handleExtract}
                  disabled={extract.phase === "loading"}
                  className="text-xs text-text-muted hover:text-accent transition-colors disabled:opacity-50"
                  title="Extract text from this PDF"
                >
                  {extract.phase === "loading" ? "Extracting…" : "Extract text"}
                </button>
                <details className="relative">
                  <summary className="list-none cursor-pointer select-none text-xs text-text-muted hover:text-accent transition-colors">
                    Download ▾
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
                      onClick={handleDownload}
                      className="block w-full text-left px-2 py-1.5 rounded-md text-xs text-text-secondary hover:bg-bg-hover"
                    >
                      Download PDF
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

            {/* Page column — render all pages in a smooth scroll list */}
            <div className="flex-1 min-h-0 overflow-auto bg-bg-primary px-4 py-4">
              {loadError ? (
                <div className="flex h-full items-center justify-center text-sm text-red-400">
                  {loadError}
                </div>
              ) : (
                <Document
                  file={fileSource}
                  onLoadSuccess={(pdf) => {
                    setNumPages(pdf.numPages);
                    setLoadError(null);
                  }}
                  onLoadError={(err) =>
                    setLoadError(err?.message || "Failed to load PDF.")
                  }
                  loading={
                    <div className="text-sm text-text-muted py-8 text-center">
                      Loading PDF…
                    </div>
                  }
                  error={
                    <div className="text-sm text-red-400 py-8 text-center">
                      Failed to load PDF.
                    </div>
                  }
                  className="flex flex-col items-center gap-4"
                >
                  {Array.from({ length: numPages ?? 0 }, (_, i) => (
                    <Page
                      key={`page-${i + 1}`}
                      pageNumber={i + 1}
                      width={820}
                      renderTextLayer
                      renderAnnotationLayer
                      className="shadow-lg"
                      loading={
                        <div className="text-xs text-text-muted py-4">
                          Rendering page {i + 1}…
                        </div>
                      }
                    />
                  ))}
                </Document>
              )}
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
            {loadingList ? "Loading…" : "Select a PDF or upload one."}
          </div>
        )}
      </main>

      {/* Extract-text modal */}
      {(extract.phase === "done" || extract.phase === "error") && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => setExtract({ phase: "idle" })}
        >
          <div
            className="w-full max-w-2xl max-h-[80vh] flex flex-col rounded-xl border border-border bg-bg-secondary shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <span className="text-sm font-semibold">
                Extracted text
                {extract.phase === "done" && (
                  <span className="ml-2 text-xs font-normal text-text-muted">
                    {extract.pages} page{extract.pages === 1 ? "" : "s"}
                  </span>
                )}
              </span>
              <button
                onClick={() => setExtract({ phase: "idle" })}
                className="p-1 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-primary transition-colors"
                title="Close"
                aria-label="Close"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="flex-1 min-h-0 overflow-auto p-4">
              {extract.phase === "error" ? (
                <p className="text-sm text-red-400">{extract.message}</p>
              ) : extract.text.trim() ? (
                <pre className="text-xs text-text-secondary whitespace-pre-wrap break-words font-mono leading-relaxed">
                  {extract.text}
                </pre>
              ) : (
                <p className="text-sm text-text-muted">
                  No extractable text found (the PDF may be image-only / scanned).
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
