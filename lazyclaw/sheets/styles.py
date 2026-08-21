"""Cell formatting for Univer workbooks — the agent-facing style model.

Pure dict-shaping, no I/O, every mutator returns a NEW snapshot.

## Why this exists

``snapshot.py`` carries the cell *content* model (``v``/``f``). Everything that
makes a sheet READABLE — weight, colour, alignment, number format — lives in
Univer's ``IStyleData``, which the backend had no notion of at all. An agent
could fill a budget table with correct numbers and it would still arrive as an
undifferentiated wall of text.

## The model

Callers speak a flat, friendly vocabulary (``{"bold": True, "bg": "yellow",
"number_format": "currency"}``); this module translates it to Univer's terse
keys and interns the result in the workbook-level ``styles`` registry, pointing
``ICellData.s`` at the id.

The key mapping is the CONTRACT with two independent readers — the web editor
(`@univerjs/core` 0.24) and the Flutter grid
(``mobile/lib/screens/documents/univer_model.dart``). Neither is ours to change,
so ``tests/sheets/test_styles.py`` asserts every field's encoding exactly.

## Patch semantics (tri-state)

Callers pass a DICT so "absent" is expressible and a patch never wipes what it
did not mention:

===================  ===========================================
key absent           left exactly as it was
key present, None    the Univer key is REMOVED (falls back to the
                     row → column → worksheet → workbook cascade)
key present, value   set per the mapping below
===================  ===========================================

``wrap: False`` is the one field where "off" is not a removal: the web editor
injects a workbook-level ``defaultStyle: {tb: WRAP}``, so removing ``tb`` would
leave the cell still wrapping. It writes OVERFLOW explicitly instead.

## Ids

A style id is a content hash (``s-lc-<blake2s>``), not a counter. That makes
:func:`apply_style` idempotent and order-independent — the id IS the dedup key,
so re-applying the same formatting is a no-op and two ranges given the same
patch share one registry entry. The ``s-lc-`` prefix keeps our ids clear of
Univer's own and of the Flutter app's ``s-mob-``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from lazyclaw.sheets import snapshot as S

# ───────────────────────── vocabulary ───────────────────────────────

#: Every friendly key this module understands. Anything else is ignored so a
#: hallucinated field in an LLM edit plan can't poison a whole style.
FRIENDLY_KEYS: tuple[str, ...] = (
    "bold", "italic", "underline", "strike", "color", "bg",
    "align", "valign", "wrap", "number_format", "font_size", "font",
)

#: Colour names models actually reach for. Anything else must be hex.
NAMED_COLORS: dict[str, str] = {
    "black": "#000000", "white": "#FFFFFF", "red": "#FF0000",
    "green": "#008000", "blue": "#0000FF", "yellow": "#FFFF00",
    "orange": "#FFA500", "purple": "#800080", "pink": "#FFC0CB",
    "grey": "#808080", "gray": "#808080", "lightgrey": "#D3D3D3",
    "lightgray": "#D3D3D3", "darkgrey": "#404040", "darkgray": "#404040",
    "cyan": "#00FFFF", "magenta": "#FF00FF", "brown": "#A52A2A",
    "navy": "#000080", "teal": "#008080", "lime": "#00FF00",
    "silver": "#C0C0C0", "gold": "#FFD700", "beige": "#F5F5DC",
}

#: Friendly number-format aliases → Excel patterns (Univer uses Excel codes
#: verbatim, so these pass straight through the xlsx bridge too).
NUMBER_FORMATS: dict[str, str] = {
    "currency": "$#,##0.00",
    "euro": "€#,##0.00",
    "percent": "0%",
    "percent2": "0.00%",
    "integer": "#,##0",
    "decimal": "#,##0.00",
    "number": "0.00",
    "date": "yyyy-mm-dd",
    "datetime": "yyyy-mm-dd hh:mm",
    "time": "hh:mm",
    "text": "@",
    "plain": "General",
}

_ALIGN = {"left": 1, "center": 2, "centre": 2, "right": 3}
_VALIGN = {"top": 1, "middle": 2, "center": 2, "centre": 2, "bottom": 3}

#: ``WrapStrategy`` — OVERFLOW spills into empty neighbours (Excel's default),
#: WRAP grows the row. CLIP (2) is unused; nothing asks for it.
_WRAP_ON = 3
_WRAP_OFF = 1

FONT_SIZE_MIN = 6.0
FONT_SIZE_MAX = 72.0
MAX_FONT_NAME_CHARS = 64
MAX_PATTERN_CHARS = 64

STYLE_ID_PREFIX = "s-lc-"
_STYLE_ID_BYTES = 5

#: Ceiling on how many cells one style call may touch. A deliberate backstop
#: against an LLM asking for ``A1:ZZ50000``; real formatting never approaches it.
MAX_RANGE_CELLS = 200_000

_HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")


# ───────────────────────── colours ──────────────────────────────────

def normalize_color(value: Any) -> str | None:
    """A colour name or hex string → ``"#RRGGBB"``; ``None`` when unusable.

    Accepts ``"red"``, ``"#f00"``, ``"ff0000"``, ``"#FfAa00"``. Returning
    ``None`` rather than raising lets a bad colour drop out of an LLM plan
    without sinking the rest of the style.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    named = NAMED_COLORS.get(raw.lower())
    if named:
        return named
    m = _HEX_RE.match(raw)
    if not m:
        return None
    digits = m.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return f"#{digits.upper()}"


# ───────────────────────── friendly ⇄ Univer ────────────────────────

def _flag(value: Any, *, wrapped: bool) -> Any:
    """A boolean flag → its Univer encoding, or ``None`` to remove the key.

    ``wrapped`` covers ``ul``/``st``, which are ``ITextDecoration`` objects
    (``{"s": 1}``) rather than bare ``BooleanNumber``s.
    """
    if not value:
        return None
    return {"s": 1} if wrapped else 1


def to_univer_style(friendly: dict[str, Any]) -> dict[str, Any]:
    """Friendly patch → ``IStyleData`` patch.

    A ``None`` VALUE in the result means "remove this key on merge"; see the
    module docstring for the tri-state. Unknown keys are dropped.
    """
    out: dict[str, Any] = {}
    for key in FRIENDLY_KEYS:
        if key not in friendly:
            continue
        value = friendly[key]

        if key == "bold":
            out["bl"] = _flag(value, wrapped=False)
        elif key == "italic":
            out["it"] = _flag(value, wrapped=False)
        elif key == "underline":
            out["ul"] = _flag(value, wrapped=True)
        elif key == "strike":
            out["st"] = _flag(value, wrapped=True)
        elif key in ("color", "bg"):
            univer_key = "cl" if key == "color" else "bg"
            if value is None:
                out[univer_key] = None
            else:
                rgb = normalize_color(value)
                if rgb:
                    out[univer_key] = {"rgb": rgb}
        elif key == "align":
            out["ht"] = _lookup(_ALIGN, value)
        elif key == "valign":
            out["vt"] = _lookup(_VALIGN, value)
        elif key == "wrap":
            # NOT a removal when off — see the module docstring.
            out["tb"] = None if value is None else (
                _WRAP_ON if value else _WRAP_OFF
            )
        elif key == "number_format":
            out["n"] = _number_format(value)
        elif key == "font_size":
            out["fs"] = _font_size(value)
        elif key == "font":
            out["ff"] = _font_name(value)

    # A field that failed validation shouldn't silently become a removal.
    return {k: v for k, v in out.items() if v is not None or _is_removal(friendly, k)}


#: Univer key → the friendly key that can legitimately null it out.
_REMOVABLE = {
    "bl": "bold", "it": "italic", "ul": "underline", "st": "strike",
    "cl": "color", "bg": "bg", "ht": "align", "vt": "valign", "tb": "wrap",
    "n": "number_format", "fs": "font_size", "ff": "font",
}


def _is_removal(friendly: dict[str, Any], univer_key: str) -> bool:
    """Whether a ``None`` for ``univer_key`` was ASKED for rather than a
    validation failure. Explicit ``None`` and the falsy flags both count."""
    key = _REMOVABLE.get(univer_key)
    if key is None or key not in friendly:
        return False
    value = friendly[key]
    if value is None:
        return True
    return univer_key in ("bl", "it", "ul", "st") and not value


def _lookup(table: dict[str, int], value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value in table.values() else None
    if isinstance(value, str):
        return table.get(value.strip().lower())
    return None


def _number_format(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    pattern = NUMBER_FORMATS.get(raw.lower(), raw)
    if len(pattern) > MAX_PATTERN_CHARS:
        return None
    return {"pattern": pattern}


def _font_size(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        size = float(value)
    except (TypeError, ValueError):
        return None
    size = max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, size))
    return int(size) if size.is_integer() else size


def _font_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > MAX_FONT_NAME_CHARS:
        return None
    return name


def from_univer_style(style: dict[str, Any]) -> dict[str, Any]:
    """``IStyleData`` → the friendly view. Inverse of :func:`to_univer_style`,
    omitting anything unset. Mirrors ``CellStyleView`` in the Flutter app."""
    if not style:
        return {}
    out: dict[str, Any] = {}
    if style.get("bl"):
        out["bold"] = True
    if style.get("it"):
        out["italic"] = True
    if (style.get("ul") or {}).get("s"):
        out["underline"] = True
    if (style.get("st") or {}).get("s"):
        out["strike"] = True
    if (style.get("cl") or {}).get("rgb"):
        out["color"] = style["cl"]["rgb"]
    if (style.get("bg") or {}).get("rgb"):
        out["bg"] = style["bg"]["rgb"]
    for univer_key, friendly_key, table in (
        ("ht", "align", _ALIGN), ("vt", "valign", _VALIGN),
    ):
        code = style.get(univer_key)
        if isinstance(code, int):
            for name, value in table.items():
                if value == code and name not in ("centre", "center"):
                    out[friendly_key] = name
                    break
            else:
                if code == 2:
                    out[friendly_key] = "center"
    if style.get("tb") is not None:
        out["wrap"] = style["tb"] == _WRAP_ON
    if (style.get("n") or {}).get("pattern"):
        out["number_format"] = style["n"]["pattern"]
    if style.get("fs") is not None:
        out["font_size"] = style["fs"]
    if style.get("ff"):
        out["font"] = style["ff"]
    return out


# ───────────────────────── merge + identity ─────────────────────────

def merge_style(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """``base`` overlaid with ``patch``; a ``None`` value removes its key.

    Returns a NEW dict — neither argument is touched.
    """
    out = copy.deepcopy(base or {})
    for key, value in (patch or {}).items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _canonicalize(value: Any) -> Any:
    """Normalise for hashing so equivalent styles share an id.

    Integral floats collapse to ints (``14.0`` == ``14``) and hex colours
    upper-case — without this, dedup silently misses and the registry grows a
    near-duplicate per edit.
    """
    if isinstance(value, dict):
        return {k: _canonicalize(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_canonicalize(v) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and _HEX_RE.match(value):
        return value.upper()
    return value


def style_id(style: dict[str, Any]) -> str:
    """A stable, content-derived id for ``style``.

    blake2s (stdlib, and unlike sha1 it doesn't trip ``bandit``) over the
    canonical JSON. 40 bits: a collision would merge two visually distinct
    styles in one workbook, which needs ~10⁴ distinct styles to become likely —
    and costs appearance, never data.
    """
    canonical = json.dumps(
        _canonicalize(style), sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.blake2s(
        canonical.encode("utf-8"), digest_size=_STYLE_ID_BYTES
    ).hexdigest()
    return f"{STYLE_ID_PREFIX}{digest}"


def is_lazyclaw_style_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(STYLE_ID_PREFIX)


# ───────────────────────── reading a cell's style ───────────────────

def _registry(snap: dict[str, Any]) -> dict[str, Any]:
    styles = snap.get("styles")
    return styles if isinstance(styles, dict) else {}


def _deref(snap: dict[str, Any], ref: Any) -> dict[str, Any]:
    """``ICellData.s`` → the style dict it denotes.

    ``s`` is ``IStyleData | string`` — an inline dict or an id into the
    workbook registry. A registry entry may legally be ``null``.
    """
    if isinstance(ref, dict):
        return ref
    if isinstance(ref, str):
        entry = _registry(snap).get(ref)
        return entry if isinstance(entry, dict) else {}
    return {}


def resolve_style(
    snap: dict[str, Any], row: int, col: int, sheet: int | str = 0
) -> dict[str, Any]:
    """The raw ``IStyleData`` in effect for a cell (empty when unstyled).

    Cell-level only — the row/column/worksheet cascade is the renderer's job,
    and both renderers already own it.
    """
    cell = S.get_cell(snap, row, col, sheet)
    return copy.deepcopy(_deref(snap, (cell or {}).get("s")))


def get_style_view(
    snap: dict[str, Any], row: int, col: int, sheet: int | str = 0
) -> dict[str, Any]:
    """The friendly view of a cell's style — what ``read_sheet`` reports."""
    return from_univer_style(resolve_style(snap, row, col, sheet))


# ───────────────────────── writing styles ───────────────────────────

def _iter_range(
    snap: dict[str, Any], r1: int, c1: int, r2: int, c2: int, sheet: int | str
):
    """Walk a range, bounded so it can't materialise absurd numbers of cells.

    An OPEN-ENDED side (from ``A:A`` / ``2:2``, which parse to the sheet
    maximum) collapses to the used bounds — styling a whole column means "the
    part of it that exists", not a million rows. An explicit finite range is
    honoured verbatim even on an empty sheet, because "bold A1:D1" before the
    data is written is a perfectly ordinary thing to ask for. Whatever survives
    that is then capped at :data:`MAX_RANGE_CELLS`.
    """
    max_row, max_col = S.used_bounds(snap, sheet)
    hi_r = min(r2, max(max_row, r1)) if r2 >= S.MAX_ROW_INDEX else r2
    hi_c = min(c2, max(max_col, c1)) if c2 >= S.MAX_COL_INDEX else c2

    cols = hi_c - c1 + 1
    if cols > 0 and (hi_r - r1 + 1) * cols > MAX_RANGE_CELLS:
        hi_r = r1 + max(0, MAX_RANGE_CELLS // cols - 1)

    for row in range(r1, hi_r + 1):
        for col in range(c1, hi_c + 1):
            yield row, col


def apply_style(
    snap: dict[str, Any],
    r1: int, c1: int, r2: int, c2: int,
    friendly: dict[str, Any],
    sheet: int | str = 0,
) -> dict[str, Any]:
    """Merge ``friendly`` into every cell of the range. Returns a NEW snapshot.

    Existing registry entries are never rewritten in place: a cell whose ``s``
    is shared with others gets a NEW id, so patching one cell can't restyle the
    rest of the workbook.
    """
    patch = to_univer_style(friendly)
    if not patch:
        return copy.deepcopy(snap)

    out = copy.deepcopy(snap)
    sid = S.resolve_sheet_id(out, sheet)
    registry = out.setdefault("styles", {})
    cell_data = out["sheets"][sid].setdefault("cellData", {})

    for row, col in _iter_range(out, r1, c1, r2, c2, sheet):
        r_key, c_key = str(row), str(col)
        cell = cell_data.setdefault(r_key, {}).setdefault(c_key, {})
        merged = merge_style(_deref(out, cell.get("s")), patch)
        if not merged:
            cell.pop("s", None)
            continue
        new_id = style_id(merged)
        registry.setdefault(new_id, merged)
        cell["s"] = new_id

    return gc_styles(_prune_empty_cells(out, sid))


def apply_style_a1(
    snap: dict[str, Any], ref: str, friendly: dict[str, Any],
    sheet: int | str = 0,
) -> dict[str, Any]:
    """:func:`apply_style` addressed by an A1 range (``"A1:C1"``, ``"B:B"``)."""
    r1, c1, r2, c2 = S.parse_range(ref)
    return apply_style(snap, r1, c1, r2, c2, friendly, sheet)


def clear_style(
    snap: dict[str, Any], r1: int, c1: int, r2: int, c2: int,
    sheet: int | str = 0,
) -> dict[str, Any]:
    """Strip formatting from a range, leaving values and formulas intact."""
    out = copy.deepcopy(snap)
    sid = S.resolve_sheet_id(out, sheet)
    cell_data = out["sheets"][sid].get("cellData") or {}
    for row, col in _iter_range(out, r1, c1, r2, c2, sheet):
        cell = (cell_data.get(str(row)) or {}).get(str(col))
        if isinstance(cell, dict):
            cell.pop("s", None)
    return gc_styles(_prune_empty_cells(out, sid))


def set_default_style(
    snap: dict[str, Any], friendly: dict[str, Any], sheet: int | str | None = None
) -> dict[str, Any]:
    """Set the workbook-level (``sheet=None``) or worksheet-level default style.

    This is the cascade the web editor uses for its wrap default; setting it
    here means "every cell unless overridden".
    """
    patch = to_univer_style(friendly)
    out = copy.deepcopy(snap)
    target = out if sheet is None else out["sheets"][S.resolve_sheet_id(out, sheet)]
    merged = merge_style(_deref(out, target.get("defaultStyle")), patch)
    if not merged:
        target.pop("defaultStyle", None)
        return gc_styles(out)
    new_id = style_id(merged)
    out.setdefault("styles", {}).setdefault(new_id, merged)
    target["defaultStyle"] = new_id
    return gc_styles(out)


# ───────────────────────── housekeeping ─────────────────────────────

def _prune_empty_cells(snap: dict[str, Any], sid: str) -> dict[str, Any]:
    """Drop cells left with nothing at all, and the rows they emptied."""
    cell_data = snap["sheets"][sid].get("cellData")
    if not isinstance(cell_data, dict):
        return snap
    for r_key in list(cell_data):
        cols = cell_data.get(r_key) or {}
        for c_key in list(cols):
            if not cols[c_key]:
                cols.pop(c_key)
        if not cols:
            cell_data.pop(r_key)
    return snap


def _referenced_ids(snap: dict[str, Any]) -> set[str]:
    used: set[str] = set()

    def note(ref: Any) -> None:
        if isinstance(ref, str):
            used.add(ref)

    note(snap.get("defaultStyle"))
    for sheet_obj in (snap.get("sheets") or {}).values():
        if not isinstance(sheet_obj, dict):
            continue
        note(sheet_obj.get("defaultStyle"))
        for bucket in ("rowData", "columnData"):
            for entry in (sheet_obj.get(bucket) or {}).values():
                if isinstance(entry, dict):
                    note(entry.get("s"))
        for cols in (sheet_obj.get("cellData") or {}).values():
            for cell in (cols or {}).values():
                if isinstance(cell, dict):
                    note(cell.get("s"))
    return used


def gc_styles(snap: dict[str, Any]) -> dict[str, Any]:
    """Drop unreferenced styles WE minted. Returns the same snapshot object.

    Every patch mints a new id and orphans the old one, so without this the
    registry grows unbounded inside the encrypted blob. Only ``s-lc-`` ids are
    swept: a Univer-minted id may be referenced from an opaque plugin resource
    (conditional formatting, data validation, notes) that we don't parse, so
    dropping one could break formatting we can't even see.
    """
    registry = snap.get("styles")
    if not isinstance(registry, dict) or not registry:
        return snap
    used = _referenced_ids(snap)
    for sid in list(registry):
        if is_lazyclaw_style_id(sid) and sid not in used:
            registry.pop(sid)
    return snap


# ───────────────────────── number formatting ────────────────────────

def _thousands(text: str) -> str:
    neg = text.startswith("-")
    body = text[1:] if neg else text
    whole, _, frac = body.partition(".")
    grouped = f"{int(whole):,}" if whole.isdigit() else whole
    out = f"{grouped}.{frac}" if frac else grouped
    return f"-{out}" if neg else out


def format_number(value: Any, pattern: str | None) -> str:
    """Render ``value`` under a Univer/Excel number ``pattern``.

    Covers the patterns :data:`NUMBER_FORMATS` can produce; anything else falls
    through to ``str(value)``. Deliberately NOT wired into ``as_grid`` — that
    stays raw so ``read_sheet`` and the CSV export keep values that survive a
    re-import. It exists for auto-fit, which must measure what the user SEES.

    Mirrors ``formatNumber`` in the Flutter app; separators are en-US there too.
    """
    if value is None:
        return ""
    if not pattern:
        return str(value)

    number: float | int | None
    if isinstance(value, bool):
        number = None
    elif isinstance(value, (int, float)):
        number = value
    else:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            number = None
    if number is None:
        return str(value)

    if pattern == "General" or pattern == "@":
        return str(value)
    if pattern == "0":
        return str(round(number))
    if pattern == "0.00":
        return f"{number:.2f}"
    if pattern == "0%":
        return f"{round(number * 100)}%"
    if pattern == "0.00%":
        return f"{number * 100:.2f}%"
    if pattern == "#,##0":
        return _thousands(str(round(number)))
    if pattern == "#,##0.00":
        return _thousands(f"{number:.2f}")
    if pattern == "$#,##0.00":
        return f"${_thousands(f'{number:.2f}')}"
    if pattern == "€#,##0.00":
        return f"€{_thousands(f'{number:.2f}')}"
    return str(value)
