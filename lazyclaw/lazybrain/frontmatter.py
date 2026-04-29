"""Minimal YAML-ish frontmatter parser + serializer for LazyBrain notes.

Mirrors the contract of ``web/src/components/lazybrain/frontmatter.ts`` so
notes round-trip cleanly between the React UI panel and Python writers
(``skill_lesson``, ``lesson_verifier``, ``auto_capture``).

Supports only the subset an Obsidian user would actually write:
  - leading ``---\\n … \\n---\\n`` delimiter
  - one key per line: ``key: value``
  - arrays in flow form: ``tags: [a, b, c]``
  - arrays in block form: lines starting with ``- item``
  - boolean literals (true/false), null, integers, floats, and strings

Not a real YAML parser — `pyyaml` would be overkill for the typed-form
subset the UI actually emits, and pulling it in for one feature is the
wrong tradeoff.
"""
from __future__ import annotations

import re
from typing import Mapping, Union

FmValue = Union[str, int, float, bool, None, list[str]]
FmProps = dict[str, FmValue]

_DELIM = "---"
_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")
_BLOCK_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _parse_scalar(raw: str) -> FmValue:
    s = raw.strip()
    if s == "":
        return ""
    if s in ("null", "~"):
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if _NUMERIC_RE.match(s):
        return float(s) if "." in s else int(s)
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        return s[1:-1]
    return s


def _parse_props(lines: list[str]) -> FmProps:
    out: FmProps = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        kv = _KV_RE.match(line)
        if not kv:
            i += 1
            continue
        key = kv.group(1)
        rest = kv.group(2)

        # Block-style list: empty value + indented `-` lines below.
        if rest.strip() == "":
            items: list[str] = []
            j = i + 1
            while j < len(lines):
                item = _BLOCK_ITEM_RE.match(lines[j])
                if not item:
                    break
                parsed = _parse_scalar(item.group(1))
                items.append(parsed if isinstance(parsed, str) else str(parsed))
                j += 1
            if items:
                out[key] = items
                i = j
                continue
            # Empty value with no list rows — keep as empty string.
            out[key] = ""
            i += 1
            continue

        # Flow-style list: `[a, b, c]`.
        rs = rest.strip()
        if rs.startswith("[") and rs.endswith("]"):
            inner = rs[1:-1]
            chunks = [c.strip() for c in inner.split(",") if c.strip()]
            out[key] = [
                v if isinstance(v, str) else str(v)
                for v in (_parse_scalar(c) for c in chunks)
            ]
            i += 1
            continue

        out[key] = _parse_scalar(rest)
        i += 1
    return out


def parse_frontmatter(content: str) -> tuple[FmProps, str, bool]:
    """Split ``---``-fenced YAML props from the body.

    Returns ``(props, body, has_fm)``. ``has_fm`` is True only when the
    content actually started with a ``---`` block AND its closing fence
    was found. Malformed/half-open blocks fall through as no-frontmatter.
    """
    if not content:
        return {}, content, False
    lines = content.split("\n")
    if not lines or lines[0].strip() != _DELIM:
        return {}, content, False

    prop_lines: list[str] = []
    close_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == _DELIM:
            close_idx = i
            break
        prop_lines.append(lines[i])
    if close_idx == -1:
        return {}, content, False

    props = _parse_props(prop_lines)
    body = "\n".join(lines[close_idx + 1:])
    if body.startswith("\n"):
        body = body[1:]
    return props, body, True


def _format_scalar(value: FmValue) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        # Avoid trailing ".0" for whole-number floats when not meaningful.
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    s = str(value)
    # Quote if the string contains YAML-significant characters.
    if re.search(r"[:#\[\]]", s):
        escaped = s.replace('"', '\\"')
        return f'"{escaped}"'
    return s


def serialize_frontmatter(
    props: Mapping[str, FmValue], body: str
) -> str:
    """Render ``props`` as a leading ``---`` block on top of ``body``.

    Empty props returns ``body`` unchanged (no empty fence). Arrays of
    length ≤ 3 with no comma-bearing members render in flow form
    ``[a, b, c]``; longer or comma-bearing lists fall back to block form.
    """
    keys = list(props.keys())
    if not keys:
        return body

    lines: list[str] = [_DELIM]
    for k in keys:
        v = props[k]
        if isinstance(v, list):
            items = [str(x) for x in v]
            if len(items) <= 3 and all("," not in x for x in items):
                lines.append(f"{k}: [{', '.join(items)}]")
            else:
                lines.append(f"{k}:")
                for item in items:
                    lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: {_format_scalar(v)}")
    lines.append(_DELIM)
    lines.append("")
    return "\n".join(lines) + body


def merge_frontmatter(
    content: str, updates: Mapping[str, FmValue]
) -> str:
    """Read existing frontmatter, merge ``updates`` over it, write back.

    Used by the upsert path in ``save_skill_lesson`` so we can bump
    ``replay_count`` / ``last_used_at`` on an existing note without
    losing other props the user (or React panel) may have edited.

    Keys whose ``updates`` value is ``None`` are deleted from the
    resulting frontmatter — useful for clearing transient state like
    ``pending_since_turn`` when promoting to ``verified``.
    """
    existing, body, _has = parse_frontmatter(content)
    merged: FmProps = dict(existing)
    for k, v in updates.items():
        if v is None and k in merged:
            del merged[k]
        elif v is not None:
            merged[k] = v
    return serialize_frontmatter(merged, body)


__all__ = [
    "FmValue",
    "FmProps",
    "parse_frontmatter",
    "serialize_frontmatter",
    "merge_frontmatter",
]
