# Notification Spine — Design Spec

**Date:** 2026-07-16
**Branch:** `feat/notification-spine`
**Status:** Approved direction (remote-control build)

## Problem (from a 5-subsystem code investigation, 2026-07-16)

LazyClaw has **no notification *system*** — it has 4 disjoint server funnels
(`dispatch.deliver`, `push.push_telegram`, `heartbeat_push.deliver_heartbeat_push`,
`telegram_notifier.TelegramNotifier`) and 2 disconnected client systems
(WS→chat toast, poll→bare OS toast). None share state. Symptoms the user reported,
mapped to root causes:

| Symptom | Root cause |
|---|---|
| "pings but god knows what it is" | Mobile server-notifications write **nothing** in-app; no notification-center screen; payload hard-coded `'chat'` so `kind`/thread/deep-link are discarded. |
| "pings on Flutter, not in chat" | Two delivery classes; pure notifications never touch chat; real turns delivered via side-callback, never streamed to the live WS. |
| "everything is noise" (watchers) | Browser watchers fire a **full LLM brain turn on every byte change** with a generic instruction; the stored watch condition is never evaluated; no dedup/severity/diff. |
| "fragile / late" | **No push transport** (no FCM/APNs); phone only checks on open/resume/~30-min poll. Feed is **default-off** (`DEFAULT_CHANNEL='telegram'` → `record_feed=False`). |
| "inbox is bad" | Inbox store has one writer (the notification watcher); empty by default; no history backfill; list (mirror) vs detail (live read) never reconciled; only WhatsApp works. |
| "WhatsApp MCP is bad" | Library (Baileys) is fine; it's **polled every 120s** instead of pushing its live socket. |

## Approved decisions

1. **Closed-app push transport → self-hosted ntfy / UnifiedPush** (no Google/Firebase).
2. **Notification surface → dedicated Notification Center + a chat card for the important ones.**

## The core idea: one Notification Spine

Replace the 4 funnels with **one typed emit API** every producer calls:

```python
await notify(config, user_id, *,
    kind,                 # channel_message | task_reminder | task_nag |
                          # watcher_hit | approval_needed | agent_reply |
                          # contract_intake | system | push | info
    title, body,
    severity='normal',    # info | normal | important | urgent
    deep_link=None,       # {"type": "thread|task|watcher|page|goal|chat", "id": ..., "channel": ...}
    actions=None,         # [{"label","action_id","style"}] → rendered on Telegram AND in-app
    dedup_key=None,       # collapse repeats within a window
    telegram=True,        # allow the loud Telegram fan-out (still gated by channel)
    inline_keyboard=None, # explicit Telegram keyboard override (else derived from actions)
    chat_card=False,      # also drop a rich card into the chat stream
    silent=False,         # suppress loud fan-out; still records a durable feed row
) -> Notification
```

**Fan-out (based on kind + severity + user channel pref):**

```
notify() ──► ALWAYS record to durable store  (the Notification Center feed)
         ├─► Telegram  (if channel∈{telegram,both} and not silent)
         ├─► chat card (if chat_card)          → [stub → WS in piece 3]
         ├─► ntfy push (if not silent)         → [stub → piece 2]
         └─► WS frame  (open app)              → [stub → piece 2]
```

### The load-bearing change

**Decouple "record to feed" from the channel toggle.** The in-app feed is a
*durable log of everything that happened* — it must always be written. The
`telegram|app|both` toggle now controls **loudness** (Telegram + push), never
whether a durable record exists. This alone makes the Notification Center
non-empty by default.

## Data model (migration on `notifications`)

Existing: `id, user_id, kind, title, body, meta, created_at`. Add:

- `severity TEXT NOT NULL DEFAULT 'normal'` — plaintext (client renders by it).
- `dedup_key TEXT` — plaintext; index `(user_id, dedup_key)`.
- `read_at TEXT` — plaintext; NULL = unread.

`deep_link` and `actions` ride inside the existing encrypted `meta` JSON
(no new encrypted columns): `meta = {"deep_link": {...}, "actions": [...], "thread_ref": {...}}`.

### Dedup

If a notification with the same `(user_id, dedup_key)` exists, is **unread**, and
was created within `DEDUP_WINDOW` (default 30 min) → **update** it in place
(refresh `title`/`body`/`created_at`, bump `meta.repeat_count`) instead of
inserting a new row. Collapses oscillating watchers and repeat nags.

### Read state / unread

- `mark_read(user_id, ids)` / `mark_all_read(user_id)` set `read_at`.
- `get_unread_count(user_id)` for the tab badge.
- `get_notifications_since` returns `severity`, `read_at`, `deep_link`, `actions`
  and an `unread` count alongside `notifications` + `now`.

## API (route additions)

- `GET  /api/notifications?since=` — existing; response gains `unread` + richer rows.
- `POST /api/notifications/read` `{ids:[...]}` — mark specific read.
- `POST /api/notifications/read-all` — mark all read.
- `GET  /api/notifications/unread-count` — `{unread: N}`.

## Backward-compat / migration of funnels

- `spine.notify()` is the new canonical implementation.
- `dispatch.deliver()` → thin wrapper over `notify()` (adds deep_link/actions).
- `deliver_heartbeat_push()` → **always records feed** (decoupled), Telegram gated.
- `push_telegram()` → gains `record_feed` (default True, always-on) + optional
  `kind`/`severity`/`deep_link`; transient callers pass `record_feed=False`.
- No emit call site is deleted; behavior only *adds* durable feed rows + enrichment.

## Out of scope for this piece (own specs later)

- **Piece 2:** ntfy self-hosted push + app-global WS notification frame + poll fallback.
- **Piece 3:** In-app Notification Center screen (mobile+web), deep-link routing,
  chat-card rendering, unified action buttons, badge.
- **Piece 4:** Watcher quietness — notify-only default, semantic condition gate,
  informative diff, emit through the spine, durable watcher-alert store.
- **Piece 5:** Inbox reliability — WhatsApp push bridge, history backfill,
  list/detail reconcile, outbound reflection, Email/IG arg fix, wire delta+WS.

## Testing

Scoped only — **never full pytest** (it hits the live `./data` DB → corruption
risk). New `tests/notifications/test_spine.py` + `test_feed_store.py`, run in
isolation against a temp DB.

## Verification

- Scoped unit tests green.
- `record_notification` writes a feed row for a `telegram`-channel user (proves
  the decoupling).
- Dedup collapses a repeat; read-state flips; unread count correct.
- No behavior regression on the existing `channel_message` path.
