"""Surgical scrub of leaked credential content from encrypted stores.

Scans personal_memory, daily_logs, agent_messages, and notes (LazyBrain) for
rows whose decrypted content looks like it contains a credential (n8n API
key, JWT, bearer token, etc.). Prints REDACTED previews so secrets never
appear on stdout; only deletes rows when ``--apply`` is set.

Usage:
    python scripts/scrub_n8n_key.py <user_id>                 # dry-run
    python scripts/scrub_n8n_key.py <user_id> --apply         # actually delete
    python scripts/scrub_n8n_key.py <user_id> --key SUFFIX    # also match rows
                                                              #   containing SUFFIX
                                                              #   (use a short tail
                                                              #   of the leaked
                                                              #   value; never paste
                                                              #   the full key)

Dry-run is the default — always inspect the output before re-running with
``--apply``. No vault entries are touched.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re

from lazyclaw.config import load_config
from lazyclaw.crypto.encryption import decrypt_field, user_aad
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logging.basicConfig(level=logging.WARNING)

# ── Heuristics ──────────────────────────────────────────────────────────

# Tokens of the "this is clearly a credential" shape. Match the secret
# itself so the redactor can mask it in the preview. Branded-only — no
# generic "long opaque string" heuristic, which false-positives heavily
# on tracker URLs in journal notes.
_SECRET_RE = re.compile(
    r"(?:eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"  # JWT
    r"|(?:sk-[A-Za-z0-9]{20,})"
    r"|(?:ghp_[A-Za-z0-9]{30,})"
    r"|(?:GOCSPX-[A-Za-z0-9_-]{10,})"
    r"|(?:AIza[0-9A-Za-z_-]{35})"
    r"|(?:xox[baprs]-[A-Za-z0-9-]{10,})"
    r"|(?:\b[0-9a-f]{40,}\b)"  # raw hex (keys often)
)

# Google OAuth client_id is also sensitive but has a distinctive shape.
_GOOGLE_OAUTH_ID_RE = re.compile(
    r"\b\d{6,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com\b"
)

# Context words present near a secret — each hit still REQUIRES a secret
# shape in the same row, so false positives stay near zero.
_MARKERS = (
    "n8n",
    "api key",
    "api_key",
    "apikey",
    "bearer",
    "token",
    "webhook",
    "secret",
    "credential",
    "oauth",
)


def _redact(text: str) -> str:
    """Mask secret-shaped substrings so previews are safe to print."""
    def _sub(m: re.Match) -> str:
        s = m.group(0)
        if len(s) <= 10:
            return "<REDACTED>"
        return f"{s[:4]}…<{len(s)}c>…{s[-3:]}"
    text = _SECRET_RE.sub(_sub, text)
    text = _GOOGLE_OAUTH_ID_RE.sub(
        lambda m: f"<google-oauth-id:{m.group(0)[:12]}…>", text,
    )
    return text


def _classify(content: str, extra: str | None) -> str | None:
    """Return short tag naming the hit class, or None if no match."""
    if not content:
        return None
    if extra and extra in content:
        return "user-provided-suffix"
    secret_hit = _SECRET_RE.search(content)
    oauth_id_hit = _GOOGLE_OAUTH_ID_RE.search(content)
    lower = content.lower()
    marker = next((m for m in _MARKERS if m in lower), None)
    if secret_hit and marker:
        raw = secret_hit.group(0)
        shape = (
            "JWT" if raw.startswith("eyJ")
            else "sk-key" if raw.startswith("sk-")
            else "github-pat" if raw.startswith("ghp_")
            else "google-client-secret" if raw.startswith("GOCSPX-")
            else "google-api-key" if raw.startswith("AIza")
            else "slack-token" if raw.startswith("xox")
            else "hex-key"
        )
        return f"{shape}+near-'{marker}'"
    if oauth_id_hit:
        return "google-oauth-id"
    return None


def _matches(content: str, extra: str | None) -> bool:
    return _classify(content, extra) is not None


# ── Scrub drivers ───────────────────────────────────────────────────────

async def _scan_personal_memory(config, user_id, dek, extra):
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, memory_type, content, created_at FROM personal_memory "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        raw = await rows.fetchall()
    hits = []
    for rid, mtype, enc, created in raw:
        plain = decrypt_field(enc, dek) or ""
        if _matches(plain, extra):
            hits.append((rid, mtype, created, plain))
    return len(raw), hits


async def _scan_daily_logs(config, user_id, dek, extra):
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, date, summary, key_events FROM daily_logs "
            "WHERE user_id = ? ORDER BY date DESC",
            (user_id,),
        )
        raw = await rows.fetchall()
    hits = []
    for rid, date, enc_summary, enc_events in raw:
        summary = decrypt_field(enc_summary, dek, fallback="") or ""
        events = decrypt_field(enc_events, dek, fallback="") or ""
        combined = f"{summary}\n{events}"
        if _matches(combined, extra):
            hits.append((rid, date, combined))
    return len(raw), hits


async def _scan_agent_messages(config, user_id, dek, extra):
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, role, chat_session_id, content, created_at "
            "FROM agent_messages WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        raw = await rows.fetchall()
    hits = []
    for rid, role, session_id, enc, created in raw:
        plain = decrypt_field(enc, dek, fallback="") or ""
        if _matches(plain, extra):
            hits.append((rid, role, session_id, created, plain))
    return len(raw), hits


async def _scan_notes(config, user_id, dek, extra):
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, title, content, created_at FROM notes "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        raw = await rows.fetchall()
    title_aad = user_aad(user_id, "notes:title")
    content_aad = user_aad(user_id, "notes:content")
    hits = []
    for rid, enc_title, enc_content, created in raw:
        title = decrypt_field(enc_title, dek, title_aad, fallback="") or ""
        content = decrypt_field(enc_content, dek, content_aad, fallback="") or ""
        combined = f"{title}\n{content}"
        if _matches(combined, extra):
            hits.append((rid, title, created, combined))
    return len(raw), hits


async def _delete(config, user_id, table, ids):
    if not ids:
        return 0
    async with db_session(config) as db:
        placeholders = ",".join("?" * len(ids))
        cursor = await db.execute(
            f"DELETE FROM {table} WHERE user_id = ? AND id IN ({placeholders})",
            (user_id, *ids),
        )
        await db.commit()
        return cursor.rowcount


def _print_hit_header(table: str, total: int, hits: list) -> None:
    print(f"\n[{table}] scanned {total} rows, matched {len(hits)}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("user_id")
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete matched rows (default: dry-run)")
    parser.add_argument("--key", default=None,
                        help="Additional literal substring to match (use a short tail)")
    parser.add_argument("--ignore-class", default="",
                        help="Comma-separated class prefixes to skip "
                             "(e.g. 'hex-key' to ignore hex tracker-token FPs)")
    args = parser.parse_args()

    ignore = tuple(s.strip() for s in args.ignore_class.split(",") if s.strip())

    def _keep(classes_fn, rows):
        return [r for r in rows
                if not any(
                    (classes_fn(r) or "").startswith(ic) for ic in ignore
                )]

    config = load_config()
    dek = await get_user_dek(config, args.user_id)

    pm_total, pm_hits = await _scan_personal_memory(config, args.user_id, dek, args.key)
    dl_total, dl_hits = await _scan_daily_logs(config, args.user_id, dek, args.key)
    am_total, am_hits = await _scan_agent_messages(config, args.user_id, dek, args.key)
    nt_total, nt_hits = await _scan_notes(config, args.user_id, dek, args.key)

    if ignore:
        # Tuple shapes per scan:
        #   personal_memory: (rid, mtype, created, plain)
        #   daily_logs:      (rid, date, combined)
        #   agent_messages:  (rid, role, session_id, created, plain)
        #   notes:           (rid, title, created, combined)
        pm_hits = _keep(lambda r: _classify(r[3], args.key), pm_hits)
        dl_hits = _keep(lambda r: _classify(r[2], args.key), dl_hits)
        am_hits = _keep(lambda r: _classify(r[4], args.key), am_hits)
        nt_hits = _keep(lambda r: _classify(r[3], args.key), nt_hits)
        print(f"(skipping classes: {', '.join(ignore)})")

    _print_hit_header("personal_memory", pm_total, pm_hits)
    for rid, mtype, created, plain in pm_hits:
        print(f"  - {rid}  [{mtype}]  {created}")
        print(f"      {_redact(plain)[:200]}")

    _print_hit_header("daily_logs", dl_total, dl_hits)
    for rid, date, combined in dl_hits:
        print(f"  - {rid}  {date}")
        print(f"      {_redact(combined)[:200]}")

    _print_hit_header("agent_messages", am_total, am_hits)
    for rid, role, session_id, created, plain in am_hits:
        tag = _classify(plain, args.key) or "?"
        print(f"  - {rid}  [{role}]  {created}  [{tag}]")
        print(f"      {_redact(plain)[:200]}")

    _print_hit_header("notes", nt_total, nt_hits)
    for rid, title, created, combined in nt_hits:
        tag = _classify(combined, args.key) or "?"
        print(f"  - {rid}  '{title[:60]}'  {created}  [{tag}]")
        print(f"      {_redact(combined)[:200]}")

    total_hits = len(pm_hits) + len(dl_hits) + len(am_hits) + len(nt_hits)
    print(f"\nTOTAL matches: {total_hits}")

    if not args.apply:
        print("(dry-run — re-run with --apply to delete)")
        return

    if total_hits == 0:
        print("nothing to delete")
        return

    pm_del = await _delete(config, args.user_id, "personal_memory",
                           [h[0] for h in pm_hits])
    dl_del = await _delete(config, args.user_id, "daily_logs",
                           [h[0] for h in dl_hits])
    am_del = await _delete(config, args.user_id, "agent_messages",
                           [h[0] for h in am_hits])
    nt_del = await _delete(config, args.user_id, "notes",
                           [h[0] for h in nt_hits])
    print(f"\nDELETED: {pm_del} personal_memory + {dl_del} daily_logs + "
          f"{am_del} agent_messages + {nt_del} notes")


if __name__ == "__main__":
    asyncio.run(main())
