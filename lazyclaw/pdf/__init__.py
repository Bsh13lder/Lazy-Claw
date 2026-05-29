"""PDF toolkit — agent-driveable PDF operations + an encrypted PDF store.

Three layers, mirroring :mod:`lazyclaw.sheets`:

- :mod:`lazyclaw.pdf.ops` — pure, stateless functions on PDF *bytes*
  (page count, text/table extraction, merge/split/rotate/delete pages, AcroForm
  fields + fill, text/signature overlay, visual redaction, flatten, generate).
- :mod:`lazyclaw.pdf.store` — AES-256-GCM encrypted store over the
  ``pdf_files`` table. ``payload`` is ``encrypt_field(base64(pdf_bytes))`` so the
  binary survives the text column; ``pages`` is plaintext for the sidebar.
- agent skills (:mod:`lazyclaw.skills.builtin.pdf`) + web routes
  (:mod:`lazyclaw.gateway.routes.pdf`) sit on top.

LICENSE: only permissively-licensed engines are used — pypdf (BSD),
reportlab (BSD), pdfplumber (MIT), pikepdf (MPL-2.0). PyMuPDF/fitz and borb
(both AGPL) are NEVER imported, to keep this project MIT-clean.

"Editing" a PDF here means structural / overlay operations (fill forms, stamp
text or a signature, merge/split/rotate/delete pages, extract text/tables,
visually redact, flatten, generate). It does NOT mean reflow-editing the body
text of an existing PDF — that is not possible with these engines and is not
attempted.
"""

from __future__ import annotations
