"""Private encrypted spreadsheets for LazyClaw.

A sheet is persisted as one AES-256-GCM JSON blob (``sheets.payload``) holding
Univer's native ``IWorkbookData`` snapshot — the same one-blob-per-document
pattern as ``lazybrain/canvas.py``. The web UI edits that snapshot directly via
the embedded Univer editor; agent skills mutate it through the pure helpers in
:mod:`lazyclaw.sheets.snapshot`; ``.xlsx`` is a *derived* export format
(:mod:`lazyclaw.sheets.xlsx_io`), never the source of truth.
"""
