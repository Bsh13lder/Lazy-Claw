"""Univer ``IStyleData`` ⇄ openpyxl formatting.

Kept apart from :mod:`lazyclaw.sheets.xlsx_io` so that module stays orchestration
and this stays a mapping table.

**Borders are deliberately out of scope, both directions.** Univer's
``IBorderData`` has ten edge keys — including four diagonals — each an enum ×
colour pair, against openpyxl's differently-shaped ``Border``/``Side``
vocabulary. A partial border model renders visibly WRONG (half a box), which is
worse than no borders at all. Don't half-add it.

Also dropped both ways: ``tr`` (rotation), ``td`` (text direction — Univer's own
typings call it not fully implemented), ``pd`` (padding), ``va``
(sub/superscript), ``bbl``/``ol``. And ``defaultStyle``, which has no openpyxl
analogue at all — so the workbook-level wrap default the web editor injects does
not survive an export.
"""

from __future__ import annotations

from typing import Any

from openpyxl.styles import Alignment, Font, PatternFill

from lazyclaw.sheets import styles as ST

#: Univer ``HorizontalAlign``/``VerticalAlign`` → openpyxl's names. Note Univer
#: says MIDDLE where openpyxl says "center" — not an identity mapping.
_H_ALIGN = {1: "left", 2: "center", 3: "right"}
_V_ALIGN = {1: "top", 2: "center", 3: "bottom"}
_H_ALIGN_BACK = {v: k for k, v in _H_ALIGN.items()}
_V_ALIGN_BACK = {"top": 1, "center": 2, "bottom": 3, "justify": 2}

#: openpyxl's sentinel for "no explicit number format".
GENERAL = "General"

#: Excel column widths are in CHARACTER units against a max-digit-width of 7px
#: for Calibri 11 — the same 7 px/char the auto-fit heuristic uses.
_PX_PER_CHAR = 7.0
_PX_PADDING = 5.0

#: px → points for row heights.
_PT_PER_PX = 0.75

#: An Excel-authored file can declare one column dimension spanning min=1 to
#: max=16384. Expanding that verbatim would write 16k keys into the encrypted
#: blob, so a span is only honoured up to here.
MAX_COL_SPAN = 256

#: Ceiling on the imported style registry — content-hash dedup keeps real files
#: far below this, but one pathological workbook shouldn't balloon the blob.
MAX_IMPORT_STYLES = 2000


def px_to_char_width(px: float) -> float:
    """Pixel column width → Excel character units."""
    return max(0.0, (float(px) - _PX_PADDING) / _PX_PER_CHAR)


def char_width_to_px(chars: float) -> float:
    """Excel character units → pixel column width."""
    return float(chars) * _PX_PER_CHAR + _PX_PADDING


def px_to_points(px: float) -> float:
    """Pixel row height → points."""
    return float(px) * _PT_PER_PX


def points_to_px(points: float) -> float:
    """Points → pixel row height."""
    return float(points) / _PT_PER_PX


def _argb(rgb: str) -> str:
    """``"#RRGGBB"`` → openpyxl's ``"FFRRGGBB"``.

    The alpha prefix is required: a bare 6-digit value comes back as
    ``00RRGGBB`` — fully transparent — and renders as nothing.
    """
    return "FF" + rgb.lstrip("#").upper()


def _rgb(color: Any) -> str | None:
    """An openpyxl ``Color`` → ``"#RRGGBB"``, or ``None`` when not usable.

    THEME colours are skipped rather than converted. This matters more than it
    sounds: a plain unstyled cell's font colour comes back as
    ``type='theme', rgb=None``, so without this guard every imported cell would
    acquire a garbage explicit colour.
    """
    if color is None or getattr(color, "type", None) != "rgb":
        return None
    raw = getattr(color, "rgb", None)
    if not isinstance(raw, str) or len(raw) < 6:
        return None
    return "#" + raw[-6:].upper()


# ───────────────────────── export (Univer → openpyxl) ───────────────

def apply_to_cell(cell: Any, style: dict[str, Any]) -> None:
    """Stamp a Univer style onto an openpyxl cell."""
    if not style:
        return

    font_kwargs: dict[str, Any] = {}
    if style.get("bl"):
        font_kwargs["bold"] = True
    if style.get("it"):
        font_kwargs["italic"] = True
    if (style.get("ul") or {}).get("s"):
        font_kwargs["underline"] = "single"
    if (style.get("st") or {}).get("s"):
        font_kwargs["strike"] = True
    if style.get("fs"):
        font_kwargs["size"] = style["fs"]
    if style.get("ff"):
        font_kwargs["name"] = style["ff"]
    cl = (style.get("cl") or {}).get("rgb")
    if cl:
        font_kwargs["color"] = _argb(cl)
    if font_kwargs:
        cell.font = Font(**font_kwargs)

    bg = (style.get("bg") or {}).get("rgb")
    if bg:
        cell.fill = PatternFill(
            fill_type="solid", start_color=_argb(bg), end_color=_argb(bg)
        )

    align_kwargs: dict[str, Any] = {}
    if style.get("ht") in _H_ALIGN:
        align_kwargs["horizontal"] = _H_ALIGN[style["ht"]]
    if style.get("vt") in _V_ALIGN:
        align_kwargs["vertical"] = _V_ALIGN[style["vt"]]
    if style.get("tb") is not None:
        align_kwargs["wrap_text"] = style["tb"] == 3
    if align_kwargs:
        cell.alignment = Alignment(**align_kwargs)

    pattern = (style.get("n") or {}).get("pattern")
    if pattern:
        cell.number_format = pattern


# ───────────────────────── import (openpyxl → Univer) ───────────────

def from_cell(cell: Any) -> dict[str, Any]:
    """An openpyxl cell's formatting → a Univer style dict (empty if plain)."""
    out: dict[str, Any] = {}

    font = getattr(cell, "font", None)
    if font is not None:
        if font.bold:
            out["bl"] = 1
        if font.italic:
            out["it"] = 1
        if font.underline:
            out["ul"] = {"s": 1}
        if font.strike:
            out["st"] = {"s": 1}
        if font.size and float(font.size) != 11.0:
            out["fs"] = float(font.size)
        if font.name and font.name not in ("Calibri", "Aptos Narrow"):
            out["ff"] = font.name
        rgb = _rgb(font.color)
        if rgb and rgb != "#000000":
            out["cl"] = {"rgb": rgb}

    fill = getattr(cell, "fill", None)
    if fill is not None and getattr(fill, "fill_type", None) == "solid":
        rgb = _rgb(fill.start_color)
        # openpyxl reports an unfilled cell as solid-white in some writers.
        if rgb and rgb != "#FFFFFF":
            out["bg"] = {"rgb": rgb}

    align = getattr(cell, "alignment", None)
    if align is not None:
        if align.horizontal in _H_ALIGN_BACK:
            out["ht"] = _H_ALIGN_BACK[align.horizontal]
        if align.vertical in _V_ALIGN_BACK:
            out["vt"] = _V_ALIGN_BACK[align.vertical]
        if align.wrap_text:
            out["tb"] = 3

    fmt = getattr(cell, "number_format", None)
    # Skip openpyxl's default, or every imported cell gets n:{pattern:"General"}.
    if isinstance(fmt, str) and fmt and fmt != GENERAL:
        out["n"] = {"pattern": fmt}

    return out


def intern(snap: dict[str, Any], style: dict[str, Any]) -> str | None:
    """Put ``style`` in the workbook registry and return its id.

    ``None`` when the style is empty or the registry is already at
    :data:`MAX_IMPORT_STYLES`.
    """
    if not style:
        return None
    registry = snap.setdefault("styles", {})
    style_id = ST.style_id(style)
    if style_id not in registry:
        if len(registry) >= MAX_IMPORT_STYLES:
            return None
        registry[style_id] = style
    return style_id
