/** Tiny modal listing LazyBrain's global keyboard shortcuts.
 *
 *  Opened via ⌘/ or the "Keyboard shortcuts" palette action. Pure
 *  presentation — the list mirrors the bindings in LazyBrain.tsx's
 *  global keydown handler. Container styling mirrors AIResultModal. */
import { useEffect } from "react";
import { Command, X } from "./icons";
import { motion, AnimatePresence } from "framer-motion";

interface Props {
  open: boolean;
  onClose: () => void;
}

const SHORTCUTS: Array<{ keys: string; label: string }> = [
  { keys: "⌘K", label: "Command palette" },
  { keys: "⌘O", label: "Quick switcher" },
  { keys: "⌘⇧F", label: "Focus search" },
  { keys: "⌘N", label: "New note" },
  { keys: "⌘S", label: "Save current page" },
  { keys: "⌘/", label: "Keyboard shortcuts" },
  { keys: "Esc", label: "Close / exit graph" },
];

export function ShortcutsModal({ open, onClose }: Props) {
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-[70] flex items-start justify-center pt-[14vh]"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.14 }}
          style={{
            background: "rgba(10,8,18,0.6)",
            backdropFilter: "blur(6px)",
            WebkitBackdropFilter: "blur(6px)",
          }}
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) onClose();
          }}
        >
          <motion.div
            className="w-[min(360px,94vw)] rounded-xl overflow-hidden flex flex-col"
            initial={{ opacity: 0, scale: 0.96, y: -6 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: -4 }}
            transition={{ type: "spring", stiffness: 420, damping: 32, mass: 0.7 }}
            style={{
              background: "rgba(30,27,43,0.98)",
              border: "1px solid rgba(16, 185, 129, 0.22)",
              boxShadow: "0 32px 64px -12px rgba(0,0,0,0.65)",
              fontFamily: "Inter, system-ui, sans-serif",
            }}
          >
            <div
              className="flex items-center gap-2 px-5 py-3"
              style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}
            >
              <Command size={16} strokeWidth={1.75} color="#10b981" />
              <div className="text-sm font-semibold text-text-primary truncate">
                Keyboard shortcuts
              </div>
              <button
                onClick={onClose}
                className="ml-auto text-text-muted hover:text-text-primary"
              >
                <X size={16} strokeWidth={1.75} />
              </button>
            </div>
            <div className="px-5 py-4 flex flex-col gap-2">
              {SHORTCUTS.map((s) => (
                <div key={s.keys} className="flex items-center justify-between gap-3">
                  <span className="text-sm text-text-secondary">{s.label}</span>
                  <kbd className="px-1.5 py-0.5 rounded bg-bg-hover border border-border font-mono text-[10px] text-text-muted">
                    {s.keys}
                  </kbd>
                </div>
              ))}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
