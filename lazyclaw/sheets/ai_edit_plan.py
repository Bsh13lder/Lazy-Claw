"""Validation for the ✨ in-editor edit plan.

The plan arrives as strict JSON from an LLM, so every shape here is untrusted.
The rule throughout: **a bad entry is skipped, never raised, and never sinks its
good siblings.** A model that gets one of six format ops wrong should still get
the other five applied — raising would cost a retry and then fail the whole
edit.

Split out of ``ai_edit.py`` so it can be tested without that module's async DB
fixture; ``ai_edit`` keeps the LLM prompt and the apply sequencing.

Plan shape (all three keys optional — a pure formatting request has no edits)::

    {
      "edits":   [{"cell": "A1", "value": 10},
                  {"cell": "C1", "formula": "=SUM(A1:B1)"}],
      "formats": [{"range": "A1:C1", "bold": true, "bg": "#e8e8e8"},
                  {"range": "B2:B9", "number_format": "currency"}],
      "layout":  {"column_widths": [{"column": "A", "width": 160}],
                  "auto_fit_columns": ["B", "C"],
                  "merge": ["A1:C1"], "freeze_rows": 1}
    }

Parallel typed lists rather than one discriminated ``ops`` array: there is no
discriminator for the model to get wrong, and every plan written against the
older ``{"edits": [...]}``-only shape still validates unchanged.
"""

from __future__ import annotations

from typing import Any

from lazyclaw.sheets import geometry as G
from lazyclaw.sheets import snapshot as S
from lazyclaw.sheets import styles as ST

#: Backstops against a runaway model. Formatting 64 ranges is already far more
#: than any real request; per-cell formats over a 1000-row sheet would otherwise
#: mean 1000 deepcopies.
MAX_FORMAT_OPS = 64
MAX_LAYOUT_OPS = 64

#: Sentinel for ``auto_fit_columns: ["*"]`` — "every used column".
AUTOFIT_ALL = "*"

_ALIGN_VALUES = {"left", "center", "right"}
_VALIGN_VALUES = {"top", "middle", "bottom"}
_BOOL_FIELDS = ("bold", "italic", "underline", "strike", "wrap")
_TRUTHY = {"true", "yes", "1", "on"}
_FALSY = {"false", "no", "0", "off"}


def _as_bool(value: Any) -> bool | None:
    """Coerce to bool, or ``None`` when it isn't one.

    Accepts the stringly forms because models emit ``"true"`` constantly and
    refusing them would burn a retry on a plan that is otherwise fine.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUTHY:
            return True
        if text in _FALSY:
            return False
    return None


# ───────────────────────── edits ────────────────────────────────────

def normalize_edits(raw: Any) -> list[dict[str, Any]]:
    """Cell edits → a list ``snapshot.set_cells`` accepts.

    Each entry addresses a cell by A1 (``cell``) or by 0-based ``row``+``col``,
    and carries a ``formula`` (which wins) or a ``value``.
    """
    edits: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return edits
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        edit: dict[str, Any] = {}
        if "cell" in entry:
            edit["cell"] = str(entry["cell"])
        elif "row" in entry and "col" in entry:
            try:
                edit["row"] = int(entry["row"])
                edit["col"] = int(entry["col"])
            except (TypeError, ValueError):
                continue
        else:
            continue
        if entry.get("formula") not in (None, ""):
            edit["formula"] = str(entry["formula"])
        elif "value" in entry:
            edit["value"] = entry["value"]
        edits.append(edit)
    return edits


# ───────────────────────── formats ──────────────────────────────────

def _format_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """The validated style fields of one format entry (address excluded)."""
    out: dict[str, Any] = {}

    for field in _BOOL_FIELDS:
        if field in entry:
            coerced = _as_bool(entry[field])
            if coerced is not None:
                out[field] = coerced

    for field in ("color", "bg"):
        if field in entry:
            rgb = ST.normalize_color(entry[field])
            if rgb:
                out[field] = rgb

    for field, allowed in (("align", _ALIGN_VALUES), ("valign", _VALIGN_VALUES)):
        value = entry.get(field)
        if isinstance(value, str) and value.strip().lower() in allowed:
            out[field] = value.strip().lower()

    pattern = entry.get("number_format")
    if isinstance(pattern, str) and pattern.strip():
        if len(pattern) <= ST.MAX_PATTERN_CHARS:
            out["number_format"] = pattern.strip()

    size = entry.get("font_size")
    if size is not None and not isinstance(size, bool):
        try:
            out["font_size"] = max(
                ST.FONT_SIZE_MIN, min(ST.FONT_SIZE_MAX, float(size))
            )
        except (TypeError, ValueError):
            pass

    font = entry.get("font")
    if isinstance(font, str) and font.strip():
        if len(font) <= ST.MAX_FONT_NAME_CHARS:
            out["font"] = font.strip()

    return out


def normalize_formats(raw: Any) -> list[dict[str, Any]]:
    """Format ops → ``[{"range": "A1:C1", <style fields>}]``.

    An entry needs a parseable ``range`` (``cell`` is accepted as an alias) and
    at least one usable field — or ``clear: true``, which stands alone.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if len(out) >= MAX_FORMAT_OPS:
            break
        if not isinstance(entry, dict):
            continue
        ref = entry.get("range") or entry.get("cell")
        if not isinstance(ref, str) or not ref.strip():
            continue
        try:
            S.parse_range(ref)
        except ValueError:
            continue

        clearing = _as_bool(entry.get("clear")) is True
        fields = _format_fields(entry)
        if not clearing and not fields:
            continue
        op: dict[str, Any] = {"range": ref.strip()}
        if clearing:
            op["clear"] = True
        else:
            op.update(fields)
        out.append(op)
    return out


# ───────────────────────── layout ───────────────────────────────────

def _axis_pairs(
    raw: Any, key: str, size_key: str, low: float, high: float,
    *, one_based: bool,
) -> dict[int, float]:
    """``[{column|row, width|height}]`` → ``{index: size}``, clamped."""
    out: dict[int, float] = {}
    if not isinstance(raw, list):
        return out
    for entry in raw[:MAX_LAYOUT_OPS]:
        if not isinstance(entry, dict):
            continue
        size = entry.get(size_key)
        if isinstance(size, bool) or not isinstance(size, (int, float)):
            continue
        index = _axis_index(entry.get(key), one_based=one_based)
        if index is None:
            continue
        out[index] = max(low, min(high, float(size)))
    return out


def _axis_index(raw: Any, *, one_based: bool) -> int | None:
    """A column letter or a row number → a 0-based index."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        index = raw - 1 if one_based else raw
        return index if index >= 0 else None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        index = int(text) - 1 if one_based else int(text)
        return index if index >= 0 else None
    if one_based:
        return None
    try:
        return S.letter_to_col(text)
    except ValueError:
        return None


def normalize_layout(raw: Any) -> dict[str, Any]:
    """Layout ops → a dict the apply step can act on directly.

    Only keys the model actually asked for appear in the result, so ``apply``
    can tell "leave the freeze alone" (absent) from "unfreeze" (``0``).
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}

    widths = _axis_pairs(
        raw.get("column_widths"), "column", "width",
        G.COL_WIDTH_MIN, G.COL_WIDTH_MAX, one_based=False,
    )
    if widths:
        out["column_widths"] = widths

    heights = _axis_pairs(
        raw.get("row_heights"), "row", "height",
        G.ROW_HEIGHT_MIN, G.ROW_HEIGHT_MAX, one_based=True,
    )
    if heights:
        out["row_heights"] = heights

    autofit = raw.get("auto_fit_columns")
    if isinstance(autofit, list) and autofit:
        letters = [str(v).strip() for v in autofit[:MAX_LAYOUT_OPS] if v is not None]
        if AUTOFIT_ALL in letters:
            out["auto_fit_columns"] = AUTOFIT_ALL
        else:
            indices = [
                i for i in (_axis_index(v, one_based=False) for v in letters)
                if i is not None
            ]
            if indices:
                out["auto_fit_columns"] = indices

    for key in ("merge", "unmerge"):
        refs = raw.get(key)
        if not isinstance(refs, list):
            continue
        valid: list[str] = []
        for ref in refs[:MAX_LAYOUT_OPS]:
            if not isinstance(ref, str) or not ref.strip():
                continue
            try:
                S.parse_range(ref)
            except ValueError:
                continue
            valid.append(ref.strip())
        if valid:
            out[key] = valid

    for key in ("freeze_rows", "freeze_columns"):
        value = raw.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            out[key] = max(0, min(G.FREEZE_MAX, int(value)))
        except (TypeError, ValueError):
            continue

    return out
