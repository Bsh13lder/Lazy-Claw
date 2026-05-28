"""Best-effort server-side formula recalculation via xlcalculator (MIT).

Univer recalculates in the browser and caches each result (``v``) next to its
formula (``f``); this module covers the other case — cells the *agent* edited
through skills without a browser pass. It renders the snapshot to xlsx, lets
xlcalculator evaluate every formula cell, and writes the results back into a
NEW snapshot. It NEVER raises: unsupported formulas (xlcalculator's coverage
is partial) keep whatever value they already had.

A heavier, fuller-fidelity alternative — driving LibreOffice headless
(``soffice``) — is intentionally not a dependency here; xlcalculator keeps the
recalc in-process with no external binary.
"""

from __future__ import annotations

import copy
import logging
import os
import tempfile
from typing import Any

from lazyclaw.sheets import snapshot as S
from lazyclaw.sheets.xlsx_io import snapshot_to_xlsx

logger = logging.getLogger(__name__)


def _to_native(value: Any) -> Any:
    """Unwrap xlcalculator/numpy scalars into plain Python values."""
    if hasattr(value, "value"):
        value = value.value
    try:  # numpy scalar → python scalar
        import numpy as np

        if isinstance(value, np.generic):
            return value.item()
    except Exception:  # pragma: no cover - numpy always present via xlcalculator
        pass
    return value


def _formula_targets(snap: dict[str, Any]) -> list[tuple[str, str, int, int]]:
    """Collect ``(sheet_id, sheet_name, row, col)`` for every formula cell."""
    sheets = snap.get("sheets") or {}
    order = snap.get("sheetOrder") or list(sheets.keys())
    targets: list[tuple[str, str, int, int]] = []
    for sid in order:
        sdata = sheets.get(sid, {})
        name = sdata.get("name") or sid
        for r_key, cols in (sdata.get("cellData") or {}).items():
            for c_key, cell in (cols or {}).items():
                if cell and cell.get("f"):
                    targets.append((sid, name, int(r_key), int(c_key)))
    return targets


def recalc(snap: dict[str, Any]) -> dict[str, Any]:
    """Return a NEW snapshot with computed values filled where possible."""
    targets = _formula_targets(snap)
    out = copy.deepcopy(snap)
    if not targets:
        return out

    try:
        from xlcalculator import Evaluator, ModelCompiler

        xlsx_bytes = snapshot_to_xlsx(snap)
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(xlsx_bytes)
                tmp_path = tmp.name
            model = ModelCompiler().read_and_parse_archive(tmp_path)
            evaluator = Evaluator(model)
            for sid, name, r, c in targets:
                addr = f"{name}!{S.rc_to_a1(r, c)}"
                try:
                    out["sheets"][sid]["cellData"][str(r)][str(c)]["v"] = _to_native(
                        evaluator.evaluate(addr)
                    )
                except Exception:
                    logger.debug("recalc: could not evaluate %s", addr)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    except Exception:
        logger.warning("recalc failed; returning snapshot unchanged", exc_info=True)

    return out
