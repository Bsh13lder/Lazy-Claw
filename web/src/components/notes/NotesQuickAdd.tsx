import { useEffect, useMemo, useRef, useState } from "react";
import { createLazyBrainNote } from "../../api";
import {
  KIND_META,
  detectTriggerPrefix,
  deriveTitle,
  extractInlineTags,
  type NoteKind,
} from "./noteHelpers";

type Props = {
  onCreated: () => void;
};

const PLACEHOLDER_LINES = [
  "note: a thought that should not be lost",
  "idea: redesign the onboarding flow without modal soup",
  "remember: mom's blood pressure 120 over 80",
  "memo: standup is at 10:00 sharp",
];

/**
 * Capture bar — the only AI-free input on this page. Detects a leading
 * `note:` / `idea:` / `remember:` prefix and posts directly to LazyBrain
 * with the matching `kind/*` tag. Inline `#hashtags` come along for the ride.
 *
 *   ┌────────────────────────────────────────────────────────────┐
 *   │ note: buy organic eggs at the market #shopping     ↵        │
 *   └────────────────────────────────────────────────────────────┘
 *      Idea · #shopping                                  ⏎ to save
 */
export function NotesQuickAdd({ onCreated }: Props) {
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Cycle the placeholder so first-time users see all the trigger forms.
  const [placeholderIdx, setPlaceholderIdx] = useState(0);
  useEffect(() => {
    if (text.length > 0) return;
    const id = window.setInterval(() => {
      setPlaceholderIdx((i) => (i + 1) % PLACEHOLDER_LINES.length);
    }, 4500);
    return () => window.clearInterval(id);
  }, [text]);

  const parsed = useMemo(() => {
    const t = detectTriggerPrefix(text);
    const inline = extractInlineTags(text);
    if (t) {
      return { kind: t.kind as NoteKind, content: t.content, tags: extractInlineTags(t.content) };
    }
    return text.trim().length > 0
      ? { kind: "note" as NoteKind, content: text.trim(), tags: inline }
      : null;
  }, [text]);

  const submit = async () => {
    if (!parsed || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const tags = [`kind/${parsed.kind}`, "owner/user", "source/web", ...parsed.tags];
      await createLazyBrainNote({
        content: parsed.content,
        title: deriveTitle(parsed.content),
        tags,
      });
      setText("");
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
    } finally {
      setSubmitting(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter saves; Shift+Enter inserts a newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  };

  // Auto-resize the textarea so multi-line notes feel natural.
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [text]);

  const meta = parsed ? KIND_META[parsed.kind] : null;

  return (
    <div className="rounded-2xl border border-border bg-bg-secondary/60 p-3 space-y-2 transition-colors focus-within:border-accent/50 focus-within:bg-bg-secondary/90">
      <div className="flex items-start gap-2">
        <span className="mt-2 text-[18px] leading-none opacity-50">✎</span>
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder={PLACEHOLDER_LINES[placeholderIdx]}
          className="flex-1 resize-none bg-transparent border-0 outline-none text-[14px] text-text-primary placeholder:text-text-placeholder leading-relaxed py-1"
          style={{ fontFamily: "'JetBrains Mono', monospace" }}
          spellCheck
          autoFocus
        />
      </div>

      {/* Ghost preview row — shows what's about to be saved. */}
      <div className="flex items-center gap-2 pl-7 text-[11px] text-text-muted">
        {parsed ? (
          <>
            <span
              className="px-2 py-0.5 rounded-full uppercase tracking-wider text-[9px] font-medium border"
              style={{
                background: meta!.chipBg,
                borderColor: meta!.chipBorder,
                color: meta!.chipText,
              }}
            >
              {meta!.label}
            </span>
            {parsed.tags.map((t) => (
              <span
                key={t}
                className="px-1.5 py-0.5 rounded bg-bg-tertiary text-text-secondary"
              >
                #{t}
              </span>
            ))}
            <span className="ml-auto flex items-center gap-2">
              {error ? <span className="text-error">{error}</span> : null}
              <kbd className="px-1.5 py-0.5 rounded border border-border-light text-[9px]">⏎</kbd>
              <span className="opacity-60">to save · </span>
              <kbd className="px-1.5 py-0.5 rounded border border-border-light text-[9px]">⇧⏎</kbd>
              <span className="opacity-60">newline</span>
            </span>
          </>
        ) : (
          <>
            <span className="opacity-50">
              Type <code className="text-text-secondary">note:</code>,
              <code className="text-text-secondary"> idea:</code>,
              <code className="text-text-secondary"> remember:</code> — or send to Telegram with
              <code className="text-text-secondary"> /note</code>.
            </span>
          </>
        )}
      </div>
    </div>
  );
}
