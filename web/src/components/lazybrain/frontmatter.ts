/** Minimal YAML-ish frontmatter parser + serializer for LazyBrain notes.
 *
 *  Supports only the subset an Obsidian user would actually write:
 *   - leading `---\n … \n---\n` delimiter
 *   - one key per line: `key: value`
 *   - arrays in flow form: `tags: [a, b, c]`
 *   - arrays in block form: lines starting with `- item`
 *   - boolean literals (true/false), null, numbers, and strings
 *
 *  Not a real YAML parser. Good enough for typed-form editing.
 */
export type FmValue = string | number | boolean | null | string[];
export type FmProps = Record<string, FmValue>;

const DELIM = "---";

export interface FrontmatterSplit {
  props: FmProps;
  body: string;
  /** True when the content actually started with a `---` block. */
  hasFm: boolean;
}

export function parseFrontmatter(content: string): FrontmatterSplit {
  if (!content) return { props: {}, body: content, hasFm: false };
  const lines = content.split("\n");
  if (lines[0].trim() !== DELIM)
    return { props: {}, body: content, hasFm: false };

  const propLines: string[] = [];
  let closeIdx = -1;
  for (let i = 1; i < lines.length; i++) {
    if (lines[i].trim() === DELIM) {
      closeIdx = i;
      break;
    }
    propLines.push(lines[i]);
  }
  if (closeIdx === -1) return { props: {}, body: content, hasFm: false };

  const props = parseProps(propLines);
  const body = lines.slice(closeIdx + 1).join("\n");
  return { props, body: body.startsWith("\n") ? body.slice(1) : body, hasFm: true };
}

function parseScalar(raw: string): FmValue {
  const s = raw.trim();
  if (s === "") return "";
  if (s === "null" || s === "~") return null;
  if (s === "true") return true;
  if (s === "false") return false;
  if (/^-?\d+(\.\d+)?$/.test(s)) return Number(s);
  // strip surrounding quotes
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'")))
    return s.slice(1, -1);
  return s;
}

function parseProps(lines: string[]): FmProps {
  const out: FmProps = {};
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const kv = /^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$/.exec(line);
    if (!kv) {
      i++;
      continue;
    }
    const key = kv[1];
    const rest = kv[2];

    // Block-style list: empty value + indented `-` lines below.
    if (rest.trim() === "") {
      const items: string[] = [];
      let j = i + 1;
      while (j < lines.length) {
        const item = /^\s*-\s+(.*)$/.exec(lines[j]);
        if (!item) break;
        const parsed = parseScalar(item[1]);
        items.push(typeof parsed === "string" ? parsed : String(parsed));
        j++;
      }
      out[key] = items;
      i = j;
      continue;
    }

    // Flow-style list: `[a, b, c]`.
    if (rest.trim().startsWith("[") && rest.trim().endsWith("]")) {
      const inner = rest.trim().slice(1, -1);
      out[key] = inner
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
        .map((s) => {
          const v = parseScalar(s);
          return typeof v === "string" ? v : String(v);
        });
      i++;
      continue;
    }

    out[key] = parseScalar(rest);
    i++;
  }
  return out;
}

export function serializeFrontmatter(props: FmProps, body: string): string {
  const keys = Object.keys(props);
  if (keys.length === 0) return body;
  const lines: string[] = [DELIM];
  for (const k of keys) {
    const v = props[k];
    if (Array.isArray(v)) {
      // Prefer flow if short, block if 4+.
      if (v.length <= 3 && v.every((x) => !x.includes(","))) {
        lines.push(`${k}: [${v.join(", ")}]`);
      } else {
        lines.push(`${k}:`);
        for (const item of v) lines.push(`  - ${item}`);
      }
    } else if (v === null) {
      lines.push(`${k}: null`);
    } else if (typeof v === "boolean") {
      lines.push(`${k}: ${v ? "true" : "false"}`);
    } else if (typeof v === "number") {
      lines.push(`${k}: ${v}`);
    } else {
      const s = String(v);
      lines.push(
        /[:#[\]]/.test(s) ? `${k}: "${s.replace(/"/g, '\\"')}"` : `${k}: ${s}`,
      );
    }
  }
  lines.push(DELIM, "");
  return lines.join("\n") + body;
}

/** Guess the best-fit type for a property value, used by the UI
 *  to render a typed input (date picker, checkbox, tag chips, …). */
export function guessKind(
  key: string,
  value: FmValue,
): "date" | "boolean" | "number" | "tags" | "status" | "string" {
  if (Array.isArray(value)) return "tags";
  const low = key.toLowerCase();
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return "number";
  if (low === "status") return "status";
  if (low.endsWith("date") || low === "due" || low === "deadline") return "date";
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value))
    return "date";
  return "string";
}
