# Unified Cross-Channel Comms — Design Spec

**Date:** 2026-06-08
**Branch:** `feat/flutter-mobile`
**Status:** Approved design, pre-implementation

## 1. Problem

Today LazyClaw treats **Telegram** as a first-class bidirectional channel and treats **WhatsApp / Email / Instagram** as poll-only MCP tools whose new-message alerts are **hardcoded to push to Telegram**. The user has a native Flutter app but:

1. **Notifications never reach Flutter.** New-message alerts from WhatsApp/Email/IG bypass the in-app notification feed entirely.
2. **No reply-from-app.** There is no unified "reply to this contact on this channel" path and no inbox UI.
3. **No autonomous conversations.** There is no primitive for "send a message, wait for the reply, continue until you have an answer, then report back."

### Root-cause findings (from codebase recon)

- A clean per-user routing layer already exists: `notifications/channel.py` (`telegram` | `app` | `both`) + an encrypted in-app feed `notifications/feed_store.py`, exposed at `GET /api/notifications?since=` (`gateway/routes/notifications.py`). **The Flutter app already polls this feed** (`mobile/lib/notifications/notifications_service.dart`) and raises native local notifications (`mobile/lib/notifications/local_notifications.dart`).
- **BUT** the MCP watcher path bypasses all of it: `heartbeat/daemon.py:1441-1464` (and the browser-watcher path `:1225-1264`) call `push_telegram()` directly. So WhatsApp/Email/IG alerts are Telegram-only and never record to the feed → never reach Flutter. **This is the delivery bug.**
- WhatsApp MCP detection **works**: Baileys caches messages; `heartbeat/mcp_watcher.py:173-351` polls `whatsapp_read` (~60s), diffs `last_seen_ids`, formats a digest. Only the *delivery sink* is wrong. Email (IMAP ~5m) and Instagram (~10m) follow the same poll-then-Telegram pattern.
- Outbound is fragmented: only `channels/telegram.py` implements `ChannelAdapter.send_message`. WhatsApp/Email/IG sends are raw MCP tool calls (`whatsapp_send`, `email_send`, `instagram_send_dm`) the agent must invoke with manually-resolved recipients. There is **no unified reply abstraction**.
- There is **no wait-for-reply / conversation-loop primitive** anywhere. Closest building blocks: `runtime/goal_executor.py`, `runtime/task_runner.py`, `teams/specialists/messaging_specialist.md` (already F1-grounded), and the `escalate_to_human.py` / `plan_checkpoint.py` `asyncio.Event` wait pattern.

## 2. Goals / Non-Goals

### Goals
- New messages on **any** channel (WhatsApp/Email/Instagram/Telegram) raise a **native notification inside the Flutter app**, reusing the existing local-notification mechanism (the same path task reminders use). **No Firebase/Google.**
- A **unified Inbox** in Flutter listing conversation threads across all channels, with a reply bar supporting **direct send** and **"Ask AI"**.
- An **autonomous `ConversationTask`**: the user states an intent ("ask him if he's coming to my birthday"); the AI sends the opener (after one approval), runs the back-and-forth, and reports the extracted answer — **generalized across every channel**.

### Non-Goals
- No Firebase Cloud Messaging / APNs (decided: reuse existing local notifications + WS + feed poll).
- No full duplicated/encrypted copy of every channel message in LazyClaw (inbox reads live via MCP).
- No new MCP push/webhook transport for WhatsApp/Email/IG in this pass (stay poll-based; latency unchanged: ~60s WA, ~5m email, ~10m IG). A push-webhook upgrade is a future, optional improvement.
- No changes to the Telegram native adapter's existing behavior beyond routing its system notifications through the new funnel.

## 3. Architecture overview

Five pieces, built together but layered:

```
                          ┌─────────────────────────────────────────┐
                          │  notifications/dispatch.py  (notify())   │  ← Section A
   watchers / crons ─────▶│  always record_feed + maybe Telegram     │
   conversation runner ──▶│                                          │
                          └───────────────┬──────────────────────────┘
                                          │ feed entry (kind=channel_message, thread_ref)
                                          ▼
   Flutter feed poll + WS frame ──▶ native LocalNotification ──▶ tap ──▶ Inbox thread

   comms/ (new module)
   ┌──────────────────────────────────────────────────────────────────┐
   │  thread_store.py      channel_threads  (encrypted, ?since= sync)  │  ← Section B
   │  gateway.py           ChannelGateway: read_thread/send/resolve    │  ← Section C
   │  conversation_store.py conversation_tasks (encrypted state)       │  ← Section E
   │  conversation_runner.py  heartbeat-driven state machine           │  ← Section E
   └──────────────────────────────────────────────────────────────────┘
            ▲                                   ▲
            │ gateway/routes/inbox.py           │ heartbeat daemon tick
            │ (list/messages/reply/read)        │ (drives due conversation_tasks)
            ▼                                   ▼
   Flutter Inbox tab + reply bar (Lz* kit)      Messaging Specialist (F1-grounded)
```

## Section A — The `notify()` funnel

**New file:** `lazyclaw/notifications/dispatch.py`

```python
async def deliver(
    config: Config,
    user_id: str,
    *,
    title: str,
    body: str,
    kind: str = "info",
    inline_keyboard: Sequence[Sequence[dict]] | None = None,
    thread_ref: ThreadRef | None = None,   # (channel, contact_handle) for tap-to-open
) -> bool:
    """Single delivery funnel. ALWAYS records to the in-app feed; sends to
    Telegram only if the user's notification channel setting includes it."""
```

Behavior:
1. Resolve channel via `notifications/channel.py:get_notification_channel`.
2. **Always** `record_notification(config, user_id, kind, title, body)` for channel-message kinds (so the app always has them regardless of the telegram/app/both toggle — channel messages must reach the phone). The `thread_ref` is stored in the feed entry metadata for tap-to-open.
3. If `should_send_telegram(channel)`: send via the existing Telegram path (retry wrapper).
4. Return delivered.

**Refactor (minimal, not a rewrite):**
- `heartbeat/daemon.py` MCP-watcher push (`:1441-1464`) and browser-watcher push (`:1225-1264`): replace direct `push_telegram(...)` with `dispatch.deliver(..., kind="channel_message", thread_ref=...)`.
- `notifications/push.py:push_telegram` stays as a back-compat wrapper that calls `deliver` for the admin user (keeps all existing callers working).
- The `notifications` table gains a nullable `meta` column (encrypted JSON: `{thread_ref:{channel,contact}}`); `feed_store` reads/writes it. (Additive migration in `db/connection.py`.)

**Real-time liveness (foreground):** add a `channel_message` frame to the chat WS (`gateway/routes/chat_ws.py`) so that while the app is open, a new channel message pushes immediately (inbox updates live + local notification fires without waiting for the next poll). Backgrounded delivery relies on the existing WorkManager feed poll + on-resume catch-up.

## Section B — Unified inbox store (thread-centric)

**New file:** `lazyclaw/comms/thread_store.py` + `lazyclaw/comms/models.py`

Encrypted table `channel_threads`:

| column | type | notes |
|---|---|---|
| `id` | TEXT PK | client-mintable UUID (offline create parity) |
| `user_id` | TEXT | FK, all queries scoped |
| `channel` | TEXT | `whatsapp\|email\|instagram\|telegram` (plaintext, for filtering) |
| `contact_handle` | TEXT | plaintext key (phone/email/username/chat_id) — needed for dedup/query |
| `contact_name` | TEXT enc | resolved display name |
| `last_preview` | TEXT enc | last message snippet |
| `unread_count` | INTEGER | plaintext |
| `last_activity` | TEXT | ISO, plaintext (sorting) |
| `last_seen_msg_id` | TEXT enc | dedup cursor mirror of watcher state |
| `created_at` / `updated_at` | TEXT | plaintext |
| `deleted_at` | TEXT NULL | tombstone for `?since=` sync |

Functions: `upsert_thread`, `bump_unread`, `mark_read`, `list_threads`, `get_thread`, `list_changes(since)`. Same encryption envelope + sync primitives as tasks/notes (`deleted_at` tombstone, client `id`, `GET .../changes?since=`).

**Wiring:** the watcher diff in `heartbeat/mcp_watcher.py` / `daemon.py` calls `thread_store.upsert_thread(...) + bump_unread(...)` alongside `dispatch.deliver(...)`. The thread row is the inbox's source of truth for the **list**; message **bodies** are fetched live (Section C).

## Section C — `ChannelGateway` (unified reply abstraction)

**New file:** `lazyclaw/comms/gateway.py`

```python
class ChannelGateway:
    async def read_thread(self, channel: str, contact: str, *, limit=30) -> list[Msg]: ...
    async def send(self, channel: str, contact: str, text: str) -> SendResult: ...
    async def resolve_contact(self, channel: str, name_or_handle: str) -> Contact: ...
```

- Dispatches to MCP tools per channel: `whatsapp_read`/`whatsapp_send`, `email_search`/`email_send`, `instagram_read_dms`/`instagram_send_dm`, and the Telegram native adapter for `telegram`.
- **Telegram caveat:** the Telegram Bot API only exposes the bot's *own* chats (i.e. the admin chat), not arbitrary contact DMs. So WhatsApp/Email/Instagram get true multi-contact inboxes; the Telegram "thread" is limited to the bot's own conversation and primarily serves the autonomous-conversation + notification paths, not a general contact inbox. The Flutter filter still lists Telegram for parity but won't surface third-party Telegram DMs.
- `resolve_contact` uses the unified contact store (`contacts/store.py` + `find_contact`).
- Honors the existing Upwork/Tiptap-style send guards where applicable; reuses each MCP's grounding hardening on reads.
- One place, channel-agnostic — every reply and every conversation step goes through this.

**New routes:** `lazyclaw/gateway/routes/inbox.py`
- `GET /api/inbox/threads?since=` → list + changes (sync).
- `GET /api/inbox/threads/{id}/messages` → live `ChannelGateway.read_thread`.
- `POST /api/inbox/threads/{id}/reply` body `{text, mode: "direct"|"ai"}` → `direct` sends verbatim via gateway; `ai` starts a `ConversationTask` (Section E) with `text` as the goal.
- `POST /api/inbox/threads/{id}/read` → mark read.

Registered in `gateway/app.py`. CORS/origin notes mirror existing routes.

## Section D — Flutter Inbox + reply bar

**New:** `mobile/lib/screens/inbox/` (inbox_screen, thread_screen, widgets) + `mobile/lib/comms/` (repo + providers).

- **Router:** add an Inbox `StatefulShellBranch` in `mobile/lib/core/router/app_router.dart` + an `LzBottomNav` destination (mail icon). Keep `StatefulShellRoute.indexedStack` so state survives tab switches.
- **Inbox list:** `LzScaffold` + `LzAppBar`, channel-filter `LzChip` row (All/Telegram/WhatsApp/Email/Instagram), thread list of `LzListTile` with channel badge (`LzStatusDot`) + unread `LzBadge`. Threads from `inboxThreadsProvider` (mirrors `tasksProvider`: local cache + `?since=` sync + reachability banner).
- **Thread screen:** message bubbles (reuse ChatBubble styling), live-loaded via `GET .../messages`. Bottom `InboxReplyBar` with a mode toggle:
  - **Send** → `POST reply {mode:"direct"}`.
  - **Ask AI** → `POST reply {mode:"ai"}` → shows a running indicator; the conversation result later arrives as a notification + a thread update.
- **Notifications:** reuse `LocalNotifications` (`lazyclaw_notifications` channel). Tapping a `channel_message` notification deep-links to the thread (extend `mobile/lib/core/actions/app_actions.dart`). While foreground, the `channel_message` WS frame updates the inbox provider live.
- **Approval:** the first-message approval reuses the existing `approval_request` WS frame + the app's current approval UI — one-tap approve/deny. No new approval surface.
- **Design rules:** `Lz*` kit only — `AppColors`/`AppText`/`AppSpacing`/`AppRadii`/`AppMotion`, no hardcoded values.

## Section E — Autonomous `ConversationTask` runner

**New files:** `lazyclaw/comms/conversation_store.py`, `lazyclaw/comms/conversation_runner.py`

Encrypted table `conversation_tasks`:

| column | notes |
|---|---|
| `id`, `user_id` | scoped |
| `channel`, `contact_handle` | plaintext routing keys |
| `contact_name` | enc |
| `goal` | enc — the user's intent ("ask if coming to birthday") |
| `completion_criteria` | enc — LLM-evaluable exit ("got a clear yes/no") |
| `status` | `drafting\|awaiting_approval\|running\|done\|failed\|expired\|aborted` |
| `transcript_json` | enc — `[{dir, text, ts}]` running record |
| `iteration`, `max_iterations` | bound |
| `poll_interval`, `next_poll_at` | heartbeat scheduling |
| `created_at`, `last_activity_at`, `expires_at` | bounds |
| `result`, `error` | enc — final answer / failure reason |
| `approval_id` | links to the pending first-message approval |

**Key decision — heartbeat-driven, not a held-open coroutine.** A `ConversationTask` is a specialized watcher: restart-safe, reuses the existing heartbeat polling infra. The daemon adds a check each tick:

```
for task in conversation_store.list_due(now):        # status=running, next_poll_at<=now
    await conversation_runner.step(task)
```

**State machine (`conversation_runner.step`):**

1. **`drafting`** → Messaging Specialist drafts the opening message from `goal` (worker LLM, F1-grounded). Persist draft. Create approval (`approval_request` frame to Flutter + Telegram inline button). → `awaiting_approval`.
2. **`awaiting_approval`** → on approve: `ChannelGateway.send(...)`, append to transcript, set `next_poll_at` (short, expect a quick reply). → `running`. On deny/timeout: → `aborted`/`expired`.
3. **`running`** → `ChannelGateway.read_thread(...)`; if a **new contact message** arrived:
   - Brain evaluates against `completion_criteria`: satisfied? → extract answer → `result`, `status=done`, `dispatch.deliver(...)` the answer to Flutter (+Telegram). 
   - Not satisfied → Messaging Specialist drafts a follow-up → `ChannelGateway.send(...)` (autonomous now, per "approve 1st then autonomous") → append transcript → reschedule with backoff.
   - No new message → reschedule with exponential backoff (30s → cap), until `max_iterations` / `expires_at` → `status=failed`/`expired` + notify.

**Triggers converge** on `conversation_runner.start(user_id, channel, contact, goal, completion_criteria=None)`:
- Inbox reply bar "Ask AI" (`POST reply {mode:"ai"}`).
- Chat NL — extend `runtime/instant_dispatch.py` with a pattern like `ask .* (on|via) (whatsapp|instagram|telegram|email)` → start a task.
- Telegram NL — same dispatch path.

Composition delegates reading + drafting to the existing **Messaging Specialist** (inherits quote-then-summarize / most-recent-wins). Generalizes across channels because it only touches `ChannelGateway` + the specialist.

## 4. Data flow examples

**New WhatsApp message → Flutter (Phase-1 fix):**
```
WA msg → Baileys cache → heartbeat poll (whatsapp_read) → mcp_watcher diff (new id)
  → thread_store.upsert_thread + bump_unread
  → dispatch.deliver(kind="channel_message", thread_ref=(whatsapp, +34...))
       → record_notification (feed)  [+ Telegram if setting includes it]
  → Flutter: WS channel_message frame (if foreground) OR feed poll (background)
       → LocalNotifications.show → tap → Inbox thread (live read)
```

**"Ask Alice on WhatsApp if she's coming to my birthday":**
```
trigger → conversation_runner.start(whatsapp, Alice, goal)  → status=drafting
  tick: specialist drafts opener → approval_request (Flutter one-tap) → awaiting_approval
  approve → gateway.send → running, next_poll_at=+30s
  tick: gateway.read_thread → Alice replied "maybe, when is it?"
       → brain: not satisfied → specialist follow-up "Sat the 14th, 7pm" → send → backoff
  tick: Alice "yes I'll be there!" → brain: satisfied → result="Yes"
       → dispatch.deliver("Alice is coming 🎉") → done
```

## 5. Testing plan (TDD, ≥80%)

**Python (pytest):**
- `comms/thread_store` CRUD + changes/tombstone.
- `comms/conversation_store` CRUD + `list_due` scheduling.
- `comms/gateway` dispatch per channel (mocked MCP tools) — incl. contact resolution + send guards.
- `comms/conversation_runner` full state machine (mocked specialist + gateway): drafting→approval→running→done, follow-up branch, timeout/expire/abort, restart-safety (rehydrate mid-run).
- `notifications/dispatch.deliver` routing matrix (telegram/app/both × channel_message always-feed).
- `gateway/routes/inbox` endpoints (list/messages/reply direct+ai/read).
- Daemon: watcher now routes through `deliver` + upserts threads (regression: no more direct `push_telegram` for channel messages).

**Flutter:**
- `inbox_threads_provider` sync (local cache + `?since=` merge, last-write-wins) — **fakes must throw the production `DioException(error: ApiError)` shape** (sync-engine lesson).
- Inbox list + thread + reply-bar widget tests (direct vs Ask-AI mode).
- Deep-link from `channel_message` notification → thread.

**Live smoke:** confirm WhatsApp MCP is actually up (`whatsapp_status`) and one real alert flows end-to-end to a Flutter local notification.

## 6. File manifest

**New (Python):** `lazyclaw/comms/__init__.py`, `models.py`, `thread_store.py`, `gateway.py`, `conversation_store.py`, `conversation_runner.py`; `lazyclaw/notifications/dispatch.py`; `lazyclaw/gateway/routes/inbox.py`.
**Edit (Python):** `heartbeat/daemon.py` (route via `deliver`, upsert threads, drive conversation tasks), `heartbeat/mcp_watcher.py` (thread upsert hook), `notifications/push.py` (wrapper), `notifications/feed_store.py` (+`meta`), `db/connection.py` (+`channel_threads`, +`conversation_tasks`, +`notifications.meta`), `gateway/app.py` (register routes), `gateway/routes/chat_ws.py` (+`channel_message` frame), `runtime/instant_dispatch.py` (ask-X pattern), `teams/specialists/messaging_specialist.md` (conversation-loop guidance).
**New (Flutter):** `mobile/lib/screens/inbox/*`, `mobile/lib/comms/*` (repo + providers + models).
**Edit (Flutter):** `core/router/app_router.dart`, `core/actions/app_actions.dart`, notifications wiring, `main.dart` deep-link.

## 7. Risks & mitigations
- **Live-read latency on thread open** (WA read = seconds) → show a skeleton loader; cache last preview from the thread row.
- **Channel session down** (MCP not logged in) → gateway returns a typed error; inbox shows a "reconnect <channel>" banner; conversation task → `failed` with a clear reason.
- **Autonomous follow-ups messaging real people** → bounded by `max_iterations` + `expires_at`; first message always approved; all sends honor existing channel send-guards (e.g. Upwork no-links). Transcript persisted for audit.
- **Notification dupes** (feed + Telegram) → `deliver` is the single funnel; `source_is_telegram` guard preserved; thread `last_seen_msg_id` dedups watcher re-fires.
- **Build sequencing** — built together but land in this internal order so each is testable: A (funnel) → B (threads) → C (gateway+routes) → D (Flutter inbox) → E (conversation runner). Plan will checkpoint between them.

## 8. Open implementation notes
- `notifications.meta` migration must be additive (existing rows NULL).
- Reuse `find_contact` before any send (existing rule) inside `ChannelGateway.resolve_contact`.
- Keep `push_telegram()` callers working via the wrapper; do not break heartbeat crons/reminders.
- Conversation runner must be idempotent per tick (a crashed mid-step must not double-send — guard with `next_poll_at` advance + transcript check before send).
