# Notification → Chat Delivery (spine pieces 2+3)

**Date:** 2026-08-09
**Status:** Approved (user: "fan agents and fix that")
**Problem:** Proactive bot pings (reminders, cron results, task nags, watcher alerts) reach the mobile Notification Center and Telegram, but never appear in the app's chat thread. The user expects: ping lands in chat as a good summarized message, or in Telegram when that channel is selected.

## Root causes (verified 2026-08-09)

Two ping classes, both failing differently:

- **Class A — agent-turn pings** (heartbeat cron jobs, one-shot reminders, watcher brain turns): already persisted to `agent_messages` via the lane queue → `Agent.process_message` (single writer, `runtime/agent.py:~7807`). Invisible on mobile because the chat screen loads history **once per app process** (`chat_screen.dart` initState + `_seeded` guard + indexedStack keep-alive) and no WS frame is emitted for heartbeat-origin turns (callback is the Telegram notifier, not a WS callback).
- **Class B — pure notifications** (task nags/pulses, EOD summaries, expense alerts, watcher/channel alerts, all `spine.notify()` calls): never touch `agent_messages` by construction. Feed row + Telegram only.
- The 2026-07-16 notification-spine spec planned `realtime.py` (piece 2) and `chat_card.py` (piece 3); `spine.py`'s fan-out legs import them and silently no-op because **neither module was ever built**.
- Inconsistency (Gap 7): raw `push_telegram` (`notifications/push.py`) still gates the FEED write on `should_record_feed`, contradicting the spine's feed-row-always contract — some pings never reach the Notification Center at all.

## Design

**Routing** (reuses existing `users.settings.notifications.channel` ∈ telegram|app|both):

| channel | Telegram msg | chat message + WS frame | feed row |
|---|---|---|---|
| telegram | yes | no | always |
| app | no | yes | always |
| both | yes | yes | always |

**Server:**
1. `notifications/chat_card.py` (new) — appends a Class-B ping as an assistant-role row in the user's primary session, content `{title}\n{body}` (reuses existing formatters), metadata marker `{"notification_card": true, "kind": ...}` via `metadata_codec`.
2. **Context-pollution guard (critical):** `runtime/context_builder.py` excludes marked rows from LLM history. Chat-visible, LLM-invisible — prevents the documented self-perpetuating-hallucination class. Summarization/quarantine paths also skip them.
3. `notifications/realtime.py` (new) — per-user in-memory event bus (mirrors `task_event_bus`), pumped by `gateway/chat_ws.py` to connected sockets as:
   `{"type":"notification","id","kind","title","body","created_at"}`
4. Wiring: `spine.notify()` legs go live; `deliver_heartbeat_push`, `TelegramNotifier` background events, and `push_telegram` gain chat_card+realtime under the channel gate. `push.py` feed write becomes unconditional (Gap-7 fix). New `should_send_chat` helper in `channel.py`.
5. Class A gets a realtime **hint frame only** (no chat_card — the turn's reply is already persisted; a duplicate row would be written otherwise). Consolidated fanout results are suppressed from chat_card to avoid double rows next to the synthesized brain reply.
6. History endpoint exposes `"kind": "notification"` on marked rows for client styling.

**Mobile:**
1. `ws_frames.dart` gains the `notification` frame (tolerant parsing).
2. On notification frame / WS reconnect / app resume: **delta merge** of history tail by message id — no full reseed flicker, never clobbers an in-flight streaming reply. `_seeded` seeds once; merges always allowed. Resume refresh is unconditional (per the "reported reachable ≠ reachable" lesson).
3. `kind == notification` rows render with a subtle distinct treatment from the `lib/ui` kit; graceful fallback to a normal assistant bubble.
4. No new banners — the feed poller owns OS notifications.

## Out of scope
- FCM/APNs true background push (tracked in TODO.md).
- Web UI live rendering of the new frame (web ignores unknown frame types; history rows appear on reload).
- Making Telegram sends transactional with feed writes.

## Testing
Server: chat_card row shape + encryption, context_builder exclusion (most important), channel routing matrix, realtime bus→pump frame shape, push.py always-feed, history marker. Mobile: frame parsing (valid/minimal/malformed), merge dedup by id, streaming-safety, reconnect/resume triggers. Known hazards honored: pytest conftest DB repoint; FakeAsync/sqflite; production exception shapes in fakes.
