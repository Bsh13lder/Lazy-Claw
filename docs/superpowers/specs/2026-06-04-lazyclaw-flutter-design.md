# LazyClaw Flutter App — Design Spec

**Date:** 2026-06-04
**Status:** Draft for review
**Author:** Claude (brainstormed with user)
**Target device:** Xiaomi Mi 15 (Android / HyperOS) first; iOS later

---

## 1. Overview

Build a native **Flutter** mobile client for LazyClaw (the E2E-encrypted AI agent platform). The app is **offline-first**: because the LazyClaw backend runs on the user's own computer — which is frequently asleep/off/away from network — the phone must let the user browse and edit core data while the backend is unreachable, store that data **encrypted on the device**, and **sync** (push queued changes + pull updates) when the backend comes back online.

The app is built by **harvesting the existing `taskbot_flutter` app as a donor** (a mature, sibling Flutter app — same backend family: Python/FastAPI + cookie sessions + AI agent + skills + memory) rather than building from scratch. We transplant the donor's proven generic scaffolding, delete its taskbot-specific business domain, rewire the remaining screens to LazyClaw's API, and build the net-new offline-sync engine and WebSocket chat client.

### Donor & target locations
- **Donor:** `/Users/blckit/Desktop/Code_Projects/taskbot/taskbot_flutter/` (Flutter, Riverpod, go_router, Dio, ~37k LOC, `v2.4.0+145`).
- **New app:** `lazyclaw/mobile/` — a new top-level directory inside the LazyClaw repo, parallel to `web/`. *(Default choice; one repo keeps the Flutter app, web app, and backend together.)*
- **Backend:** `lazyclaw/lazyclaw/gateway/` (FastAPI HTTP + WS, default port `18789`).

---

## 2. Goals / Non-Goals

### Goals (v1 — "broad parity")
1. **Agent chat** over WebSocket streaming (tokens, tool-calls, approval gates, plan gates, background-task results).
2. **Offline-first CRUD** for: Tasks, Notes (LazyBrain), Budgets/Expenses/Projects — read + create + edit while backend is offline, synced on reconnect.
3. **Encrypted local store** on the device (SQLCipher), key held in Android Keystore.
4. **Sync engine**: outbox (queued mutations) + pull (delta) + last-write-wins conflict resolution with a conflict log (no silent data loss).
5. **Read views** for Memory, Settings/Models, Skills, Jobs/Watchers.
6. **Notifications** (WS-while-open + polling) with tap-to-deep-link.
7. **Android release** build, tested on the Mi 15, including a HyperOS autostart/battery-optimization onboarding prompt.
8. **APK delivery from the web UI**: a **Download APK** button in web Settings (version + QR code to scan from the phone), served by the backend, plus an **Android controls** panel (device-permission helper + app-behavior toggles).

### Non-Goals (v1)
- Univer Sheets/Docs editing, full PDF editing (web-only; mobile shows read-only/export at most).
- LazyBrain force-directed **graph** visualization (replace with a fast note **list + search** on mobile).
- ReactFlow browser-template canvas editor (configure on web).
- Browser-automation live canvas / VNC takeover (online-only; minimal status view only).
- Real FCM/APNs push (deferred to phase 2).
- Client-side E2E crypto for the *transport* (server decrypts server-side; see §4).
- iOS release (codebase stays cross-platform, but signing/TestFlight is a later phase).

---

## 3. Architecture

```
┌──────────────────────── Flutter app (lazyclaw/mobile) ────────────────────────┐
│ Presentation: screens + widgets (Riverpod ConsumerWidgets)                     │
│      reads/writes ↓ (never calls the API directly for syncable domains)        │
│ Local domain: Riverpod providers → Repositories                                │
│      ├─ syncable repos read/write the LOCAL DB first (offline-first)            │
│      └─ online-only repos call the API directly (chat exec, search, browser)    │
│ Data:                                                                           │
│   ├─ Local DB: Drift (SQLite) + SQLCipher  ← encrypted at rest on device        │
│   │     tables: tasks, notes, projects, expenses, outbox, sync_state, conflicts │
│   ├─ Outbox: queued create/update/delete mutations (encrypted)                  │
│   └─ Secure: session_id cookie + DB key (flutter_secure_storage → Keystore)     │
│ Services:                                                                        │
│   ├─ ApiClient (Dio + PersistCookieJar)  ← harvested, repointed                 │
│   ├─ ReachabilityService (is the user's computer actually up?)                  │
│   ├─ SyncEngine (push outbox → pull deltas → reconcile, LWW)                     │
│   ├─ ChatSocket (web_socket_channel, authed via session_id cookie)              │
│   └─ NotificationService (local notifications + deep-link on tap)               │
└────────────────────────────────────────────────────────────────────────────────┘
        │ HTTPS REST + WSS                         │ background
        ▼                                          ▼
  LazyClaw gateway (FastAPI)                  WorkManager periodic sync
  /api/auth, /api/tasks, /api/budgets,        (HyperOS-throttled; best effort)
  /api/lazybrain, /ws/chat, /api/system
```

**Key principle:** syncable screens **never touch the network directly**. They read/write the local DB; the SyncEngine reconciles with the backend out of band. This is what makes the app instant and fully usable offline.

### Tech stack (matches the donor where possible)
| Concern | Choice | Source |
|---|---|---|
| State mgmt | Riverpod | donor |
| Routing | go_router (auth-guard + ShellRoute) | donor (copy-and-tweak) |
| HTTP | Dio + `dio_cookie_manager` + `cookie_jar` (file-backed `PersistCookieJar`) | donor (copy-as-is) |
| WebSocket | `web_socket_channel` | net-new |
| Local DB | **Drift** (SQLite) + **SQLCipher** (`sqlcipher_flutter_libs`) | net-new |
| Secure key store | `flutter_secure_storage` (Android Keystore) | net-new |
| Connectivity | `connectivity_plus` + active `/api/system/about` health ping | net-new |
| Background sync | `workmanager` | net-new |
| Notifications | `flutter_local_notifications` | donor (port pattern) |
| Markdown render | `flutter_markdown` (donor only had regex bold/italic) | net-new |

---

## 4. Encryption & secrets (important nuance)

LazyClaw is E2E-encrypted, but **the mobile client needs no crypto to talk to the server**: every API route is `user_id`-scoped and returns **fully decrypted JSON** to the owner; the server holds the DEK and decrypts server-side. So we **drop the donor's client-side `CryptoService`** entirely (it persisted an E2E key in plaintext SharedPreferences — a gap we simply remove by not porting it).

**However**, caching data locally introduces a *new, separate* requirement: **device-at-rest encryption**. The local Drift DB is encrypted with **SQLCipher**. The DB key is a random 256-bit value generated on first launch and stored in the **Android Keystore** via `flutter_secure_storage`. The `session_id` cookie is likewise kept in secure storage (not plaintext prefs). On logout, the local DB is wiped.

Two device secrets total: `session_id` (talk to backend) and the local DB key (decrypt local cache). Both device-only, independent of the LazyClaw account password and the server DEK.

---

## 5. Data & Sync design

### 5.1 Syncable domains (v1)
| Domain | Backend table | Mobile capability |
|---|---|---|
| Tasks | `tasks` | read + create + edit + complete + delete, offline |
| Notes (LazyBrain) | `notes` | read + create + edit + delete, offline; markdown + wikilinks + local search |
| Projects | `projects` | read + create + edit + delete, offline |
| Expenses | `project_expenses` | read + create + edit + delete, offline |
| Memory (view) | `notes` filtered by `memory_type` | **read-only on mobile v1** (sidesteps the `personal_memory`↔`notes` duality) |

**Online-only** (no offline cache; show a friendly "needs your computer online" state): agent execution, browser automation, skill execution, semantic search / RAG / ask, document editing, vault.

### 5.2 Local row contract
Every locally-cached syncable row carries sync bookkeeping columns in addition to the server fields:
- `id` — **client-generatable UUID** (all four LazyClaw tables already use UUID PKs).
- `updated_at` — server timestamp (authoritative for LWW).
- `dirty` (bool) — locally modified, not yet pushed.
- `deleted` (bool) — local tombstone (pending delete push).
- `last_synced_at` — cursor bookkeeping.

### 5.3 Write path (offline-capable)
1. User edits → repository **writes the local DB immediately** (optimistic UI) and sets `dirty=1`.
2. Repository appends a mutation to the **outbox**: `{op: create|update|delete, entity, id, payload, client_ts}`.
3. UI re-reads from the local DB → instant feedback, fully offline.

### 5.4 Sync engine
Triggered on: app foreground, connectivity-regained, WorkManager periodic (~15–30 min), and manual pull-to-refresh.

- **Reachability gate:** ping `GET /api/system/about` — having internet ≠ the user's computer being awake. Only sync when the backend actually answers. UI shows a **"Computer offline — changes will sync"** banner otherwise.
- **Push phase:** drain the outbox in order. Creates send the **client UUID** so the server upserts by id (idempotent replay — no duplicates if a push is retried). Updates carry `client_ts`.
- **Pull phase:** `GET /api/<domain>/changes?since=<cursor>` → rows with `updated_at > cursor` + tombstones since the cursor. (v1 fallback before the endpoint exists: full-list fetch for these small personal datasets.)
- **Conflict resolution — last-write-wins by `updated_at`** (matches LazyClaw's existing *most-recent-wins* doctrine). If a row changed both locally and on the server since last sync, the newer `updated_at` wins; the **losing version is written to a local `conflicts` table** the user can review. **Never silently discard.** *(Default; alternative "server-always-wins" is a one-line policy swap.)*
- **Deletes:** propagated via tombstones so an offline delete actually removes the row on the server and a server-side delete removes it locally (instead of the row resurrecting on the next pull).

### 5.5 Chat offline
Sending a chat message while the backend is unreachable: the message is stored and shown as **"queued — will send when your computer is back."** On reconnect it is delivered over the WS and the reply streams in. No offline agent reply (the LLM, tools, and browser all live server-side).

---

## 6. Required backend additions (the "Sync API" sub-phase)

These are small, additive Python changes in `lazyclaw/lazyclaw/`. Ordered by necessity:

1. **`tasks.updated_at` (MANDATORY).** Add `updated_at TEXT` column (migration in `db/connection.py`), add it to `TASK_COLUMNS` in `tasks/store.py`, and **bump it on every UPDATE** (`update_task`, `complete_task`, `fail_task`, `set_steps`, `append_progress_entry`). Without this, last-write-wins cannot work for tasks. *(Notes and projects/expenses already have a bumped `updated_at`; `budget_entries` needs one too if it becomes syncable.)*
2. **Accept optional client `id` on creates** for tasks, notes, projects, expenses (`CreateTaskBody`/`NoteCreate`/etc. + the store `create_*` functions, upsert-by-id) → idempotent outbox replay.
3. **Tombstones** per syncable domain: add `deleted_at TEXT` (or per-domain tombstone table); flip the hard `DELETE FROM` to soft-delete + filter from lists. *(Mind the FK-cascade caveat: SQLite `PRAGMA foreign_keys=0`, so `delete_note` manually sweeps embeddings/chunks/links — keep those sweeps gated on real/hard deletion, not soft.)*
4. **`GET /api/<domain>/changes?since=<iso>`** per syncable domain → rows where `updated_at > since` + tombstones since that cursor. Notes already list by `updated_at DESC` (cheap to add); others get an index.
5. **Memory duality:** v1 reads memory from `notes` (filtered by `memory_type`); we do **not** sync the legacy `personal_memory` table. (Revisit only if mobile needs offline memory *writes*.)

These backend changes ship with their own tests and a `make rebuild` step. They are backward-compatible (additive columns/endpoints; web app unaffected).

---

## 7. Donor harvest manifest

From the audit of `taskbot_flutter/lib/`:

| Donor module | Action | Notes |
|---|---|---|
| `core/api/api_client.dart`, `api_exceptions.dart` | **COPY-AS-IS** | Repoint base URL to gateway; change cookie name `session` → **`session_id`** in `getSessionCookie()`. |
| `core/router/app_router.dart` | COPY-AND-TWEAK | Keep the auth-guard redirect + ShellRoute skeleton; replace the route table with LazyClaw screens. |
| `core/theme/app_theme.dart` | COPY-AND-TWEAK | Keep dark palette + accents; source `themeId` from LazyClaw settings. |
| `core/constants/app_constants.dart` | COPY-AND-TWEAK | Change all values; base URL → `:18789`. |
| `providers/auth_provider.dart` + `repositories/auth_repository.dart` | COPY-AND-TWEAK | Keep cookie-session state machine + `on401 → logout`; swap endpoints; **drop** all `CryptoService`/`encryptionSalt`/NaCl/project-key plumbing. |
| `widgets/layout/app_shell.dart` | COPY-AND-TWEAK | Keep lifecycle observer (refresh on resume), last-route restore, draggable FAB, double-back-to-exit; swap nav destinations. |
| `widgets/layout/app_drawer.dart` | REFERENCE-ONLY | Port the header + sectioned nav idiom; rebuild contents. |
| `core/services/notification_service.dart` | REFERENCE-ONLY | Keep plugin init + `rootNavigatorKey` tap→route + markdown-strip; replace the two 30s pollers with WS-driven triggers. |
| `models/agent_message.dart` | COPY-AND-TWEAK | Align fields to LazyClaw; `metadata['type']` stays the inline-card discriminant. |
| `screens/agent/browser_agent_screen.dart` + `repositories/browser_repository.dart` | REFERENCE-ONLY | Port the visual idioms (reverse ListView, optimistic bubble, collapse-long-message, **typed inline-card dispatch**, tool-context label); **replace** the 10s `Timer.periodic` polling + `POST /api/agent/chat` with the WS stream. |
| `core/services/crypto_service.dart` | **DROP** | Not ported — server-side encryption (§4). |
| All `tasks/expenses/budgets/labels/projects/sharing/timeline/stats` screens/models/repos | **DELETE** | Taskbot domain. (Task/expense **screens** are rebuilt against LazyClaw's API — keep the UX idioms, not the taskbot data layer.) |

---

## 8. WebSocket chat client (net-new, contract confirmed)

- **Connect:** `wss://<host>/ws/chat` (or `ws://` for localhost), send the `session_id` cookie in the handshake headers (reuse `ApiClient.getSessionCookie()`). **Do not send an Origin header** (native client → server allows absent Origin; presence triggers CORS check). Failure codes: `4001` unauthorized, `1008` origin-not-allowed.
- **Client → server frames:** `message {content, session_id?}`, `side_note {content}`, `approval_response {request_id, approved}`, `cancel`, `ping`.
- **Server → client frames (handle):** `token`, `tool_call`, `tool_result`, `bg_event` / `bg_tool_call` / `bg_tool_result`, `phase`, `thinking_delta` / `thinking_done`, `team_delegate`, `specialist_*`, `plan_pending` / `plan_question` / `plan_approved`, `approval_request`, `side_note_ack`, `queued_user_message`, `background_done` / `background_failed`, `done`, `cancelled`, `error`, `pong`, `browser_event`, `template_*`.
- **Quiet mode (`bg_streaming=false`):** progress frames (`token`/`tool_call`/`phase`/`specialist_*`/`bg_*`) are dropped server-side; the final reply arrives in `done.content`. Gate frames (`plan_*`, `approval_request`) and terminal frames (`done`/`error`/`cancelled`, `background_done/failed`) are always sent. Toggle via `POST /api/agent/streaming/settings {bg_streaming}` or the `/streaming on|off` chat command.
- **Reconnect:** exponential backoff (1s→30s), `ping` keep-alive every 30s, replay queued outgoing messages on reconnect.
- **UI:** mobile shines for one-tap **approve/deny** on `approval_request` and accept on `plan_pending`.

---

## 9. Xiaomi Mi 15 / HyperOS considerations

HyperOS aggressively kills background apps and throttles background work:
- **Foreground sync and sync-on-reconnect always work** (app open) — the offline experience is solid in active use.
- **Background WorkManager catch-up sync** can be delayed/skipped unless the user grants the app **Autostart** + sets battery to **"No restrictions."** Ship an in-app **first-launch onboarding** that deep-links the user to those two HyperOS settings and explains why.
- This also motivates FCM in phase 2 (high-priority push can wake the app past HyperOS limits).
- Build/run target: real device (Mi 15) — no Apple Developer account needed for Android.

---

## 10. Phasing

| Phase | Deliverable |
|---|---|
| **0. Build env + scaffold** | Repair the Android toolchain (JDK on PATH, `ANDROID_HOME`, `cmdline-tools`, accept licenses — proven-good since the donor built APKs here before). New `lazyclaw/mobile` Flutter project; copy donor `core/api` + `api_exceptions`; repoint base URL + cookie name; secure session storage; delete taskbot domain. |
| **1. Offline foundation** | Auth (login/register/me) + shell + theme against LazyClaw login; **encrypted Drift+SQLCipher DB**; Keystore key; `ReachabilityService` + offline banner. |
| **★ A. First installable APK** | A **thin WS chat** (connect → send → stream text reply) + **APK delivery**: backend serve endpoint (`GET /api/mobile/apk` + version) and web **Settings → Mobile App** panel with **Download button + QR + Android-controls helper**. `flutter build apk` → **installable on the Mi 15**. Every later phase re-publishes the same APK via this button. |
| **2. Sync API (backend)** | `tasks.updated_at`, client-id-on-create, tombstones, `?since=` changes endpoints, memory-from-notes; tests + `make rebuild`. |
| **3. Sync engine** | Outbox + push/pull + last-write-wins + conflict log + WorkManager; piloted end-to-end on **Tasks**. |
| **4. Tasks** | Offline-first task UI (buckets, quick-add NL, steps, complete, priorities) on the sync engine. |
| **5. Budgets** | Offline-first projects + expenses + budget bars + receipt picker. |
| **6. Notes (LazyBrain)** | Offline-first notes: markdown render + wikilinks + local search + backlinks (list, not graph). Memory read-view via `memory_type`. |
| **7. Chat (full)** | Upgrade the thin chat: tool-call cards, approval/plan gates, background-task result cards, typed inline-card dispatch, offline send-queue. |
| **8. Read views + notifications** | Settings/Models, Skills, Jobs/Watchers (read); notifications (WS+poll) + deep-link; HyperOS autostart/battery prompt. |
| **9. Hardening** | Decompose oversized ported screens (donor had 2,800-line files; cap 800), tests to **80%**, signed Android **release** build tested on the Mi 15. |

*The first **installable APK lands at Milestone A** (right after the offline foundation), deliberately early so you have something on the Mi 15 fast; chat starts thin there and is fleshed out in phase 7. Each later phase rebuilds the APK behind the same Settings download button.*

---

## 11. Testing strategy (target 80%)

The donor ships with ~0 tests — all coverage is written fresh.
- **Unit:** sync engine reconciliation (LWW outcomes, tombstone propagation, idempotent replay, conflict-log writes); outbox ordering; WS frame parser (every `type`); reachability gating.
- **Integration:** repository ↔ Drift DB round-trips; offline-edit → reconnect → server-state assertions (against a test gateway or mocked API); auth state machine + `on401→logout`.
- **Widget:** chat bubble / tool-call card / approval dialog; task & expense forms; offline banner states.
- **E2E (device):** manual Mi 15 pass — airplane-mode edit → reconnect sync; notification deep-link; HyperOS background-sync behavior.
- **Backend:** tests for the new `updated_at` bumps, client-id upsert, tombstones, and `?since=` endpoints (Python, in the lazyclaw suite).

---

## 12. Risks & open questions

- **Offline sync is the hardest part of the app.** It gets its own foundation (phases 1–3) and the bulk of the unit tests. Conflict edge-cases (clock skew between phone and the self-hosted server) are mitigated by trusting the **server `updated_at`** as authoritative and keeping a conflict log.
- **HyperOS background limits** may make background catch-up unreliable; foreground/reconnect sync is the guaranteed path. Documented + onboarding prompt.
- **No FCM in v1** → background-task completion only notifies while the app/WS is open or on the next poll. Acceptable for v1; FCM is phase 2.
- **Memory duality** deferred by making mobile memory read-only from `notes`. Offline memory *writes* are out of scope until the `personal_memory`↔`notes` story is unified server-side.
- **Open question:** confirm `lazyclaw/mobile/` as the app location (vs a separate repo). Default assumed.
- **Open question:** chat phase ordering — keep at phase 7 (infra-first) or pull earlier?

---

## 13. Out of scope (explicit)

Univer Sheets/Docs editing, full PDF editing, LazyBrain graph viz, ReactFlow template canvas, browser live-canvas/VNC, FCM/APNs, iOS release, client-side transport crypto, offline memory writes.

---

## 14. APK distribution & Android controls

The app is **sideloaded** (not via Play Store), so distribution is self-hosted by LazyClaw itself.

### 14.1 Backend serve
- A `flutter build apk --release` artifact (`app-release.apk`) is published to a known location the gateway can serve (e.g. `lazyclaw/mobile/dist/app-release.apk`, gitignored).
- New routes:
  - `GET /api/mobile/apk` → streams the latest APK (`Content-Type: application/vnd.android.package-archive`, `Content-Disposition: attachment`).
  - `GET /api/mobile/version` → `{version, build, sha256, built_at, min_android}` so the web panel and the app can show "what's new" and detect updates.
- Auth: served to the logged-in user (session cookie); for first-install convenience the download link may carry a short-lived signed token so the phone (not yet logged in) can fetch it via the QR.

### 14.2 Web Settings → "Mobile App" panel
A new sub-tab in `web/src/pages/Settings.tsx`:
- **Download APK** button (hits `/api/mobile/apk`) + version/build/size/sha256.
- **QR code** of the download URL — scan from the Mi 15 to download directly to the phone (nicest sideload UX). *(QR generated client-side; no new heavy dep — a tiny QR lib or a data-URL generator.)*
- **Android controls** help card: step-by-step for HyperOS — enable **Install unknown apps** for the browser, then **Autostart** + battery **No restrictions** for the LazyClaw app (so background sync/notifications survive). Deep-link hints where possible.

### 14.3 In-app Android controls (app side, server-backed)
On the phone, a **Settings → Android** screen exposes app-behavior toggles, persisted server-side (in `users.settings`) so they round-trip with the web UI:
- Background-sync interval (Off / 15m / 30m / 1h).
- Notifications on/off (per kind: chat reply, background-task done, approvals).
- Offline cache controls (size/age, "clear local cache", "wipe on logout").
- First-launch **HyperOS onboarding** that walks the user into Autostart + battery settings (intent deep-links: `Settings.ACTION_APPLICATION_DETAILS_SETTINGS` and the MIUI autostart/power activities where available).

### 14.4 Update flow
On launch (and on pull-to-refresh), the app compares its build number against `/api/mobile/version`; if the server has a newer APK it shows a non-blocking "Update available — download" prompt that opens the same `/api/mobile/apk` URL. (No silent auto-update — Android sideload requires user confirmation per install.)

---

## 15. Build toolchain & prerequisites

Verified on this Mac (2026-06-04): **Flutter 3.41.6 stable** and Dart are installed; the donor `taskbot_flutter` has **already produced `app-release.apk`/`app-debug.apk` here** and ships a release keystore (`android/lazytasker-release.keystore` + `key.properties`) — so Android APK builds are proven-possible on this machine.

**Currently broken in the shell (one-time fix, phase 0):**
- **No JDK on PATH** (`java -version` fails) — Gradle needs JDK 17+. Use Android Studio's bundled JDK (`flutter config --jdk-dir <studio-jbr>`) or install Temurin 17.
- **`ANDROID_HOME` unset** and **`cmdline-tools` missing** — SDK exists at `~/Library/Android/sdk` (build-tools, platform-tools, ndk, platforms present). Export `ANDROID_HOME`, install `cmdline-tools;latest`, run `flutter doctor --android-licenses`.
- **`adb` not on PATH** — add `$ANDROID_HOME/platform-tools` (needed for on-device install/debug to the Mi 15).

**Signing:** a debug-signed APK installs fine for sideload testing. For the "release" APK in phase 9, generate a LazyClaw keystore (mirror the donor's `key.properties` pattern; do **not** reuse the donor's keystore). Keystore stays out of git.

**Phone side:** enable **Install unknown apps** on the Mi 15; for USB install, enable Developer Options → USB debugging.
