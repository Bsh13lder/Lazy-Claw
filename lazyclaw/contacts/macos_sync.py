"""macOS Contacts.app → encrypted contact store, via JXA + osascript.

First run requires Contacts permission for whichever process invoked
``osascript`` (typically Terminal.app or iTerm.app). macOS pops a
permission dialog the first time; if previously denied, the user must
re-enable it in System Settings → Privacy & Security → Contacts.

We detect the silent-deny case (only the "Me" card returned, all handle
arrays empty) and surface a clear instruction so the user knows what to do.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from lazyclaw.config import Config
from lazyclaw.contacts.store import upsert_from_macos

logger = logging.getLogger(__name__)


# JXA script as a single string. We intentionally avoid template-style
# interpolation so the script is exactly the same bytes every run — easier
# for the OS permission system to whitelist via tccutil.
_JXA_SCRIPT = r"""
const Contacts = Application("Contacts");
const people = Contacts.people();
const out = [];
for (let i = 0; i < people.length; i++) {
  const p = people[i];
  const phones = [];
  try {
    const ph = p.phones();
    for (let j = 0; j < ph.length; j++) {
      const v = ph[j].value();
      if (v) phones.push(v);
    }
  } catch (_e) {}
  const emails = [];
  try {
    const em = p.emails();
    for (let j = 0; j < em.length; j++) {
      const v = em[j].value();
      if (v) emails.push(v);
    }
  } catch (_e) {}
  let instagram = null;
  try {
    const sp = p.socialProfiles();
    for (let j = 0; j < sp.length; j++) {
      const svc = (sp[j].serviceName() || "").toLowerCase();
      if (svc.indexOf("instagram") !== -1) {
        instagram = sp[j].userName();
        break;
      }
    }
  } catch (_e) {}
  let nickname = null;
  try { nickname = p.nickname(); } catch (_e) {}
  let organization = null;
  try { organization = p.organization(); } catch (_e) {}
  out.push({
    id: p.id(),
    name: p.name(),
    nickname: nickname,
    organization: organization,
    phones: phones,
    emails: emails,
    instagram: instagram,
  });
}
JSON.stringify(out);
"""


@dataclass
class SyncResult:
    total_in_address_book: int
    created: int
    updated: int
    unchanged: int
    skipped: int
    permission_likely_denied: bool
    error: str | None = None


def _normalize_phone(raw: str) -> str:
    """Best-effort E.164 normalization without an external library.

    Strips formatting, preserves a leading + when present, and otherwise
    leaves the digits alone. Real E.164 validation would need
    ``phonenumbers``; we deliberately avoid the dependency for now and
    push robust validation to ``sock.onWhatsApp()`` in mcp-whatsapp.
    """
    if not raw:
        return ""
    raw = raw.strip()
    if raw.startswith("+"):
        digits = "+" + "".join(c for c in raw[1:] if c.isdigit())
    else:
        digits = "".join(c for c in raw if c.isdigit())
    return digits


async def fetch_macos_contacts() -> tuple[list[dict], str | None]:
    """Run the JXA fetch. Returns (contacts, error_message)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-l",
            "JavaScript",
            "-e",
            _JXA_SCRIPT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        return [], "macOS Contacts fetch timed out after 60s"
    except FileNotFoundError:
        return [], "osascript not found — this skill only runs on macOS"
    except Exception as exc:  # pragma: no cover
        return [], f"osascript invocation failed: {exc}"

    if proc.returncode != 0:
        err = (stderr or b"").decode(errors="replace").strip()
        return [], f"osascript exited {proc.returncode}: {err}"

    try:
        raw = (stdout or b"").decode(errors="replace").strip()
        data = json.loads(raw) if raw else []
    except (ValueError, TypeError) as exc:
        return [], f"failed to parse JXA output: {exc}"

    if not isinstance(data, list):
        return [], "unexpected JXA output shape"

    return data, None


def _looks_like_permission_denied(contacts: list[dict]) -> bool:
    """JXA returns the user's own 'Me' card even when Contacts access is
    silently denied. Detect: 0 or 1 contact AND no handles at all.
    """
    if len(contacts) > 1:
        return False
    if len(contacts) == 0:
        return True  # empty book OR full denial — treat as suspicious
    only = contacts[0]
    return not any(only.get(k) for k in ("phones", "emails", "instagram"))


async def sync_from_macos(config: Config, user_id: str) -> SyncResult:
    """Pull every contact from macOS Contacts.app and upsert into the store."""
    contacts, error = await fetch_macos_contacts()
    if error:
        return SyncResult(0, 0, 0, 0, 0, False, error=error)

    if _looks_like_permission_denied(contacts):
        return SyncResult(
            total_in_address_book=len(contacts),
            created=0,
            updated=0,
            unchanged=0,
            skipped=0,
            permission_likely_denied=True,
            error=(
                "macOS Contacts access appears to be denied. Open "
                "System Settings → Privacy & Security → Contacts and "
                "enable access for the app running lazyclaw "
                "(Terminal.app, iTerm.app, etc.). Then re-run this sync."
            ),
        )

    created = updated = unchanged = skipped = 0
    for raw in contacts:
        # Skip contacts with no name AND no phone — they're useless for
        # any kind of name lookup or messaging.
        name = (raw.get("name") or "").strip()
        phones_raw = raw.get("phones") or []
        emails_raw = raw.get("emails") or []
        if not name and not phones_raw and not emails_raw:
            skipped += 1
            continue
        if not name:
            # Use first phone as a placeholder name so the row isn't anonymous.
            name = phones_raw[0] if phones_raw else (emails_raw[0] if emails_raw else "Unnamed")

        phones = [p for p in (_normalize_phone(p) for p in phones_raw) if p]
        normalized = {
            "id": raw.get("id"),
            "name": name,
            "phones": phones,
            "emails": [e.strip() for e in emails_raw if e and e.strip()],
            "instagram": (raw.get("instagram") or "").strip() or None,
        }
        try:
            result = await upsert_from_macos(config, user_id, normalized)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("upsert_from_macos failed for %s: %s", normalized.get("id"), exc)
            skipped += 1
            continue

        record, action = result if isinstance(result, tuple) else (None, "skipped")
        if action == "created":
            created += 1
        elif action == "updated":
            updated += 1
        elif action == "unchanged":
            unchanged += 1
        else:
            skipped += 1

    return SyncResult(
        total_in_address_book=len(contacts),
        created=created,
        updated=updated,
        unchanged=unchanged,
        skipped=skipped,
        permission_likely_denied=False,
    )
