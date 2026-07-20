# Sync Integrity Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the recurring class of "data written on the server but invisible/stale on a client" bugs by (1) fixing the one live root cause, (2) adding a self-healing reconciliation digest, and (3) locking both in behind a shared conformance test suite.

**Architecture:** Three layers. **Fix** — replace the mobile lexical timestamp compare with an instant-parsing compare (kills the same-second "phone keeps the stale copy" bug). **Heal** — a `GET /api/sync/digest` endpoint (`count + checksum` per entity) plus a mobile `ReconciliationService` that, after a clean drain, compares local-vs-server and clears a drifted entity's cursor to force a full re-pull. **Guard** — one parametrized conformance suite (adapters per domain) run on both backend (pytest) and mobile (Dart) so no future change can reintroduce this class.

**Tech Stack:** Backend — Python 3.11+, FastAPI, aiosqlite (`db_session` pool), pytest (`pytest.mark.asyncio`). Mobile — Flutter/Dart, sqflite (+ `sqflite_common_ffi` for tests), Riverpod, Dio.

## Global Constraints

- **User isolation:** every backend query is scoped `WHERE user_id = ?` with bound params — never string-interpolate user data. (Column-name SELECT lists may be f-string'd from constants.)
- **Store convention:** backend stores are flat modules of `async def fn(config, user_id, *, ...)`. The DEK is fetched *inside* the store via `key = await get_user_dek(config, user_id)`; the route passes only `_config` (module singleton `load_config()`) and `user.id`. Rows decode **positionally** (`row[i]`), matching a `*_COLUMNS` list.
- **Route convention:** `@router.get(...)`, `user: User = Depends(get_current_user)`, `since: str | None = Query(default=None)`. A static path (`/digest`) must be declared before any `/{param}` route on the same prefix.
- **Server is timestamp-authoritative:** the server re-stamps `updated_at` on every create and update. Clients never persist a client-minted `updated_at` for a clean row — a synced (`dirty=0`) cache row holds the server's exact string.
- **Test DB isolation (CRITICAL):** backend tests construct their own `Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")`, `await init_db(c)`, and `await close_pool()` in `finally`. NEVER run the suite against the live `./data` DB (2026-07-03 outage). Module-level `pytestmark = pytest.mark.asyncio`.
- **Fake transports throw the production shape:** Dart fakes raise `DioException(..., error: ApiError(status, msg))` (via `_serverDio`/`_connectionDio`), never a bare `Exception` — otherwise data-loss paths test green.
- **Immutability:** produce new objects; never mutate a shared row/model in place.
- **No AI attribution in commit messages.**
- **Digest scope:** the 5 core offline-first entities — `task`, `project`, `expense`, `budget_entry`, `note` (all in `lazyclaw.db`, all `(id, user_id, updated_at, deleted_at)`). Documents are explicitly deferred (separate per-kind stores, already correct per-kind cursors, never the bug source).

---

## File Structure

**New files**
- `mobile/lib/sync/sync_time.dart` — instant-parsing timestamp compare (`serverWinsByTime`, `parseInstantMicros`). Replaces the three copy-pasted `_gte` helpers.
- `mobile/lib/sync/sync_hash.dart` — FNV-1a-64 fold (`foldDigest`), identical algorithm to the Python side.
- `mobile/lib/sync/digest.dart` — `computeCacheDigest(db, table, {kind})` → `EntityDigest{count, checksum}` over clean (`dirty=0 AND deleted=0`) cache rows.
- `mobile/lib/sync/reconciliation.dart` — `ReconciliationService`: throttled compare of local-vs-server digests; clears drifted cursors; returns the set of cursor-keys to re-pull.
- `mobile/lib/repositories/sync_digest_repository.dart` — `SyncDigestTransport` interface + `DioSyncDigestTransport` (GET `/api/sync/digest`).
- `lazyclaw/sync_integrity/__init__.py`, `lazyclaw/sync_integrity/digest.py` — `fnv1a64`, `fold_digest`, and `compute_user_digest(config, user_id)` (the 5-entity aggregate).
- `lazyclaw/gateway/routes/sync.py` — `router = APIRouter(prefix="/api/sync")` with `GET /digest`.
- `tests/sync_integrity/test_digest.py` — backend digest unit tests.
- `tests/sync_integrity/test_sync_conformance.py` — backend parametrized conformance suite (adapters per entity).
- `tests/sync_integrity/test_digest_route.py` — endpoint test.
- `mobile/test/sync/sync_time_test.dart`, `mobile/test/sync/sync_hash_test.dart`, `mobile/test/sync/reconciliation_test.dart`, `mobile/test/sync/sync_conformance_test.dart` — mobile unit + conformance suites.

**Modified files**
- `mobile/lib/sync/task_sync.dart`, `budgets_sync.dart`, `note_sync.dart` — replace `_gte(...)` call sites with `serverWinsByTime(...)`; delete the three `_gte` methods.
- `mobile/lib/main.dart` — after the foreground drains, run reconciliation and re-drain drifted domains.
- `mobile/lib/sync/background_sync.dart` — same, in the headless path.
- `lazyclaw/gateway/app.py` — `include_router(sync_router)`.
- `mobile/lib/local/app_db.dart` — bump `kAppDbVersion` only if a new column is needed (it is not — no schema change).
- `mobile/pubspec.yaml`, `docs/DOCS.md`, memory files — release + docs (final task).

---

## Phase 0 & 1 — Fix the live timestamp bug

### Task 1: Instant-parsing timestamp compare

**Files:**
- Create: `mobile/lib/sync/sync_time.dart`
- Test: `mobile/test/sync/sync_time_test.dart`

**Interfaces:**
- Produces: `bool serverWinsByTime(String? server, String? local)` — true when the server row wins LWW; and `int? parseInstantMicros(String? s)` — UTC epoch-micros or null.

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/sync/sync_time_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/sync/sync_time.dart';

void main() {
  group('parseInstantMicros', () {
    test('parses Z, +00:00, and space-separated to the SAME instant', () {
      final z = parseInstantMicros('2026-06-05T11:00:00.000Z');
      final off = parseInstantMicros('2026-06-05T11:00:00.000000+00:00');
      final sp = parseInstantMicros('2026-06-05 11:00:00.000000');
      expect(z, isNotNull);
      expect(z, equals(off));
      expect(z, equals(sp));
    });

    test('returns null for empty/garbage', () {
      expect(parseInstantMicros(''), isNull);
      expect(parseInstantMicros(null), isNull);
      expect(parseInstantMicros('not-a-date'), isNull);
    });
  });

  group('serverWinsByTime (replaces lexical _gte)', () {
    test('SAME instant, server=+00:00 vs local=Z → server wins (the live bug)',
        () {
      // Lexical compareTo ranks "...000000+00:00" < "...000Z" (0x30 < 0x5A),
      // so the old _gte returned false and the phone kept the stale local row.
      const server = '2026-06-05T11:00:00.000000+00:00';
      const local = '2026-06-05T11:00:00.000Z';
      expect(serverWinsByTime(server, local), isTrue);
    });

    test('server strictly newer → server wins', () {
      expect(
        serverWinsByTime('2026-06-05T12:00:00Z', '2026-06-05T11:00:00Z'),
        isTrue,
      );
    });

    test('local strictly newer → local wins', () {
      expect(
        serverWinsByTime('2026-06-05T11:00:00Z', '2026-06-05T12:00:00Z'),
        isFalse,
      );
    });

    test('empty server never wins; empty local always loses', () {
      expect(serverWinsByTime('', '2026-06-05T11:00:00Z'), isFalse);
      expect(serverWinsByTime('2026-06-05T11:00:00Z', ''), isTrue);
    });

    test('unparseable either side → lexical fallback (no crash)', () {
      expect(serverWinsByTime('zzz', 'aaa'), isTrue); // 'zzz' >= 'aaa'
    });
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/sync/sync_time_test.dart`
Expected: FAIL — `Error: Couldn't resolve the package 'sync_time.dart'` / undefined `serverWinsByTime`.

- [ ] **Step 3: Write minimal implementation**

```dart
// mobile/lib/sync/sync_time.dart

/// Parse an ISO-8601 timestamp to UTC microseconds-since-epoch.
///
/// Accepts every shape that crosses the sync boundary: a `T` or space
/// separator, and a `Z` or `+00:00` (or absent) UTC offset. Returns null when
/// the string is empty or unparseable.
int? parseInstantMicros(String? s) {
  if (s == null || s.isEmpty) return null;
  final dt = DateTime.tryParse(s);
  return dt?.toUtc().microsecondsSinceEpoch;
}

/// True when the SERVER row should win last-write-wins over the LOCAL row.
///
/// Preserves the exact contract of the old `_gte(server, local)` helpers:
///  - an empty server time never wins;
///  - an empty local time always loses to the server;
///  - equal instants → server wins (tie-break toward server authority).
///
/// The one behavioral change: it compares PARSED INSTANTS, not raw strings, so
/// a same-instant `...Z` (client-minted) vs `...+00:00` (server-stamped) pair
/// no longer mis-ranks and strands the server's edit. Falls back to the legacy
/// lexical compare only if a value fails to parse, so a malformed timestamp
/// can never crash the merge.
bool serverWinsByTime(String? server, String? local) {
  final s = server ?? '';
  final l = local ?? '';
  if (s.isEmpty) return false;
  if (l.isEmpty) return true;
  final si = parseInstantMicros(s);
  final li = parseInstantMicros(l);
  if (si == null || li == null) {
    return s.compareTo(l) >= 0;
  }
  return si >= li;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && flutter test test/sync/sync_time_test.dart`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/sync/sync_time.dart mobile/test/sync/sync_time_test.dart
git commit -m "fix(mobile,sync): compare updated_at as instants, not lexical strings"
```

---

### Task 2: Adopt `serverWinsByTime` in all three engines

**Files:**
- Modify: `mobile/lib/sync/task_sync.dart` (call site `:608`; delete `_gte` `:730-738`)
- Modify: `mobile/lib/sync/budgets_sync.dart` (call sites `:680`, `:787`, `:881`; delete `_gte` `:1097-1105`)
- Modify: `mobile/lib/sync/note_sync.dart` (call site `:492`; delete `_gte` `:626-634`)
- Test: `mobile/test/sync/budgets_sync_test.dart` (add one engine-level regression test)

**Interfaces:**
- Consumes: `serverWinsByTime` from `sync_time.dart` (Task 1).

- [ ] **Step 1: Write the failing engine-level test**

Append inside the existing `main()` of `mobile/test/sync/budgets_sync_test.dart` (it already has `sqfliteFfiInit`, `_freshDao`, `_FakeTransport`, `_serverProjectJson`):

```dart
  test(
      'REGRESSION: same-second server(+00:00) beats dirty local(Z) — server wins',
      () async {
    // Dirty local row minted with a Z-suffixed millisecond timestamp.
    final dao = await _freshDao(now: () => '2026-06-05T11:00:00.000Z');
    await dao.applyLocalProjectCreate('Local name', id: 'ts1');

    // Server change at the SAME instant, stamped +00:00 with microseconds.
    final transport = _FakeTransport(changesResponse: {
      'projects': [
        _serverProjectJson(
            id: 'ts1',
            name: 'Server name',
            updatedAt: '2026-06-05T11:00:00.000000+00:00')
      ],
      'expenses': [],
      'deleted_projects': [],
      'deleted_expenses': [],
      'now': '2026-06-05T12:00:00Z',
    });

    await BudgetsSync(dao, BudgetsRepository(transport)).pull();

    // With the old lexical _gte this stayed 'Local name' (server stranded).
    expect((await dao.getProject('ts1'))!.name, 'Server name');
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/sync/budgets_sync_test.dart --plain-name "same-second server"`
Expected: FAIL — project name is `'Local name'` (old lexical `_gte` ranked local as newer).

- [ ] **Step 3: Replace the call sites and delete `_gte`**

In `mobile/lib/sync/budgets_sync.dart`, add the import near the other `import 'package:lazyclaw_mobile/...';` lines:

```dart
import 'package:lazyclaw_mobile/sync/sync_time.dart';
```

Replace each of the three occurrences (`:680`, `:787`, `:881`) of:

```dart
    final serverWins = _gte(serverUpdatedAt, localUpdatedAt);
```

with:

```dart
    final serverWins = serverWinsByTime(serverUpdatedAt, localUpdatedAt);
```

Delete the now-unused `_gte` method (`:1097-1105`):

```dart
  /// True when [a] >= [b] as ISO-8601 strings (lexicographic == chronological
  /// for zero-padded ISO timestamps). Empty server time loses to any local time.
  static bool _gte(String? a, String? b) {
    final av = a ?? '';
    final bv = b ?? '';
    if (av.isEmpty) return false;
    if (bv.isEmpty) return true;
    return av.compareTo(bv) >= 0;
  }
```

Do the identical edit in `mobile/lib/sync/task_sync.dart` — add the import, change `:608` `_gte(...)` → `serverWinsByTime(...)`, delete `_gte` (`:730-738`).

Do the identical edit in `mobile/lib/sync/note_sync.dart` — add the import, change `:492` `_gte(...)` → `serverWinsByTime(...)`, delete `_gte` (`:626-634`).

- [ ] **Step 4: Run the full sync test suite**

Run: `cd mobile && flutter test test/sync/`
Expected: PASS — the new regression test passes; every existing LWW test still passes (parsed compare is a superset of the old lexical order for same-format timestamps).

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/sync/task_sync.dart mobile/lib/sync/budgets_sync.dart mobile/lib/sync/note_sync.dart mobile/test/sync/budgets_sync_test.dart
git commit -m "fix(mobile,sync): route all LWW compares through serverWinsByTime"
```

---

## Phase 2 — Reconciliation digest: backend

### Task 3: Digest fold + per-user compute (backend)

**Files:**
- Create: `lazyclaw/sync_integrity/__init__.py` (empty)
- Create: `lazyclaw/sync_integrity/digest.py`
- Test: `tests/sync_integrity/__init__.py` (empty), `tests/sync_integrity/test_digest.py`

**Interfaces:**
- Produces:
  - `fnv1a64(text: str) -> int` — 64-bit FNV-1a over UTF-8.
  - `fold_digest(pairs: Iterable[tuple[str, str]]) -> str` — order-independent 16-hex fold of `(id, updated_at)` pairs.
  - `DIGEST_TABLES: dict[str, str]` — entity → table name.
  - `async compute_user_digest(config, user_id) -> dict` — `{"entities": {entity: {"count": int, "checksum": str}}, "now": str}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/sync_integrity/test_digest.py
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.budgets import store as budget_store
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.sync_integrity.digest import (
    DIGEST_TABLES,
    compute_user_digest,
    fnv1a64,
    fold_digest,
)

pytestmark = pytest.mark.asyncio


def test_fnv1a64_is_stable_and_nonzero():
    assert fnv1a64("id-1|2026-06-05T10:00:00+00:00") == fnv1a64(
        "id-1|2026-06-05T10:00:00+00:00"
    )
    assert fnv1a64("a") != fnv1a64("b")


def test_fold_is_order_independent_and_16_hex():
    pairs_a = [("a", "t1"), ("b", "t2"), ("c", "t3")]
    pairs_b = list(reversed(pairs_a))
    d = fold_digest(pairs_a)
    assert d == fold_digest(pairs_b)
    assert len(d) == 16
    assert int(d, 16) >= 0


def test_empty_fold_is_zero():
    assert fold_digest([]) == "0000000000000000"


def test_digest_tables_cover_the_five_core_entities():
    assert set(DIGEST_TABLES) == {
        "task",
        "project",
        "expense",
        "budget_entry",
        "note",
    }


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "testuser", "x", "salt-test"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


async def test_compute_user_digest_counts_live_rows_only(cfg):
    project = await budget_store.create_project(cfg, "u1", "P", budget=100.0)
    e1 = await budget_store.add_budget_entry(cfg, "u1", project["id"], amount=10.0)
    await budget_store.add_budget_entry(cfg, "u1", project["id"], amount=20.0)
    await budget_store.delete_budget_entry(cfg, "u1", e1["id"])  # soft-delete

    digest = await compute_user_digest(cfg, "u1")
    assert digest["entities"]["project"]["count"] == 1
    # one live top-up remains (the other was soft-deleted)
    assert digest["entities"]["budget_entry"]["count"] == 1
    assert "now" in digest
    # checksum is a 16-hex string per entity
    assert len(digest["entities"]["project"]["checksum"]) == 16


async def test_digest_changes_when_a_row_is_added(cfg):
    project = await budget_store.create_project(cfg, "u1", "P", budget=100.0)
    before = await compute_user_digest(cfg, "u1")
    await budget_store.add_budget_entry(cfg, "u1", project["id"], amount=10.0)
    after = await compute_user_digest(cfg, "u1")
    assert (
        before["entities"]["budget_entry"]["checksum"]
        != after["entities"]["budget_entry"]["checksum"]
    )
    assert (
        before["entities"]["budget_entry"]["count"]
        != after["entities"]["budget_entry"]["count"]
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/sync_integrity/test_digest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'lazyclaw.sync_integrity'`.

- [ ] **Step 3: Write minimal implementation**

```python
# lazyclaw/sync_integrity/__init__.py
```

```python
# lazyclaw/sync_integrity/digest.py
"""Per-user sync digest — a cheap count + content checksum per syncable entity.

A client compares this against its own local digest (over CLEAN cache rows) to
detect drift from any cause — a stranded cursor, a dropped tombstone, a bug we
have not found yet — and self-heal by forcing a full re-pull of the drifted
entity. The checksum is an order-independent FNV-1a-64 fold of each live row's
``(id, updated_at)`` so it moves when a row is added, removed, or re-stamped.

The Dart side (`mobile/lib/sync/sync_hash.dart`) implements the SAME FNV-1a-64
so the two checksums are directly comparable. A clean client cache row holds the
server's exact ``updated_at`` string, so hashing the raw string on both sides
matches by construction; a false mismatch only ever triggers a harmless
re-pull.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3
_MASK64 = 0xFFFFFFFFFFFFFFFF

# entity name -> table. All five live in lazyclaw.db and share the
# (id, user_id, updated_at, deleted_at) shape, so one generic query serves all.
DIGEST_TABLES: dict[str, str] = {
    "task": "tasks",
    "project": "projects",
    "expense": "project_expenses",
    "budget_entry": "budget_entries",
    "note": "notes",
}


def fnv1a64(text: str) -> int:
    h = _FNV_OFFSET
    for b in text.encode("utf-8"):
        h ^= b
        h = (h * _FNV_PRIME) & _MASK64
    return h


def fold_digest(pairs: Iterable[tuple[str, str]]) -> str:
    acc = 0
    for row_id, updated_at in pairs:
        acc ^= fnv1a64(f"{row_id}|{updated_at}")
    return format(acc & _MASK64, "016x")


async def _entity_digest(config: Config, user_id: str, table: str) -> dict:
    async with db_session(config) as db:
        cur = await db.execute(
            f"SELECT id, updated_at FROM {table} "
            "WHERE user_id = ? AND deleted_at IS NULL",
            (user_id,),
        )
        rows = await cur.fetchall()
    pairs = [(str(r[0]), str(r[1]) if r[1] is not None else "") for r in rows]
    return {"count": len(pairs), "checksum": fold_digest(pairs)}


async def compute_user_digest(config: Config, user_id: str) -> dict:
    entities: dict[str, dict] = {}
    for entity, table in DIGEST_TABLES.items():
        entities[entity] = await _entity_digest(config, user_id, table)
    return {
        "entities": entities,
        "now": datetime.now(timezone.utc).isoformat(),
    }
```

Note: `table` is interpolated from the hardcoded `DIGEST_TABLES` values only (never user input), matching the store's `f"SELECT {COLS} FROM ..."` convention. `user_id` is always bound.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/sync_integrity/test_digest.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/sync_integrity/ tests/sync_integrity/__init__.py tests/sync_integrity/test_digest.py
git commit -m "feat(sync): per-user digest (count + FNV-1a-64 checksum) for 5 core entities"
```

---

### Task 4: `GET /api/sync/digest` endpoint

**Files:**
- Create: `lazyclaw/gateway/routes/sync.py`
- Modify: `lazyclaw/gateway/app.py` (import + `include_router`, near the other route registrations ~`:43`/`:246`)
- Test: `tests/sync_integrity/test_digest_route.py`

**Interfaces:**
- Consumes: `compute_user_digest` (Task 3); `get_current_user`, `User` (`lazyclaw/gateway/auth.py`); `load_config`.
- Produces: `GET /api/sync/digest` → `{"entities": {...}, "now": "..."}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/sync_integrity/test_digest_route.py
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.budgets import store as budget_store
from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    await create_user_dek(c, "u1", "salt-a")

    import lazyclaw.gateway.routes.sync as sync_routes
    monkeypatch.setattr(sync_routes, "_config", c)

    app = FastAPI()
    app.include_router(sync_routes.router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    try:
        yield (TestClient(app), c)
    finally:
        await close_pool()


async def test_digest_route_returns_all_five_entities(client):
    tc, cfg = client
    await budget_store.create_project(cfg, "u1", "P", budget=100.0)
    resp = tc.get("/api/sync/digest")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["entities"]) == {
        "task", "project", "expense", "budget_entry", "note",
    }
    assert body["entities"]["project"]["count"] == 1
    assert "now" in body


async def test_digest_route_requires_auth(tmp_path, monkeypatch):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(c)
    import lazyclaw.gateway.routes.sync as sync_routes
    monkeypatch.setattr(sync_routes, "_config", c)
    app = FastAPI()
    app.include_router(sync_routes.router)
    try:
        resp = TestClient(app).get("/api/sync/digest")
        assert resp.status_code == 401
    finally:
        await close_pool()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/sync_integrity/test_digest_route.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'lazyclaw.gateway.routes.sync'`.

- [ ] **Step 3: Write the route + register it**

```python
# lazyclaw/gateway/routes/sync.py
"""Sync integrity API — the reconciliation digest offline clients poll to
detect drift and self-heal. See ``lazyclaw/sync_integrity/digest.py``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.sync_integrity.digest import compute_user_digest

logger = logging.getLogger(__name__)

_config = load_config()

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.get("/digest")
async def sync_digest_route(user: User = Depends(get_current_user)):
    """Per-user count + checksum for each syncable entity.

    Clients compare this against their local digest (over CLEAN cache rows). Any
    mismatch → force a full re-pull of that entity. Returns::

        {"entities": {"task": {"count": N, "checksum": "..."}, ...}, "now": "..."}
    """
    result = await compute_user_digest(_config, user.id)
    logger.debug(
        "[route:sync] GET digest user=%s -> %s",
        user.id,
        {k: v["count"] for k, v in result["entities"].items()},
    )
    return result
```

In `lazyclaw/gateway/app.py`, next to the other route imports (~`:43`):

```python
from lazyclaw.gateway.routes.sync import router as sync_router
```

and next to the other `include_router` calls (~`:246`):

```python
app.include_router(sync_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/sync_integrity/test_digest_route.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/gateway/routes/sync.py lazyclaw/gateway/app.py tests/sync_integrity/test_digest_route.py
git commit -m "feat(sync): GET /api/sync/digest endpoint"
```

---

## Phase 3 — Reconciliation digest: mobile

### Task 5: Dart fold hash + local cache digest

**Files:**
- Create: `mobile/lib/sync/sync_hash.dart`
- Create: `mobile/lib/sync/digest.dart`
- Test: `mobile/test/sync/sync_hash_test.dart`

**Interfaces:**
- Produces:
  - `String foldDigest(Iterable<List<String>> pairs)` — FNV-1a-64 fold, identical output to Python `fold_digest` for the same `(id, updated_at)` set.
  - `class EntityDigest { final int count; final String checksum; }`.
  - `Future<EntityDigest> computeCacheDigest(DatabaseExecutor db, {required String table, String? kind})` — over `dirty=0 AND deleted=0` rows.

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/sync/sync_hash_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/sync/sync_hash.dart';

void main() {
  test('fold is order-independent and 16 lowercase hex', () {
    final a = foldDigest([
      ['a', 't1'],
      ['b', 't2'],
      ['c', 't3'],
    ]);
    final b = foldDigest([
      ['c', 't3'],
      ['a', 't1'],
      ['b', 't2'],
    ]);
    expect(a, equals(b));
    expect(a.length, 16);
    expect(RegExp(r'^[0-9a-f]{16}$').hasMatch(a), isTrue);
  });

  test('empty fold is all zeros', () {
    expect(foldDigest(const []), '0000000000000000');
  });

  // Golden vector shared with the Python side (tests/sync_integrity/test_digest.py).
  // Both must produce this exact value for {(id-1, t1), (id-2, t2)}.
  test('golden cross-language vector', () {
    final d = foldDigest([
      ['id-1', '2026-06-05T10:00:00+00:00'],
      ['id-2', '2026-06-05T11:00:00+00:00'],
    ]);
    expect(d, equals(kGoldenDigestVector));
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/sync/sync_hash_test.dart`
Expected: FAIL — undefined `foldDigest` / `kGoldenDigestVector`.

- [ ] **Step 3: Write the implementation, then pin the golden vector**

```dart
// mobile/lib/sync/sync_hash.dart
import 'dart:convert';

import 'package:sqflite/sqflite.dart';

/// FNV-1a-64 over UTF-8. Native Dart ints are 64-bit two's-complement and the
/// multiply wraps mod 2^64, matching the Python side's explicit `& MASK64`.
int fnv1a64(String text) {
  const int prime = 0x100000001b3;
  int h = 0xcbf29ce484222325; // FNV offset basis
  for (final b in utf8.encode(text)) {
    h ^= b;
    h = h * prime; // wraps to 64-bit on native
  }
  return h;
}

/// Order-independent fold of `(id, updated_at)` pairs → 16 lowercase hex.
/// Each `pair` is a 2-element list `[id, updatedAt]`.
String foldDigest(Iterable<List<String>> pairs) {
  int acc = 0;
  for (final p in pairs) {
    acc ^= fnv1a64('${p[0]}|${p[1]}');
  }
  return acc.toUnsigned(64).toRadixString(16).padLeft(16, '0');
}

class EntityDigest {
  final int count;
  final String checksum;
  const EntityDigest(this.count, this.checksum);
}

/// Digest over CLEAN cache rows (`dirty=0 AND deleted=0`) — the rows that should
/// mirror the server exactly. Pending local writes (`dirty=1`) are excluded so
/// they never cause a false drift signal. `kind` filters the shared
/// `document_cache` table (unused for the 5 core entities).
Future<EntityDigest> computeCacheDigest(
  DatabaseExecutor db, {
  required String table,
  String? kind,
}) async {
  final where = StringBuffer('dirty = 0 AND deleted = 0');
  final args = <Object?>[];
  if (kind != null) {
    where.write(' AND kind = ?');
    args.add(kind);
  }
  final rows = await db.query(
    table,
    columns: ['id', 'updated_at'],
    where: where.toString(),
    whereArgs: args.isEmpty ? null : args,
  );
  final pairs = rows.map<List<String>>(
    (r) => [r['id'] as String, (r['updated_at'] as String?) ?? ''],
  );
  return EntityDigest(rows.length, foldDigest(pairs));
}
```

Compute the golden vector once (Python is the source of truth), then hardcode it in the Dart lib so the test pins cross-language agreement. Run:

`python -c "from lazyclaw.sync_integrity.digest import fold_digest; print(fold_digest([('id-1','2026-06-05T10:00:00+00:00'),('id-2','2026-06-05T11:00:00+00:00')]))"`

Add the printed value to `sync_hash.dart`:

```dart
/// Golden fold of {(id-1, 2026-06-05T10:00:00+00:00), (id-2, ...T11:...)} —
/// pinned to the Python `fold_digest` output to guard cross-language drift.
const String kGoldenDigestVector = '<paste python output here>';
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && flutter test test/sync/sync_hash_test.dart`
Expected: PASS. If the golden test fails, the Dart/Python folds diverged — fix before proceeding (this is the whole point of the golden vector).

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/sync/sync_hash.dart mobile/lib/sync/digest.dart mobile/test/sync/sync_hash_test.dart
git commit -m "feat(mobile,sync): FNV-1a-64 fold + clean-row cache digest (matches server)"
```

---

### Task 6: `ReconciliationService` + digest transport

**Files:**
- Create: `mobile/lib/repositories/sync_digest_repository.dart`
- Create: `mobile/lib/sync/reconciliation.dart`
- Test: `mobile/test/sync/reconciliation_test.dart`

**Interfaces:**
- Consumes: `computeCacheDigest`, `EntityDigest` (Task 5); `ApiError`, `DioException`.
- Produces:
  - `abstract class SyncDigestTransport { Future<Map<String, dynamic>> fetchDigest(); }` + `DioSyncDigestTransport`.
  - `class ReconciliationService` with `Future<Set<String>> reconcile()` returning the set of cursor-keys cleared (empty = in sync or throttled/offline).
  - `class EntitySpec { final String table; final String cursorKey; }` (public, so the guard test in Task 10 can read `spec.cursorKey`).
  - `const Map<String, EntitySpec> kReconcileEntities` — entity → (table, cursorKey) map for the 5 core entities.

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/sync/reconciliation_test.dart
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/repositories/sync_digest_repository.dart';
import 'package:lazyclaw_mobile/sync/digest.dart';
import 'package:lazyclaw_mobile/sync/reconciliation.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _c = 0;

Future<dynamic> _freshDb() async {
  return databaseFactoryFfi.openDatabase(
    'file:reconmem${_c++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
}

class _FakeDigest implements SyncDigestTransport {
  _FakeDigest(this.body, {this.throwDio = false});
  final Map<String, dynamic> body;
  final bool throwDio;
  int calls = 0;
  @override
  Future<Map<String, dynamic>> fetchDigest() async {
    calls++;
    if (throwDio) {
      throw DioException(
        requestOptions: RequestOptions(path: '/api/sync/digest'),
        type: DioExceptionType.connectionError,
        error: ApiError(0, 'Network error'),
      );
    }
    return body;
  }
}

Map<String, dynamic> _digest(Map<String, List<dynamic>> counts) => {
      'entities': {
        for (final e in [
          'task', 'project', 'expense', 'budget_entry', 'note',
        ])
          e: {
            'count': (counts[e]?[0] ?? 0),
            'checksum': (counts[e]?[1] ?? '0000000000000000'),
          }
      },
      'now': '2026-06-05T12:00:00Z',
    };

void main() {
  setUpAll(() => sqfliteFfiInit());

  test('in sync → clears nothing', () async {
    final db = await _freshDb();
    // local project_cache empty & clean → local checksum for project is all-zero
    final server = _digest({}); // all counts 0, all checksums zero
    final svc = ReconciliationService(
      db,
      _FakeDigest(server),
      now: () => DateTime.parse('2026-06-05T12:00:00Z'),
    );
    final cleared = await svc.reconcile();
    expect(cleared, isEmpty);
  });

  test('server has a project we lack → clears the budgets cursor', () async {
    final db = await _freshDb();
    await db.insert('sync_state', {'entity': 'budgets', 'cursor': 'x'});
    final server = _digest({
      'project': [1, 'deadbeefdeadbeef'],
    });
    final svc = ReconciliationService(
      db,
      _FakeDigest(server),
      now: () => DateTime.parse('2026-06-05T12:00:00Z'),
    );
    final cleared = await svc.reconcile();
    expect(cleared, contains('budgets'));
    final rows = await db.query('sync_state', where: 'entity = ?', whereArgs: ['budgets']);
    expect(rows, isEmpty); // cursor cleared → next pull is full
  });

  test('throttled: a second call within the window is a no-op', () async {
    final db = await _freshDb();
    final fake = _FakeDigest(_digest({'project': [1, 'deadbeefdeadbeef']}));
    var t = DateTime.parse('2026-06-05T12:00:00Z');
    final svc = ReconciliationService(db, fake, now: () => t);
    await svc.reconcile();
    t = t.add(const Duration(minutes: 1)); // inside 5-min window
    final calls0 = fake.calls;
    await svc.reconcile();
    expect(fake.calls, calls0); // no second network call
  });

  test('offline (DioException) → clears nothing, does not throw', () async {
    final db = await _freshDb();
    final svc = ReconciliationService(
      db,
      _FakeDigest(const {}, throwDio: true),
      now: () => DateTime.parse('2026-06-05T12:00:00Z'),
    );
    final cleared = await svc.reconcile();
    expect(cleared, isEmpty);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/sync/reconciliation_test.dart`
Expected: FAIL — undefined `SyncDigestTransport` / `ReconciliationService`.

- [ ] **Step 3: Write the implementations**

```dart
// mobile/lib/repositories/sync_digest_repository.dart
import 'package:lazyclaw_mobile/core/api/api_client.dart';

/// Transport seam for GET /api/sync/digest (mirrors BudgetsTransport's shape so
/// tests inject a fake that throws the production DioException).
abstract class SyncDigestTransport {
  Future<Map<String, dynamic>> fetchDigest();
}

class DioSyncDigestTransport implements SyncDigestTransport {
  DioSyncDigestTransport(this._client);
  final ApiClient _client;

  @override
  Future<Map<String, dynamic>> fetchDigest() =>
      _client.getJson('/api/sync/digest');
}
```

```dart
// mobile/lib/sync/reconciliation.dart
import 'package:flutter/foundation.dart';
import 'package:sqflite/sqflite.dart';

import 'package:lazyclaw_mobile/repositories/sync_digest_repository.dart';
import 'package:lazyclaw_mobile/sync/digest.dart';

/// The 5 core entities: entity name → (cache table, sync_state cursor key).
/// project/expense/budget_entry share the single 'budgets' cursor, so a drift
/// in any of them clears 'budgets' once and the next budgets pull is full.
/// Public so the Task 10 guard test can read `spec.cursorKey`.
class EntitySpec {
  const EntitySpec(this.table, this.cursorKey);
  final String table;
  final String cursorKey;
}

const Map<String, EntitySpec> kReconcileEntities = {
  'task': EntitySpec('task_cache', 'task'),
  'project': EntitySpec('project_cache', 'budgets'),
  'expense': EntitySpec('expense_cache', 'budgets'),
  'budget_entry': EntitySpec('budget_entry_cache', 'budgets'),
  'note': EntitySpec('note_cache', 'note'),
};

/// sync_state key holding the last successful reconcile time (ISO string).
const String _kReconcileAtKey = '__reconcile_at__';
const Duration kReconcileMinInterval = Duration(minutes: 5);

/// Compares local (clean-row) digests against the server's and clears the
/// cursor of any entity that drifted, so the next pull re-fetches it in full.
/// Bug-agnostic: it heals drift from any cause. Read-mostly and idempotent —
/// worst case it triggers an unnecessary (and safe) full re-pull.
class ReconciliationService {
  ReconciliationService(this._db, this._transport, {DateTime Function()? now})
      : _now = now ?? DateTime.now;

  final DatabaseExecutor _db;
  final SyncDigestTransport _transport;
  final DateTime Function() _now;

  Future<Set<String>> reconcile() async {
    if (await _throttled()) return const {};

    final Map<String, dynamic> body;
    try {
      body = await _transport.fetchDigest();
    } catch (e) {
      debugPrint('Reconciliation: digest fetch failed (skipping): $e');
      return const {};
    }

    final serverEntities =
        (body['entities'] as Map?)?.cast<String, dynamic>() ?? const {};
    final cursorsToClear = <String>{};

    for (final entry in kReconcileEntities.entries) {
      final server = (serverEntities[entry.key] as Map?)?.cast<String, dynamic>();
      if (server == null) continue;
      final local = await computeCacheDigest(_db, table: entry.value.table);
      final serverCount = (server['count'] as num?)?.toInt() ?? 0;
      final serverChecksum = server['checksum'] as String? ?? '';
      if (local.count != serverCount || local.checksum != serverChecksum) {
        debugPrint(
          'Reconciliation: drift on ${entry.key} '
          '(local ${local.count}/${local.checksum} vs '
          'server $serverCount/$serverChecksum) → clearing '
          "'${entry.value.cursorKey}' cursor",
        );
        cursorsToClear.add(entry.value.cursorKey);
      }
    }

    for (final cursorKey in cursorsToClear) {
      await _db.delete('sync_state', where: 'entity = ?', whereArgs: [cursorKey]);
    }
    await _stampReconcileAt();
    return cursorsToClear;
  }

  // (kReconcileEntities values are EntitySpec, declared above.)

  Future<bool> _throttled() async {
    final rows = await _db.query('sync_state',
        where: 'entity = ?', whereArgs: [_kReconcileAtKey], limit: 1);
    if (rows.isEmpty) return false;
    final last = DateTime.tryParse(rows.first['cursor'] as String? ?? '');
    if (last == null) return false;
    return _now().toUtc().difference(last.toUtc()) < kReconcileMinInterval;
  }

  Future<void> _stampReconcileAt() async {
    await _db.insert(
      'sync_state',
      {'entity': _kReconcileAtKey, 'cursor': _now().toUtc().toIso8601String()},
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && flutter test test/sync/reconciliation_test.dart`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/repositories/sync_digest_repository.dart mobile/lib/sync/reconciliation.dart mobile/test/sync/reconciliation_test.dart
git commit -m "feat(mobile,sync): ReconciliationService — digest-drift self-heal"
```

---

### Task 7: Wire reconciliation into foreground + background sync

**Files:**
- Modify: `mobile/lib/main.dart` (`_fgSync` `onSync`, `:210-220`)
- Modify: `mobile/lib/sync/background_sync.dart` (`runHeadlessSync`, after all engines drain, `:57-156`)
- Modify: `mobile/lib/providers/` — add a `reconciliationServiceProvider` (in `mobile/lib/providers/gateway_provider.dart` or the closest sync-provider file that already reads `appDatabaseProvider` + `apiClientProvider`)

**Interfaces:**
- Consumes: `ReconciliationService`, `DioSyncDigestTransport`, `kReconcileEntities` (Task 6); the existing per-domain notifier `syncNow()` methods; `appDatabaseProvider`, `apiClientProvider`.

- [ ] **Step 1: Add the provider**

In the provider file that already exposes `appDatabaseProvider` and `apiClientProvider`:

```dart
final reconciliationServiceProvider = Provider<ReconciliationService>((ref) {
  return ReconciliationService(
    ref.watch(appDatabaseProvider),
    DioSyncDigestTransport(ref.watch(apiClientProvider)),
  );
});
```

- [ ] **Step 2: Wire the foreground path**

In `mobile/lib/main.dart`, extend the `onSync` callback (`:210-220`) so reconciliation runs AFTER the four drains, then re-drains only the drifted cursor-keys:

```dart
    _fgSync = ForegroundSyncScheduler(onSync: () async {
      await ref.read(tasksProvider.notifier).syncNow();
      await ref.read(notesProvider.notifier).syncNow();
      await ref.read(budgetsProvider.notifier).syncNow();
      await syncAllDocuments(ref.read);
      await pullNotificationsFeed(ref.read(apiClientProvider));

      // Self-heal: after a clean drain, detect drift vs the server and force a
      // full re-pull of any entity that no longer matches.
      final cleared = await ref.read(reconciliationServiceProvider).reconcile();
      if (cleared.contains('task')) {
        await ref.read(tasksProvider.notifier).syncNow();
      }
      if (cleared.contains('note')) {
        await ref.read(notesProvider.notifier).syncNow();
      }
      if (cleared.contains('budgets')) {
        await ref.read(budgetsProvider.notifier).syncNow();
      }
    });
    _fgSync!.start();
```

- [ ] **Step 3: Wire the headless path**

In `mobile/lib/sync/background_sync.dart`, after the Documents loop and before the notifications feed (inside the `try`, before `finally`), add:

```dart
    // Self-heal (headless): clear drifted cursors, then re-drain them once.
    try {
      final recon = ReconciliationService(
        db,
        DioSyncDigestTransport(client),
      );
      final cleared = await recon.reconcile();
      if (cleared.contains('task')) {
        await TaskSync(TaskDao(db), TasksRepository(DioTasksTransport(client)))
            .sync();
      }
      if (cleared.contains('note')) {
        await NoteSync(NoteDao(db), NotesRepository(DioNotesTransport(client)))
            .sync();
      }
      if (cleared.contains('budgets')) {
        await BudgetsSync(
          BudgetsDao(db),
          BudgetsRepository(DioBudgetsTransport(client)),
        ).sync();
      }
    } catch (e) {
      debugPrint('runHeadlessSync: reconciliation failed (non-fatal): $e');
    }
```

Add the imports at the top of `background_sync.dart`:

```dart
import 'package:lazyclaw_mobile/repositories/sync_digest_repository.dart';
import 'package:lazyclaw_mobile/sync/reconciliation.dart';
```

- [ ] **Step 4: Verify the app builds and existing tests pass**

Run: `cd mobile && flutter analyze && flutter test test/sync/`
Expected: analyze clean (no unused imports, no undefined names); all sync tests PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/main.dart mobile/lib/sync/background_sync.dart mobile/lib/providers/
git commit -m "feat(mobile,sync): run reconciliation after foreground + headless drains"
```

---

## Phase 4 — Conformance harness

### Task 8: Backend conformance suite (parametrized across the 5 entities)

**Files:**
- Create: `tests/sync_integrity/conformance.py` (the shared adapter spec — data, not tests)
- Create: `tests/sync_integrity/test_sync_conformance.py` (the parametrized invariants)

**Interfaces:**
- Consumes: each domain store (`tasks`, `budgets`, `lazybrain`) create/update/delete/get_changes fns; `compute_user_digest`.
- Produces: `CONFORMANCE_ADAPTERS: list[EntityAdapter]` — one per entity, each exposing `create`, `soft_delete`, `get_changes`, `entity_key`, `changes_key`, `deleted_key`.

- [ ] **Step 1: Write the adapters + the failing invariant tests**

```python
# tests/sync_integrity/conformance.py
"""Shared conformance adapters — one per syncable entity. A single invariant
suite (test_sync_conformance.py) runs against every adapter, so a new domain
inherits the whole suite by adding an entry here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from lazyclaw.budgets import store as budget_store
from lazyclaw.lazybrain import store as note_store
from lazyclaw.tasks import store as task_store


@dataclass(frozen=True)
class EntityAdapter:
    name: str
    changes_key: str          # array of live rows in the /changes payload
    deleted_key: str          # array of tombstone ids in the /changes payload
    create: Callable[..., Awaitable[dict]]     # (cfg, user_id) -> row dict (has "id")
    soft_delete: Callable[..., Awaitable[bool]]  # (cfg, user_id, row_id) -> bool
    get_changes: Callable[..., Awaitable[dict]]  # (cfg, user_id, since) -> payload


async def _create_task(cfg, user_id):
    return await task_store.create_task(cfg, user_id, title="conf-task")


async def _create_note(cfg, user_id):
    return await note_store.create_note(cfg, user_id, content="conf-note")


async def _create_project(cfg, user_id):
    return await budget_store.create_project(cfg, user_id, "conf-proj", budget=10.0)


async def _create_budget_entry(cfg, user_id):
    proj = await budget_store.create_project(cfg, user_id, "conf-be-proj", budget=10.0)
    return await budget_store.add_budget_entry(cfg, user_id, proj["id"], amount=5.0)


CONFORMANCE_ADAPTERS: list[EntityAdapter] = [
    EntityAdapter(
        "task", "tasks", "deleted",
        _create_task, task_store.delete_task, task_store.get_task_changes,
    ),
    EntityAdapter(
        "note", "notes", "deleted",
        _create_note, note_store.delete_note, note_store.get_note_changes,
    ),
    EntityAdapter(
        "project", "projects", "deleted_projects",
        _create_project, budget_store.delete_project, budget_store.get_budget_changes,
    ),
    EntityAdapter(
        "budget_entry", "budget_entries", "deleted_budget_entries",
        _create_budget_entry, budget_store.delete_budget_entry,
        budget_store.get_budget_changes,
    ),
]
```

> Adapter note for the implementer: confirm each store fn's exact name/signature before wiring (e.g. `task_store.create_task`, `task_store.delete_task`, `note_store.create_note`, `note_store.delete_note`). If a create needs extra required args, add them here — the store call is the only place that knows them. `get_*_changes` are keyword-`since` for budgets/notes and positional for tasks; the test calls them via a thin lambda in the adapter if signatures differ.

```python
# tests/sync_integrity/test_sync_conformance.py
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.sync_integrity.digest import compute_user_digest
from tests.sync_integrity.conformance import CONFORMANCE_ADAPTERS

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "u", "x", "salt"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


def _iso_now():
    return datetime.now(timezone.utc).isoformat()


@pytest.mark.parametrize("ad", CONFORMANCE_ADAPTERS, ids=lambda a: a.name)
async def test_full_pull_returns_the_created_row(cfg, ad):
    row = await ad.create(cfg, "u1")
    payload = await ad.get_changes(cfg, "u1", since=None)
    ids = [r["id"] for r in payload[ad.changes_key]]
    assert row["id"] in ids


@pytest.mark.parametrize("ad", CONFORMANCE_ADAPTERS, ids=lambda a: a.name)
async def test_soft_delete_tombstones_in_both_delta_and_full(cfg, ad):
    row = await ad.create(cfg, "u1")
    checkpoint = _iso_now()
    await asyncio.sleep(0.02)
    await ad.soft_delete(cfg, "u1", row["id"])

    # (a) tombstone appears in the delta after `since`
    delta = await ad.get_changes(cfg, "u1", since=checkpoint)
    assert row["id"] in delta[ad.deleted_key]

    # (b) tombstone appears in a full pull too
    full = await ad.get_changes(cfg, "u1", since=None)
    assert row["id"] in full[ad.deleted_key]
    # ...and the row is NOT in the live array
    assert row["id"] not in [r["id"] for r in full[ad.changes_key]]


@pytest.mark.parametrize("ad", CONFORMANCE_ADAPTERS, ids=lambda a: a.name)
async def test_digest_excludes_deleted_and_moves_on_create(cfg, ad):
    d0 = await compute_user_digest(cfg, "u1")
    row = await ad.create(cfg, "u1")
    d1 = await compute_user_digest(cfg, "u1")
    assert d1["entities"][ad.name]["checksum"] != d0["entities"][ad.name]["checksum"]

    await ad.soft_delete(cfg, "u1", row["id"])
    d2 = await compute_user_digest(cfg, "u1")
    # deleting the just-created row returns that entity's checksum to baseline
    assert d2["entities"][ad.name]["checksum"] == d0["entities"][ad.name]["checksum"]


async def test_cursor_isolation_shared_budgets_cursor(cfg):
    """A budget_entry created AFTER a project pull is still delivered on the next
    pull — the 2026-07-20 shared-cursor stranding, encoded as a guard."""
    from lazyclaw.budgets import store as budget_store

    proj = await budget_store.create_project(cfg, "u1", "iso", budget=10.0)
    # First pull advances the shared cursor to `now`.
    first = await budget_store.get_budget_changes(cfg, "u1", since=None)
    cursor = first["now"]
    await asyncio.sleep(0.02)
    entry = await budget_store.add_budget_entry(cfg, "u1", proj["id"], amount=5.0)
    # Second pull with the advanced cursor MUST still surface the new top-up.
    second = await budget_store.get_budget_changes(cfg, "u1", since=cursor)
    assert entry["id"] in [e["id"] for e in second["budget_entries"]]
```

- [ ] **Step 2: Run to verify (RED where a domain is non-conformant)**

Run: `python -m pytest tests/sync_integrity/test_sync_conformance.py -q`
Expected: the parametrized tests PASS for conformant domains; any FAIL pinpoints a real non-conformance (e.g. a store fn name/signature mismatch, or a domain whose tombstone/`since` handling diverges). Fix the adapter (wrong fn name/args) or the domain until green.

- [ ] **Step 3: Resolve any RED**

If a `get_*_changes` signature differs (tasks is positional `since`, budgets/notes are keyword `since=`), wrap it in the adapter with a lambda so the test's `since=` call works uniformly, e.g.:

```python
    EntityAdapter(
        "task", "tasks", "deleted",
        _create_task, task_store.delete_task,
        lambda cfg, user_id, since=None: task_store.get_task_changes(cfg, user_id, since),
    ),
```

- [ ] **Step 4: Run to verify all pass**

Run: `python -m pytest tests/sync_integrity/ -q`
Expected: PASS (all digest + route + conformance tests).

- [ ] **Step 5: Commit**

```bash
git add tests/sync_integrity/conformance.py tests/sync_integrity/test_sync_conformance.py
git commit -m "test(sync): backend conformance suite (tombstone, cursor-isolation, digest) across entities"
```

---

### Task 9: Mobile conformance suite + cross-format & round-trip invariants

**Files:**
- Create: `mobile/test/sync/sync_conformance_test.dart`

**Interfaces:**
- Consumes: `serverWinsByTime` (Task 1); `computeCacheDigest` (Task 5); `BudgetsSync`/`BudgetsDao` + the in-file fakes pattern from `budgets_sync_test.dart`.

- [ ] **Step 1: Write the invariant tests**

```dart
// mobile/test/sync/sync_conformance_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/sync/digest.dart';
import 'package:lazyclaw_mobile/sync/sync_time.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _c = 0;
Future<dynamic> _db() => databaseFactoryFfi.openDatabase(
      'file:confmem${_c++}?mode=memory&cache=shared',
      options: OpenDatabaseOptions(
        version: kAppDbVersion,
        singleInstance: false,
        onCreate: (db, v) async => createAppDbSchema(db),
      ),
    );

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('INVARIANT 2 — instant order == chronological order across formats', () {
    final sameInstant = [
      ['2026-06-05T11:00:00.000Z', '2026-06-05T11:00:00.000000+00:00'],
      ['2026-06-05T11:00:00Z', '2026-06-05 11:00:00.000000'],
    ];
    for (final pair in sameInstant) {
      test('same instant ${pair[0]} vs ${pair[1]} → server wins (tie)', () {
        expect(serverWinsByTime(pair[0], pair[1]), isTrue);
        expect(serverWinsByTime(pair[1], pair[0]), isTrue);
      });
    }
    test('strictly ordered pairs agree with real time', () {
      expect(serverWinsByTime('2026-06-05T11:00:01Z', '2026-06-05T11:00:00Z'), isTrue);
      expect(serverWinsByTime('2026-06-05T11:00:00Z', '2026-06-05T11:00:01Z'), isFalse);
    });
  });

  group('INVARIANT 4 — round-trip: a synced (clean) row matches server digest', () {
    test('upserted server rows produce a digest that excludes dirty rows', () async {
      final db = await _db();
      final dao = BudgetsDao(db, now: () => '2026-06-05T10:00:00.000Z');
      // A clean, server-sourced row:
      await dao.upsertProjectFromServer(
        Project.fromJson({
          'id': 'p1', 'name': 'S', 'budget': 10.0, 'currency': 'USD',
          'status': 'active', 'spent': 0.0, 'remaining': 10.0,
          'created_at': '2026-06-05T10:00:00+00:00',
          'updated_at': '2026-06-05T10:00:00+00:00',
        }),
        serverUpdatedAt: '2026-06-05T10:00:00+00:00',
      );
      // A dirty, local-only row:
      await dao.applyLocalProjectCreate('local', id: 'p2');

      final d = await computeCacheDigest(db, table: 'project_cache');
      // Only the clean row counts.
      expect(d.count, 1);
    });
  });

  group('INVARIANT 5 — digest self-heal signal', () {
    test('a stranded clean row makes the local digest differ from an empty set',
        () async {
      final db = await _db();
      final dao = BudgetsDao(db, now: () => '2026-06-05T10:00:00Z');
      await dao.upsertProjectFromServer(
        Project.fromJson({
          'id': 'p1', 'name': 'S', 'budget': 10.0, 'currency': 'USD',
          'status': 'active', 'spent': 0.0, 'remaining': 10.0,
          'created_at': '2026-06-05T10:00:00+00:00',
          'updated_at': '2026-06-05T10:00:00+00:00',
        }),
        serverUpdatedAt: '2026-06-05T10:00:00+00:00',
      );
      final d = await computeCacheDigest(db, table: 'project_cache');
      expect(d.checksum, isNot('0000000000000000'));
      expect(d.count, 1);
    });
  });
}
```

> Import note: `Project` comes from `package:lazyclaw_mobile/models/project.dart` — add the import (mirror `budgets_sync_test.dart:9`). Confirm `upsertProjectFromServer`'s exact parameter name (`serverUpdatedAt:`) against `budgets_sync_test.dart:740`.

- [ ] **Step 2: Run to verify it fails, then passes**

Run: `cd mobile && flutter test test/sync/sync_conformance_test.dart`
Expected: PASS (relies only on already-implemented Task 1 + Task 5 code). If INVARIANT 2 fails, Task 2's replacement was incomplete somewhere; if INVARIANT 4 fails, `computeCacheDigest`'s `dirty=0` filter is wrong.

- [ ] **Step 3: Commit**

```bash
git add mobile/test/sync/sync_conformance_test.dart
git commit -m "test(mobile,sync): conformance — cross-format order, round-trip, digest signal"
```

---

### Task 10: Static shared-cursor guard

**Files:**
- Test: `mobile/test/sync/reconciliation_test.dart` (append) OR `tests/sync_integrity/test_sync_conformance.py` (append)

**Interfaces:**
- Consumes: `kReconcileEntities` (Task 6).

- [ ] **Step 1: Write the guard test (Dart)**

Append to `mobile/test/sync/reconciliation_test.dart`:

```dart
  test('GUARD: only the budgets cursor is shared across entity types', () {
    // Group entities by the cursor they clear. Any cursor covering >1 entity is
    // a shared cursor — the 2026-07-20 stranding hazard. Only 'budgets' is
    // knowingly shared; a NEW shared cursor must be a deliberate change that
    // updates this guard (and is covered by reconciliation + full re-pull).
    final byCursor = <String, List<String>>{};
    kReconcileEntities.forEach((entity, spec) {
      byCursor.putIfAbsent(spec.cursorKey, () => []).add(entity);
    });
    final shared = byCursor.entries.where((e) => e.value.length > 1).toList();
    expect(shared.length, 1);
    expect(shared.single.key, 'budgets');
    expect(shared.single.value.toSet(),
        {'project', 'expense', 'budget_entry'});
  });
```

> `kReconcileEntities` (map) and `EntitySpec.cursorKey` are already public from Task 6, so the test can read `spec.cursorKey` directly — no code change needed, only the test.

- [ ] **Step 2: Run to verify it passes**

Run: `cd mobile && flutter test test/sync/reconciliation_test.dart`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add mobile/test/sync/reconciliation_test.dart
git commit -m "test(mobile,sync): guard — 'budgets' is the only sanctioned shared cursor"
```

---

## Phase 5 — Verify + release

### Task 11: Full verification, device check, release

**Files:**
- Modify: `mobile/pubspec.yaml` (version bump)
- Modify: `docs/DOCS.md` (Sync Integrity Layer section), `MEMORY.md` + a new memory file
- Run: full test suites + APK build

- [ ] **Step 1: Backend suite (isolated DB — never live)**

Run: `python -m pytest tests/sync_integrity/ tests/tasks/ tests/budgets/ tests/lazybrain/ -q`
Expected: PASS. (Scope to these dirs — do NOT run the whole suite against a running container; see the test-DB isolation constraint.)

- [ ] **Step 2: Mobile suite**

Run: `cd mobile && flutter analyze && flutter test test/sync/`
Expected: analyze clean; all sync tests PASS.

- [ ] **Step 3: Device verification of self-heal**

Reproduce a stranded row and confirm auto-heal (mirror the 2026-07-20 method):
1. With the app synced, on the device DB manually advance a cursor past a row — e.g. via the app's debug hook or by inserting a server row while the phone's cursor is ahead. Simplest repeatable check: create a top-up on the web UI, then on the phone open the app so a foreground `onSync` fires.
2. Confirm the phone shows the new top-up within one reconcile cycle (or immediately on resume, since reconcile re-drains the drifted domain in the same `onSync`).
3. Capture a screenshot before/after.

Expected: the previously-invisible row appears without a "Clear cache".

- [ ] **Step 4: Bump version + build APK**

Edit `mobile/pubspec.yaml` `version:` (increment build number, e.g. `1.22.1+111`).

Run: `./scripts/build-mobile-apk.sh`
Expected: `mobile/dist/app-release.apk` + `version.json` produced.

- [ ] **Step 5: Docs + memory + commit**

Add a "Sync Integrity Layer" subsection to `docs/DOCS.md` (self-healing digest, `serverWinsByTime`, conformance harness, adapter-to-add-a-domain). Add a memory file `project_sync_integrity_layer_2026_07_20.md` and an index line in `MEMORY.md`.

```bash
git add mobile/pubspec.yaml docs/DOCS.md mobile/dist/app-release.apk mobile/dist/version.json
git commit -m "chore(mobile): release with sync integrity layer (self-heal + conformance harness)"
```

(Memory files live outside the repo — write them directly with the Write tool, not via git.)

---

## Self-Review

**Spec coverage**
- Part 1 (self-healing digest): Tasks 3–7 (backend digest + endpoint; mobile fold/digest/service; foreground+headless wiring). ✓
- Part 2 (root-cause fixes): timestamp compare — Tasks 1–2 ✓. Shared-cursor safety — covered at runtime by reconciliation (Task 6/7) and as a static guard (Task 10) + cursor-isolation invariant (Task 8); the heavyweight boot-time manifest from the spec is intentionally replaced by these lighter, equivalent guards (documented as a deliberate simplification). ✓
- Part 3 (conformance harness): backend Task 8 (tombstone, cursor-isolation, digest), mobile Task 9 (cross-format order, round-trip, digest signal), guard Task 10. Fake transports use the production `DioException`/`ApiError` shape and backend tests use the isolated-DB fixture. ✓
- Phasing (RED tests → fixes → digest → harness → release): Tasks 1→11. ✓

**Placeholder scan:** one intentional literal — `kGoldenDigestVector = '<paste python output here>'` — Task 5 Step 3 gives the exact command to compute it and Step 4 fails loudly until it's filled. Adapter store-fn names in Task 8 carry a verify note because their exact signatures weren't extracted; Step 2/3 make any mismatch a hard RED with the fix shown.

**Type consistency:** `serverWinsByTime(String?, String?)→bool`, `parseInstantMicros(String?)→int?`, `foldDigest(Iterable<List<String>>)→String`, `EntityDigest{int count, String checksum}`, `computeCacheDigest(DatabaseExecutor,{required String table,String? kind})→Future<EntityDigest>`, `ReconciliationService.reconcile()→Future<Set<String>>`, `EntitySpec{String table,String cursorKey}` (public), `kReconcileEntities: Map<String,EntitySpec>`, backend `fold_digest(Iterable[tuple[str,str]])→str` / `compute_user_digest(config,user_id)→dict` — used consistently across tasks. No renames: `EntitySpec` is declared public in Task 6 and read by the Task 10 guard.
