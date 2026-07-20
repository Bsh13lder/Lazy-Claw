# Sync Integrity Layer — Design

**Date:** 2026-07-20
**Status:** Approved (design), pending implementation plan
**Author:** session 2026-07-20

---

## 1. Problem

The same class of bug has been fixed reactively many times: data that is written correctly on the server (expenses, tasks, budget top-ups, notes) intermittently **does not become visible** on a client, or a client keeps a **stale** copy. Recent instances:

- **2026-07-20** — budget top-ups invisible on mobile. A *shared* sync cursor advanced past pre-migration rows, so the delta pull returned `0` budget_entries forever while the server held all 12 top-ups (`+€20,580`). Patched once by clearing the cursor in DB migration v10.
- **2026-07-08 → 15** — Reserva expenses double-adding + delete-freeze (7 phases of sync patches).
- **2026-07-02** — "nothing syncs" self-heal.
- Earlier — tombstones not propagating, `PATCH null vs absent` unable to clear a field, metadata-shape mismatch = phantom data loss, test mocks that masked the real production bug.

### Structural root cause

There are **five independent, hand-rolled sync engines** — one per domain — each re-implementing the same push/pull/cursor/last-write-wins protocol with subtle differences and **no shared contract**:

| Domain | Engine | Backend `/changes` | Cursor key |
|---|---|---|---|
| Tasks | `mobile/lib/sync/task_sync.dart` | `lazyclaw/tasks/store.py:1475 get_task_changes` | `'task'` |
| Budgets (projects + expenses + budget_entries) | `mobile/lib/sync/budgets_sync.dart` | `lazyclaw/budgets/store.py:888 get_budget_changes` | **`'budgets'` — shared by 3 entities** |
| Notes | `mobile/lib/sync/note_sync.dart` | `lazyclaw/lazybrain/store.py:1141 get_note_changes` | `'note'` |
| Documents (sheet/doc/pdf) | `mobile/lib/sync/document_sync.dart` | per-kind `/api/<kind>/changes` | `'document:<kind>'` (correct — per entity) |

Every past bug lived in the *gaps between* these engines. Two are still live in the code:

- **Shared cursor (mode a).** `budgets_dao.dart:22-33` keys projects + expenses + budget_entries under one `'budgets'` cursor. v10 (`app_db.dart:332-349`) is a one-time data patch, not a structural guarantee — the next entity folded into any shared cursor silently reintroduces the bug.
- **Cross-format timestamp comparison (mode f) — currently unfixed in tasks & budgets.** The client mints `updated_at` as `...Z` (`task_dao.dart:91`, `budgets_dao.dart:115`, `note_dao.dart:103`); the server stamps `...+00:00` (tasks/budgets) or a space-separated no-tz string (notes). The mobile last-write-wins compare `_gte` (`task_sync.dart:732-738`) is a **lexical string compare**. For the same instant, server `.500000+00:00` vs local `.500Z`: after `.500`, `'0'`(0x30) < `'Z'`(0x5A), so the server value compares *less* → `serverWins=false` → **the phone rejects the server's change and keeps the stale local copy**, with no network error. Notes works around this with a `since` normalization shim (`lazybrain/store.py:1174`); tasks & budgets have no such protection.

Additional latent modes found in the code: tombstone-column asymmetry (budgets filters tombstones by `deleted_at` and does **not** bump `updated_at` on delete, `budgets/store.py:683,879,1201`; tasks/notes bump and filter by `updated_at`), cursor advancing past an LWW-*skipped* row, fallback-cursor strict-`>` off-by-one, and multi-isolate DB lock contention degrading to an empty in-memory DB.

### The tests didn't catch any of this

Each domain tests its own `/changes` in isolation. There is **no cross-domain invariant suite**, no shared-cursor-hazard test, no timestamp-format contract test, no full round-trip test across the client↔server format boundary, and (historically) fake transports that did not throw the production exception shape — so tests green-lit data-loss bugs.

---

## 2. Goals & non-goals

**Goals**
- **G1 — Self-healing:** a client that drifts from the server (for *any* reason, including bugs we have not found) detects it and heals automatically.
- **G2 — Root-cause fixes:** eliminate the two live structural bugs (cross-format timestamp compare; unsafe shared-cursor widening).
- **G3 — Anti-regression:** one shared invariant suite that every domain must pass, on both mobile and backend, so no future change — human or agent — can reintroduce this bug class and still go green.

**Non-goals (YAGNI)**
- **N1** — Rewriting the five engines into one generic engine (approach B). Deferred; safe to do *later* once G3's harness exists to protect it.
- **N2** — Changing the encryption model, the `enc:v1` storage format, or the transport/auth.
- **N3** — Real-time push sync. This stays pull/poll based.

**Success criteria**
- Injecting a stranded/stale row on either side is healed within one reconcile cycle (verified by test + device).
- The two live bugs have failing tests that pass after the fix.
- The conformance suite runs against tasks, budgets (all 3 entities), notes, and documents from a single spec; adding a new domain requires only an adapter.

---

## 3. Design

Three parts. Parts 1 and 3 are new; Part 2 is targeted fixes to existing code.

### Part 1 — Reconciliation digest (self-healing, G1)

**Backend — one new endpoint** `GET /api/sync/digest`:

```jsonc
{
  "entities": {
    "task":            { "count": 42, "checksum": "a1b2c3d4e5f60718" },
    "project":         { "count": 7,  "checksum": "..." },
    "expense":         { "count": 210,"checksum": "..." },
    "budget_entry":    { "count": 12, "checksum": "..." },
    "note":            { "count": 88, "checksum": "..." },
    "document:sheet":  { "count": 3,  "checksum": "..." }
    // ...doc:doc, doc:pdf
  },
  "now": "2026-07-20T18:30:00.000000+00:00"
}
```

- `count` = live (non-deleted) rows for `user_id`.
- `checksum` = **order-independent fold**: `reduce(xor, int(sha256(f"{id}|{updated_at}").hexdigest()[:16], 16) for row)` → 64-bit hex. Order-independent (no sort needed), detects missing rows, extra rows, and stale content (changed `updated_at`).
- Computed per entity from its table with the *same* live-row predicate the domain's `/changes` uses. One query per entity: `SELECT id, updated_at FROM <table> WHERE user_id=? AND deleted_at IS NULL`.
- Personal-scale tables (hundreds of rows) → negligible cost. If any table grows large later, the fold is still O(rows) with no sort; can be cached/materialized then (out of scope now).

**Mobile — `ReconciliationService`** (`mobile/lib/sync/reconciliation.dart`):

- Runs **after a clean drain** (push complete, pull complete, outbox empty for the entity), throttled: on app-resume and at most once per `kReconcileMinInterval` (default 5 min).
- Computes the **local** digest over **non-dirty** rows only (`dirty = 0`) using the identical fold, so pending local writes never cause a false positive.
- Compares local vs server digest per entity:
  - `count` and `checksum` match → healthy, do nothing.
  - Mismatch (either field) → **clear that entity's cursor** in `sync_state`, so the next pull runs `since=null` (full snapshot). The existing LWW + tombstone merge reconciles: server adds appear, missed tombstones are applied (a full pull returns all live rows *and* all tombstones). No local wipe; outbox untouched → un-pushed offline writes still push.
  - For a **shared** cursor (budgets), clearing it re-pulls all three entities — correct and cheap.
- Reconciliation is **read-mostly and idempotent**: worst case it triggers an unnecessary full re-pull, which the merge absorbs safely.

**Why this is the load-bearing guarantee:** it is bug-agnostic. Any drift — a cursor bug, a format bug, a dropped tombstone, a future bug we have not imagined — surfaces as a digest mismatch and is healed by a full re-pull. Parts 2 and 3 reduce how often we drift; Part 1 guarantees we recover when we do.

### Part 2 — Root-cause fixes (G2)

**Fix 2a — Timestamps compared as instants, not strings.**
- New shared util `mobile/lib/sync/sync_time.dart`:
  - `int compareInstants(String a, String b)` — parse both with `DateTime.parse(...).toUtc()`, compare; fall back to lexical only if a parse fails (logged).
  - `String canonicalNow()` / `String canonicalize(DateTime)` — one canonical UTC ISO-8601 microsecond format, `...+00:00` (matches server `isoformat()`), used for all client minting.
- Replace `_gte` in `task_sync.dart`, `budgets_sync.dart`, `note_sync.dart` with `compareInstants`. Replace `Z`-minting in the DAOs with `canonicalNow()`.
- **Backend authority:** the server **re-stamps `updated_at` on every write** in canonical format, so all stored timestamps are uniform and the `/changes` `since` filter compares same-format against same-format. Where a write path currently trusts a client-supplied `updated_at`, change it to re-stamp. Generalize the notes `since`-normalization defensively to tasks/budgets so a stray format can never silently over/under-include.

**Fix 2b — Shared-cursor safety guard.**
- Declare a `cursor → entities` manifest (single source of truth, e.g. `mobile/lib/sync/cursor_manifest.dart`): `'budgets' → {project, expense, budget_entry}`, `'task' → {task}`, `'note' → {note}`, `'document:<kind>' → {document:<kind>}`.
- Boot check: persist the last-seen entity set per cursor; if a cursor's covered set has **grown** since last run, force a one-time full re-pull (clear that cursor). This turns the v10 one-time patch into a permanent, automatic rule — no future migration needed when an entity is folded in.
- Part 1's digest already heals this at runtime; 2b prevents the drift window entirely at the schema level.

*(Splitting the budgets shared cursor into three per-entity cursors is explicitly NOT done now — it requires the backend `/api/budgets/changes` to accept per-entity `since`. The manifest guard + digest make it unnecessary. Deferred.)*

### Part 3 — Conformance harness (anti-regression, G3)

**One** shared, parametrized invariant spec — single source of truth — run against **every** domain, on **both** mobile (Dart) and backend (Python). A domain plugs in via a small **adapter** exposing: table/collection name, entity name, cursor key, and `create/update/softDelete/getChanges/getDigest` hooks. Adding a domain = write the adapter; it inherits the full suite.

Invariants:

1. **Cursor isolation** — create entity X, advance the cursor by pulling entity Y, pull again → X is still delivered, never stranded. (Encodes the 2026-07-20 shared-cursor bug.)
2. **Timestamp order == chronological order** — property test feeding same-instant `...Z` and `...+00:00` strings to `compareInstants` and to the backend `since` filter; both must agree with real `DateTime` ordering. (Encodes modes f, i.)
3. **Tombstone contract** — soft-deleting a row (a) surfaces it in `/changes` after `since`, and (b) returns it in a full (`since=null`) pull; asserted against whichever column the domain filters on. (Encodes modes c, d, g.)
4. **Round-trip, zero data loss** — mint (client id) → push → server re-stamp → pull → merge, under: (a) a same-second concurrent edit on both sides, (b) a mid-pull failure (cursor must be *held*, not advanced), (c) a row that predates the cursor. Assert no row is lost or left stale.
5. **Digest self-heal** — after a clean drain, local digest == server digest for every entity; then inject a stranded/stale row → digest mismatch → reconcile → full re-pull → digest matches again. (Tests the safety net itself.)

**Test-harness discipline (paid-for lessons):**
- Fake transports throw the **production exception shape** (`DioException(error: ApiError)`), never a bare `Exception` — otherwise data-loss paths test green.
- Backend tests run against an **isolated** temp DB, never the live `./data` DB.
- The suite is the gate a fan-out agent runs before merging any sync change; a new domain that skips the adapter fails an explicit "every registered syncable domain has a conformance adapter" meta-test.

---

## 4. Data flow

**Steady state (per sync tick):** `foreground_sync`/`background_sync` → per-domain engine `_drainOnce()` → `push()` (outbox in `seq` order) → `pull()` (`GET /<domain>/changes?since=<cursor>` → LWW-merge + apply tombstones → advance cursor to server `now`).

**Reconciliation tick (throttled, after clean drain):** `ReconciliationService` → `GET /api/sync/digest` → compute local digest over non-dirty rows → per-entity compare → on mismatch clear that entity's cursor → next `pull()` runs `since=null` → merge heals.

**Timestamp authority:** client mints `canonicalNow()` for optimistic local writes; on push the server re-stamps `updated_at` canonically and returns it; the client stores the server value on pull. All compares (`compareInstants`, backend `since`) operate on the uniform canonical format.

---

## 5. Error handling

- **Digest endpoint failure / offline** → reconciliation is skipped this tick; normal sync unaffected; retried next throttle window.
- **Digest false-positive risk** (pending local writes) → eliminated by digesting only non-dirty rows and only when the entity's outbox is empty.
- **Full re-pull after mismatch** → uses the existing, battle-tested LWW + tombstone merge; a dirty local row strictly newer than server is preserved and re-pushed (unchanged behavior).
- **Mid-pull failure** → cursor held (existing M3 invariant, `budgets_sync.dart:630-646`); reconciliation re-checks next window.
- **Timestamp parse failure** → `compareInstants` logs and falls back to lexical compare so a malformed value cannot crash the merge.
- **Cursor-manifest growth false trigger** → worst case an extra full re-pull, absorbed by merge.

---

## 6. Testing

- Part 3 *is* the primary test deliverable (the conformance suite + adapters for tasks, budgets×3, notes, documents).
- Plus targeted regression tests for the two live bugs (2a, 2b) written **first** (RED) so they fail on current code and pass after the fix.
- Backend digest endpoint: unit tests for count/checksum correctness, order-independence, and `deleted_at` exclusion.
- Mobile `ReconciliationService`: unit tests for match/mismatch/dirty-exclusion/shared-cursor-clear, using production-shaped fake transports.
- Device verification: reproduce a stranded row (e.g. a manually pre-advanced cursor) and confirm auto-heal, mirroring the 2026-07-20 verification method.

---

## 7. Rollout / phases

1. **Phase 0 — RED tests** for the two live bugs (timestamp compare, shared-cursor widening).
2. **Phase 1 — Part 2 fixes** (`sync_time.dart`, DAO mint change, server re-stamp, cursor manifest guard) → Phase 0 tests green.
3. **Phase 2 — Part 1** (backend `/api/sync/digest` + mobile `ReconciliationService` + wiring into foreground/background sync).
4. **Phase 3 — Part 3** (conformance spec + per-domain adapters, mobile + backend).
5. **Phase 4 — device verification + release** (mobile version bump, APK, memory update).

Each phase is independently shippable and leaves the app in a working state.

---

## 8. Assumptions & open questions

- **A1** — "Both accs" = web + mobile clients of one account. Design is user-scoped; unchanged if it is two separate accounts.
- **A2** — Server currently stamps `updated_at` on write in `isoformat()`. To confirm during Phase 1: whether any write path trusts a client-supplied `updated_at` (would need to switch to server re-stamp).
- **A3** — Digest cost is negligible at personal scale; revisit materialization only if a table grows to many thousands of rows.
- **OQ1** — Reconciliation trigger cadence: app-resume + 5-min throttle is the default; tune after device testing.
- **OQ2** — Whether to include the Documents domain in the digest from day one (it already uses correct per-kind cursors). Default: yes, for uniformity and cheap coverage.
