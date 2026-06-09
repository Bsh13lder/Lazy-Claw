# Unified Cross-Channel Comms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every messaging channel (WhatsApp/Email/Instagram/Telegram) deliver new-message notifications into the Flutter app, give the app a unified inbox with reply (direct or "Ask AI"), and add an autonomous `ConversationTask` runner that holds a back-and-forth on a channel until it has an answer and reports back.

**Architecture:** A single `notify()` delivery funnel that always records to the in-app feed (fixing the Telegram-only bug); a new `lazyclaw/comms/` module holding a thread-metadata store, a channel-agnostic `ChannelGateway`, and a heartbeat-driven `ConversationTask` state machine; new `/api/inbox/*` routes; and a Flutter Inbox tab built from the existing `Lz*` kit. Inbox message bodies are read **live** through the existing MCP tools (not duplicated).

**Tech Stack:** Python 3 (FastAPI, asyncio, aiosqlite), AES-256-GCM via `lazyclaw.crypto`, pytest. Flutter (Riverpod, go_router, Dio, web_socket_channel, sqflite_sqlcipher, flutter_local_notifications).

---

## Conventions & test harness notes

- **Encryption:** mirror `lazyclaw/tasks/store.py`. `from lazyclaw.crypto.encryption import encrypt, decrypt_field`; `from lazyclaw.crypto.key_manager import get_user_dek`. Encrypt user content with `encrypt(value, key)`; decrypt with `decrypt_field(value, key)`. Plaintext columns: ids, `user_id`, `channel`, `contact_handle`, status, all timestamps.
- **DB access:** `from lazyclaw.db.connection import db_session`; `async with db_session(config) as db:`.
- **Migrations:** add `CREATE TABLE IF NOT EXISTS ...` blocks and idempotent `ALTER TABLE` entries inside the existing migration section of `lazyclaw/db/connection.py` (the same place the `notifications` table is created, ~line 278). Wrap each in `try/except` + `logger.debug(..., exc_info=True)` exactly like the existing blocks.
- **Tests:** use the existing pytest fixtures (look in `tests/conftest.py`). Mirror an existing store test for fixture usage — `config` is a temp-DB `Config`, and a registered user id is available (register one via the same helper existing store tests use). Where a test needs a registered user, copy the setup lines from `tests/` tests that touch `tasks/store.py` or `notifications/feed_store.py`.
- **Run a single test:** `pytest tests/path/test_x.py::test_name -v`. **Run a file:** `pytest tests/path/test_x.py -v`.
- **Commits:** stage only the files you changed by explicit path (a pre-commit hook auto-stages adjacent files otherwise). `git add <exact paths> && git commit -m "..."`. No AI attribution in messages.
- **Phasing:** Phases A→E land in order; each is independently testable. Checkpoint with the user between phases.

---

# PHASE A — The `notify()` funnel (fixes the Telegram-only bug)

### Task A1: Add `meta` column to the notification feed

**Files:**
- Modify: `lazyclaw/db/connection.py` (migration section, ~line 290 after the `notifications` table block)
- Modify: `lazyclaw/notifications/feed_store.py`
- Test: `tests/notifications/test_feed_store_meta.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/notifications/test_feed_store_meta.py
import json
import pytest
from lazyclaw.notifications.feed_store import record_notification, get_notifications_since

@pytest.mark.asyncio
async def test_record_and_read_meta(config, user_id):
    rec = await record_notification(
        config, user_id, "channel_message", "New WhatsApp message", "Hi there",
        meta={"thread_ref": {"channel": "whatsapp", "contact": "+34600000000"}},
    )
    assert rec["meta"]["thread_ref"]["channel"] == "whatsapp"
    feed = await get_notifications_since(config, user_id, None)
    got = [n for n in feed["notifications"] if n["id"] == rec["id"]][0]
    assert got["meta"]["thread_ref"]["contact"] == "+34600000000"

@pytest.mark.asyncio
async def test_meta_defaults_none(config, user_id):
    rec = await record_notification(config, user_id, "info", "t", "b")
    assert rec["meta"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/notifications/test_feed_store_meta.py -v`
Expected: FAIL (`record_notification` has no `meta` param / `meta` key missing).

- [ ] **Step 3: Add the migration**

In `lazyclaw/db/connection.py`, immediately after the `notifications` table `CREATE TABLE` block, add:

```python
            # meta: encrypted JSON sidecar for feed entries (thread_ref for
            # tap-to-open deep links on channel_message notifications).
            try:
                cols = await db.execute("PRAGMA table_info(notifications)")
                names = [r[1] for r in await cols.fetchall()]
                if "meta" not in names:
                    await db.execute("ALTER TABLE notifications ADD COLUMN meta TEXT")
            except Exception:
                logger.debug("notifications.meta migration skipped", exc_info=True)
```

- [ ] **Step 4: Thread `meta` through `feed_store.py`**

Update `record_notification` signature and body:

```python
async def record_notification(
    config: Config,
    user_id: str,
    kind: str,
    title: str,
    body: str,
    meta: dict | None = None,
) -> dict:
    key = await get_user_dek(config, user_id)
    notif_id = str(uuid4())
    created_at = _now()
    safe_kind = (kind or "info").strip() or "info"
    enc_title = encrypt(title or "", key)
    enc_body = encrypt(body or "", key)
    enc_meta = encrypt(json.dumps(meta), key) if meta is not None else None
    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO notifications "
            "(id, user_id, kind, title, body, meta, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (notif_id, user_id, safe_kind, enc_title, enc_body, enc_meta, created_at),
        )
        await db.commit()
    return {
        "id": notif_id, "kind": safe_kind, "title": title or "",
        "body": body or "", "meta": meta, "created_at": created_at,
    }
```

In `get_notifications_since`, select `meta` (add to the SELECT column list) and decrypt per row:

```python
        # after decrypting title/body for row r (meta is the new last column):
        raw_meta = decrypt_field(r["meta"], key) if r["meta"] else None
        item["meta"] = json.loads(raw_meta) if raw_meta else None
```

Ensure `import json` is present.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/notifications/test_feed_store_meta.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lazyclaw/db/connection.py lazyclaw/notifications/feed_store.py tests/notifications/test_feed_store_meta.py
git commit -m "feat(notifications): encrypted meta sidecar on feed entries"
```

---

### Task A2: Extract `_send_telegram_raw` from `push_telegram`

Splits the raw Telegram send (no feed routing) out of `push_telegram` so the new funnel can record the feed itself without double-recording.

**Files:**
- Modify: `lazyclaw/notifications/push.py`
- Test: `tests/notifications/test_push_raw.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/notifications/test_push_raw.py
import pytest
from lazyclaw.notifications import push as push_mod

@pytest.mark.asyncio
async def test_send_raw_no_token_returns_false(monkeypatch, config):
    # No telegram token configured -> raw send returns False, never touches feed.
    monkeypatch.delenv("TELEGRAM_ADMIN_CHAT", raising=False)
    ok = await push_mod._send_telegram_raw(config, "hello", parse_mode=None)
    assert ok is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/notifications/test_push_raw.py -v`
Expected: FAIL (`_send_telegram_raw` does not exist).

- [ ] **Step 3: Refactor `push.py`**

Add `_send_telegram_raw` containing everything from the current `push_telegram` body **after** the `_route_to_feed_or_skip` call (token resolve → truncate → build keyboard → `Bot.send_message`):

```python
async def _send_telegram_raw(
    config: Any,
    text: str,
    *,
    parse_mode: str | None = "Markdown",
    max_chars: int = 3800,
    inline_keyboard: Sequence[Sequence[dict]] | None = None,
) -> bool:
    """Send to the admin Telegram chat. NO feed routing. Returns True on send."""
    token = getattr(config, "telegram_bot_token", None) if config else None
    chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT")
    if not token or not chat_id:
        logger.debug("telegram send skipped: missing token or admin chat id")
        return False
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    try:
        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup  # type: ignore
    except ImportError:
        logger.debug("telegram send skipped: telegram package not installed")
        return False
    reply_markup = None
    if inline_keyboard:
        rows: list[list[InlineKeyboardButton]] = []
        for row in inline_keyboard:
            btn_row: list[InlineKeyboardButton] = []
            for btn in row:
                if not isinstance(btn, dict):
                    continue
                txt = btn.get("text"); cd = btn.get("callback_data")
                if not txt or not cd:
                    continue
                btn_row.append(InlineKeyboardButton(str(txt), callback_data=str(cd)[:64]))
            if btn_row:
                rows.append(btn_row)
        if rows:
            reply_markup = InlineKeyboardMarkup(rows)
    bot = Bot(token=token)
    await bot.send_message(
        chat_id=int(chat_id), text=text, parse_mode=parse_mode,
        disable_web_page_preview=True, reply_markup=reply_markup,
    )
    return True
```

Rewrite `push_telegram` to delegate (preserving its existing feed-routing behavior):

```python
async def push_telegram(
    config: Any, text: str, *, parse_mode: str | None = "Markdown",
    max_chars: int = 3800, inline_keyboard: Sequence[Sequence[dict]] | None = None,
) -> bool:
    send_telegram, delivered_as_app = await _route_to_feed_or_skip(config, text)
    if not send_telegram:
        return delivered_as_app
    return await _send_telegram_raw(
        config, text, parse_mode=parse_mode, max_chars=max_chars,
        inline_keyboard=inline_keyboard,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/notifications/test_push_raw.py -v`
Expected: PASS. Also run the existing push tests: `pytest tests/notifications/ -v` — all green (no behavior change).

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/notifications/push.py tests/notifications/test_push_raw.py
git commit -m "refactor(notifications): extract _send_telegram_raw (no feed routing)"
```

---

### Task A3: The `deliver()` funnel

**Files:**
- Create: `lazyclaw/notifications/dispatch.py`
- Test: `tests/notifications/test_dispatch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/notifications/test_dispatch.py
import pytest
from unittest.mock import AsyncMock
from lazyclaw.notifications import dispatch
from lazyclaw.notifications.feed_store import get_notifications_since

@pytest.mark.asyncio
async def test_channel_message_always_records_even_in_telegram_mode(monkeypatch, config, user_id):
    # Force telegram-only channel; channel_message must STILL hit the feed.
    monkeypatch.setattr(dispatch, "get_notification_channel", AsyncMock(return_value="telegram"))
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(dispatch, "_send_telegram_raw", sent)
    await dispatch.deliver(
        config, user_id, title="New WhatsApp message", body="Hi",
        kind="channel_message",
        thread_ref={"channel": "whatsapp", "contact": "+34600000000"},
    )
    feed = await get_notifications_since(config, user_id, None)
    assert any(n["kind"] == "channel_message" for n in feed["notifications"])
    sent.assert_awaited()  # telegram mode -> also pushed

@pytest.mark.asyncio
async def test_app_mode_records_but_no_telegram(monkeypatch, config, user_id):
    monkeypatch.setattr(dispatch, "get_notification_channel", AsyncMock(return_value="app"))
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(dispatch, "_send_telegram_raw", sent)
    await dispatch.deliver(config, user_id, title="t", body="b", kind="info")
    sent.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/notifications/test_dispatch.py -v`
Expected: FAIL (`dispatch` module missing).

- [ ] **Step 3: Implement `dispatch.py`**

```python
"""Single notification delivery funnel.

Always records channel-message notifications to the in-app feed (so the
Flutter app receives them regardless of the telegram/app/both toggle), and
sends to Telegram only when the user's channel setting includes it.
"""
from __future__ import annotations

from typing import Any, Sequence

from lazyclaw.notifications.channel import (
    get_notification_channel, should_record_feed, should_send_telegram,
)
from lazyclaw.notifications.feed_store import record_notification
from lazyclaw.notifications.push import _send_telegram_raw

# kinds that must always reach the phone regardless of the channel toggle
_ALWAYS_FEED_KINDS = frozenset({"channel_message", "conversation_result"})


async def deliver(
    config: Any,
    user_id: str,
    *,
    title: str,
    body: str,
    kind: str = "info",
    inline_keyboard: Sequence[Sequence[dict]] | None = None,
    thread_ref: dict | None = None,
) -> bool:
    """Record to feed (per channel + always for channel messages), then maybe Telegram."""
    channel = await get_notification_channel(config, user_id)
    if should_record_feed(channel) or kind in _ALWAYS_FEED_KINDS:
        meta = {"thread_ref": thread_ref} if thread_ref else None
        await record_notification(config, user_id, kind, title, body, meta=meta)
    if should_send_telegram(channel):
        text = f"{title}\n{body}".strip() if title else body
        return await _send_telegram_raw(
            config, text, parse_mode=None, inline_keyboard=inline_keyboard,
        )
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/notifications/test_dispatch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/notifications/dispatch.py tests/notifications/test_dispatch.py
git commit -m "feat(notifications): unified deliver() funnel (feed + telegram)"
```

---

### Task A4: Route channel-message watcher alerts through `deliver()`

Replaces the hardcoded `push_telegram()` at the MCP-watcher site so WhatsApp/Email/IG alerts reach the feed (and the Flutter app).

**Files:**
- Modify: `lazyclaw/heartbeat/daemon.py:1457` (MCP watcher push)
- Test: `tests/heartbeat/test_mcp_watcher_delivery.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_mcp_watcher_delivery.py
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_mcp_watcher_routes_through_deliver(config, user_id):
    """The MCP watcher push must call notifications.dispatch.deliver with a
    channel_message kind + thread_ref, not push_telegram directly."""
    from lazyclaw.heartbeat.daemon import HeartbeatDaemon
    daemon = HeartbeatDaemon(config)  # adapt to actual constructor
    with patch("lazyclaw.heartbeat.daemon.deliver", new=AsyncMock()) as deliver_mock:
        await daemon._notify_channel_message(
            user_id, service="whatsapp",
            notification="🔔 New message from Alice", contact="+34600000000",
            inline_keyboard=[[{"text": "Mute", "callback_data": "m"}]],
        )
        deliver_mock.assert_awaited_once()
        kwargs = deliver_mock.await_args.kwargs
        assert kwargs["kind"] == "channel_message"
        assert kwargs["thread_ref"] == {"channel": "whatsapp", "contact": "+34600000000"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/heartbeat/test_mcp_watcher_delivery.py -v`
Expected: FAIL (`_notify_channel_message` missing).

- [ ] **Step 3: Add the helper + rewire the call site**

At the top of `daemon.py` add: `from lazyclaw.notifications.dispatch import deliver`.

Add a method to `HeartbeatDaemon`:

```python
    async def _notify_channel_message(
        self, user_id: str, *, service: str, notification: str,
        contact: str | None = None,
        inline_keyboard: list[list[dict]] | None = None,
    ) -> None:
        """Deliver a new-channel-message alert via the unified funnel so it
        reaches the in-app feed (Flutter), plus Telegram if enabled."""
        await deliver(
            self._config, user_id,
            title=notification.lstrip("🔔 ").strip()[:80] or "New message",
            body=notification,
            kind="channel_message",
            inline_keyboard=inline_keyboard,
            thread_ref={"channel": service, "contact": contact} if contact else None,
        )
```

Replace the MCP-watcher `push_telegram(...)` block (~line 1457) with:

```python
                    await self._notify_channel_message(
                        user_id, service=_service,
                        notification=notification,
                        contact=new_ctx.get("_latest_contact"),
                        inline_keyboard=keyboard,
                    )
```

(`_service` is already in scope per the existing block; `new_ctx.get("_latest_contact")` is populated in Task B4. If absent it is `None` — harmless.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/heartbeat/test_mcp_watcher_delivery.py -v`
Expected: PASS. Also run `pytest tests/heartbeat/ -v`.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/heartbeat/daemon.py tests/heartbeat/test_mcp_watcher_delivery.py
git commit -m "feat(heartbeat): route channel-message alerts through deliver() funnel"
```

> **Checkpoint A:** New WhatsApp/Email/IG messages now record to the feed. Verify end-to-end after Phase D wires the app; for now confirm `GET /api/notifications` returns a `channel_message` entry after a watcher fires.

---

# PHASE B — Thread-metadata store (unified inbox source of truth)

### Task B1: `comms` models

**Files:**
- Create: `lazyclaw/comms/__init__.py` (empty)
- Create: `lazyclaw/comms/models.py`
- Test: `tests/comms/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/comms/test_models.py
from lazyclaw.comms.models import ChannelThread, ThreadRef

def test_thread_ref_roundtrip():
    ref = ThreadRef(channel="whatsapp", contact="+34600000000")
    assert ref.as_dict() == {"channel": "whatsapp", "contact": "+34600000000"}

def test_channel_thread_is_frozen():
    t = ChannelThread(
        id="t1", user_id="u1", channel="whatsapp", contact_handle="+34600000000",
        contact_name="Alice", last_preview="hi", unread_count=2,
        last_activity="2026-06-09T10:00:00+00:00", last_seen_msg_id="m9",
        created_at="2026-06-09T09:00:00+00:00", updated_at="2026-06-09T10:00:00+00:00",
        deleted_at=None,
    )
    import dataclasses
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        t.unread_count = 3  # type: ignore
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/comms/test_models.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `models.py`**

```python
"""Immutable data models for the unified comms layer."""
from __future__ import annotations

from dataclasses import dataclass

VALID_COMMS_CHANNELS = ("whatsapp", "email", "instagram", "telegram")


@dataclass(frozen=True)
class ThreadRef:
    channel: str
    contact: str
    def as_dict(self) -> dict:
        return {"channel": self.channel, "contact": self.contact}


@dataclass(frozen=True)
class ChannelThread:
    id: str
    user_id: str
    channel: str
    contact_handle: str
    contact_name: str | None
    last_preview: str | None
    unread_count: int
    last_activity: str
    last_seen_msg_id: str | None
    created_at: str
    updated_at: str
    deleted_at: str | None

    def as_dict(self) -> dict:
        return {
            "id": self.id, "channel": self.channel,
            "contact_handle": self.contact_handle, "contact_name": self.contact_name,
            "last_preview": self.last_preview, "unread_count": self.unread_count,
            "last_activity": self.last_activity, "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class Msg:
    """One message in a thread (from a live channel read)."""
    sender: str
    text: str
    timestamp: str
    is_mine: bool = False


@dataclass(frozen=True)
class Contact:
    name: str
    handle: str


@dataclass(frozen=True)
class SendResult:
    ok: bool
    error: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/comms/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/comms/__init__.py lazyclaw/comms/models.py tests/comms/test_models.py
git commit -m "feat(comms): immutable models (ChannelThread, ThreadRef, Msg)"
```

---

### Task B2: `channel_threads` migration

**Files:**
- Modify: `lazyclaw/db/connection.py` (migration section)
- Test: `tests/comms/test_threads_table.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/comms/test_threads_table.py
import pytest
from lazyclaw.db.connection import db_session

@pytest.mark.asyncio
async def test_channel_threads_table_exists(config):
    async with db_session(config) as db:
        cur = await db.execute("PRAGMA table_info(channel_threads)")
        cols = {r[1] for r in await cur.fetchall()}
    assert {"id", "user_id", "channel", "contact_handle", "contact_name",
            "last_preview", "unread_count", "last_activity", "last_seen_msg_id",
            "created_at", "updated_at", "deleted_at"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/comms/test_threads_table.py -v`
Expected: FAIL (no such table).

- [ ] **Step 3: Add the migration**

In `lazyclaw/db/connection.py`, after the notifications/meta block:

```python
            try:
                await db.execute(
                    "CREATE TABLE IF NOT EXISTS channel_threads ("
                    "id TEXT PRIMARY KEY, "
                    "user_id TEXT NOT NULL REFERENCES users(id), "
                    "channel TEXT NOT NULL, "
                    "contact_handle TEXT NOT NULL, "
                    "contact_name TEXT, "          # encrypted
                    "last_preview TEXT, "          # encrypted
                    "unread_count INTEGER NOT NULL DEFAULT 0, "
                    "last_activity TEXT NOT NULL DEFAULT (datetime('now')), "
                    "last_seen_msg_id TEXT, "      # encrypted
                    "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
                    "updated_at TEXT NOT NULL DEFAULT (datetime('now')), "
                    "deleted_at TEXT"
                    ")"
                )
                await db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_threads_unique "
                    "ON channel_threads(user_id, channel, contact_handle)"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_channel_threads_updated "
                    "ON channel_threads(user_id, updated_at)"
                )
            except Exception:
                logger.debug("channel_threads migration skipped", exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/comms/test_threads_table.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/db/connection.py tests/comms/test_threads_table.py
git commit -m "feat(comms): channel_threads table migration"
```

---

### Task B3: `thread_store` CRUD + changes feed

**Files:**
- Create: `lazyclaw/comms/thread_store.py`
- Test: `tests/comms/test_thread_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/comms/test_thread_store.py
import pytest
from lazyclaw.comms import thread_store

@pytest.mark.asyncio
async def test_upsert_creates_then_bumps(config, user_id):
    t = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+34600000000",
        contact_name="Alice", preview="hello", last_seen_msg_id="m1", increment_unread=True,
    )
    assert t["unread_count"] == 1 and t["contact_name"] == "Alice"
    t2 = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+34600000000",
        contact_name="Alice", preview="second", last_seen_msg_id="m2", increment_unread=True,
    )
    assert t2["id"] == t["id"] and t2["unread_count"] == 2 and t2["last_preview"] == "second"

@pytest.mark.asyncio
async def test_mark_read_zeroes_unread(config, user_id):
    t = await thread_store.upsert_thread(
        config, user_id, channel="email", contact_handle="bob@x.com",
        contact_name="Bob", preview="hi", last_seen_msg_id="e1", increment_unread=True,
    )
    await thread_store.mark_thread_read(config, user_id, t["id"])
    got = await thread_store.get_thread(config, user_id, t["id"])
    assert got["unread_count"] == 0

@pytest.mark.asyncio
async def test_changes_includes_deletes(config, user_id):
    t = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+1", contact_name="C",
        preview="p", last_seen_msg_id="m1", increment_unread=False,
    )
    snap = await thread_store.get_thread_changes(config, user_id, None)
    since = snap["now"]
    await thread_store.delete_thread(config, user_id, t["id"])
    delta = await thread_store.get_thread_changes(config, user_id, since)
    assert t["id"] in delta["deleted"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/comms/test_thread_store.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `thread_store.py`**

```python
"""Encrypted store for cross-channel inbox threads (metadata only —
message bodies are read live via ChannelGateway)."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt, decrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row, key: bytes) -> dict:
    return {
        "id": row["id"], "channel": row["channel"],
        "contact_handle": row["contact_handle"],
        "contact_name": decrypt_field(row["contact_name"], key),
        "last_preview": decrypt_field(row["last_preview"], key),
        "unread_count": row["unread_count"],
        "last_activity": row["last_activity"],
        "last_seen_msg_id": decrypt_field(row["last_seen_msg_id"], key),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "deleted_at": row["deleted_at"],
    }


async def upsert_thread(
    config: Config, user_id: str, *, channel: str, contact_handle: str,
    contact_name: str | None = None, preview: str | None = None,
    last_seen_msg_id: str | None = None, increment_unread: bool = False,
) -> dict:
    key = await get_user_dek(config, user_id)
    now = _now()
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT id, unread_count FROM channel_threads "
            "WHERE user_id=? AND channel=? AND contact_handle=?",
            (user_id, channel, contact_handle),
        )
        existing = await cur.fetchone()
        if existing is None:
            tid = str(uuid4())
            await db.execute(
                "INSERT INTO channel_threads "
                "(id,user_id,channel,contact_handle,contact_name,last_preview,"
                "unread_count,last_activity,last_seen_msg_id,created_at,updated_at,deleted_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (tid, user_id, channel, contact_handle,
                 encrypt(contact_name or "", key), encrypt(preview or "", key),
                 1 if increment_unread else 0, now,
                 encrypt(last_seen_msg_id or "", key), now, now),
            )
        else:
            tid = existing["id"]
            new_unread = existing["unread_count"] + (1 if increment_unread else 0)
            await db.execute(
                "UPDATE channel_threads SET contact_name=?, last_preview=?, "
                "unread_count=?, last_activity=?, last_seen_msg_id=?, updated_at=?, "
                "deleted_at=NULL WHERE id=?",
                (encrypt(contact_name or "", key), encrypt(preview or "", key),
                 new_unread, now, encrypt(last_seen_msg_id or "", key), now, tid),
            )
        await db.commit()
    return await get_thread(config, user_id, tid)


async def get_thread(config: Config, user_id: str, thread_id: str) -> dict | None:
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT * FROM channel_threads WHERE id=? AND user_id=?", (thread_id, user_id),
        )
        row = await cur.fetchone()
    return _row_to_dict(row, key) if row else None


async def list_threads(config: Config, user_id: str, channel: str | None = None) -> list[dict]:
    key = await get_user_dek(config, user_id)
    sql = ("SELECT * FROM channel_threads WHERE user_id=? AND deleted_at IS NULL")
    params: list = [user_id]
    if channel:
        sql += " AND channel=?"; params.append(channel)
    sql += " ORDER BY last_activity DESC"
    async with db_session(config) as db:
        cur = await db.execute(sql, tuple(params))
        rows = await cur.fetchall()
    return [_row_to_dict(r, key) for r in rows]


async def mark_thread_read(config: Config, user_id: str, thread_id: str) -> bool:
    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE channel_threads SET unread_count=0, updated_at=? "
            "WHERE id=? AND user_id=?", (_now(), thread_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_thread(config: Config, user_id: str, thread_id: str) -> bool:
    now = _now()
    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE channel_threads SET deleted_at=?, updated_at=? "
            "WHERE id=? AND user_id=?", (now, now, thread_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_thread_changes(config: Config, user_id: str, since: str | None) -> dict:
    key = await get_user_dek(config, user_id)
    now_iso = _now()
    sql = "SELECT * FROM channel_threads WHERE user_id=?"
    params: list = [user_id]
    if since is not None:
        sql += " AND updated_at > ?"; params.append(since)
    sql += " ORDER BY updated_at ASC"
    async with db_session(config) as db:
        cur = await db.execute(sql, tuple(params))
        rows = await cur.fetchall()
    threads, deleted = [], []
    for r in rows:
        if r["deleted_at"] is not None:
            deleted.append(r["id"])
        else:
            threads.append(_row_to_dict(r, key))
    return {"threads": threads, "deleted": deleted, "now": now_iso}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/comms/test_thread_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/comms/thread_store.py tests/comms/test_thread_store.py
git commit -m "feat(comms): thread_store CRUD + changes feed"
```

---

### Task B4: Upsert threads when the MCP watcher finds new messages

**Files:**
- Modify: `lazyclaw/heartbeat/mcp_watcher.py` (after `_extract_new_items`, ~line 257)
- Test: `tests/heartbeat/test_watcher_thread_upsert.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_watcher_thread_upsert.py
import pytest
from lazyclaw.heartbeat.mcp_watcher import _upsert_threads_for_items
from lazyclaw.comms import thread_store

@pytest.mark.asyncio
async def test_upsert_threads_for_items(config, user_id):
    items = [
        {"id": "m1", "from": "Alice", "handle": "+34600000000", "body": "Hi!"},
        {"id": "m2", "from": "Bob", "handle": "bob@x.com", "body": "Yo"},
    ]
    latest = await _upsert_threads_for_items(config, user_id, "whatsapp", items)
    threads = await thread_store.list_threads(config, user_id, channel="whatsapp")
    assert len(threads) == 2
    assert latest == "+34600000000"  # last item's contact handle
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/heartbeat/test_watcher_thread_upsert.py -v`
Expected: FAIL (`_upsert_threads_for_items` missing).

- [ ] **Step 3: Implement + wire**

In `mcp_watcher.py` add:

```python
async def _upsert_threads_for_items(
    config, user_id: str, service: str, new_items: list[dict],
) -> str | None:
    """Mirror each new channel message into channel_threads. Returns the
    most-recent contact handle (for the notification thread_ref)."""
    from lazyclaw.comms import thread_store
    latest_handle = None
    for item in new_items:
        handle = item.get("handle") or item.get("from") or ""
        if not handle:
            continue
        await thread_store.upsert_thread(
            config, user_id, channel=service, contact_handle=str(handle),
            contact_name=item.get("from"), preview=item.get("body"),
            last_seen_msg_id=str(item.get("id") or ""), increment_unread=True,
        )
        latest_handle = str(handle)
    return latest_handle
```

After the `new_items = _extract_new_items(...)` line (~257), add:

```python
    if new_items and config and user_id:
        try:
            latest = await _upsert_threads_for_items(config, user_id, service, new_items)
            updated_ctx["_latest_contact"] = latest
        except Exception:
            logger.debug("thread upsert skipped", exc_info=True)
```

(Use the existing returned-context variable name in this function for `updated_ctx`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/heartbeat/test_watcher_thread_upsert.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/heartbeat/mcp_watcher.py tests/heartbeat/test_watcher_thread_upsert.py
git commit -m "feat(comms): upsert inbox threads on new channel messages"
```

> **Checkpoint B:** threads now populate from live watcher activity. `thread_store.list_threads` returns one row per contact with unread counts.

---

# PHASE C — `ChannelGateway` + inbox routes

### Task C1: `ChannelGateway.resolve_contact` + `send`

**Files:**
- Create: `lazyclaw/comms/gateway.py`
- Test: `tests/comms/test_gateway_send.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/comms/test_gateway_send.py
import pytest
from unittest.mock import AsyncMock
from lazyclaw.comms.gateway import ChannelGateway

@pytest.mark.asyncio
async def test_send_dispatches_to_whatsapp_tool():
    call_tool = AsyncMock(return_value={"status": "sent"})
    gw = ChannelGateway(mcp_call=call_tool)
    res = await gw.send("whatsapp", "+34600000000", "hello")
    assert res.ok is True
    name, args = call_tool.await_args.args
    assert name == "whatsapp_send"
    assert args["to"] == "+34600000000" and args["message"] == "hello"

@pytest.mark.asyncio
async def test_send_unknown_channel_errors():
    gw = ChannelGateway(mcp_call=AsyncMock())
    res = await gw.send("carrier-pigeon", "x", "y")
    assert res.ok is False and "unsupported" in (res.error or "").lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/comms/test_gateway_send.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `gateway.py` (send + dispatch table)**

```python
"""Channel-agnostic façade over per-channel MCP read/send tools.

`mcp_call(tool_name, args) -> dict` is injected so the gateway is testable
without a live MCP runtime. The production caller passes the registry's MCP
invoker (see routes/inbox.py)."""
from __future__ import annotations

from typing import Awaitable, Callable

from lazyclaw.comms.models import Contact, Msg, SendResult

McpCall = Callable[[str, dict], Awaitable[dict]]

# channel -> (read_tool, send_tool, send_recipient_key, send_text_key)
_DISPATCH = {
    "whatsapp": ("whatsapp_read", "whatsapp_send", "to", "message"),
    "email": ("email_search", "email_send", "to", "body"),
    "instagram": ("instagram_read_dms", "instagram_send_dm", "to_username", "message"),
}


class ChannelGateway:
    def __init__(self, mcp_call: McpCall):
        self._call = mcp_call

    async def send(self, channel: str, contact: str, text: str) -> SendResult:
        spec = _DISPATCH.get(channel)
        if not spec:
            return SendResult(ok=False, error=f"unsupported channel: {channel}")
        _, send_tool, rcpt_key, text_key = spec
        try:
            result = await self._call(send_tool, {rcpt_key: contact, text_key: text})
        except Exception as e:  # surface as typed failure, never raise
            return SendResult(ok=False, error=str(e))
        status = str(result.get("status", "")).lower() if isinstance(result, dict) else ""
        if status in ("blocked", "error", "failed"):
            return SendResult(ok=False, error=str(result))
        return SendResult(ok=True)

    async def read_thread(self, channel: str, contact: str, *, limit: int = 30) -> list[Msg]:
        spec = _DISPATCH.get(channel)
        if not spec:
            return []
        read_tool = spec[0]
        try:
            result = await self._call(read_tool, {"contact": contact, "limit": limit})
        except Exception:
            return []
        return _parse_messages(result)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/comms/test_gateway_send.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/comms/gateway.py tests/comms/test_gateway_send.py
git commit -m "feat(comms): ChannelGateway send dispatch"
```

---

### Task C2: `ChannelGateway.read_thread` parsing

**Files:**
- Modify: `lazyclaw/comms/gateway.py` (add `_parse_messages`)
- Test: `tests/comms/test_gateway_read.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/comms/test_gateway_read.py
import pytest
from unittest.mock import AsyncMock
from lazyclaw.comms.gateway import ChannelGateway, _parse_messages

def test_parse_messages_normalizes_shapes():
    raw = {"messages": [
        {"sender": "Alice", "content": "hi", "timestamp": "10:00", "is_mine": False},
        {"from": "me", "body": "yo", "ts": "10:01", "is_mine": True},
    ]}
    msgs = _parse_messages(raw)
    assert msgs[0].sender == "Alice" and msgs[0].text == "hi" and msgs[0].is_mine is False
    assert msgs[1].text == "yo" and msgs[1].is_mine is True

@pytest.mark.asyncio
async def test_read_thread_returns_msgs():
    call = AsyncMock(return_value={"messages": [{"sender": "Bob", "content": "yo", "timestamp": "9:00"}]})
    gw = ChannelGateway(mcp_call=call)
    msgs = await gw.read_thread("whatsapp", "+1")
    assert len(msgs) == 1 and msgs[0].sender == "Bob"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/comms/test_gateway_read.py -v`
Expected: FAIL (`_parse_messages` missing).

- [ ] **Step 3: Implement `_parse_messages`**

Append to `gateway.py`:

```python
def _parse_messages(result) -> list[Msg]:
    if not isinstance(result, dict):
        return []
    raw = result.get("messages") or result.get("items") or result.get("emails") or []
    out: list[Msg] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        out.append(Msg(
            sender=str(m.get("sender") or m.get("from") or m.get("author") or ""),
            text=str(m.get("content") or m.get("body") or m.get("text") or ""),
            timestamp=str(m.get("timestamp") or m.get("ts") or m.get("date") or ""),
            is_mine=bool(m.get("is_mine", False)),
        ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/comms/test_gateway_read.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/comms/gateway.py tests/comms/test_gateway_read.py
git commit -m "feat(comms): ChannelGateway read_thread message parsing"
```

---

### Task C3: MCP invoker adapter

Bridges `ChannelGateway`'s `mcp_call` to the real skill registry so routes can build a live gateway.

**Files:**
- Modify: `lazyclaw/comms/gateway.py` (add `build_gateway`)
- Test: `tests/comms/test_gateway_build.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/comms/test_gateway_build.py
import pytest
from unittest.mock import AsyncMock
from lazyclaw.comms.gateway import build_gateway

@pytest.mark.asyncio
async def test_build_gateway_invokes_registry_tool():
    registry = AsyncMock()
    registry.execute_skill = AsyncMock(return_value={"status": "sent"})
    gw = build_gateway(registry, user_id="u1")
    res = await gw.send("whatsapp", "+1", "hi")
    assert res.ok is True
    registry.execute_skill.assert_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/comms/test_gateway_build.py -v`
Expected: FAIL (`build_gateway` missing).

- [ ] **Step 3: Implement `build_gateway`**

> First confirm the registry's tool-execution method name. Grep: `grep -rn "async def execute_skill\|async def execute\|async def call_tool" lazyclaw/skills/registry.py`. Use whatever it is below in place of `execute_skill`.

```python
def build_gateway(registry, user_id: str) -> "ChannelGateway":
    async def _call(tool_name: str, args: dict) -> dict:
        # Adapt to the registry's real signature (see grep above).
        return await registry.execute_skill(tool_name, args, user_id=user_id)
    return ChannelGateway(mcp_call=_call)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/comms/test_gateway_build.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/comms/gateway.py tests/comms/test_gateway_build.py
git commit -m "feat(comms): build_gateway registry adapter"
```

---

### Task C4: Inbox routes

**Files:**
- Create: `lazyclaw/gateway/routes/inbox.py`
- Modify: `lazyclaw/gateway/app.py` (register router)
- Test: `tests/gateway/test_inbox_routes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/gateway/test_inbox_routes.py
import pytest
from httpx import AsyncClient, ASGITransport

@pytest.mark.asyncio
async def test_threads_changes_endpoint(app, auth_cookies, config, user_id):
    # Seed a thread, then hit the changes endpoint.
    from lazyclaw.comms import thread_store
    await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+1",
        contact_name="A", preview="hi", last_seen_msg_id="m1", increment_unread=True,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t", cookies=auth_cookies) as c:
        r = await c.get("/api/inbox/threads/changes")
    assert r.status_code == 200
    body = r.json()
    assert "threads" in body and "now" in body
    assert any(t["contact_handle"] == "+1" for t in body["threads"])
```

> Use the existing app/auth fixtures from `tests/gateway/conftest.py` (mirror an existing `tests/gateway/test_*` for `app`, `auth_cookies`, `user_id`).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_inbox_routes.py -v`
Expected: FAIL (route missing → 404).

- [ ] **Step 3: Implement `inbox.py`**

```python
"""Unified inbox routes: list threads, read live messages, reply, mark read."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from lazyclaw.config import Config, load_config
from lazyclaw.comms import thread_store
from lazyclaw.comms.gateway import build_gateway
from lazyclaw.gateway.auth import User, get_current_user

router = APIRouter(prefix="/api/inbox", tags=["inbox"])
_config = load_config()


class ReplyBody(BaseModel):
    text: str
    mode: str = "direct"  # "direct" | "ai"


@router.get("/threads")
async def list_threads_route(
    user: User = Depends(get_current_user),
    channel: str | None = Query(None),
):
    threads = await thread_store.list_threads(_config, user.id, channel=channel)
    return {"threads": threads, "count": len(threads)}


@router.get("/threads/changes")
async def threads_changes_route(
    user: User = Depends(get_current_user),
    since: str | None = Query(None),
):
    return await thread_store.get_thread_changes(_config, user.id, since)


@router.get("/threads/{thread_id}/messages")
async def thread_messages_route(thread_id: str, user: User = Depends(get_current_user)):
    thread = await thread_store.get_thread(_config, user.id, thread_id)
    if not thread:
        raise HTTPException(404, "thread not found")
    gw = build_gateway(_get_registry(), user.id)
    msgs = await gw.read_thread(thread["channel"], thread["contact_handle"])
    return {"messages": [m.__dict__ for m in msgs], "thread": thread}


@router.post("/threads/{thread_id}/read")
async def thread_read_route(thread_id: str, user: User = Depends(get_current_user)):
    ok = await thread_store.mark_thread_read(_config, user.id, thread_id)
    return {"success": ok}


@router.post("/threads/{thread_id}/reply")
async def thread_reply_route(
    thread_id: str, body: ReplyBody, user: User = Depends(get_current_user),
):
    thread = await thread_store.get_thread(_config, user.id, thread_id)
    if not thread:
        raise HTTPException(404, "thread not found")
    if body.mode == "ai":
        from lazyclaw.comms import conversation_runner  # Phase E
        conv = await conversation_runner.start(
            _config, user.id, channel=thread["channel"],
            contact=thread["contact_handle"], goal=body.text,
        )
        return {"success": True, "conversation_id": conv["id"], "mode": "ai"}
    gw = build_gateway(_get_registry(), user.id)
    res = await gw.send(thread["channel"], thread["contact_handle"], body.text)
    if not res.ok:
        raise HTTPException(502, res.error or "send failed")
    return {"success": True, "mode": "direct"}


def _get_registry():
    """Resolve the process-wide skill registry. Adapt to the app's accessor."""
    from lazyclaw.gateway.app import get_skill_registry  # see note in Step 4
    return get_skill_registry()
```

- [ ] **Step 4: Register the router + confirm the registry accessor**

In `lazyclaw/gateway/app.py` add near the other route imports:

```python
from lazyclaw.gateway.routes.inbox import router as inbox_router
```

and near the other `include_router` calls:

```python
    app.include_router(inbox_router)
```

Then confirm how the skill registry is exposed app-wide: `grep -rn "registry" lazyclaw/gateway/app.py | head`. Replace `_get_registry()` body with the real accessor (e.g. a module global or `app.state.registry`). If the registry lives on `app.state`, change `thread_messages_route`/`thread_reply_route` to take `request: Request` and use `request.app.state.registry`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/gateway/test_inbox_routes.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lazyclaw/gateway/routes/inbox.py lazyclaw/gateway/app.py tests/gateway/test_inbox_routes.py
git commit -m "feat(inbox): list/messages/reply/read routes"
```

> **Checkpoint C:** the backend inbox API is live. `GET /api/inbox/threads`, live messages, direct reply, mark-read all work. `mode:"ai"` is stubbed until Phase E.

---

# PHASE D — Flutter Inbox tab + reply bar

> All new widgets use ONLY the `Lz*` kit + tokens (`AppColors`/`AppText`/`AppSpacing`/`AppRadii`). Imports: `import '../../ui/components/components.dart';` and `import '../../ui/tokens/tokens.dart';` (adjust depth per file location).

### Task D1: Inbox models + repository

**Files:**
- Create: `mobile/lib/comms/inbox_models.dart`
- Create: `mobile/lib/comms/inbox_repository.dart`
- Test: `mobile/test/comms/inbox_repository_test.dart`

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/comms/inbox_repository_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/comms/inbox_models.dart';

void main() {
  test('InboxThread.fromJson parses server shape', () {
    final t = InboxThread.fromJson({
      'id': 't1', 'channel': 'whatsapp', 'contact_handle': '+34600000000',
      'contact_name': 'Alice', 'last_preview': 'hi', 'unread_count': 2,
      'last_activity': '2026-06-09T10:00:00Z', 'updated_at': '2026-06-09T10:00:00Z',
    });
    expect(t.channel, 'whatsapp');
    expect(t.unreadCount, 2);
    expect(t.contactName, 'Alice');
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/comms/inbox_repository_test.dart`
Expected: FAIL (file missing).

- [ ] **Step 3: Implement models + repository**

```dart
// mobile/lib/comms/inbox_models.dart
class InboxThread {
  final String id;
  final String channel;
  final String contactHandle;
  final String? contactName;
  final String? lastPreview;
  final int unreadCount;
  final String lastActivity;
  final String updatedAt;

  const InboxThread({
    required this.id, required this.channel, required this.contactHandle,
    this.contactName, this.lastPreview, this.unreadCount = 0,
    required this.lastActivity, required this.updatedAt,
  });

  factory InboxThread.fromJson(Map<String, dynamic> j) => InboxThread(
    id: j['id'] as String,
    channel: j['channel'] as String,
    contactHandle: j['contact_handle'] as String? ?? '',
    contactName: j['contact_name'] as String?,
    lastPreview: j['last_preview'] as String?,
    unreadCount: (j['unread_count'] as int?) ?? 0,
    lastActivity: (j['last_activity'] ?? '').toString(),
    updatedAt: (j['updated_at'] ?? '').toString(),
  );
}

class InboxMessage {
  final String sender;
  final String text;
  final String timestamp;
  final bool isMine;
  const InboxMessage({required this.sender, required this.text, required this.timestamp, this.isMine = false});
  factory InboxMessage.fromJson(Map<String, dynamic> j) => InboxMessage(
    sender: (j['sender'] ?? '').toString(),
    text: (j['text'] ?? '').toString(),
    timestamp: (j['timestamp'] ?? '').toString(),
    isMine: j['is_mine'] == true,
  );
}
```

```dart
// mobile/lib/comms/inbox_repository.dart
import '../core/api/api_client.dart';
import 'inbox_models.dart';

class InboxRepository {
  final ApiClient _api;
  InboxRepository(this._api);

  Future<List<InboxThread>> fetchThreads({String? channel}) async {
    final res = await _api.get<Map<String, dynamic>>(
      '/api/inbox/threads',
      queryParameters: channel == null ? null : {'channel': channel},
      fromJson: (d) => Map<String, dynamic>.from(d as Map),
    );
    final raw = (res['threads'] as List? ?? const []);
    return raw.map((e) => InboxThread.fromJson(Map<String, dynamic>.from(e as Map))).toList();
  }

  Future<List<InboxMessage>> fetchMessages(String threadId) async {
    final res = await _api.get<Map<String, dynamic>>(
      '/api/inbox/threads/$threadId/messages',
      fromJson: (d) => Map<String, dynamic>.from(d as Map),
    );
    final raw = (res['messages'] as List? ?? const []);
    return raw.map((e) => InboxMessage.fromJson(Map<String, dynamic>.from(e as Map))).toList();
  }

  Future<void> markRead(String threadId) =>
      _api.post('/api/inbox/threads/$threadId/read', data: const {});

  Future<Map<String, dynamic>> reply(String threadId, String text, {String mode = 'direct'}) async {
    return _api.post<Map<String, dynamic>>(
      '/api/inbox/threads/$threadId/reply',
      data: {'text': text, 'mode': mode},
      fromJson: (d) => Map<String, dynamic>.from(d as Map),
    );
  }
}
```

> Adjust `_api.get`/`_api.post` calls to the exact `ApiClient` signatures (see `mobile/lib/core/api/api_client.dart`). Keep the `fromJson` deserializer pattern the existing repositories use.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && flutter test test/comms/inbox_repository_test.dart`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/comms/inbox_models.dart mobile/lib/comms/inbox_repository.dart mobile/test/comms/inbox_repository_test.dart
git commit -m "feat(mobile): inbox models + repository"
```

---

### Task D2: Inbox providers

**Files:**
- Create: `mobile/lib/comms/inbox_providers.dart`
- Test: `mobile/test/comms/inbox_providers_test.dart`

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/comms/inbox_providers_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/comms/inbox_providers.dart';
import 'package:lazyclaw_mobile/comms/inbox_models.dart';
import 'package:lazyclaw_mobile/comms/inbox_repository.dart';

class _FakeRepo implements InboxRepository {
  @override
  Future<List<InboxThread>> fetchThreads({String? channel}) async => [
    const InboxThread(id: 't1', channel: 'whatsapp', contactHandle: '+1',
      contactName: 'Alice', lastPreview: 'hi', unreadCount: 1,
      lastActivity: '2026-06-09T10:00:00Z', updatedAt: '2026-06-09T10:00:00Z'),
  ];
  @override
  noSuchMethod(Invocation i) => super.noSuchMethod(i);
}

void main() {
  test('inboxThreadsProvider loads threads', () async {
    final container = ProviderContainer(overrides: [
      inboxRepositoryProvider.overrideWithValue(_FakeRepo()),
    ]);
    addTearDown(container.dispose);
    final threads = await container.read(inboxThreadsProvider.future);
    expect(threads.length, 1);
    expect(threads.first.contactName, 'Alice');
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/comms/inbox_providers_test.dart`
Expected: FAIL (providers missing).

- [ ] **Step 3: Implement providers**

```dart
// mobile/lib/comms/inbox_providers.dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/auth_provider.dart'; // apiClientProvider
import 'inbox_models.dart';
import 'inbox_repository.dart';

final inboxRepositoryProvider = Provider<InboxRepository>((ref) {
  return InboxRepository(ref.watch(apiClientProvider));
});

final inboxChannelFilterProvider = StateProvider<String?>((ref) => null);

final inboxThreadsProvider = FutureProvider<List<InboxThread>>((ref) async {
  final repo = ref.watch(inboxRepositoryProvider);
  final channel = ref.watch(inboxChannelFilterProvider);
  return repo.fetchThreads(channel: channel);
});

final inboxMessagesProvider =
    FutureProvider.family<List<InboxMessage>, String>((ref, threadId) async {
  final repo = ref.watch(inboxRepositoryProvider);
  return repo.fetchMessages(threadId);
});

// Set by a tapped channel_message notification to deep-link a thread.
final pendingInboxThreadProvider = StateProvider<String?>((ref) => null);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && flutter test test/comms/inbox_providers_test.dart`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/comms/inbox_providers.dart mobile/test/comms/inbox_providers_test.dart
git commit -m "feat(mobile): inbox riverpod providers"
```

---

### Task D3: Inbox list screen

**Files:**
- Create: `mobile/lib/screens/inbox/inbox_screen.dart`
- Test: `mobile/test/screens/inbox_screen_test.dart`

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/screens/inbox_screen_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/comms/inbox_models.dart';
import 'package:lazyclaw_mobile/comms/inbox_providers.dart';
import 'package:lazyclaw_mobile/screens/inbox/inbox_screen.dart';

void main() {
  testWidgets('renders thread rows', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        inboxThreadsProvider.overrideWith((ref) async => [
          const InboxThread(id: 't1', channel: 'whatsapp', contactHandle: '+1',
            contactName: 'Alice', lastPreview: 'see you', unreadCount: 2,
            lastActivity: '2026-06-09T10:00:00Z', updatedAt: '2026-06-09T10:00:00Z'),
        ]),
      ],
      child: const MaterialApp(home: InboxScreen()),
    ));
    await tester.pumpAndSettle();
    expect(find.text('Alice'), findsOneWidget);
    expect(find.text('see you'), findsOneWidget);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/screens/inbox_screen_test.dart`
Expected: FAIL (screen missing).

- [ ] **Step 3: Implement the screen**

```dart
// mobile/lib/screens/inbox/inbox_screen.dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../comms/inbox_models.dart';
import '../../comms/inbox_providers.dart';
import '../../ui/components/components.dart';
import '../../ui/tokens/tokens.dart';

const _channels = <String?, String>{
  null: 'All', 'whatsapp': 'WhatsApp', 'email': 'Email',
  'instagram': 'Instagram', 'telegram': 'Telegram',
};

IconData _iconFor(String channel) => switch (channel) {
  'whatsapp' => Icons.chat,
  'email' => Icons.email_outlined,
  'instagram' => Icons.camera_alt_outlined,
  'telegram' => Icons.send,
  _ => Icons.message_outlined,
};

class InboxScreen extends ConsumerWidget {
  const InboxScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final threadsAsync = ref.watch(inboxThreadsProvider);
    final filter = ref.watch(inboxChannelFilterProvider);
    return LzScaffold(
      title: 'Inbox',
      body: Column(
        children: [
          SizedBox(
            height: 44,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: AppSpacing.listH,
              children: [
                for (final entry in _channels.entries)
                  Padding(
                    padding: const EdgeInsets.only(right: AppSpacing.sm),
                    child: LzChip(
                      label: entry.value,
                      selected: filter == entry.key,
                      onTap: () => ref.read(inboxChannelFilterProvider.notifier).state = entry.key,
                    ),
                  ),
              ],
            ),
          ),
          Expanded(
            child: threadsAsync.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (e, _) => LzEmptyState(
                icon: Icons.error_outline, title: 'Could not load inbox', hint: '$e'),
              data: (threads) => threads.isEmpty
                  ? const LzEmptyState(
                      icon: Icons.mail_outline, title: 'No messages yet',
                      hint: 'New messages from your channels will show up here.')
                  : RefreshIndicator(
                      onRefresh: () => ref.refresh(inboxThreadsProvider.future),
                      child: ListView.builder(
                        padding: AppSpacing.listH,
                        itemCount: threads.length,
                        itemBuilder: (ctx, i) => _ThreadRow(thread: threads[i]),
                      ),
                    ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ThreadRow extends StatelessWidget {
  final InboxThread thread;
  const _ThreadRow({required this.thread});
  @override
  Widget build(BuildContext context) {
    return LzListTile(
      leading: Icon(_iconFor(thread.channel), color: AppColors.accent),
      title: thread.contactName?.isNotEmpty == true ? thread.contactName! : thread.contactHandle,
      subtitle: thread.lastPreview ?? '',
      trailing: thread.unreadCount > 0
          ? LzBadge(label: '${thread.unreadCount}')
          : null,
      onTap: () => context.push('/inbox/${thread.id}'),
    );
  }
}
```

> If `LzBadge` is not in the kit, use an `LzChip(label: '${thread.unreadCount}', dense: true)`. Confirm with the components barrel.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && flutter test test/screens/inbox_screen_test.dart`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/screens/inbox/inbox_screen.dart mobile/test/screens/inbox_screen_test.dart
git commit -m "feat(mobile): inbox list screen"
```

---

### Task D4: Thread screen + reply bar (Send / Ask AI)

**Files:**
- Create: `mobile/lib/screens/inbox/inbox_thread_screen.dart`
- Test: `mobile/test/screens/inbox_thread_screen_test.dart`

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/screens/inbox_thread_screen_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/comms/inbox_models.dart';
import 'package:lazyclaw_mobile/comms/inbox_providers.dart';
import 'package:lazyclaw_mobile/screens/inbox/inbox_thread_screen.dart';

void main() {
  testWidgets('shows messages and a reply bar with mode toggle', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        inboxMessagesProvider('t1').overrideWith((ref) async => [
          const InboxMessage(sender: 'Alice', text: 'are you coming?', timestamp: '10:00'),
        ]),
      ],
      child: const MaterialApp(home: InboxThreadScreen(threadId: 't1', title: 'Alice')),
    ));
    await tester.pumpAndSettle();
    expect(find.text('are you coming?'), findsOneWidget);
    expect(find.text('Send'), findsOneWidget);
    expect(find.text('Ask AI'), findsOneWidget);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/screens/inbox_thread_screen_test.dart`
Expected: FAIL (screen missing).

- [ ] **Step 3: Implement the thread screen**

```dart
// mobile/lib/screens/inbox/inbox_thread_screen.dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../comms/inbox_models.dart';
import '../../comms/inbox_providers.dart';
import '../../ui/components/components.dart';
import '../../ui/tokens/tokens.dart';

class InboxThreadScreen extends ConsumerStatefulWidget {
  final String threadId;
  final String title;
  const InboxThreadScreen({super.key, required this.threadId, required this.title});
  @override
  ConsumerState<InboxThreadScreen> createState() => _InboxThreadScreenState();
}

class _InboxThreadScreenState extends ConsumerState<InboxThreadScreen> {
  final _ctrl = TextEditingController();
  bool _aiMode = false;
  bool _sending = false;

  @override
  void initState() {
    super.initState();
    // Mark read on open.
    Future.microtask(() =>
        ref.read(inboxRepositoryProvider).markRead(widget.threadId));
  }

  @override
  void dispose() { _ctrl.dispose(); super.dispose(); }

  Future<void> _send() async {
    final text = _ctrl.text.trim();
    if (text.isEmpty || _sending) return;
    setState(() => _sending = true);
    try {
      await ref.read(inboxRepositoryProvider).reply(
        widget.threadId, text, mode: _aiMode ? 'ai' : 'direct');
      _ctrl.clear();
      if (!_aiMode) ref.invalidate(inboxMessagesProvider(widget.threadId));
      if (mounted && _aiMode) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('On it — I\'ll run the conversation and report back.')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed: $e')));
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final msgsAsync = ref.watch(inboxMessagesProvider(widget.threadId));
    return LzScaffold(
      title: widget.title,
      body: Column(
        children: [
          Expanded(
            child: msgsAsync.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (e, _) => LzEmptyState(
                icon: Icons.error_outline, title: 'Could not load messages', hint: '$e'),
              data: (msgs) => ListView.builder(
                padding: AppSpacing.screen,
                itemCount: msgs.length,
                itemBuilder: (ctx, i) => _Bubble(msg: msgs[i]),
              ),
            ),
          ),
          _ReplyBar(
            controller: _ctrl, aiMode: _aiMode, sending: _sending,
            onToggleMode: (v) => setState(() => _aiMode = v),
            onSend: _send,
          ),
        ],
      ),
    );
  }
}

class _Bubble extends StatelessWidget {
  final InboxMessage msg;
  const _Bubble({required this.msg});
  @override
  Widget build(BuildContext context) {
    final mine = msg.isMine;
    return Align(
      alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
        padding: AppSpacing.card,
        decoration: BoxDecoration(
          color: mine ? AppColors.accent : AppColors.bgSurfaceElevated,
          borderRadius: AppRadii.rLg,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (!mine && msg.sender.isNotEmpty)
              Text(msg.sender, style: AppText.caption.copyWith(color: AppColors.textMuted)),
            Text(msg.text, style: AppText.body.copyWith(
              color: mine ? AppColors.onAccent : AppColors.textPrimary)),
          ],
        ),
      ),
    );
  }
}

class _ReplyBar extends StatelessWidget {
  final TextEditingController controller;
  final bool aiMode;
  final bool sending;
  final ValueChanged<bool> onToggleMode;
  final VoidCallback onSend;
  const _ReplyBar({
    required this.controller, required this.aiMode, required this.sending,
    required this.onToggleMode, required this.onSend,
  });
  @override
  Widget build(BuildContext context) {
    return SafeArea(
      top: false,
      child: Padding(
        padding: AppSpacing.screen,
        child: Column(
          children: [
            Row(
              children: [
                LzChip(label: 'Send', selected: !aiMode, onTap: () => onToggleMode(false)),
                AppSpacing.hGap(AppSpacing.sm),
                LzChip(label: 'Ask AI', icon: Icons.auto_awesome, selected: aiMode,
                    onTap: () => onToggleMode(true)),
              ],
            ),
            AppSpacing.vGap(AppSpacing.sm),
            Row(
              children: [
                Expanded(child: LzTextField(
                  controller: controller,
                  hint: aiMode ? 'Tell the AI what to ask…' : 'Type a reply…',
                )),
                AppSpacing.hGap(AppSpacing.sm),
                LzButton(
                  label: aiMode ? 'Ask' : 'Send',
                  onPressed: sending ? null : onSend,
                  loading: sending,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
```

> Confirm `LzTextField`'s param name (`hint` vs `hintText`) and `LzButton`'s `onPressed` nullable signature against the kit; adjust if needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && flutter test test/screens/inbox_thread_screen_test.dart`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/screens/inbox/inbox_thread_screen.dart mobile/test/screens/inbox_thread_screen_test.dart
git commit -m "feat(mobile): inbox thread screen + Send/Ask-AI reply bar"
```

---

### Task D5: Router wiring (Inbox tab + thread route)

**Files:**
- Modify: `mobile/lib/core/router/app_router.dart`
- Test: manual `flutter analyze` + boot

- [ ] **Step 1: Add the tab + branch + thread route**

In the `_tabs` list, insert after the Chat tab:

```dart
  _Tab(path: '/inbox', label: 'Inbox', icon: Icons.mail_outline, activeIcon: Icons.mail),
```

In the `branches:` list of `StatefulShellRoute.indexedStack`, insert a matching branch in the same position:

```dart
    StatefulShellBranch(
      routes: [
        GoRoute(
          path: '/inbox',
          builder: (ctx, _) => const InboxScreen(),
          routes: [
            GoRoute(
              path: ':threadId',
              builder: (ctx, state) => InboxThreadScreen(
                threadId: state.pathParameters['threadId']!,
                title: state.uri.queryParameters['title'] ?? 'Conversation',
              ),
            ),
          ],
        ),
      ],
    ),
```

Add imports at the top:

```dart
import '../../screens/inbox/inbox_screen.dart';
import '../../screens/inbox/inbox_thread_screen.dart';
```

> The `_tabs` order must match the `branches` order exactly (indexedStack maps by position). Inserting Inbox at index 2 shifts Tasks→3, etc. — verify the `LzBottomNav` builds from `_tabs` so it stays in sync automatically.

- [ ] **Step 2: Verify it builds**

Run: `cd mobile && flutter analyze`
Expected: No errors in the changed files.

- [ ] **Step 3: Commit**

```bash
git add mobile/lib/core/router/app_router.dart
git commit -m "feat(mobile): add Inbox tab + thread route"
```

---

### Task D6: Channel-message notification deep link + live WS frame

**Files:**
- Modify: `mobile/lib/core/actions/app_actions.dart` (add `openInbox`)
- Modify: `mobile/lib/notifications/local_notifications.dart` (add inbox channel + payload)
- Modify: `mobile/lib/chat/ws_frames.dart` (add `ChannelMessageFrame`)
- Modify: `mobile/lib/chat/chat_controller.dart` (notify + refresh on frame)
- Modify: `mobile/lib/main.dart` (notification-tap → inbox)
- Test: `mobile/test/chat/ws_frames_inbox_test.dart`

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/chat/ws_frames_inbox_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';

void main() {
  test('parses channel_message frame', () {
    final f = parseServerFrame('{"type":"channel_message","thread_id":"t1",'
        '"sender_name":"Alice","content":"hi","timestamp":"10:00"}');
    expect(f, isA<ChannelMessageFrame>());
    final c = f as ChannelMessageFrame;
    expect(c.threadId, 't1');
    expect(c.senderName, 'Alice');
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/chat/ws_frames_inbox_test.dart`
Expected: FAIL (`ChannelMessageFrame` missing).

- [ ] **Step 3: Add the frame + parser case**

In `ws_frames.dart`, add the class:

```dart
class ChannelMessageFrame extends ServerFrame {
  final String threadId;
  final String senderName;
  final String content;
  final String timestamp;
  const ChannelMessageFrame(this.threadId, this.senderName, this.content, this.timestamp);
}
```

In `parseServerFrame`'s switch, add:

```dart
    case 'channel_message':
      return ChannelMessageFrame(
        (m['thread_id'] as String?) ?? '',
        (m['sender_name'] as String?) ?? '',
        (m['content'] as String?) ?? '',
        (m['timestamp'] as String?) ?? '',
      );
```

- [ ] **Step 4: Run the frame test**

Run: `cd mobile && flutter test test/chat/ws_frames_inbox_test.dart`
Expected: PASS.

- [ ] **Step 5: Wire notification channel, deep link, and live refresh**

`app_actions.dart` — add `openInbox` to the enum, to `kActionIds` (`AppAction.openInbox: 'inbox'`), and to `routeForAction` (`case AppAction.openInbox: return '/inbox';`).

`local_notifications.dart` — add an inbox group key + `showInboxNotification(title, body, {String? threadId})` mirroring `showTaskNotification` but channel id `'lazyclaw_inbox'` / name `'Inbox Messages'` and `payload: threadId ?? 'inbox'`.

`chat_controller.dart` — in the notification handler switch, add:

```dart
    case ChannelMessageFrame(:final senderName, :final content, :final threadId):
      onNotify?.call('New message from $senderName', content);
      onChannelMessage?.call(threadId);
```

Add an `onChannelMessage` callback field to the controller (nullable `void Function(String threadId)?`), set from where the controller is constructed so it can `ref.invalidate(inboxThreadsProvider)` and, if that thread is open, `inboxMessagesProvider(threadId)`.

`main.dart` — update the notification-tap handler:

```dart
    LocalNotifications.onSelectNotification = (payload) {
      if (!mounted) return;
      if (payload != null && payload.isNotEmpty && payload != 'inbox') {
        ref.read(pendingInboxThreadProvider.notifier).state = payload;
      }
      ref.read(pendingActionProvider.notifier).state = AppAction.openInbox;
    };
```

Ensure the `build` listener that consumes `pendingActionProvider` routes `openInbox` to `/inbox` (and, if `pendingInboxThreadProvider` is set, pushes `/inbox/<id>`). Import `pendingInboxThreadProvider` from `comms/inbox_providers.dart`.

- [ ] **Step 6: Verify build + commit**

Run: `cd mobile && flutter analyze`
Expected: no errors in changed files.

```bash
git add mobile/lib/core/actions/app_actions.dart mobile/lib/notifications/local_notifications.dart mobile/lib/chat/ws_frames.dart mobile/lib/chat/chat_controller.dart mobile/lib/main.dart mobile/test/chat/ws_frames_inbox_test.dart
git commit -m "feat(mobile): channel_message notifications + inbox deep link"
```

> **Checkpoint D:** the app shows the Inbox tab, lists threads, opens a thread (live read), replies directly, and surfaces channel-message notifications that deep-link to the thread. Build the APK and confirm on the device.

---

# PHASE E — Autonomous `ConversationTask` runner

### Task E1: `conversation_tasks` migration

**Files:**
- Modify: `lazyclaw/db/connection.py` (migration section)
- Test: `tests/comms/test_conversations_table.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/comms/test_conversations_table.py
import pytest
from lazyclaw.db.connection import db_session

@pytest.mark.asyncio
async def test_conversation_tasks_table_exists(config):
    async with db_session(config) as db:
        cur = await db.execute("PRAGMA table_info(conversation_tasks)")
        cols = {r[1] for r in await cur.fetchall()}
    assert {"id","user_id","channel","contact_handle","contact_name","goal",
            "completion_criteria","status","transcript_json","iteration",
            "max_iterations","poll_interval","next_poll_at","created_at",
            "last_activity_at","expires_at","result","error","approval_id"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/comms/test_conversations_table.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the migration**

```python
            try:
                await db.execute(
                    "CREATE TABLE IF NOT EXISTS conversation_tasks ("
                    "id TEXT PRIMARY KEY, "
                    "user_id TEXT NOT NULL REFERENCES users(id), "
                    "channel TEXT NOT NULL, "
                    "contact_handle TEXT NOT NULL, "
                    "contact_name TEXT, "           # encrypted
                    "goal TEXT, "                   # encrypted
                    "completion_criteria TEXT, "    # encrypted
                    "status TEXT NOT NULL DEFAULT 'drafting', "
                    "transcript_json TEXT, "        # encrypted
                    "iteration INTEGER NOT NULL DEFAULT 0, "
                    "max_iterations INTEGER NOT NULL DEFAULT 20, "
                    "poll_interval INTEGER NOT NULL DEFAULT 60, "
                    "next_poll_at TEXT, "
                    "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
                    "last_activity_at TEXT NOT NULL DEFAULT (datetime('now')), "
                    "expires_at TEXT, "
                    "result TEXT, "                 # encrypted
                    "error TEXT, "                  # encrypted
                    "approval_id TEXT"
                    ")"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_conversation_due "
                    "ON conversation_tasks(status, next_poll_at)"
                )
            except Exception:
                logger.debug("conversation_tasks migration skipped", exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/comms/test_conversations_table.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/db/connection.py tests/comms/test_conversations_table.py
git commit -m "feat(comms): conversation_tasks table migration"
```

---

### Task E2: `conversation_store` CRUD + `list_due`

**Files:**
- Create: `lazyclaw/comms/conversation_store.py`
- Test: `tests/comms/test_conversation_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/comms/test_conversation_store.py
import pytest
from lazyclaw.comms import conversation_store as cs

@pytest.mark.asyncio
async def test_create_and_get(config, user_id):
    c = await cs.create_conversation(
        config, user_id, channel="whatsapp", contact_handle="+1",
        contact_name="Alice", goal="ask if coming to birthday",
    )
    got = await cs.get_conversation(config, user_id, c["id"])
    assert got["goal"] == "ask if coming to birthday"
    assert got["status"] == "drafting"
    assert got["transcript"] == []

@pytest.mark.asyncio
async def test_update_status_and_transcript(config, user_id):
    c = await cs.create_conversation(config, user_id, channel="whatsapp",
        contact_handle="+1", contact_name="A", goal="g")
    await cs.update_conversation(config, user_id, c["id"],
        status="running", append_transcript={"dir": "out", "text": "hi", "ts": "10:00"})
    got = await cs.get_conversation(config, user_id, c["id"])
    assert got["status"] == "running"
    assert got["transcript"][-1]["text"] == "hi"

@pytest.mark.asyncio
async def test_list_due(config, user_id):
    c = await cs.create_conversation(config, user_id, channel="whatsapp",
        contact_handle="+1", contact_name="A", goal="g")
    await cs.update_conversation(config, user_id, c["id"],
        status="running", next_poll_at="2000-01-01T00:00:00+00:00")
    due = await cs.list_due(config, "2030-01-01T00:00:00+00:00")
    assert any(t["id"] == c["id"] for t in due)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/comms/test_conversation_store.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `conversation_store.py`**

```python
"""Encrypted persistence + scheduling for autonomous conversation tasks."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt, decrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

_ENC = {"contact_name", "goal", "completion_criteria", "transcript_json", "result", "error"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row, key: bytes) -> dict:
    transcript_raw = decrypt_field(row["transcript_json"], key) if row["transcript_json"] else None
    return {
        "id": row["id"], "channel": row["channel"], "contact_handle": row["contact_handle"],
        "contact_name": decrypt_field(row["contact_name"], key),
        "goal": decrypt_field(row["goal"], key),
        "completion_criteria": decrypt_field(row["completion_criteria"], key),
        "status": row["status"],
        "transcript": json.loads(transcript_raw) if transcript_raw else [],
        "iteration": row["iteration"], "max_iterations": row["max_iterations"],
        "poll_interval": row["poll_interval"], "next_poll_at": row["next_poll_at"],
        "created_at": row["created_at"], "last_activity_at": row["last_activity_at"],
        "expires_at": row["expires_at"],
        "result": decrypt_field(row["result"], key),
        "error": decrypt_field(row["error"], key),
        "approval_id": row["approval_id"],
    }


async def create_conversation(
    config: Config, user_id: str, *, channel: str, contact_handle: str,
    contact_name: str | None, goal: str, completion_criteria: str | None = None,
    max_iterations: int = 20, poll_interval: int = 60, expires_at: str | None = None,
) -> dict:
    key = await get_user_dek(config, user_id)
    cid = str(uuid4())
    now = _now()
    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO conversation_tasks "
            "(id,user_id,channel,contact_handle,contact_name,goal,completion_criteria,"
            "status,transcript_json,iteration,max_iterations,poll_interval,next_poll_at,"
            "created_at,last_activity_at,expires_at,result,error,approval_id) "
            "VALUES (?,?,?,?,?,?,?,'drafting',?,0,?,?,?,?,?,?,NULL,NULL,NULL)",
            (cid, user_id, channel, contact_handle, encrypt(contact_name or "", key),
             encrypt(goal, key), encrypt(completion_criteria or "", key) if completion_criteria else None,
             encrypt(json.dumps([]), key), max_iterations, poll_interval, now,
             now, now, expires_at),
        )
        await db.commit()
    return await get_conversation(config, user_id, cid)


async def get_conversation(config: Config, user_id: str, cid: str) -> dict | None:
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT * FROM conversation_tasks WHERE id=? AND user_id=?", (cid, user_id))
        row = await cur.fetchone()
    return _row_to_dict(row, key) if row else None


async def update_conversation(
    config: Config, user_id: str, cid: str, *, append_transcript: dict | None = None, **fields,
) -> dict | None:
    key = await get_user_dek(config, user_id)
    current = await get_conversation(config, user_id, cid)
    if current is None:
        return None
    sets, params = [], []
    for col, val in fields.items():
        if col in _ENC:
            val = encrypt(val or "", key) if val is not None else None
        sets.append(f"{col}=?"); params.append(val)
    if append_transcript is not None:
        transcript = current["transcript"] + [append_transcript]
        sets.append("transcript_json=?"); params.append(encrypt(json.dumps(transcript), key))
    sets.append("last_activity_at=?"); params.append(_now())
    params.extend([cid, user_id])
    async with db_session(config) as db:
        await db.execute(
            f"UPDATE conversation_tasks SET {', '.join(sets)} WHERE id=? AND user_id=?",
            tuple(params))
        await db.commit()
    return await get_conversation(config, user_id, cid)


async def list_due(config: Config, now_iso: str) -> list[dict]:
    """All conversations across users that need a step now."""
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT id, user_id FROM conversation_tasks "
            "WHERE status IN ('drafting','running') AND next_poll_at IS NOT NULL "
            "AND next_poll_at <= ?", (now_iso,))
        rows = await cur.fetchall()
    out = []
    for r in rows:
        conv = await get_conversation(config, r["user_id"], r["id"])
        if conv:
            out.append(conv)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/comms/test_conversation_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/comms/conversation_store.py tests/comms/test_conversation_store.py
git commit -m "feat(comms): conversation_store CRUD + list_due scheduling"
```

---

### Task E3: `conversation_runner.start` + draft/approval step

Drafts the opener via the messaging specialist and requests first-message approval.

**Files:**
- Create: `lazyclaw/comms/conversation_runner.py`
- Test: `tests/comms/test_conversation_runner_start.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/comms/test_conversation_runner_start.py
import pytest
from unittest.mock import AsyncMock, patch
from lazyclaw.comms import conversation_runner as cr
from lazyclaw.comms import conversation_store as cs

@pytest.mark.asyncio
async def test_start_creates_drafting_due_now(config, user_id):
    conv = await cr.start(config, user_id, channel="whatsapp",
        contact="+34600000000", goal="ask if coming to birthday")
    assert conv["status"] == "drafting"
    assert conv["next_poll_at"] is not None  # due immediately

@pytest.mark.asyncio
async def test_step_drafting_requests_approval(config, user_id):
    conv = await cr.start(config, user_id, channel="whatsapp",
        contact="+1", goal="ask X")
    with patch.object(cr, "_draft_message", new=AsyncMock(return_value="Hi! Quick q…")), \
         patch.object(cr, "_request_approval", new=AsyncMock(return_value="appr-1")) as req:
        updated = await cr.step(config, _make_deps(), conv)
    req.assert_awaited_once()
    assert updated["status"] == "awaiting_approval"
    assert updated["approval_id"] == "appr-1"

def _make_deps():
    from types import SimpleNamespace
    return SimpleNamespace(registry=None, eco_router=None, permission_checker=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/comms/test_conversation_runner_start.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `start` + the drafting branch of `step`**

```python
"""Heartbeat-driven state machine for autonomous channel conversations.

States: drafting -> awaiting_approval -> running -> done | failed | expired | aborted
`step()` advances ONE conversation by one move per heartbeat tick (restart-safe).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lazyclaw.config import Config
from lazyclaw.comms import conversation_store as cs


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def start(
    config: Config, user_id: str, *, channel: str, contact: str, goal: str,
    completion_criteria: str | None = None, max_iterations: int = 20,
    poll_interval: int = 60, ttl_hours: int = 24,
) -> dict:
    expires = _iso(_now() + timedelta(hours=ttl_hours))
    conv = await cs.create_conversation(
        config, user_id, channel=channel, contact_handle=contact,
        contact_name=None, goal=goal, completion_criteria=completion_criteria,
        max_iterations=max_iterations, poll_interval=poll_interval, expires_at=expires,
    )
    # Due immediately so the next heartbeat drafts the opener.
    return await cs.update_conversation(
        config, user_id, conv["id"], next_poll_at=_iso(_now()))


async def step(config: Config, deps, conv: dict) -> dict:
    """Advance one conversation by one move. `deps` carries registry/eco_router/
    permission_checker for specialist runs."""
    user_id = conv.get("user_id") or deps_user(conv)
    # Expiry guard
    if conv.get("expires_at") and conv["expires_at"] < _iso(_now()):
        return await _fail(config, user_id, conv, "expired", error="timed out")

    status = conv["status"]
    if status == "drafting":
        draft = await _draft_message(config, deps, conv)
        if not draft:
            return await _fail(config, user_id, conv, "failed", error="could not draft opener")
        approval_id = await _request_approval(config, user_id, conv, draft)
        return await cs.update_conversation(
            config, user_id, conv["id"], status="awaiting_approval",
            approval_id=approval_id, next_poll_at=None,
            append_transcript={"dir": "draft", "text": draft, "ts": _iso(_now())})
    if status == "running":
        return await _run_step(config, deps, conv)  # Task E5
    return conv


async def _fail(config, user_id, conv, status, *, error):
    from lazyclaw.notifications.dispatch import deliver
    updated = await cs.update_conversation(
        config, user_id, conv["id"], status=status, error=error, next_poll_at=None)
    await deliver(config, user_id, title="Conversation ended",
        body=f"Couldn't finish asking {conv['contact_handle']}: {error}",
        kind="conversation_result",
        thread_ref={"channel": conv["channel"], "contact": conv["contact_handle"]})
    return updated


def deps_user(conv: dict) -> str:
    # conv dicts from conversation_store don't carry user_id; runner threads it via deps.
    raise RuntimeError("user_id must be present")
```

> Note: `conversation_store.get_conversation` returns rows without `user_id`. To keep `step()` self-contained, include `user_id` when the daemon builds the work item (Task E6 passes `conv["user_id"]`). Add `"user_id": row["user_id"]` to `_row_to_dict` in `conversation_store.py` now, and update the E2 tests if needed (they will still pass — extra key).

Add the helper stubs that later tasks fill in (so this task's tests run): `_draft_message`, `_request_approval`, `_run_step`. Implement `_draft_message` and `_request_approval` as minimal real versions:

```python
async def _draft_message(config, deps, conv: dict) -> str | None:
    """Ask the messaging specialist to draft the opening message from the goal."""
    from lazyclaw.teams.specialist import BUILTIN_SPECIALISTS
    from lazyclaw.teams.runner import run_specialist
    spec = next((s for s in BUILTIN_SPECIALISTS if s.name == "messaging_specialist"), None)
    if spec is None or deps.registry is None:
        return None
    instruction = (
        f"Draft a short, friendly opening message to send on {conv['channel']} to "
        f"contact {conv['contact_handle']}. Goal: {conv['goal']}. "
        f"Return ONLY the message text, no preamble.")
    result = await run_specialist(
        user_id=conv["user_id"], specialist=spec, task=instruction,
        registry=deps.registry, eco_router=deps.eco_router,
        permission_checker=deps.permission_checker)
    return result.result.strip() if result.success else None


async def _request_approval(config, user_id: str, conv: dict, draft: str) -> str:
    """Emit an approval request (Telegram inline buttons + feed) and return its id."""
    from lazyclaw.comms import approvals  # Task E4
    return await approvals.request_first_message_approval(config, user_id, conv, draft)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/comms/test_conversation_runner_start.py -v`
Expected: PASS (the test patches `_draft_message`/`_request_approval`).

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/comms/conversation_runner.py lazyclaw/comms/conversation_store.py tests/comms/test_conversation_runner_start.py
git commit -m "feat(comms): conversation_runner start + drafting/approval step"
```

---

### Task E4: Approval registry + endpoints

Self-contained approval mechanism (decoupled from the chat WS approval frame): a pending row resolved by a mobile endpoint or a Telegram callback.

**Files:**
- Create: `lazyclaw/comms/approvals.py`
- Modify: `lazyclaw/gateway/routes/inbox.py` (approve/deny endpoints)
- Test: `tests/comms/test_approvals.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/comms/test_approvals.py
import pytest
from unittest.mock import AsyncMock, patch
from lazyclaw.comms import approvals

@pytest.mark.asyncio
async def test_request_and_resolve(config, user_id):
    conv = {"id": "c1", "channel": "whatsapp", "contact_handle": "+1", "goal": "g"}
    with patch.object(approvals, "deliver", new=AsyncMock()) as d:
        aid = await approvals.request_first_message_approval(config, user_id, conv, "Hi!")
        d.assert_awaited_once()
    # approval id encodes the conversation id
    assert "c1" in aid

@pytest.mark.asyncio
async def test_resolve_runs_callback(config, user_id):
    called = {}
    async def cb(approved): called["v"] = approved
    approvals.register_waiter("appr-c1", cb)
    await approvals.resolve("appr-c1", True)
    assert called["v"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/comms/test_approvals.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `approvals.py`**

```python
"""First-message approval for autonomous conversations.

Approval id format: ``appr-<conversation_id>``. Resolution is driven by the
heartbeat (which checks conversation status) OR by an in-process waiter when
the resolving call happens in the same process (mobile endpoint / Telegram cb).
The durable signal is `conversation_tasks.status`; the in-process waiter is a
fast-path convenience."""
from __future__ import annotations

from typing import Awaitable, Callable

from lazyclaw.notifications.dispatch import deliver

_WAITERS: dict[str, Callable[[bool], Awaitable[None]]] = {}


def register_waiter(approval_id: str, cb: Callable[[bool], Awaitable[None]]) -> None:
    _WAITERS[approval_id] = cb


async def request_first_message_approval(config, user_id: str, conv: dict, draft: str) -> str:
    approval_id = f"appr-{conv['id']}"
    keyboard = [[
        {"text": "✅ Send it", "callback_data": f"convok:{conv['id']}"},
        {"text": "✋ Cancel", "callback_data": f"convno:{conv['id']}"},
    ]]
    await deliver(
        config, user_id,
        title=f"Approve message to {conv['contact_handle']}",
        body=f"I want to send on {conv['channel']}:\n\n“{draft}”\n\nSend it?",
        kind="conversation_approval", inline_keyboard=keyboard,
        thread_ref={"channel": conv["channel"], "contact": conv["contact_handle"]})
    return approval_id


async def resolve(approval_id: str, approved: bool) -> None:
    cb = _WAITERS.pop(approval_id, None)
    if cb is not None:
        await cb(approved)
```

- [ ] **Step 4: Add approve/deny endpoints to `inbox.py`**

```python
@router.post("/conversations/{conversation_id}/approve")
async def approve_conversation(
    conversation_id: str, approved: bool = Query(True),
    user: User = Depends(get_current_user),
):
    from lazyclaw.comms import conversation_runner, conversation_store
    conv = await conversation_store.get_conversation(_config, user.id, conversation_id)
    if not conv:
        raise HTTPException(404, "conversation not found")
    await conversation_runner.on_approval(_config, _get_registry_deps(user.id), conv, approved)
    return {"success": True, "approved": approved}
```

Add `on_approval` to `conversation_runner.py`:

```python
async def on_approval(config, deps, conv: dict, approved: bool) -> dict:
    user_id = conv["user_id"]
    if not approved:
        return await _fail(config, user_id, conv, "aborted", error="you cancelled the first message")
    draft = next((t["text"] for t in reversed(conv["transcript"]) if t["dir"] == "draft"), None)
    from lazyclaw.comms.gateway import build_gateway
    gw = build_gateway(deps.registry, user_id)
    res = await gw.send(conv["channel"], conv["contact_handle"], draft or "")
    if not res.ok:
        return await _fail(config, user_id, conv, "failed", error=res.error or "send failed")
    return await cs.update_conversation(
        config, user_id, conv["id"], status="running", approval_id=None,
        next_poll_at=_iso(_now() + timedelta(seconds=conv["poll_interval"])),
        append_transcript={"dir": "out", "text": draft, "ts": _iso(_now())})
```

Add a `_get_registry_deps(user_id)` helper in `inbox.py` returning a `SimpleNamespace(registry=..., eco_router=..., permission_checker=...)` resolved from the app — adapt to the app's accessors (same source as `_get_registry`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/comms/test_approvals.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lazyclaw/comms/approvals.py lazyclaw/comms/conversation_runner.py lazyclaw/gateway/routes/inbox.py tests/comms/test_approvals.py
git commit -m "feat(comms): first-message approval registry + endpoints"
```

---

### Task E5: `_run_step` — poll, evaluate, follow-up or finish

**Files:**
- Modify: `lazyclaw/comms/conversation_runner.py`
- Test: `tests/comms/test_conversation_runner_run.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/comms/test_conversation_runner_run.py
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from lazyclaw.comms import conversation_runner as cr
from lazyclaw.comms import conversation_store as cs

@pytest.mark.asyncio
async def test_run_step_finishes_when_goal_met(config, user_id):
    conv = await cs.create_conversation(config, user_id, channel="whatsapp",
        contact_handle="+1", contact_name="Alice", goal="coming to birthday?")
    conv = await cs.update_conversation(config, user_id, conv["id"], status="running")
    conv["user_id"] = user_id
    deps = SimpleNamespace(registry=object(), eco_router=None, permission_checker=None)
    with patch.object(cr, "_read_new_contact_messages",
                      new=AsyncMock(return_value=[{"sender": "Alice", "text": "yes!", "ts": "10:01"}])), \
         patch.object(cr, "_evaluate_goal",
                      new=AsyncMock(return_value={"done": True, "answer": "Yes, Alice is coming"})), \
         patch.object(cr, "deliver", new=AsyncMock()) as d:
        updated = await cr.step(config, deps, conv)
    assert updated["status"] == "done"
    assert "Yes" in (updated["result"] or "")
    d.assert_awaited()

@pytest.mark.asyncio
async def test_run_step_sends_followup_when_not_done(config, user_id):
    conv = await cs.create_conversation(config, user_id, channel="whatsapp",
        contact_handle="+1", contact_name="Alice", goal="coming?")
    conv = await cs.update_conversation(config, user_id, conv["id"], status="running")
    conv["user_id"] = user_id
    deps = SimpleNamespace(registry=object(), eco_router=None, permission_checker=None)
    with patch.object(cr, "_read_new_contact_messages",
                      new=AsyncMock(return_value=[{"sender": "Alice", "text": "when is it?", "ts": "10:01"}])), \
         patch.object(cr, "_evaluate_goal",
                      new=AsyncMock(return_value={"done": False, "next": "It's Saturday 7pm — can you make it?"})), \
         patch.object(cr, "_send", new=AsyncMock(return_value=True)) as send:
        updated = await cr.step(config, deps, conv)
    assert updated["status"] == "running"
    assert updated["iteration"] == 1
    send.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/comms/test_conversation_runner_run.py -v`
Expected: FAIL (`_run_step` not implemented / helpers missing).

- [ ] **Step 3: Implement `_run_step` + helpers**

```python
from lazyclaw.notifications.dispatch import deliver  # add at top


async def _run_step(config, deps, conv: dict) -> dict:
    user_id = conv["user_id"]
    if conv["iteration"] >= conv["max_iterations"]:
        return await _fail(config, user_id, conv, "failed", error="no clear answer after max turns")
    new_msgs = await _read_new_contact_messages(config, deps, conv)
    if not new_msgs:
        # No reply yet — back off and reschedule.
        backoff = min(conv["poll_interval"] * (2 ** conv["iteration"]), 1800)
        return await cs.update_conversation(
            config, user_id, conv["id"],
            next_poll_at=_iso(_now() + timedelta(seconds=backoff)))
    # Record inbound, evaluate against the goal.
    for m in new_msgs:
        await cs.update_conversation(config, user_id, conv["id"],
            append_transcript={"dir": "in", "text": m["text"], "ts": m.get("ts", "")})
    verdict = await _evaluate_goal(config, deps, conv, new_msgs)
    if verdict.get("done"):
        answer = verdict.get("answer", "")
        updated = await cs.update_conversation(
            config, user_id, conv["id"], status="done", result=answer, next_poll_at=None)
        await deliver(config, user_id, title="Got your answer",
            body=f"{conv['contact_name'] or conv['contact_handle']}: {answer}",
            kind="conversation_result",
            thread_ref={"channel": conv["channel"], "contact": conv["contact_handle"]})
        return updated
    # Not done — send the follow-up (autonomous now).
    followup = verdict.get("next", "")
    ok = await _send(config, deps, conv, followup)
    next_poll = _iso(_now() + timedelta(seconds=conv["poll_interval"]))
    return await cs.update_conversation(
        config, user_id, conv["id"], iteration=conv["iteration"] + 1,
        next_poll_at=next_poll,
        append_transcript={"dir": "out", "text": followup, "ts": _iso(_now())})


async def _read_new_contact_messages(config, deps, conv: dict) -> list[dict]:
    """Live-read the channel; return contact-side messages newer than our transcript."""
    from lazyclaw.comms.gateway import build_gateway
    gw = build_gateway(deps.registry, conv["user_id"])
    msgs = await gw.read_thread(conv["channel"], conv["contact_handle"])
    seen = {t["text"] for t in conv["transcript"]}
    return [{"sender": m.sender, "text": m.text, "ts": m.timestamp}
            for m in msgs if not m.is_mine and m.text and m.text not in seen]


async def _evaluate_goal(config, deps, conv: dict, new_msgs: list[dict]) -> dict:
    """Ask the messaging specialist whether the goal is satisfied. Returns
    {"done": bool, "answer"?: str, "next"?: str}."""
    import json as _json
    from lazyclaw.teams.specialist import BUILTIN_SPECIALISTS
    from lazyclaw.teams.runner import run_specialist
    spec = next((s for s in BUILTIN_SPECIALISTS if s.name == "messaging_specialist"), None)
    if spec is None or deps.registry is None:
        return {"done": False, "next": ""}
    transcript = "\n".join(f"{t['dir']}: {t['text']}" for t in conv["transcript"])
    instruction = (
        f"Goal: {conv['goal']}\nConversation so far:\n{transcript}\n"
        f"Latest from contact: {new_msgs[-1]['text']}\n\n"
        "If the goal is answered, reply with JSON {\"done\": true, \"answer\": \"...\"}. "
        "If not, reply with JSON {\"done\": false, \"next\": \"<the next short message to send>\"}. "
        "Return ONLY the JSON.")
    result = await run_specialist(
        user_id=conv["user_id"], specialist=spec, task=instruction,
        registry=deps.registry, eco_router=deps.eco_router,
        permission_checker=deps.permission_checker)
    if not result.success:
        return {"done": False, "next": ""}
    try:
        text = result.result.strip()
        start = text.index("{"); end = text.rindex("}") + 1
        return _json.loads(text[start:end])
    except Exception:
        return {"done": False, "next": ""}


async def _send(config, deps, conv: dict, text: str) -> bool:
    from lazyclaw.comms.gateway import build_gateway
    gw = build_gateway(deps.registry, conv["user_id"])
    res = await gw.send(conv["channel"], conv["contact_handle"], text)
    return res.ok
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/comms/test_conversation_runner_run.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/comms/conversation_runner.py tests/comms/test_conversation_runner_run.py
git commit -m "feat(comms): conversation run step (poll/evaluate/follow-up/finish)"
```

---

### Task E6: Drive conversations from the heartbeat

**Files:**
- Modify: `lazyclaw/heartbeat/daemon.py` (add `_check_conversations` to the tick)
- Test: `tests/heartbeat/test_conversation_tick.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/heartbeat/test_conversation_tick.py
import pytest
from unittest.mock import AsyncMock, patch
from lazyclaw.comms import conversation_store as cs

@pytest.mark.asyncio
async def test_tick_steps_due_conversations(config, user_id):
    from lazyclaw.heartbeat.daemon import HeartbeatDaemon
    c = await cs.create_conversation(config, user_id, channel="whatsapp",
        contact_handle="+1", contact_name="A", goal="g")
    await cs.update_conversation(config, user_id, c["id"], status="running",
        next_poll_at="2000-01-01T00:00:00+00:00")
    daemon = HeartbeatDaemon(config)
    with patch("lazyclaw.heartbeat.daemon.conversation_runner.step",
               new=AsyncMock(return_value={})) as step_mock:
        await daemon._check_conversations()
    step_mock.assert_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/heartbeat/test_conversation_tick.py -v`
Expected: FAIL (`_check_conversations` missing).

- [ ] **Step 3: Implement the tick hook**

At the top of `daemon.py`: `from lazyclaw.comms import conversation_runner, conversation_store`.

Add the method:

```python
    async def _check_conversations(self) -> None:
        """Advance every due autonomous conversation by one step."""
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        due = await conversation_store.list_due(self._config, now_iso)
        for conv in due:
            try:
                deps = self._conversation_deps(conv["user_id"])
                await conversation_runner.step(self._config, deps, conv)
            except Exception:
                logger.exception("conversation step failed for %s", conv.get("id"))
```

Add `_conversation_deps` returning a `SimpleNamespace(registry=..., eco_router=..., permission_checker=...)` from whatever the daemon already holds (the daemon constructs agents, so these are available on `self`; grep `self._registry`/`self._eco_router` in `daemon.py` and reuse them).

Call it once per tick, after the per-user loop (it scans all users itself):

```python
        await self._check_conversations()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/heartbeat/test_conversation_tick.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/heartbeat/daemon.py tests/heartbeat/test_conversation_tick.py
git commit -m "feat(heartbeat): drive autonomous conversations each tick"
```

---

### Task E7: NL trigger — "ask X on <channel> …"

Lets a chat/Telegram message start a conversation without the inbox UI.

**Files:**
- Modify: `lazyclaw/runtime/instant_dispatch.py`
- Test: `tests/runtime/test_instant_dispatch_ask.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/runtime/test_instant_dispatch_ask.py
import pytest
from lazyclaw.runtime.instant_dispatch import match_ask_conversation

def test_matches_ask_on_whatsapp():
    m = match_ask_conversation("ask Alice on WhatsApp if she's coming to my birthday")
    assert m is not None
    assert m["channel"] == "whatsapp"
    assert "birthday" in m["goal"]

def test_matches_via_email():
    m = match_ask_conversation("ask Bob via email whether the invoice was paid")
    assert m["channel"] == "email"

def test_no_match_plain_message():
    assert match_ask_conversation("what's the weather") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/runtime/test_instant_dispatch_ask.py -v`
Expected: FAIL (`match_ask_conversation` missing).

- [ ] **Step 3: Implement the matcher**

```python
import re

_ASK_RE = re.compile(
    r"\bask\s+(?P<who>[A-Za-z0-9 ._-]+?)\s+(?:on|via|through)\s+"
    r"(?P<channel>whatsapp|email|instagram|telegram)\b[,:]?\s+(?P<goal>.+)",
    re.IGNORECASE,
)


def match_ask_conversation(text: str) -> dict | None:
    """Detect 'ask <who> on <channel> <goal>' → conversation task params."""
    m = _ASK_RE.search(text or "")
    if not m:
        return None
    return {
        "who": m.group("who").strip(),
        "channel": m.group("channel").lower(),
        "goal": m.group("goal").strip(),
    }
```

> Wiring into the dispatch flow: where `instant_dispatch` already routes recognized intents, add a branch that, on a `match_ask_conversation` hit, resolves `who`→handle via `find_contact` for that channel and calls `conversation_runner.start(...)`, then replies "On it — I'll ask <who> and report back." Follow the existing pattern other instant-dispatch intents use to obtain `config`/`user_id`/registry. Keep this branch behind the same guard that prevents false positives on normal chat (the regex already requires the `on/via <channel>` anchor).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/runtime/test_instant_dispatch_ask.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/runtime/instant_dispatch.py tests/runtime/test_instant_dispatch_ask.py
git commit -m "feat(runtime): NL trigger to start an autonomous conversation"
```

---

### Task E8: Telegram approval callback + full-suite green

**Files:**
- Modify: `lazyclaw/channels/telegram_commands.py` (callback handler for `convok:`/`convno:`)
- Test: `tests/channels/test_conversation_callbacks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/channels/test_conversation_callbacks.py
import pytest
from lazyclaw.channels.telegram_commands import parse_conversation_callback

def test_parse_ok():
    assert parse_conversation_callback("convok:c123") == ("c123", True)

def test_parse_cancel():
    assert parse_conversation_callback("convno:c123") == ("c123", False)

def test_parse_other_returns_none():
    assert parse_conversation_callback("accept:slug") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/channels/test_conversation_callbacks.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the parser + wire the handler**

```python
def parse_conversation_callback(data: str) -> tuple[str, bool] | None:
    """Map a Telegram callback_data to (conversation_id, approved) or None."""
    if data.startswith("convok:"):
        return data.split(":", 1)[1], True
    if data.startswith("convno:"):
        return data.split(":", 1)[1], False
    return None
```

In the existing `CallbackQueryHandler` dispatch, add a branch: if `parse_conversation_callback(query.data)` returns a tuple, load the conversation, call `conversation_runner.on_approval(config, deps, conv, approved)`, and answer the callback with "Sent ✅" / "Cancelled". Follow how the existing `accept:`/`wmute:` callbacks resolve `config` + the user.

- [ ] **Step 4: Run test + full suites**

Run: `pytest tests/comms tests/heartbeat tests/notifications tests/channels/test_conversation_callbacks.py -v`
Then the Flutter suite: `cd mobile && flutter test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/channels/telegram_commands.py tests/channels/test_conversation_callbacks.py
git commit -m "feat(telegram): approve/cancel autonomous-conversation first message"
```

> **Checkpoint E:** End-to-end — "ask Alice on WhatsApp if she's coming to my birthday" drafts an opener, asks you to approve (Flutter one-tap or Telegram button), then autonomously runs the back-and-forth and reports the answer to the Flutter app. Run a live smoke test against a real WhatsApp contact.

---

## Final verification

- [ ] **Backend full suite:** `pytest tests/comms tests/heartbeat tests/notifications tests/gateway/test_inbox_routes.py tests/runtime/test_instant_dispatch_ask.py tests/channels/test_conversation_callbacks.py -v` — all green.
- [ ] **Flutter:** `cd mobile && flutter analyze && flutter test` — clean.
- [ ] **Rebuild:** `make rebuild` (Docker) so the new routes/migrations load; `scripts/build-mobile-apk.sh` for the app.
- [ ] **Live smoke:** confirm `whatsapp_status` is connected; send yourself a WhatsApp message → it appears as a Flutter notification + inbox thread; reply direct; then run an "Ask AI" conversation end-to-end.
- [ ] **DOCS.md / CLAUDE.md:** add a short "Unified Comms" entry under Key Patterns (the `notify()` funnel, `comms/` module, live-read inbox, heartbeat-driven ConversationTask).

---

## Self-review notes (author)

- **Spec coverage:** A (funnel) → Tasks A1–A4; B (thread store) → B1–B4; C (gateway+routes) → C1–C4; D (Flutter inbox) → D1–D6; E (conversation runner) → E1–E8. Telegram-inbox caveat is honored by `_DISPATCH` omitting `telegram` reads (WhatsApp/Email/IG only); Telegram remains a notification + conversation target.
- **Refinement vs spec:** approval uses a dedicated endpoint + Telegram callback (`comms/approvals.py`) rather than the chat-WS `approval_request` frame — simpler and decoupled; the spec's intent (one-tap approve first message) is preserved.
- **Adapters to confirm at execution time (flagged inline):** the skill-registry tool-execution method name (Task C3), the app's registry/eco_router/permission_checker accessors (Tasks C4, E4, E6), and a few `Lz*`/`ApiClient` param names (Tasks D1, D3, D4). Each is called out in its task.
- **Restart safety:** conversation state lives entirely in `conversation_tasks`; `step()` is idempotent per tick (advances `next_poll_at` and appends transcript before/after sends; dedups inbound by transcript text).
