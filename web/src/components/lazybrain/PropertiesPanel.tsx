/** Typed form over a note's leading YAML frontmatter.
 *
 *  Renders collapsible. Click a chip / date / checkbox to edit; changes
 *  are serialized straight back into the note content string and handed
 *  to the parent for save. */
import { useMemo, useState } from "react";
import { Calendar, Plus, Tag as TagIcon, Trash2, X } from "lucide-react";
import {
  guessKind,
  parseFrontmatter,
  serializeFrontmatter,
  type FmProps,
  type FmValue,
} from "./frontmatter";

interface Props {
  content: string;
  onChange: (nextContent: string) => void;
  /** When true, show even when frontmatter is empty (lets user add). */
  showEmpty?: boolean;
}

const STATUS_OPTIONS = ["idea", "draft", "active", "paused", "done", "archived"];

export function PropertiesPanel({ content, onChange, showEmpty }: Props) {
  const { props, body, hasFm } = useMemo(
    () => parseFrontmatter(content),
    [content],
  );
  const [open, setOpen] = useState(true);
  const [newKey, setNewKey] = useState("");

  const updateProp = (key: string, value: FmValue) => {
    const next: FmProps = { ...props, [key]: value };
    onChange(serializeFrontmatter(next, body));
  };

  const removeProp = (key: string) => {
    const next = { ...props };
    delete next[key];
    onChange(serializeFrontmatter(next, body));
  };

  const addProp = () => {
    const k = newKey.trim();
    if (!k || k in props) return;
    setNewKey("");
    const kind = guessKind(k, "");
    const seed: FmValue =
      kind === "tags"
        ? []
        : kind === "boolean"
          ? false
          : kind === "number"
            ? 0
            : "";
    onChange(serializeFrontmatter({ ...props, [k]: seed }, body));
  };

  const keys = Object.keys(props);
  if (!hasFm && keys.length === 0 && !showEmpty) return null;

  return (
    <div
      className="rounded-lg border border-border"
      style={{
        background: "rgba(167, 139, 250, 0.04)",
        borderColor: "rgba(167, 139, 250, 0.18)",
        fontFamily: "Inter, system-ui, sans-serif",
      }}
    >
      <div
        onClick={() => setOpen((v) => !v)}
        className="px-3 py-1.5 flex items-center gap-2 cursor-pointer text-[11px] uppercase tracking-wider"
        style={{ color: "#a78bfa", fontWeight: 600 }}
      >
        <span style={{ opacity: 0.7 }}>{open ? "▾" : "▸"}</span>
        <span>Properties</span>
        <span className="text-text-muted normal-case tracking-normal">
          {keys.length} {keys.length === 1 ? "field" : "fields"}
        </span>
      </div>
      {open && (
        <div className="px-3 pb-3 pt-1 flex flex-col gap-2">
          {keys.map((k) => (
            <PropRow
              key={k}
              k={k}
              v={props[k]}
              onChange={(v) => updateProp(k, v)}
              onRemove={() => removeProp(k)}
            />
          ))}
          <div className="flex items-center gap-2 pt-1.5 border-t border-border/40">
            <input
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addProp();
                }
              }}
              placeholder="New property key (e.g. status, due, tags)…"
              className="flex-1 bg-transparent outline-none text-xs text-text-primary placeholder-text-muted"
            />
            <button
              onClick={addProp}
              className="text-[11px] text-accent hover:text-accent-dim flex items-center gap-1"
            >
              <Plus size={12} strokeWidth={2} /> add
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function PropRow({
  k,
  v,
  onChange,
  onRemove,
}: {
  k: string;
  v: FmValue;
  onChange: (v: FmValue) => void;
  onRemove: () => void;
}) {
  const kind = guessKind(k, v);

  return (
    <div className="flex items-center gap-2 group">
      <div className="w-24 shrink-0 text-[11px] text-text-muted truncate" title={k}>
        {k}
      </div>
      <div className="flex-1 min-w-0">
        {kind === "date" ? (
          <div className="flex items-center gap-1">
            <Calendar size={11} className="text-text-muted" />
            <input
              type="date"
              value={typeof v === "string" ? v : ""}
              onChange={(e) => onChange(e.target.value)}
              className="bg-transparent text-xs text-text-primary outline-none"
            />
          </div>
        ) : kind === "boolean" ? (
          <input
            type="checkbox"
            checked={!!v}
            onChange={(e) => onChange(e.target.checked)}
          />
        ) : kind === "number" ? (
          <input
            type="number"
            value={typeof v === "number" ? v : 0}
            onChange={(e) => onChange(Number(e.target.value))}
            className="bg-transparent text-xs text-text-primary outline-none w-24"
          />
        ) : kind === "tags" ? (
          <TagsInput
            value={Array.isArray(v) ? v : []}
            onChange={(arr) => onChange(arr)}
          />
        ) : kind === "status" ? (
          <select
            value={typeof v === "string" ? v : ""}
            onChange={(e) => onChange(e.target.value)}
            className="bg-transparent text-xs text-text-primary outline-none"
          >
            <option value="">—</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        ) : (
          <input
            type="text"
            value={v == null ? "" : String(v)}
            onChange={(e) => onChange(e.target.value)}
            className="bg-transparent text-xs text-text-primary outline-none w-full"
          />
        )}
      </div>
      <button
        onClick={onRemove}
        className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-red-400 transition-opacity"
        title="Remove field"
      >
        <Trash2 size={11} />
      </button>
    </div>
  );
}

function TagsInput({
  value,
  onChange,
}: {
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  return (
    <div className="flex items-center gap-1 flex-wrap">
      <TagIcon size={11} className="text-text-muted" />
      {value.map((t) => (
        <span
          key={t}
          className="inline-flex items-center gap-1 px-1.5 rounded text-[11px]"
          style={{
            background: "rgba(167, 139, 250, 0.14)",
            color: "#c4b5fd",
          }}
        >
          {t}
          <button
            onClick={() => onChange(value.filter((x) => x !== t))}
            className="opacity-60 hover:opacity-100"
          >
            <X size={10} />
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === ",") && draft.trim()) {
            e.preventDefault();
            const t = draft.trim().replace(/[,#]/g, "");
            if (t && !value.includes(t)) onChange([...value, t]);
            setDraft("");
          }
          if (e.key === "Backspace" && !draft && value.length) {
            onChange(value.slice(0, -1));
          }
        }}
        placeholder="+ tag"
        className="bg-transparent text-xs text-text-primary outline-none min-w-[60px]"
      />
    </div>
  );
}
