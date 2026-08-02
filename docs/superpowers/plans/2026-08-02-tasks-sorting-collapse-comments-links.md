# Tasks Update (Sorting, Collapse, Comments, Links) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Done-last sorting, persisted collapse + hide-completed, comment threads on tasks/subtasks (user + agent), and tappable links — mobile-first with the backend built web-ready.

**Architecture:** Comments are a new encrypted JSON-list column on the `tasks` row (the proven `steps`/`tags` pattern) with append-only add/delete endpoints — they sync for free inside the existing `/api/tasks/changes` row feed and replay from the mobile outbox as appends (no last-write-wins loss). Sorting and collapse are purely client-side display concerns. Links are a shared Flutter rich-text widget; no backend involvement.

**Tech Stack:** Python (FastAPI + aiosqlite + AES-256-GCM envelope), Flutter (Riverpod, sqflite_sqlcipher offline cache + outbox sync, url_launcher).

**Spec:** `docs/superpowers/specs/2026-08-02-tasks-sorting-collapse-comments-links-design.md`

## Global Constraints

- **NEVER run full `pytest tests/` while the Docker container is up** (./data = live prod DB). Run only the targeted test files listed in each task; each constructs its own `Config(database_dir=tmp_path)`.
- Backend deploys via `make rebuild` (image is baked; source not mounted). Don't rebuild until the final task.
- Commit messages: conventional format (`feat:`/`fix:`/`test:`/`docs:`), **no Co-Authored-By / AI attribution**.
- Immutability: always build new lists/dicts, never mutate fetched ones (see `append_steps` for the house style).
- Canonical comment shape (both sides, exact keys): `{"id": "c-<hex>", "ts": "<ISO-8601 UTC>", "author": "user"|"agent", "text": "<=2000 chars>", "subtask_id": "<step id>"|null}`.
- Comments are **append/delete only** — they must NEVER ride PATCH `update_task`, the mobile `update` outbox op, or the steps codec (`_normalize_steps` must not learn comment keys).
- Caps: 500 comments/task (`MAX_COMMENTS_PER_TASK`), 2000 chars/comment (`MAX_COMMENT_CHARS`).
- Mobile: sorting is **display-only** — stored `steps` array order and cache row order are never rewritten by a sort.
- Widget tests must NOT touch a real sqflite DB (FakeAsync hang) — DAO tests use `sqflite_common_ffi`, widget tests use plain callbacks/fakes.
- Flutter checks per task: `flutter analyze` on touched files' package + the task's `flutter test <file>`.

---

### Task 1: Backend — `comments` column, migration, respawn classification

**Files:**
- Modify: `lazyclaw/tasks/store.py` (TASK_COLUMNS ~line 33-89, ENCRYPTED_FIELDS line 21, `_RESPAWN_RESET_COLUMNS` ~line 408)
- Modify: `lazyclaw/db/connection.py` (the `("tasks", <col>, "ALTER TABLE ...")` migration tuple list, ~line 123-143)
- Test: `tests/tasks/test_task_comments.py` (new)

**Interfaces:**
- Produces: `tasks.comments` column (encrypted JSON list, NULL default), classified as respawn-RESET (comments stay with the completed occurrence).
- Later tasks rely on: `"comments"` present in `TASK_COLUMNS` and `ENCRYPTED_FIELDS` so `_row_to_dict` decrypts it into every list/get/changes payload as a JSON *string* (same as `steps`).

- [ ] **Step 1: Write the failing tests** — new file `tests/tasks/test_task_comments.py` with the same fixture as `tests/tasks/test_task_store_t1_t3.py`:

```python
"""Comment-thread storage on the tasks row (encrypted JSON list column)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.tasks import store as task_store

pytestmark = pytest.mark.asyncio


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


async def test_comments_column_exists_and_defaults_null(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "a task")
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert "comments" in fetched
    assert fetched["comments"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run (container down): `pytest tests/tasks/test_task_comments.py -v`
Expected: FAIL — `KeyError`/assert on `"comments"` (column not in TASK_COLUMNS).

- [ ] **Step 3: Implement.** In `lazyclaw/tasks/store.py`:
  1. Add to `TASK_COLUMNS` right after `"progress_log"`:
     ```python
     # Comment thread — encrypted JSON list, canonical entry shape
     # {id, ts, author: user|agent, text, subtask_id|null}. Append-only via
     # add_comment / delete_comment; NEVER rides update_task/PATCH.
     "comments",
     ```
  2. `ENCRYPTED_FIELDS = frozenset({"title", "description", "category", "tags", "steps", "comments"})` — unlike `progress_log`, clients must read comments, so it goes through `decrypt_field` in `_row_to_dict`.
  3. Add `"comments"` to `_RESPAWN_RESET_COLUMNS` with the comment `"comments",  # the thread belongs to the occurrence (progress_log precedent)`.

  In `lazyclaw/db/connection.py`, append to the migration tuple list (~line 143):
  ```python
  ("tasks", "comments", "ALTER TABLE tasks ADD COLUMN comments TEXT"),
  ```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/tasks/test_task_comments.py tests/tasks/test_recurring_carry_forward.py -v`
Expected: PASS — including `test_every_task_column_has_a_respawn_disposition` (this test FAILS if the new column is left unclassified; its passing proves the classification landed).

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/tasks/store.py lazyclaw/db/connection.py tests/tasks/test_task_comments.py
git commit -m "feat: add encrypted comments column to tasks (respawn-reset classified)"
```

---

### Task 2: Backend — `decode_comments`, `add_comment`, `delete_comment`

**Files:**
- Modify: `lazyclaw/tasks/store.py` (new section after the progress-log section, ~line 2170)
- Test: `tests/tasks/test_task_comments.py` (extend)

**Interfaces:**
- Produces (exact signatures later tasks call):
  - `MAX_COMMENTS_PER_TASK = 500`, `MAX_COMMENT_CHARS = 2000`
  - `class CommentLimitReached(Exception)`
  - `decode_comments(raw: str | list | None) -> list[dict]` — tolerant, mirrors `decode_steps`
  - `async def add_comment(config, user_id, task_id, *, text: str, author: str = "user", subtask_id: str | None = None, comment_id: str | None = None) -> dict | None` — returns the entry; `None` = task missing; raises `ValueError` (empty/too-long text, unknown subtask_id) or `CommentLimitReached`. A provided `comment_id` already in the list returns the existing entry unchanged (idempotent offline replay).
  - `async def delete_comment(config, user_id, task_id, comment_id: str) -> bool | None` — `None` = task missing, `False` = comment id not found, `True` = deleted.
- Consumes: Task 1's column + `decode_steps`, `get_task`, `encrypt`, `get_user_dek`, `db_session`.

- [ ] **Step 1: Write the failing tests** — append to `tests/tasks/test_task_comments.py`:

```python
async def test_add_comment_appends_and_round_trips(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "commented")
    entry = await task_store.add_comment(
        cfg, "u1", task["id"], text="first comment", author="user",
    )
    assert entry["id"].startswith("c-")
    assert entry["author"] == "user"
    assert entry["subtask_id"] is None

    fetched = await task_store.get_task(cfg, "u1", task["id"])
    parsed = task_store.decode_comments(fetched["comments"])
    assert [c["text"] for c in parsed] == ["first comment"]
    # Encrypted at rest: the raw column must be an enc:v1 envelope.
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT comments FROM tasks WHERE id = ?", (task["id"],)
        )
        raw = (await cur.fetchone())[0]
    assert raw.startswith("enc:v1:")


async def test_add_comment_bumps_updated_at_for_sync(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "sync me")
    before = (await task_store.get_task(cfg, "u1", task["id"]))["updated_at"]
    await task_store.add_comment(cfg, "u1", task["id"], text="hi")
    after = (await task_store.get_task(cfg, "u1", task["id"]))["updated_at"]
    assert after > before


async def test_add_comment_validates_subtask_id(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "with steps", steps=["step one"])
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    step_id = task_store.decode_steps(fetched["steps"])[0]["id"]

    ok = await task_store.add_comment(
        cfg, "u1", task["id"], text="on the step", subtask_id=step_id,
    )
    assert ok["subtask_id"] == step_id
    with pytest.raises(ValueError):
        await task_store.add_comment(
            cfg, "u1", task["id"], text="bad", subtask_id="s-nope",
        )


async def test_add_comment_idempotent_on_client_id(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "replayed")
    a = await task_store.add_comment(
        cfg, "u1", task["id"], text="once", comment_id="c-client1",
    )
    b = await task_store.add_comment(
        cfg, "u1", task["id"], text="once", comment_id="c-client1",
    )
    assert a["id"] == b["id"] == "c-client1"
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert len(task_store.decode_comments(fetched["comments"])) == 1


async def test_comment_cap_raises(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "capped")
    key_patch = task_store.MAX_COMMENTS_PER_TASK
    # Seed to the cap via the public API-shape (write the column directly to
    # keep the test fast), then assert the 501st append refuses.
    from lazyclaw.crypto.encryption import encrypt
    from lazyclaw.crypto.key_manager import get_user_dek
    key = await get_user_dek(cfg, "u1")
    filler = [
        {"id": f"c-{i}", "ts": "2026-01-01T00:00:00+00:00",
         "author": "user", "text": "x", "subtask_id": None}
        for i in range(key_patch)
    ]
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE tasks SET comments = ? WHERE id = ?",
            (encrypt(json.dumps(filler), key), task["id"]),
        )
        await db.commit()
    with pytest.raises(task_store.CommentLimitReached):
        await task_store.add_comment(cfg, "u1", task["id"], text="overflow")


async def test_delete_comment(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "deletable")
    entry = await task_store.add_comment(cfg, "u1", task["id"], text="bye")
    assert await task_store.delete_comment(cfg, "u1", task["id"], entry["id"]) is True
    assert await task_store.delete_comment(cfg, "u1", task["id"], entry["id"]) is False
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched["comments"] is None  # empty list clears the column (steps convention)
    assert await task_store.delete_comment(cfg, "u1", "missing-task", "c-x") is None


async def test_changes_feed_carries_decrypted_comments(cfg) -> None:
    task = await task_store.create_task(cfg, "u1", "feed me")
    await task_store.add_comment(cfg, "u1", task["id"], text="in the feed")
    feed = await task_store.get_task_changes(cfg, "u1", None)
    row = next(t for t in feed["tasks"] if t["id"] == task["id"])
    assert [c["text"] for c in task_store.decode_comments(row["comments"])] == ["in the feed"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/tasks/test_task_comments.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'add_comment'` (the Task 1 tests still pass).

- [ ] **Step 3: Implement** — new section in `store.py` after `read_progress_log`:

```python
# ---------------------------------------------------------------------------
# Comments — encrypted JSON thread of {id, ts, author, text, subtask_id}.
# Append/delete only; NEVER rides update_task (no full-replace surface).
# ---------------------------------------------------------------------------

MAX_COMMENTS_PER_TASK = 500
MAX_COMMENT_CHARS = 2000
_VALID_COMMENT_AUTHORS = frozenset({"user", "agent"})


class CommentLimitReached(Exception):
    """The per-task comment cap was hit; the append is refused loudly."""


def decode_comments(raw: str | list | None) -> list[dict]:
    """Decode a task's ``comments`` value into a list of dicts.

    ``comments`` is an ENCRYPTED field, so ``_row_to_dict`` hands back the
    decrypted JSON *string* (same contract as ``steps``). Malformed input
    degrades to an empty thread, never raises.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Could not parse comments JSON; treating as empty thread")
        return []
    if not isinstance(parsed, list):
        return []
    return [c for c in parsed if isinstance(c, dict)]


async def _write_comments(
    config: Config, user_id: str, task_id: str, comments: list[dict], key: bytes,
) -> None:
    enc = encrypt(json.dumps(comments), key) if comments else None
    now = datetime.now(timezone.utc).isoformat()
    async with db_session(config) as db:
        await db.execute(
            "UPDATE tasks SET comments = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (enc, now, task_id, user_id),
        )
        await db.commit()


async def add_comment(
    config: Config,
    user_id: str,
    task_id: str,
    *,
    text: str,
    author: str = "user",
    subtask_id: str | None = None,
    comment_id: str | None = None,
) -> dict | None:
    """Append one comment. Returns the entry, or None when the task is missing.

    Raises ValueError on bad input and CommentLimitReached at the cap. A
    caller-supplied ``comment_id`` already present returns the existing entry
    unchanged — the mobile outbox replays appends at-least-once.
    """
    task = await get_task(config, user_id, task_id)
    if task is None:
        return None

    clean = (text or "").strip()
    if not clean:
        raise ValueError("Comment text is required")
    if len(clean) > MAX_COMMENT_CHARS:
        raise ValueError(f"Comment too long (max {MAX_COMMENT_CHARS} chars)")
    if author not in _VALID_COMMENT_AUTHORS:
        raise ValueError(f"Unknown comment author: {author!r}")
    if subtask_id is not None:
        step_ids = {s.get("id") for s in decode_steps(task.get("steps"))}
        if subtask_id not in step_ids:
            raise ValueError("Unknown subtask_id for this task")

    current = decode_comments(task.get("comments"))
    if comment_id:
        for existing in current:
            if existing.get("id") == comment_id:
                return existing
    if len(current) >= MAX_COMMENTS_PER_TASK:
        raise CommentLimitReached(
            f"Task already has {MAX_COMMENTS_PER_TASK} comments"
        )

    entry = {
        "id": comment_id or f"c-{uuid4().hex[:12]}",
        "ts": datetime.now(timezone.utc).isoformat(),
        "author": author,
        "text": clean,
        "subtask_id": subtask_id,
    }
    key = await get_user_dek(config, user_id)
    await _write_comments(config, user_id, task_id, [*current, entry], key)
    return entry


async def delete_comment(
    config: Config, user_id: str, task_id: str, comment_id: str,
) -> bool | None:
    """Remove one comment by id. None = task missing, False = id not found."""
    task = await get_task(config, user_id, task_id)
    if task is None:
        return None
    current = decode_comments(task.get("comments"))
    remaining = [c for c in current if c.get("id") != comment_id]
    if len(remaining) == len(current):
        return False
    key = await get_user_dek(config, user_id)
    await _write_comments(config, user_id, task_id, remaining, key)
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/tasks/test_task_comments.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/tasks/store.py tests/tasks/test_task_comments.py
git commit -m "feat: add_comment/delete_comment store API with cap + idempotent replay"
```

---

### Task 3: Backend — comment REST endpoints

**Files:**
- Modify: `lazyclaw/gateway/routes/tasks.py` (models near `StepDraft` ~line 50; routes after the steps section ~line 517)
- Test: `tests/test_task_comment_routes.py` (new; model the app/client fixture on `tests/test_specialists_routes.py`)

**Interfaces:**
- Produces (the mobile sync + future web client contract):
  - `POST /api/tasks/{task_id}/comments` body `{"id"?: str, "text": str, "subtask_id"?: str}` → `200 {"comment": {...}}`; author is ALWAYS forced to `"user"` on this route; `404` task missing, `400` validation, `409` cap.
  - `DELETE /api/tasks/{task_id}/comments/{comment_id}` → `200 {"deleted": true|false}`; `404` task missing.
- Consumes: Task 2's `add_comment` / `delete_comment` / `CommentLimitReached`.

- [ ] **Step 1: Write the failing tests** — `tests/test_task_comment_routes.py`, copying the auth/client fixture pattern from `tests/test_specialists_routes.py` (isolated tmp DB config + logged-in test client), with test bodies:

```python
async def test_post_comment_forces_user_author(client, seeded_task_id):
    r = await client.post(
        f"/api/tasks/{seeded_task_id}/comments",
        json={"text": "from the API", "id": "c-clientid1"},
    )
    assert r.status_code == 200
    c = r.json()["comment"]
    assert c["author"] == "user" and c["id"] == "c-clientid1"

async def test_post_comment_404_400_409(client, seeded_task_id):
    assert (await client.post(
        "/api/tasks/nope/comments", json={"text": "x"},
    )).status_code == 404
    assert (await client.post(
        f"/api/tasks/{seeded_task_id}/comments",
        json={"text": "x", "subtask_id": "s-nope"},
    )).status_code == 400

async def test_delete_comment_roundtrip(client, seeded_task_id):
    cid = (await client.post(
        f"/api/tasks/{seeded_task_id}/comments", json={"text": "bye"},
    )).json()["comment"]["id"]
    assert (await client.delete(
        f"/api/tasks/{seeded_task_id}/comments/{cid}",
    )).json()["deleted"] is True
    assert (await client.delete(
        f"/api/tasks/{seeded_task_id}/comments/{cid}",
    )).json()["deleted"] is False
```

(`seeded_task_id` = a fixture that creates one task for the logged-in user via `task_store.create_task`. The 409-cap case is already store-tested; skip it at the route level.)

- [ ] **Step 2: Run to verify they fail** — `pytest tests/test_task_comment_routes.py -v` → FAIL 404/405 (routes absent).

- [ ] **Step 3: Implement** in `gateway/routes/tasks.py`:

```python
class CommentBody(BaseModel):
    # Optional client-minted id for offline-first idempotent replay
    # (same convention as CreateTaskBody.id).
    id: str | None = Field(default=None, max_length=64)
    text: str = Field(min_length=1, max_length=2000)
    subtask_id: str | None = Field(default=None, max_length=64)
```

and after the steps routes:

```python
# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@router.post("/{task_id}/comments")
async def add_comment_route(
    task_id: str,
    body: CommentBody,
    user: User = Depends(get_current_user),
):
    """Append one user-authored comment to a task (or one of its subtasks)."""
    try:
        entry = await add_comment(
            _config, user.id, task_id,
            text=body.text, author="user",
            subtask_id=body.subtask_id, comment_id=body.id,
        )
    except CommentLimitReached as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if entry is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"comment": entry}


@router.delete("/{task_id}/comments/{comment_id}")
async def delete_comment_route(
    task_id: str,
    comment_id: str,
    user: User = Depends(get_current_user),
):
    """Remove one comment by id. Idempotent: an unknown id returns deleted=false."""
    result = await delete_comment(_config, user.id, task_id, comment_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"deleted": result}
```

Extend the existing `from lazyclaw.tasks.store import (...)` import with `add_comment, delete_comment, CommentLimitReached`.

- [ ] **Step 4: Run tests** — `pytest tests/test_task_comment_routes.py tests/tasks/test_task_comments.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/gateway/routes/tasks.py tests/test_task_comment_routes.py
git commit -m "feat: comment add/delete REST endpoints on tasks"
```

---

### Task 4: Backend — `add_task_comment` agent skill + quiet notification

**Files:**
- Modify: `lazyclaw/skills/builtin/task_manager.py` (new skill class after `UpdateTaskSkill`)
- Modify: `lazyclaw/skills/registry.py` (~line 186-193: import + register alongside `AddTaskSkill`)
- Test: `tests/tasks/test_add_task_comment_skill.py` (new)

**Interfaces:**
- Produces: skill `add_task_comment(task_name, text, subtask_name?)` — fuzzy-matches the task (todo + in_progress, same as `CompleteTaskSkill`), optional fuzzy subtask match by title, writes with `author="agent"`, then emits a QUIET feed notification (`telegram=False, silent=True`).
- Consumes: Task 2's `add_comment`; `_fuzzy_match_task` (already in task_manager.py); `lazyclaw.notifications.spine.notify`.

- [ ] **Step 1: Write the failing test** — same `cfg` fixture as Task 1; monkeypatch the spine so no real notification fires:

```python
async def test_skill_adds_agent_comment(cfg, monkeypatch) -> None:
    from lazyclaw.skills.builtin.task_manager import AddTaskCommentSkill
    from lazyclaw.tasks import store as task_store

    sent = []
    async def fake_notify(*a, **k):
        sent.append(k)
    monkeypatch.setattr("lazyclaw.notifications.spine.notify", fake_notify)

    task = await task_store.create_task(cfg, "u1", "buy paint")
    skill = AddTaskCommentSkill(config=cfg)
    out = await skill.execute("u1", {"task_name": "paint", "text": "found 2 shops"})
    assert "buy paint" in out

    fetched = await task_store.get_task(cfg, "u1", task["id"])
    comments = task_store.decode_comments(fetched["comments"])
    assert comments[-1]["author"] == "agent"
    assert comments[-1]["text"] == "found 2 shops"
    assert sent and sent[0]["telegram"] is False and sent[0]["silent"] is True
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/tasks/test_add_task_comment_skill.py -v` → FAIL (ImportError).

- [ ] **Step 3: Implement** the skill (mirror `CompleteTaskSkill`'s structure exactly):

```python
class AddTaskCommentSkill(BaseSkill):
    """Append an agent-authored comment to a task's thread."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "add_task_comment"

    @property
    def description(self) -> str:
        return (
            "Add a comment to a task's comment thread (author=agent). Use for "
            "progress notes, findings, or follow-ups the user should see on the "
            "task itself. Matches the task by name (partial match works). "
            "Optionally target one sub-task by its title via subtask_name. "
            "This is a COMMENT — it never edits the task's description."
        )

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_name": {
                    "type": "string",
                    "description": "Task name or partial name to match",
                },
                "text": {
                    "type": "string",
                    "description": "The comment text (max 2000 chars)",
                },
                "subtask_name": {
                    "type": "string",
                    "description": "Optional sub-task title to attach the comment to",
                },
            },
            "required": ["task_name", "text"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.notifications import spine
        from lazyclaw.tasks.store import (
            CommentLimitReached, add_comment, decode_steps, list_tasks,
        )

        task_name = params.get("task_name", "").strip()
        text = params.get("text", "").strip()
        if not task_name or not text:
            return "Both task_name and text are required."

        tasks = await list_tasks(self._config, user_id, status="todo")
        tasks += await list_tasks(self._config, user_id, status="in_progress")
        match = _fuzzy_match_task(tasks, task_name)
        if not match:
            available = ", ".join(t.get("title", "?") for t in tasks[:5])
            return f"No task matching '{task_name}'. Active tasks: {available}"

        subtask_id = None
        subtask_name = (params.get("subtask_name") or "").strip()
        if subtask_name:
            wanted = subtask_name.casefold()
            for step in decode_steps(match.get("steps")):
                if wanted in str(step.get("title", "")).casefold():
                    subtask_id = step.get("id")
                    break
            if subtask_id is None:
                return f"No sub-task matching '{subtask_name}' on '{match['title']}'."

        try:
            entry = await add_comment(
                self._config, user_id, match["id"],
                text=text, author="agent", subtask_id=subtask_id,
            )
        except CommentLimitReached:
            return f"'{match['title']}' already has the maximum number of comments."
        except ValueError as e:
            return f"Could not add comment: {e}"
        if entry is None:
            return "Task disappeared before the comment could be added."

        # Quiet feed entry — visible in the app's notification feed, no
        # Telegram push, no sound.
        try:
            await spine.notify(
                self._config, user_id,
                kind="task_comment",
                title=f"💬 {match['title']}",
                body=text[:200],
                telegram=False,
                silent=True,
                deep_link={"type": "task", "id": match["id"]},
            )
        except Exception:
            logger.debug("task_comment notify failed", exc_info=True)

        return f"Commented on '{match['title']}': {text[:80]}"
```

Register in `registry.py`: add `AddTaskCommentSkill` to the existing `from lazyclaw.skills.builtin.task_manager import (...)` block and `self.register(AddTaskCommentSkill(config=config))` beside the other task skills.

- [ ] **Step 4: Run tests** — `pytest tests/tasks/test_add_task_comment_skill.py -v` → PASS. Also run `pytest tests/teams/test_specialist_mcp_allowlist.py -v` (an unstaged-WIP file in this repo touches it; just confirm no interference — do NOT modify it).

- [ ] **Step 5: Commit**

```bash
git add lazyclaw/skills/builtin/task_manager.py lazyclaw/skills/registry.py tests/tasks/test_add_task_comment_skill.py
git commit -m "feat: add_task_comment agent skill with quiet feed notification"
```

---

### Task 5: Mobile — `TaskComment` model + codec + `Task.comments` field

**Files:**
- Create: `mobile/lib/models/comment.dart`
- Modify: `mobile/lib/models/task.dart` (field + fromJson/toJson/copyWith + getter)
- Test: `mobile/test/models/comment_test.dart` (new)

**Interfaces:**
- Produces (exact API later tasks import from `models/comment.dart`):
  - `class TaskComment { final String id; final String ts; final String author; final String text; final String? subtaskId; }` with `toJson()`, `static TaskComment? fromMap(Map)`, `copyWith`
  - `String newCommentId()` → `'c-' + uuidV4()`
  - `List<TaskComment> parseComments(String? raw)` (tolerant, `[]` on garbage)
  - `String? serializeComments(List<TaskComment>)` (empty → null, steps convention)
  - `Task.comments` (`String?` raw JSON) + `List<TaskComment> get taskComments`

- [ ] **Step 1: Write the failing tests** — `mobile/test/models/comment_test.dart`:

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/comment.dart';

void main() {
  test('parseComments round-trips the canonical shape', () {
    const raw =
        '[{"id":"c-1","ts":"2026-08-02T10:00:00+00:00","author":"agent",'
        '"text":"hi","subtask_id":"s-9"}]';
    final parsed = parseComments(raw);
    expect(parsed, hasLength(1));
    expect(parsed.first.author, 'agent');
    expect(parsed.first.subtaskId, 's-9');
    expect(serializeComments(parsed), raw);
  });

  test('parseComments tolerates garbage and empties', () {
    expect(parseComments(null), isEmpty);
    expect(parseComments(''), isEmpty);
    expect(parseComments('not json'), isEmpty);
    expect(parseComments('{"a":1}'), isEmpty);
    // entries with empty text are dropped; unknown author coerces to user
    expect(parseComments('[{"id":"c-1","text":""}]'), isEmpty);
    expect(parseComments('[{"text":"x","author":"martian"}]').first.author, 'user');
  });

  test('serializeComments returns null for empty (column clears)', () {
    expect(serializeComments(const []), isNull);
  });
}
```

- [ ] **Step 2: Run to verify it fails** — `cd mobile && flutter test test/models/comment_test.dart` → FAIL (no such file).

- [ ] **Step 3: Implement** `mobile/lib/models/comment.dart` (mirror `subtask.dart`'s structure and doc style):

```dart
import 'dart:convert';

import '../local/uuid.dart';

/// One comment in a task's thread. Lives in the `task_cache.comments` TEXT
/// column (and the server's encrypted `tasks.comments`) as a JSON array:
/// `[{"id","ts","author","text","subtask_id"}]` — the canonical shape the
/// server's `add_comment` emits. `subtask_id` null = task-level comment.
class TaskComment {
  final String id;
  final String ts;      // ISO-8601 UTC
  final String author;  // 'user' | 'agent'
  final String text;
  final String? subtaskId;

  const TaskComment({
    required this.id,
    required this.ts,
    required this.author,
    required this.text,
    this.subtaskId,
  });

  TaskComment copyWith({String? id, String? ts, String? author, String? text,
      String? subtaskId}) =>
      TaskComment(
        id: id ?? this.id,
        ts: ts ?? this.ts,
        author: author ?? this.author,
        text: text ?? this.text,
        subtaskId: subtaskId ?? this.subtaskId,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'ts': ts,
        'author': author,
        'text': text,
        'subtask_id': subtaskId,
      };

  static TaskComment? fromMap(Map<dynamic, dynamic> map) {
    final text = (map['text'] ?? '').toString().trim();
    if (text.isEmpty) return null;
    final author = (map['author'] ?? '').toString();
    final rawId = (map['id'] ?? '').toString().trim();
    final sub = (map['subtask_id'] ?? '').toString().trim();
    return TaskComment(
      id: rawId.isEmpty ? newCommentId() : rawId,
      ts: (map['ts'] ?? '').toString(),
      author: author == 'agent' ? 'agent' : 'user',
      text: text,
      subtaskId: sub.isEmpty ? null : sub,
    );
  }
}

/// Mint a stable client-side comment id (replays idempotently to the server).
String newCommentId() => 'c-${uuidV4()}';

/// Parse the `comments` column JSON. Tolerant: `[]` on null/garbage.
List<TaskComment> parseComments(String? raw) {
  if (raw == null) return const [];
  final trimmed = raw.trim();
  if (trimmed.isEmpty) return const [];
  dynamic decoded;
  try {
    decoded = jsonDecode(trimmed);
  } catch (_) {
    return const [];
  }
  if (decoded is! List) return const [];
  final out = <TaskComment>[];
  for (final entry in decoded) {
    if (entry is Map) {
      final c = TaskComment.fromMap(entry);
      if (c != null) out.add(c);
    }
  }
  return out;
}

/// Serialise back to the column JSON. Empty list → null (column clears).
String? serializeComments(List<TaskComment> comments) {
  if (comments.isEmpty) return null;
  return jsonEncode(comments.map((c) => c.toJson()).toList());
}
```

In `mobile/lib/models/task.dart`: add `final String? comments;` beside `steps` (constructor param `this.comments`), `comments: _str(json['comments'])` in `fromJson`, `'comments': comments` in `toJson`, `String? comments` param in `copyWith` (`comments: comments ?? this.comments`), and beside the `subtasks` getter:

```dart
/// The comment thread parsed from the `comments` JSON column.
List<TaskComment> get taskComments => parseComments(comments);
```
with `import 'comment.dart';` at the top.

- [ ] **Step 4: Run tests** — `flutter test test/models/comment_test.dart test/models/` → PASS (existing model tests stay green).

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/models/comment.dart mobile/lib/models/task.dart mobile/test/models/comment_test.dart
git commit -m "feat(mobile): TaskComment model + tolerant codec + Task.comments field"
```

---

### Task 6: Mobile — DB v12 migration, DAO comment ops, `ui_prefs` table

**Files:**
- Modify: `mobile/lib/local/app_db.dart` (`kAppDbVersion` → 12 + doc line; `task_cache` DDL + new `ui_prefs` DDL in `kAppDbSchema`; `migrateAppDb` branch)
- Modify: `mobile/lib/local/task_dao.dart` (`OutboxOp` constants; `_rowFromTask`/`_taskFromRow` add `comments`; new `applyLocalAddComment`/`applyLocalDeleteComment`)
- Create: `mobile/lib/local/ui_prefs_dao.dart`
- Test: `mobile/test/local/app_db_migration_v12_test.dart`, `mobile/test/local/task_dao_comments_test.dart`, `mobile/test/local/ui_prefs_dao_test.dart` (all new)

**Interfaces:**
- Produces:
  - `kAppDbVersion = 12`; `task_cache.comments TEXT`; table `ui_prefs (key TEXT PRIMARY KEY, value TEXT)`
  - `OutboxOp.commentAdd = 'comment_add'`, `OutboxOp.commentDelete = 'comment_delete'`
  - `Future<Task?> TaskDao.applyLocalAddComment(String taskId, TaskComment comment)` — appends to the cache column, sets `dirty=1`, enqueues `comment_add` with payload `{'id': taskId, 'comment': comment.toJson()}`
  - `Future<Task?> TaskDao.applyLocalDeleteComment(String taskId, String commentId)` — removes from cache, `dirty=1`, enqueues `comment_delete` with payload `{'id': taskId, 'comment_id': commentId}`
  - `class UiPrefsDao { UiPrefsDao(Database db); Future<String?> get(String key); Future<void> set(String key, String value); Future<bool> getBool(String key, {bool fallback = false}); Future<void> setBool(String key, bool value); Future<Set<String>> getStringSet(String key); Future<void> setStringSet(String key, Set<String> values); }`

- [ ] **Step 1: Write the failing tests.**

`mobile/test/local/app_db_migration_v12_test.dart` — copy the structure of `app_db_migration_v11_test.dart` verbatim, adjusted: `openV11Shaped()` creates `task_cache` WITHOUT `comments` and no `ui_prefs` table, seeds one task row; assert `kAppDbVersion >= 12`; fresh `createAppDbSchema` has `task_cache.comments` + a `ui_prefs` table; `await migrateAppDb(db, 11, 12)` adds the column, creates `ui_prefs`, and the seeded row survives.

`mobile/test/local/task_dao_comments_test.dart` (ffi in-memory, same bootstrap as existing DAO tests — `sqfliteFfiInit(); databaseFactory = databaseFactoryFfi;` + `createAppDbSchema`):

```dart
test('applyLocalAddComment appends, dirties, enqueues comment_add', () async {
  final created = await dao.applyLocalCreate('with thread');
  final c = TaskComment(
      id: 'c-t1', ts: '2026-08-02T10:00:00Z', author: 'user', text: 'hi');
  final updated = await dao.applyLocalAddComment(created.id, c);
  expect(updated!.taskComments.map((x) => x.text), ['hi']);
  expect(await dao.dirtyIds(), contains(created.id));
  final ops = await dao.pendingOutbox();
  final op = ops.lastWhere((o) => o.op == OutboxOp.commentAdd);
  expect(op.entityId, created.id);
  expect(op.payload['comment']['id'], 'c-t1');
});

test('applyLocalDeleteComment removes and enqueues comment_delete', () async {
  final created = await dao.applyLocalCreate('t');
  await dao.applyLocalAddComment(created.id, TaskComment(
      id: 'c-t2', ts: '2026-08-02T10:00:00Z', author: 'user', text: 'bye'));
  final after = await dao.applyLocalDeleteComment(created.id, 'c-t2');
  expect(after!.taskComments, isEmpty);
  final ops = await dao.pendingOutbox();
  expect(ops.last.op, OutboxOp.commentDelete);
  expect(ops.last.payload['comment_id'], 'c-t2');
});

test('add to a missing task returns null and enqueues nothing', () async {
  expect(await dao.applyLocalAddComment('ghost', TaskComment(
      id: 'c-x', ts: '', author: 'user', text: 'x')), isNull);
});
```

(If the outbox-listing method has a different name than `pendingOutbox`, use the one the existing `task_dao` tests use — do not invent a second one.)

`mobile/test/local/ui_prefs_dao_test.dart`: set/get round-trip, `getBool` fallback on absent key, `getStringSet`/`setStringSet` JSON round-trip, overwrite replaces.

- [ ] **Step 2: Run to verify they fail** — `flutter test test/local/app_db_migration_v12_test.dart test/local/task_dao_comments_test.dart test/local/ui_prefs_dao_test.dart` → FAIL.

- [ ] **Step 3: Implement.**

`app_db.dart`: bump `kAppDbVersion = 12` with doc line `/// v12: adds task_cache.comments (comment-thread JSON) + the ui_prefs KV table (persisted collapse/hide-completed UI state).`; add `comments TEXT,` to the `task_cache` DDL after `steps TEXT,`; append to `kAppDbSchema`:

```dart
'''
CREATE TABLE IF NOT EXISTS ui_prefs (
  key TEXT PRIMARY KEY,
  value TEXT
)
''',
```

and the migration branch (follow the v11 branch's try/catch duplicate-column tolerance):

```dart
if (oldVersion < 12) {
  try {
    await db.execute('ALTER TABLE task_cache ADD COLUMN comments TEXT');
  } on DatabaseException catch (e) {
    if (!_isDuplicateColumn(e)) rethrow;  // match the existing helper/pattern
  }
  await db.execute(
      'CREATE TABLE IF NOT EXISTS ui_prefs (key TEXT PRIMARY KEY, value TEXT)');
}
```

`task_dao.dart`: add the two `OutboxOp` constants; map `comments` in `_rowFromTask`/`_taskFromRow` exactly as `steps` is mapped; then:

```dart
/// Append one comment locally + enqueue a `comment_add`. The op replays as a
/// server-side APPEND (idempotent by comment id), so offline comments from two
/// devices merge instead of last-write-wins clobbering.
Future<Task?> applyLocalAddComment(String taskId, TaskComment comment) async {
  final existing = await getById(taskId);
  if (existing == null) return null;
  final now = _now();
  final next = serializeComments([...existing.taskComments, comment]);
  await _db.transaction((txn) async {
    await txn.update(
      'task_cache',
      {'comments': next, 'updated_at': now, 'dirty': 1},
      where: 'id = ?',
      whereArgs: [taskId],
    );
    await _enqueueTxn(txn, OutboxOp.commentAdd, taskId,
        {'id': taskId, 'comment': comment.toJson()}, now);
  });
  return existing.copyWith(comments: next);
}

/// Remove one comment locally + enqueue a `comment_delete` (idempotent).
Future<Task?> applyLocalDeleteComment(String taskId, String commentId) async {
  final existing = await getById(taskId);
  if (existing == null) return null;
  final now = _now();
  final remaining =
      [for (final c in existing.taskComments) if (c.id != commentId) c];
  final next = serializeComments(remaining);
  await _db.transaction((txn) async {
    await txn.update(
      'task_cache',
      {'comments': next, 'updated_at': now, 'dirty': 1},
      where: 'id = ?',
      whereArgs: [taskId],
    );
    await _enqueueTxn(txn, OutboxOp.commentDelete, taskId,
        {'id': taskId, 'comment_id': commentId}, now);
  });
  return existing.copyWith(comments: next);
}
```

Caveat: `Task.copyWith(comments: null)` can't clear — when `next` is null (last comment deleted) build the return via `Task.fromJson({...existing.toJson(), 'comments': null})` and write the cache column with an explicit `'comments': next` map entry (sqflite writes NULL fine). Follow whichever null-clearing convention `_fieldUpdates` already uses.

`ui_prefs_dao.dart`:

```dart
import 'dart:convert';

import 'package:sqflite_sqlcipher/sqflite.dart';

/// Tiny KV store for client-local UI state (collapse/expand, hide-completed).
/// Deliberately NOT synced — this is per-device preference, not user data.
class UiPrefsDao {
  final Database _db;
  UiPrefsDao(this._db);

  Future<String?> get(String key) async {
    final rows = await _db.query('ui_prefs',
        where: 'key = ?', whereArgs: [key], limit: 1);
    return rows.isEmpty ? null : rows.first['value'] as String?;
  }

  Future<void> set(String key, String value) => _db.insert(
      'ui_prefs', {'key': key, 'value': value},
      conflictAlgorithm: ConflictAlgorithm.replace);

  Future<bool> getBool(String key, {bool fallback = false}) async =>
      switch (await get(key)) { '1' => true, '0' => false, _ => fallback };

  Future<void> setBool(String key, bool value) => set(key, value ? '1' : '0');

  Future<Set<String>> getStringSet(String key) async {
    final raw = await get(key);
    if (raw == null || raw.isEmpty) return <String>{};
    try {
      final decoded = jsonDecode(raw);
      if (decoded is List) return decoded.map((e) => e.toString()).toSet();
    } catch (_) {}
    return <String>{};
  }

  Future<void> setStringSet(String key, Set<String> values) =>
      set(key, jsonEncode(values.toList()));
}
```

- [ ] **Step 4: Run tests** — the three new files + `flutter test test/local/` (existing DAO/migration suites must stay green) → PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/local/app_db.dart mobile/lib/local/task_dao.dart mobile/lib/local/ui_prefs_dao.dart mobile/test/local/app_db_migration_v12_test.dart mobile/test/local/task_dao_comments_test.dart mobile/test/local/ui_prefs_dao_test.dart
git commit -m "feat(mobile): v12 schema (comments column + ui_prefs), DAO comment ops"
```

---

### Task 7: Mobile — repository + sync push + provider methods for comments

**Files:**
- Modify: `mobile/lib/repositories/tasks_repository.dart` (two methods)
- Modify: `mobile/lib/sync/task_sync.dart` (`_pushOne` cases ~line 200; 404-idempotency in `_classifyPushFailure` ~line 267)
- Modify: `mobile/lib/providers/tasks_provider.dart` (two notifier methods)
- Test: `mobile/test/sync/task_sync_comments_test.dart` (new; mirror the fixture/fake-repo pattern of the existing `mobile/test/sync/` task-sync tests)

**Interfaces:**
- Produces:
  - `TasksRepository.addComment(String taskId, Map<String, dynamic> body)` → `postJson('/api/tasks/$taskId/comments', body)`
  - `TasksRepository.deleteComment(String taskId, String commentId)` → `deleteJson('/api/tasks/$taskId/comments/$commentId')`
  - `TasksNotifier.addComment(String taskId, String text, {String? subtaskId})` and `TasksNotifier.deleteComment(String taskId, String commentId)`
- Consumes: Task 6's DAO ops + OutboxOp constants; Task 3's endpoints; `newCommentId()` from Task 5.

- [ ] **Step 1: Write the failing tests** (`test/sync/task_sync_comments_test.dart`):
  - `comment_add op pushes POST body {id, text, subtask_id}` — enqueue via `dao.applyLocalAddComment`, run `sync.push()`, assert the fake repo captured `addComment('t1', {'id': 'c-t1', 'text': 'hi', 'subtask_id': null})` and the outbox drained.
  - `comment_delete 404 is idempotent success` — fake repo throws the suite's standard 404 ApiError from `deleteComment`; assert `push()` retires the op (no dead-letter, no crash).
  - `comment_add on a 404'd task drains without retry` (task deleted server-side → definitive 4xx path).

- [ ] **Step 2: Run to verify they fail** — `flutter test test/sync/task_sync_comments_test.dart` → FAIL (no `addComment` on repo / unknown op ignored).

- [ ] **Step 3: Implement.**

`tasks_repository.dart`:

```dart
Future<Map<String, dynamic>> addComment(
        String taskId, Map<String, dynamic> body) =>
    _api.postJson('/api/tasks/$taskId/comments', body);

Future<void> deleteComment(String taskId, String commentId) async {
  await _api.deleteJson('/api/tasks/$taskId/comments/$commentId');
}
```

(match the class's actual field name for the API client and return conventions of its siblings).

`task_sync.dart` — in `_pushOne`'s switch, before `default`:

```dart
case OutboxOp.commentAdd:
  final c = Map<String, dynamic>.from((p['comment'] as Map?) ?? {});
  await _repo.addComment(item.entityId, {
    'id': c['id'],
    'text': c['text'],
    'subtask_id': c['subtask_id'],
  });
  break;
case OutboxOp.commentDelete:
  await _repo.deleteComment(
      item.entityId, (p['comment_id'] ?? '').toString());
  break;
```

and extend the 404-idempotency condition in `_classifyPushFailure`:

```dart
if (status == 404 &&
    (item.op == OutboxOp.delete ||
        item.op == OutboxOp.complete ||
        item.op == OutboxOp.commentDelete)) {
```

(a 404 on `commentAdd` stays on the definitive-4xx drain path: the task is gone server-side and the next pull delivers its tombstone.)

`tasks_provider.dart` — beside `setSubtasks`:

```dart
/// Append a comment (author=user) optimistically + queue the server append.
Future<void> addComment(String taskId, String text,
    {String? subtaskId}) async {
  final trimmed = text.trim();
  if (trimmed.isEmpty) return;
  try {
    final comment = TaskComment(
      id: newCommentId(),
      ts: DateTime.now().toUtc().toIso8601String(),
      author: 'user',
      text: trimmed,
      subtaskId: subtaskId,
    );
    await _dao.applyLocalAddComment(taskId, comment);
    await _refreshFromCache();
    unawaited(_syncThenRefresh());
  } catch (e) {
    state = state.copyWith(error: e.toString());
  }
}

Future<void> deleteComment(String taskId, String commentId) async {
  try {
    await _dao.applyLocalDeleteComment(taskId, commentId);
    await _refreshFromCache();
    unawaited(_syncThenRefresh());
  } catch (e) {
    state = state.copyWith(error: e.toString());
  }
}
```

- [ ] **Step 4: Run tests** — `flutter test test/sync/ test/local/task_dao_comments_test.dart` → PASS (whole sync suite green).

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/repositories/tasks_repository.dart mobile/lib/sync/task_sync.dart mobile/lib/providers/tasks_provider.dart mobile/test/sync/task_sync_comments_test.dart
git commit -m "feat(mobile): comment add/delete through outbox sync + provider"
```

---

### Task 8: Mobile — done-last sort utility, applied to Projects view, calendar, subtasks

**Files:**
- Create: `mobile/lib/screens/tasks/task_sort.dart`
- Modify: `mobile/lib/screens/tasks/tasks_project_view.dart` (`_ProjectBucket.build`, task loop ~line 357)
- Modify: `mobile/lib/screens/tasks/task_calendar_utils.dart` (`groupTasksByDay` sorts each day bucket)
- Modify: `mobile/lib/screens/tasks/subtask_editor.dart` (render order only, ~line 61)
- Test: `mobile/test/screens/task_sort_test.dart` (new)

**Interfaces:**
- Produces:
  - `List<Task> sortDoneLast(List<Task> tasks)` — stable partition, pending (`!isDone`) first in existing relative order, done after in existing relative order
  - `List<Subtask> sortSubtasksDoneLast(List<Subtask> subtasks)` — same for checklists

- [ ] **Step 1: Write the failing tests** (`test/screens/task_sort_test.dart`; build minimal `Task`s with only required fields):

```dart
test('sortDoneLast is a stable partition', () {
  Task t(String id, String status) => Task(
      id: id, userId: '', title: id, priority: 'medium', status: status,
      owner: 'user', nagCount: 0, createdAt: '2026-01-01');
  final input = [t('d1', 'done'), t('p1', 'todo'), t('d2', 'done'),
      t('p2', 'in_progress'), t('p3', 'todo')];
  expect(sortDoneLast(input).map((x) => x.id).toList(),
      ['p1', 'p2', 'p3', 'd1', 'd2']);
  expect(input.map((x) => x.id).toList(),
      ['d1', 'p1', 'd2', 'p2', 'p3']); // input untouched (immutability)
});

test('sortSubtasksDoneLast partitions and preserves order', () {
  Subtask s(String id, bool done) => Subtask(id: id, title: id, done: done);
  final input = [s('a', true), s('b', false), s('c', true), s('d', false)];
  expect(sortSubtasksDoneLast(input).map((x) => x.id).toList(),
      ['b', 'd', 'a', 'c']);
});
```

- [ ] **Step 2: Run to verify it fails** — `flutter test test/screens/task_sort_test.dart` → FAIL.

- [ ] **Step 3: Implement** `task_sort.dart`:

```dart
import '../../models/subtask.dart';
import '../../models/task.dart';

/// Stable done-last partition: pending tasks first (original relative order),
/// completed after (original relative order). Returns a NEW list — display
/// ordering only, never a storage rewrite.
List<Task> sortDoneLast(List<Task> tasks) => [
      for (final t in tasks) if (!t.isDone) t,
      for (final t in tasks) if (t.isDone) t,
    ];

/// The checklist equivalent: unchecked sub-tasks first, checked sink to the
/// bottom. Display-only — the stored `steps` array order is never rewritten,
/// so unticking restores the item's original position.
List<Subtask> sortSubtasksDoneLast(List<Subtask> subtasks) => [
      for (final s in subtasks) if (!s.done) s,
      for (final s in subtasks) if (s.done) s,
    ];
```

Apply:
- `tasks_project_view.dart` (`_ProjectBucket.build`): introduce `final ordered = sortDoneLast(tasks);` and iterate `ordered[i]` in the expanded task loop (the header's `_CountBadge` keeps using the unsorted `tasks` counts).
- `task_calendar_utils.dart`: in `groupTasksByDay`, sort each day's bucket with `sortDoneLast(...)` before returning.
- `subtask_editor.dart`: `for (final s in sortSubtasksDoneLast(subtasks))` in `build` — the mutation callbacks (`_toggle`/`_editText`/`_delete`/`_add`) keep operating on the canonical `subtasks` list by id, so stored order is untouched.

- [ ] **Step 4: Run tests + analyze** — `flutter test test/screens/task_sort_test.dart test/screens/ && flutter analyze` → PASS, no new analyzer issues.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/screens/tasks/task_sort.dart mobile/lib/screens/tasks/tasks_project_view.dart mobile/lib/screens/tasks/task_calendar_utils.dart mobile/lib/screens/tasks/subtask_editor.dart mobile/test/screens/task_sort_test.dart
git commit -m "feat(mobile): completed tasks and subtasks sink to the bottom (display-only)"
```

---

### Task 9: Mobile — persisted Projects-view expansion + Hide-completed toggle

**Files:**
- Create: `mobile/lib/providers/ui_prefs_provider.dart`
- Modify: `mobile/lib/screens/tasks/tasks_project_view.dart` (constructor params + state init/persist + filter)
- Modify: `mobile/lib/screens/tasks_screen.dart` (load prefs, pass to `TasksProjectView`, persist on change)
- Test: `mobile/test/screens/tasks_project_view_prefs_test.dart` (new, widget test with plain callbacks — NO real DB)

**Interfaces:**
- Produces:
  - `final uiPrefsDaoProvider = Provider<UiPrefsDao>((ref) => UiPrefsDao(ref.watch(appDatabaseProvider)));` (same wiring as `TaskDao` in `tasks_provider.dart:37`)
  - Pref keys (constants in `ui_prefs_provider.dart`): `kPrefProjectsExpanded = 'tasks.projects.expanded'`, `kPrefProjectsHideCompleted = 'tasks.projects.hideCompleted'`, `kPrefListSectionCollapsed(String section) => 'tasks.list.$section.collapsed'`
  - `TasksProjectView` new params: `initialExpanded: Set<String>`, `onExpandedChanged: ValueChanged<Set<String>>?`, `hideCompleted: bool`, `onHideCompletedChanged: ValueChanged<bool>?`

- [ ] **Step 1: Write the failing widget test** — pump `TasksProjectView` directly (it's a plain StatefulWidget; existing screen tests show the harness) with two projects + tasks incl. done ones:
  - passing `initialExpanded: {'Errands'}` renders the Errands bucket expanded on first frame;
  - tapping a bucket header fires `onExpandedChanged` with the updated set;
  - `hideCompleted: true` removes done task rows from an expanded bucket but the header badge still shows `open/total`;
  - an eye toggle is present and fires `onHideCompletedChanged`.

- [ ] **Step 2: Run to verify it fails** — `flutter test test/screens/tasks_project_view_prefs_test.dart` → FAIL (unknown params).

- [ ] **Step 3: Implement.**
  - `ui_prefs_provider.dart` with the provider + the three key constants above.
  - `tasks_project_view.dart`: add the four params (defaults: `initialExpanded = const <String>{}`, `hideCompleted = false`, callbacks nullable). `_TasksProjectViewState`: `late final Set<String> _expanded = {...widget.initialExpanded};`; `_toggle` additionally calls `widget.onExpandedChanged?.call({..._expanded});`. Filter inside `_ProjectBucket` (add a `hideCompleted` field): expanded body uses `final visible = hideCompleted ? [for (final t in ordered) if (!t.isDone) t] : ordered;` — badge counts stay computed from the full list. Eye toggle: in the "Projects" `LzSection` action row, an `Icon(hideCompleted ? Icons.visibility_off_outlined : Icons.visibility_outlined, size: 20)` wrapped in a `GestureDetector` calling `onHideCompletedChanged`.
  - `tasks_screen.dart`: in the screen's state, load once on init (`UiPrefsDao` via `ref.read(uiPrefsDaoProvider)`): `_projectsExpanded = await prefs.getStringSet(kPrefProjectsExpanded)`, `_hideCompleted = await prefs.getBool(kPrefProjectsHideCompleted)` (a `setState` after the async load; empty defaults render meanwhile). Pass them into `TasksProjectView` and persist in the callbacks (`unawaited(prefs.setStringSet(...))` / `setBool`). Because the view now receives `initialExpanded`, give `TasksProjectView` a `ValueKey` that does NOT change per rebuild so its state survives view toggling; state restore across screen mounts comes from the persisted set.

- [ ] **Step 4: Run tests** — new test + `flutter test test/screens/` → PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/providers/ui_prefs_provider.dart mobile/lib/screens/tasks/tasks_project_view.dart mobile/lib/screens/tasks_screen.dart mobile/test/screens/tasks_project_view_prefs_test.dart
git commit -m "feat(mobile): persisted project expansion + hide-completed toggle"
```

---

### Task 10: Mobile — List view: all sections collapsible + persisted

**Files:**
- Modify: `mobile/lib/screens/tasks_screen.dart` (`_TaskSection` ~line 736-861)
- Test: `mobile/test/screens/task_section_collapse_test.dart` (new)

**Interfaces:**
- Produces: `_TaskSection` gains `initialCollapsed: bool` and `onCollapsedChanged: ValueChanged<bool>?`; the chevron renders for EVERY section (drop the `if (widget.section == _Section.done)` guard at ~line 842); defaults preserved (Overdue/Today/Upcoming expanded, Done collapsed).
- Consumes: Task 9's `uiPrefsDaoProvider` + `kPrefListSectionCollapsed(section.name)`.

- [ ] **Step 1: Write the failing widget test** — pump the list view section widget: every section shows a chevron; tapping Today's chevron hides its rows and fires `onCollapsedChanged(true)`; Done still starts collapsed when `initialCollapsed` not supplied.

- [ ] **Step 2: Run to verify it fails.** (`_TaskSection` is private — test through the screen, or make the section widget public as `TaskSection` in the same file if the screen harness is too heavy; prefer the smallest change that lets the test pump it.)

- [ ] **Step 3: Implement:** thread `initialCollapsed` (screen state loads all four `kPrefListSectionCollapsed(...)` values in the same init pass as Task 9, defaulting Done→true, others→false), replace `_collapsed = widget.section == _Section.done;` with `_collapsed = widget.initialCollapsed;`, un-gate the chevron, call `widget.onCollapsedChanged?.call(_collapsed)` inside the toggle's `setState`, and persist from the screen callback.

- [ ] **Step 4: Run tests** — new test + `flutter test test/screens/` → PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/screens/tasks_screen.dart mobile/test/screens/task_section_collapse_test.dart
git commit -m "feat(mobile): collapsible Overdue/Today/Upcoming sections with persisted state"
```

---

### Task 11: Mobile — `LinkText` widget (bare URLs + `[text](url)`)

**Files:**
- Create: `mobile/lib/widgets/link_text.dart`
- Test: `mobile/test/widgets/link_text_test.dart` (new)

**Interfaces:**
- Produces:
  - `class LinkText extends StatefulWidget { const LinkText(this.text, {super.key, this.style, this.onOpen}); final String text; final TextStyle? style; final Future<void> Function(Uri uri)? onOpen; }` — `onOpen` defaults to `launchUrl(uri, mode: LaunchMode.externalApplication)`; injectable for tests.
  - Exposed pure helper for tests: `List<LinkSpanToken> tokenizeLinks(String text)` where `class LinkSpanToken { final String text; final String? url; }` (url null = plain text).
- Parsing rules (from `univer_links.dart` precedents): named links `RegExp(r'\[([^\]]+)\]\((https?://[^\s)]+)\)')` matched first; then bare URLs `RegExp(r'https?://[^\s<>"]+')` in the remaining plain segments, with trailing `.,;:!?)` trimmed off the match (trimmed chars stay as plain text).

- [ ] **Step 1: Write the failing tests:**

```dart
test('tokenizeLinks handles named links, bare urls, trailing punctuation', () {
  expect(
    tokenizeLinks('see [docs](https://a.io/d) or https://b.io/x, ok'),
    [
      LinkSpanToken('see ', null),
      LinkSpanToken('docs', 'https://a.io/d'),
      LinkSpanToken(' or ', null),
      LinkSpanToken('https://b.io/x', 'https://b.io/x'),
      LinkSpanToken(', ok', null),
    ],
  );
  expect(tokenizeLinks('no links here'),
      [LinkSpanToken('no links here', null)]);
});

testWidgets('tapping a link span invokes onOpen with the Uri', (tester) async {
  Uri? opened;
  await tester.pumpWidget(MaterialApp(
      home: LinkText('go https://a.io/d now',
          onOpen: (u) async => opened = u)));
  // Fire the link span's recognizer directly (span taps aren't hit-testable
  // by widget predicates).
  final rich = tester.widget<Text>(find.byType(Text).first);
  final root = rich.textSpan as TextSpan;
  final linkSpan = root.children!
      .whereType<TextSpan>()
      .firstWhere((s) => s.recognizer != null);
  (linkSpan.recognizer as TapGestureRecognizer).onTap!();
  expect(opened, Uri.parse('https://a.io/d'));
});
```

(give `LinkSpanToken` `==`/`hashCode`/`toString` so the list equality works.)

- [ ] **Step 2: Run to verify it fails** — `flutter test test/widgets/link_text_test.dart` → FAIL.

- [ ] **Step 3: Implement:** stateful widget owning a `final List<TapGestureRecognizer> _recognizers = [];` (dispose all in `dispose()`, rebuild in `build` after clearing). Render `Text.rich(TextSpan(children: [...]), style: widget.style)`; link tokens get `AppColors.accent` + underline and a recognizer calling `_open(url)`:

```dart
Future<void> _open(String url) async {
  final uri = Uri.tryParse(url);
  if (uri == null) return;
  try {
    await (widget.onOpen?.call(uri) ??
        launchUrl(uri, mode: LaunchMode.externalApplication));
  } catch (_) {
    if (mounted) {
      ScaffoldMessenger.maybeOf(context)?.showSnackBar(
          const SnackBar(content: Text('Could not open link.')));
    }
  }
}
```

- [ ] **Step 4: Run tests** — `flutter test test/widgets/link_text_test.dart` → PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/widgets/link_text.dart mobile/test/widgets/link_text_test.dart
git commit -m "feat(mobile): LinkText widget - tappable bare URLs and [text](url) links"
```

---

### Task 12: Mobile — Notes read-only preview + "Add link" dialog; subtask titles linkified

**Files:**
- Create: `mobile/lib/screens/tasks/add_link_dialog.dart`
- Modify: `mobile/lib/screens/tasks/task_detail_sheet.dart` (Notes block ~line 445-454)
- Modify: `mobile/lib/screens/tasks/subtask_editor.dart` (read-only title ~line 208-222)
- Test: `mobile/test/screens/add_link_dialog_test.dart`, extend `mobile/test/screens/` detail-sheet coverage

**Interfaces:**
- Produces: `Future<String?> showAddLinkDialog(BuildContext context)` — dialog with "Text" + "URL" fields; returns `'[text](url)'` (URL must match `https?://`, else inline error) or null on cancel.
- Detail sheet behavior: when notes are non-empty AND not being edited → render `LinkText(notes)` preview (tap non-link → switches to the existing `LzTextField` editor and focuses it); empty notes → editor directly (today's behavior). An "Add link" affordance (`Icons.add_link`) sits beside the Notes editor and inserts the returned markdown at the cursor of `_notesController`.
- SubtaskEditor: the read-only title `Text` (line 212) becomes `LinkText(widget.subtask.title, style: <same done/pending style>)` wrapped so that taps on NON-link text still call `_beginEdit` (keep the outer `GestureDetector`; `LinkText`'s recognizers win on link spans automatically).

- [ ] **Step 1: Write the failing tests:**
  - dialog: enter text `docs` + url `https://a.io` → pops with `'[docs](https://a.io)'`; url `notaurl` → error text shown, no pop; **dialog buttons must pop the DIALOG's own context** (over-sheet freeze gotcha — assert the sheet under it survives).
  - detail sheet: pumping the sheet with `description: 'see https://a.io'` renders a `LinkText` (find.byType) instead of a notes `TextField`; tapping the preview swaps in the `TextField` with the text preserved.

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement.** `add_link_dialog.dart`:

```dart
Future<String?> showAddLinkDialog(BuildContext context) {
  final textCtrl = TextEditingController();
  final urlCtrl = TextEditingController();
  return showDialog<String>(
    context: context,
    builder: (dialogCtx) {
      String? urlError;
      return StatefulBuilder(builder: (dialogCtx, setState) {
        return AlertDialog(
          title: const Text('Add link'),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            TextField(
                key: const Key('add-link-text'),
                controller: textCtrl,
                decoration: const InputDecoration(labelText: 'Text')),
            TextField(
                key: const Key('add-link-url'),
                controller: urlCtrl,
                keyboardType: TextInputType.url,
                decoration: InputDecoration(
                    labelText: 'URL', errorText: urlError)),
          ]),
          actions: [
            TextButton(
              // Pop the DIALOG's context, never the sheet's — a wrong ctx
              // here freezes the sheet underneath (documented gotcha).
              onPressed: () => Navigator.of(dialogCtx).pop(),
              child: const Text('Cancel'),
            ),
            TextButton(
              key: const Key('add-link-insert'),
              onPressed: () {
                final url = urlCtrl.text.trim();
                if (!RegExp(r'^https?://\S+$').hasMatch(url)) {
                  setState(() => urlError = 'Enter a full http(s):// URL');
                  return;
                }
                final label =
                    textCtrl.text.trim().isEmpty ? url : textCtrl.text.trim();
                Navigator.of(dialogCtx).pop('[$label]($url)');
              },
              child: const Text('Insert'),
            ),
          ],
        );
      });
    },
  );
}
```

Detail sheet: add `bool _editingNotes = false;` initialized to `widget.task.description?.trim().isNotEmpty != true` (empty notes open straight in the editor). Replace the Notes `LzTextField` block with the preview/editor switch + a small trailing `IconButton(Icons.add_link)` visible in editor mode that inserts `showAddLinkDialog`'s result at `_notesController.selection` (append when there's no valid selection). The save path (`_notesController.text` at line 349) is untouched.

- [ ] **Step 4: Run tests + analyze** — new tests + `flutter test test/screens/ && flutter analyze` → PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/screens/tasks/add_link_dialog.dart mobile/lib/screens/tasks/task_detail_sheet.dart mobile/lib/screens/tasks/subtask_editor.dart mobile/test/screens/add_link_dialog_test.dart
git commit -m "feat(mobile): notes read-only preview, Add-link dialog, linkified subtask titles"
```

---

### Task 13: Mobile — Comments UI (detail-sheet thread + subtask mini-threads)

**Files:**
- Create: `mobile/lib/screens/tasks/task_comments_section.dart` (thread list + input row + the subtask comments bottom sheet)
- Modify: `mobile/lib/screens/tasks/task_detail_sheet.dart` (mount the section after Subtasks; wire subtask 💬)
- Modify: `mobile/lib/screens/tasks/subtask_editor.dart` (optional 💬 badge)
- Test: `mobile/test/screens/task_comments_section_test.dart` (new; plain callbacks, no DB/providers)

**Interfaces:**
- Produces:
  - `class TaskCommentsSection extends StatelessWidget { const TaskCommentsSection({required this.comments, required this.onAdd, required this.onDelete, this.onAddLink}); final List<TaskComment> comments; final ValueChanged<String> onAdd; final ValueChanged<String> onDelete; final Future<String?> Function()? onAddLink; }` — renders task-level comments only (`subtaskId == null`), oldest-first: author label (`You` / `Lazy 🤖`) + relative timestamp + `LinkText(text)`; long-press → delete confirm (confirm dialog pops its OWN context); input row (`TextField` + send icon + add-link icon).
  - `Future<void> showSubtaskCommentsSheet(BuildContext context, {required String subtaskTitle, required List<TaskComment> comments, required ValueChanged<String> onAdd, required ValueChanged<String> onDelete})` — an `LzBottomSheet` with the same thread UI filtered to one subtask.
  - `SubtaskEditor` new optional params: `commentCounts: Map<String, int>` (default const {}), `onOpenComments: ValueChanged<String>?` — when a count > 0 or the callback is set, each tile shows a small 💬+count between title and delete.
- Consumes: Task 7's `TasksNotifier.addComment/deleteComment`; Task 11's `LinkText`; Task 12's `showAddLinkDialog`.
- Live data: the detail sheet is snapshot-based (`widget.task`), so it watches the provider for the fresh row: `final live = ref.watch(tasksProvider).tasks.where((t) => t.id == widget.task.id).firstOrNull ?? widget.task;` and feeds `live.taskComments` to the section — comments appear instantly after `addComment` (optimistic cache refresh) without waiting for Save.

- [ ] **Step 1: Write the failing widget tests** — section renders authors/text through `LinkText`; submitting the input fires `onAdd('typed text')` and clears the field; long-press → confirm → `onDelete(id)`; subtask filtering (a comment with `subtaskId` set does NOT render in the task-level section).

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement** the section + sheet (LzSection/AppText/AppColors styling like SubtaskEditor), mount in the detail sheet after the Subtasks block:

```dart
_SectionLabel('COMMENTS'),
const SizedBox(height: AppSpacing.sm),
TaskCommentsSection(
  comments: live.taskComments,
  onAdd: (text) => ref
      .read(tasksProvider.notifier)
      .addComment(widget.task.id, text),
  onDelete: (cid) => ref
      .read(tasksProvider.notifier)
      .deleteComment(widget.task.id, cid),
  onAddLink: () => showAddLinkDialog(context),
),
```

and wire the subtask badge: detail sheet computes `commentCounts` from `live.taskComments` (`{for (final s in _subtasks) s.id: live.taskComments.where((c) => c.subtaskId == s.id).length}`) and passes `onOpenComments: (sid) => showSubtaskCommentsSheet(...)` with `onAdd: (text) => notifier.addComment(widget.task.id, text, subtaskId: sid)`.

- [ ] **Step 4: Run tests** — `flutter test test/screens/task_comments_section_test.dart test/screens/` → PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/screens/tasks/task_comments_section.dart mobile/lib/screens/tasks/task_detail_sheet.dart mobile/lib/screens/tasks/subtask_editor.dart mobile/test/screens/task_comments_section_test.dart
git commit -m "feat(mobile): comment threads on tasks and subtasks"
```

---

### Task 14: Final verification, docs, version bump

**Files:**
- Modify: `DOCS.md` (short "Task comments (2026-08-02)" subsection under the tasks implementation notes: column shape, endpoints, respawn-reset rule, mobile outbox ops)
- Modify: `mobile/pubspec.yaml` (version bump, next `1.x.y+N` after the current `1.23.3+121` line)

**Interfaces:** none — verification gate.

- [ ] **Step 1: Backend suite (container DOWN)** — `docker compose ps` must show the app stopped; then:

Run: `pytest tests/tasks/ tests/test_task_comment_routes.py -v`
Expected: ALL PASS (including every pre-existing tasks test — the respawn/carry suites prove no regression).

- [ ] **Step 2: Mobile suite** — `cd mobile && flutter analyze && flutter test`
Expected: analyzer clean (no NEW issues vs. main), full test suite green.

- [ ] **Step 3: Behavioral spot-check** (documented in the commit message): `make rebuild`, then in the app — add a comment on a task and on a subtask, kill network, add another, restore network, pull-refresh, confirm both synced (check the server row via the web API or `sqlite3` on a COPY of the DB, never the live file); tick a subtask and watch it sink; restart the app and confirm the Projects view remembered its expansion + hide-completed.

- [ ] **Step 4: Docs + version bump + commit**

```bash
git add DOCS.md mobile/pubspec.yaml
git commit -m "docs: task comments + sorting/collapse/links notes; bump mobile version"
```

- [ ] **Step 5: Update the spec status line** to `Status: Implemented (2026-08-02)` and commit with the previous step if not yet pushed.
