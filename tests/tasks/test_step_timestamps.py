"""Sub-task timestamps: ``created_at`` / ``completed_at`` on a step dict.

Why this file exists
--------------------
``_normalize_steps`` REBUILDS every step dict from scratch and every steps
write in the product funnels through it (REST ``PUT /tasks/{id}/steps``,
``update_task(steps=...)``, ``set_steps``, ``append_steps``, ``toggle_step``,
the NL parser, the AI quick-add, the recurring respawn). So a key the
normaliser does not explicitly carry is not "usually kept" — it is *deleted on
the next unrelated edit*. That is exactly the failure the ``cascaded`` marker
already documents inside the function, and it is why sub-task timestamps have
to be defended by tests at the normaliser, not at the UI.

Wire contract under test (canonical sub-task JSON)::

    {"id", "title", "done", "created_at", "completed_at"}

* ``created_at`` / ``completed_at`` are ISO-8601 UTC strings in the SAME shape
  a task's own ``created_at`` uses — ``datetime.now(timezone.utc).isoformat()``
  (``store.create_task``). One format, so sub-task times sort and render
  alongside task times.
* Either key is OMITTED when null, so a checklist item keeps its lean shape and
  no client has to special-case a ``null``.
* Stamping happens ONLY when the normaliser is MINTING the step — i.e. the
  payload arrived with no ``id``. Steps are born server-side too (NL parser, AI
  quick-add, recurring respawn) and leaving those blank forever while
  app-created ones carry times is the "works locally then quietly rots"
  outcome. But an incoming ``id`` means the step already existed and this
  function never witnessed its creation, so it must not invent one: because
  every write re-normalises the WHOLE list, unconditional stamping made a
  single tick backdate an entire legacy checklist to today.
* ``completed_at`` is likewise never DERIVED from ``done``. A stateless
  function cannot tell a fresh tick from a legacy one. The callers that do
  witness the transition stamp it: ``toggle_step``,
  ``_cascade_complete_steps``, and ``subtask_editor.dart`` on mobile.
* Parsing is TOLERANT on both sides: junk degrades to "absent", never raises.
  ``_normalize_steps`` sits on every write path, so raising over one malformed
  sub-task field would 500 an entire task edit.

Pure tests: ``_normalize_steps`` and ``_cascade_complete_steps`` are plain
functions. No DB, no config, no event loop — nothing here can reach the live
``./data`` SQLite file.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lazyclaw.tasks.store import _cascade_complete_steps, _normalize_steps

# A timestamp that is unambiguously NOT "now", so a test proving preservation
# cannot accidentally pass against a freshly stamped value.
OLD_CREATED = "2026-01-02T03:04:05.678901+00:00"
OLD_COMPLETED = "2026-03-04T05:06:07.891011+00:00"


def _assert_is_fresh_utc_iso(value: object, label: str) -> None:
    """The value is an ISO-8601 UTC instant stamped within this test run.

    Also pins the FORMAT: ``datetime.fromisoformat`` round-trips it and the
    offset is UTC, which is what ``datetime.now(timezone.utc).isoformat()``
    emits and what every other timestamp column in the tasks table carries.
    """
    assert isinstance(value, str), f"{label} must be a string, got {type(value)!r}"
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, f"{label}={value!r} is naive; must be UTC-aware"
    assert parsed.utcoffset() == timedelta(0), f"{label}={value!r} is not UTC"
    assert abs(datetime.now(timezone.utc) - parsed) < timedelta(minutes=5), (
        f"{label}={value!r} was not stamped during this test run"
    )


# ---------------------------------------------------------------------------
# Preservation — the normaliser must not eat what the client sent
# ---------------------------------------------------------------------------

def test_timestamps_present_are_preserved_verbatim() -> None:
    """Both keys survive a normalisation round-trip, character-for-character."""
    out = _normalize_steps([
        {
            "id": "s1",
            "title": "buy milk",
            "done": True,
            "created_at": OLD_CREATED,
            "completed_at": OLD_COMPLETED,
        },
    ])

    assert out == [{
        "id": "s1",
        "title": "buy milk",
        "done": True,
        "created_at": OLD_CREATED,
        "completed_at": OLD_COMPLETED,
    }]


def test_existing_completed_at_survives_an_unrelated_edit() -> None:
    """Re-titling a finished step must NOT move its completion time.

    Every steps write re-normalises the WHOLE list, so a title edit on step A
    re-runs the normaliser over already-finished step B. Re-stamping there
    would silently rewrite history: "done at 09:00" becomes "done at 17:42"
    because the user fixed a typo elsewhere.
    """
    stored = _normalize_steps([
        {"id": "s1", "title": "old title", "done": True,
         "created_at": OLD_CREATED, "completed_at": OLD_COMPLETED},
    ])

    edited = _normalize_steps([{**stored[0], "title": "new title"}])

    assert edited[0]["title"] == "new title"
    assert edited[0]["completed_at"] == OLD_COMPLETED
    assert edited[0]["created_at"] == OLD_CREATED


# ---------------------------------------------------------------------------
# Stamping — server-born steps must not be permanently blank
# ---------------------------------------------------------------------------

def test_absent_created_at_is_stamped() -> None:
    """A step arriving with no ``created_at`` gets one now.

    The NL parser and the AI quick-add build steps as bare
    ``{"title": ...}``; without this they would render a blank creation time
    forever while app-created sub-tasks show one.
    """
    out = _normalize_steps([{"title": "call the dentist"}])

    assert len(out) == 1
    _assert_is_fresh_utc_iso(out[0].get("created_at"), "created_at")


def test_minted_done_step_is_stamped() -> None:
    """A step BORN done (no incoming id) gets both timestamps.

    This is the agent path — "add a checklist and tick the first item" — where
    the normaliser really is the party creating the row, so it is entitled to
    stamp it.
    """
    out = _normalize_steps([{"title": "ship it", "done": True}])

    _assert_is_fresh_utc_iso(out[0].get("created_at"), "created_at")
    _assert_is_fresh_utc_iso(out[0].get("completed_at"), "completed_at")


def test_existing_done_step_without_completed_at_is_left_alone() -> None:
    """A step that ALREADY EXISTS (has an id) and arrives ticked but
    untimestamped keeps NO completion time — it is not invented.

    ``_normalize_steps`` is stateless: it cannot distinguish "the user ticked
    this a moment ago" from "this was ticked months ago by a client that never
    sent a timestamp". Deriving one would date old work to today, which is the
    same lie as backfilling ``created_at``. The parties that genuinely witness
    a tick stamp it themselves — ``toggle_step`` and ``_cascade_complete_steps``
    here, ``subtask_editor.dart`` on mobile.
    """
    out = _normalize_steps([{"id": "s1", "title": "ship it", "done": True}])

    assert "completed_at" not in out[0]
    assert out[0]["done"] is True


def test_a_legacy_checklist_is_never_backfilled() -> None:
    """THE REGRESSION THIS RULE EXISTS FOR.

    Every steps write re-normalises the WHOLE list. When the normaliser
    stamped ``created_at`` unconditionally, ticking ONE item in a checklist
    created last year brought back every untouched sibling claiming it was
    created today. The Flutter model deliberately refuses to invent creation
    times for pre-existing rows (see ``mobile/lib/models/subtask.dart``), and
    backfilling here erased that honesty one hop later.
    """
    out = _normalize_steps([
        {"id": "s-a", "title": "old A", "done": True},
        {"id": "s-b", "title": "old B", "done": False},
    ])

    for step in out:
        assert "created_at" not in step, (
            f"invented a creation time for pre-existing step {step['id']}"
        )
    assert "completed_at" not in out[0]


def test_unticking_clears_completed_at() -> None:
    """``done`` true → false must DROP the key, not leave a stale time.

    A step showing "completed 3 days ago" next to an empty checkbox is worse
    than no timestamp at all, and any consumer counting completions by the
    presence of the key would double-count it.
    """
    stored = _normalize_steps([
        {"id": "s1", "title": "ship it", "done": True,
         "created_at": OLD_CREATED, "completed_at": OLD_COMPLETED},
    ])
    assert stored[0]["completed_at"] == OLD_COMPLETED  # precondition

    reopened = _normalize_steps([{**stored[0], "done": False}])

    assert "completed_at" not in reopened[0], (
        "an un-ticked step kept its completion time"
    )
    assert reopened[0]["created_at"] == OLD_CREATED, (
        "un-ticking must not disturb the creation time"
    )


# ---------------------------------------------------------------------------
# Shape — no key leaks beyond the contract
# ---------------------------------------------------------------------------

def test_unfinished_step_carries_no_completed_at() -> None:
    """The lean shape: an ordinary checklist item is exactly
    ``{id, title, done, created_at}``.

    NOTE on ``created_at``: the wire contract omits a NULL timestamp, and
    after the stamping rule above ``created_at`` is never null — so it is
    always present and ``completed_at`` is the only optional key. The guard
    this test really owns is that NOTHING ELSE leaks in (no ``cascaded``, no
    ``null`` placeholders, no client-supplied junk keys).
    """
    out = _normalize_steps([{"title": "water the plants"}])

    assert set(out[0]) == {"id", "title", "done", "created_at"}
    assert out[0]["done"] is False


def test_unknown_client_keys_are_not_carried() -> None:
    """The normaliser stays a whitelist — a client cannot smuggle fields in."""
    out = _normalize_steps([
        {"id": "s1", "title": "x", "done": False, "colour": "red", "notes": "hi"},
    ])

    # An id was supplied, so nothing is stamped — the leanest possible shape.
    assert set(out[0]) == {"id", "title", "done"}


# ---------------------------------------------------------------------------
# Tolerance — junk degrades to absent, never raises
# ---------------------------------------------------------------------------

def test_non_string_timestamps_degrade_to_absent() -> None:
    """An epoch int / dict / None must not be propagated or raised on.

    Mobile has shipped both a naive-local and an epoch-millis flavour of
    timestamps in its history; a client regression must cost a missing
    timestamp, not a 500 on the whole task edit.
    """
    out = _normalize_steps([
        {"id": "s1", "title": "a", "done": True,
         "created_at": 1767225845000, "completed_at": {"nope": True}},
    ])

    # Junk is unreadable → treated as absent. The step carries an id, so
    # nothing is invented in its place and the garbage never propagates.
    assert set(out[0]) == {"id", "title", "done"}


def test_unparseable_timestamp_strings_degrade_to_absent() -> None:
    out = _normalize_steps([
        {"id": "s1", "title": "a", "done": False, "created_at": "yesterday-ish"},
    ])

    assert set(out[0]) == {"id", "title", "done"}


def test_empty_string_timestamp_degrades_to_absent() -> None:
    """`''` is how a form-bound client sends "unset" — treat it as absent."""
    out = _normalize_steps([
        {"id": "s1", "title": "a", "done": True,
         "created_at": "", "completed_at": ""},
    ])

    assert set(out[0]) == {"id", "title", "done"}


def test_junk_on_a_MINTED_step_still_gets_stamped() -> None:
    """Tolerance and stamping compose: an id-less step whose timestamps are
    unreadable is still being minted here, so it is stamped rather than left
    blank. Guards the seam between the two rules.
    """
    out = _normalize_steps([{"title": "a", "created_at": 1767225845000}])

    _assert_is_fresh_utc_iso(out[0]["created_at"], "created_at")


# ---------------------------------------------------------------------------
# Loose-input contract — the existing callers must keep working
# ---------------------------------------------------------------------------

def test_plain_string_input_still_works() -> None:
    """``append_steps`` and the NL parser hand over bare title strings."""
    out = _normalize_steps(["milk", "  eggs  ", "", "   "])

    assert [s["title"] for s in out] == ["milk", "eggs"]
    for step in out:
        assert step["done"] is False
        assert set(step) == {"id", "title", "done", "created_at"}
        _assert_is_fresh_utc_iso(step["created_at"], "created_at")


def test_partial_dict_input_still_works() -> None:
    """A dict with only a title gets an id minted and stays untouched otherwise."""
    out = _normalize_steps([{"title": "bread"}, {"title": ""}, {"nope": 1}, 42])

    assert len(out) == 1
    assert out[0]["title"] == "bread"
    assert out[0]["id"]


def test_empty_and_none_input_still_return_empty_list() -> None:
    assert _normalize_steps(None) == []
    assert _normalize_steps([]) == []


def test_cascade_marker_still_survives() -> None:
    """Regression guard: adding timestamps must not evict ``cascaded``.

    Same class of bug in the other direction — the two features share the one
    rebuild loop, so a careless edit to either drops the other.
    """
    out = _normalize_steps([
        {"id": "s1", "title": "a", "done": True, "cascaded": True},
    ])

    assert out[0]["cascaded"] is True
    assert set(out[0]) == {"id", "title", "done", "cascaded"}


# ---------------------------------------------------------------------------
# Parent-completion cascade — auto-ticked steps need a completion time too
# ---------------------------------------------------------------------------

def test_cascade_stamps_completed_at_on_the_steps_it_ticks() -> None:
    """Completing a parent ticks its checklist; those ticks are completions.

    Without a stamp here the cascade produces ``done: true`` with no
    ``completed_at``, and the FIRST unrelated edit afterwards would then
    derive one from that later moment — a completion time that lies about
    when the work finished.
    """
    out = _cascade_complete_steps([
        {"id": "s1", "title": "tag", "done": False, "created_at": OLD_CREATED},
        {"id": "s2", "title": "build", "done": False, "created_at": OLD_CREATED},
    ])

    for step in out:
        assert step["done"] is True
        assert step["cascaded"] is True
        assert step["created_at"] == OLD_CREATED, "the cascade rewrote creation time"
        _assert_is_fresh_utc_iso(step["completed_at"], "completed_at")


def test_cascade_leaves_user_completed_steps_untouched() -> None:
    """A step the user genuinely finished keeps ITS completion time and stays
    untagged — a later reopen must un-tick only what the cascade ticked."""
    out = _cascade_complete_steps([
        {"id": "s1", "title": "tag", "done": True,
         "created_at": OLD_CREATED, "completed_at": OLD_COMPLETED},
        {"id": "s2", "title": "build", "done": False, "created_at": OLD_CREATED},
    ])

    by_title = {s["title"]: s for s in out}
    assert by_title["tag"]["completed_at"] == OLD_COMPLETED
    assert "cascaded" not in by_title["tag"]
    assert by_title["build"]["cascaded"] is True


def test_cascade_returns_a_new_list_and_never_mutates_its_input() -> None:
    """Immutability: ``complete_task`` reuses the decoded task dict afterwards
    (LazyBrain mirror, recurring respawn), so an in-place tick would leak."""
    original = [{"id": "s1", "title": "tag", "done": False}]
    snapshot = [dict(s) for s in original]

    out = _cascade_complete_steps(original)

    assert original == snapshot, "the cascade mutated the caller's step list"
    assert out is not original


# ---------------------------------------------------------------------------
# The REST boundary — where the timestamps were being silently discarded
# ---------------------------------------------------------------------------

def test_step_draft_preserves_client_timestamps() -> None:
    """``StepDraft`` must DECLARE the timestamps or they never reach the store.

    pydantic v2 defaults to ``extra='ignore'``, and both write routes consume
    this model via ``s.model_dump()`` — so an undeclared field is dropped at
    the boundary with no error anywhere. Mobile pushes steps through
    ``PUT /api/tasks/{id}/steps`` (PATCH has no ``steps`` field at all), and it
    is the only party that observes the tick for an OFFLINE edit, so losing its
    timestamps here would make every client tick look untimestamped to
    ``_normalize_steps`` — which by design refuses to invent one.

    This boundary had zero coverage; the feature shipped broken end-to-end
    while every store-level test passed.
    """
    from lazyclaw.gateway.routes.tasks import StepDraft

    dumped = StepDraft(
        id="s1",
        title="ship it",
        done=True,
        created_at=OLD_CREATED,
        completed_at=OLD_COMPLETED,
    ).model_dump()

    assert dumped["created_at"] == OLD_CREATED
    assert dumped["completed_at"] == OLD_COMPLETED

    # And the round trip the real request actually performs.
    normalized = _normalize_steps([dumped])
    assert normalized[0]["created_at"] == OLD_CREATED
    assert normalized[0]["completed_at"] == OLD_COMPLETED


def test_step_draft_omits_timestamps_when_client_sends_none() -> None:
    """A client that knows nothing about timestamps still round-trips cleanly:
    the drafted step carries explicit ``None``s, which ``_step_timestamp``
    reads as absent — so a legacy row is not backfilled via this path either.
    """
    from lazyclaw.gateway.routes.tasks import StepDraft

    dumped = StepDraft(id="s1", title="x", done=False).model_dump()
    assert dumped["created_at"] is None

    normalized = _normalize_steps([dumped])
    assert set(normalized[0]) == {"id", "title", "done"}
