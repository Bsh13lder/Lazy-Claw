"""Private encrypted word processor — Univer ``IDocumentData`` per doc.

Mirrors :mod:`lazyclaw.sheets`: each document is stored as ONE AES-256-GCM
encrypted JSON blob (Univer's native ``IDocumentData`` snapshot). ``.docx`` /
``.pdf`` are derived export formats, never the source of truth. All access is
scoped by ``user_id`` (no cross-user data).
"""
