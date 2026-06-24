"""delegate must deliver a fire-and-forget specialist's result proactively.

2026-06-23 "Locals" sheet incident: the brain delegated a sheet edit via
`delegate` (dispatch-and-exit). The specialist finished the edit, but
`delegate` fired its terminal `background_done` on the foreground turn's
ALREADY-EXITED callback — so the Telegram user waiting on the result got
nothing. Fix: register a consolidation fan-out (the same machinery
dispatch_subagents/run_background use) and settle it on completion, with the
legacy background_done/failed events suppressed when consolidating (no
double-send).
"""
import inspect

from lazyclaw.skills.builtin import delegate as _delegate_mod
from lazyclaw.skills.builtin.delegate import DelegateSkill

_SRC = inspect.getsource(_delegate_mod)


def test_delegate_accepts_task_runner_and_session():
    params = inspect.signature(DelegateSkill.__init__).parameters
    assert "task_runner" in params
    assert "chat_session_id" in params


def test_delegate_constructs_and_stores_runner():
    skill = DelegateSkill(
        config=None, registry=None, eco_router=None,
        task_runner=object(), chat_session_id="sess-1",
    )
    assert skill._task_runner is not None
    assert skill._chat_session_id == "sess-1"


def test_delegate_registers_fanout_keyed_on_the_specialist_task():
    assert "register_subagent_fanout(" in _SRC
    # the single specialist's task_id is the group's only member
    assert "[task_id]" in _SRC
    # only registered when a lane-queue-backed runner exists
    assert 'getattr(_tr, "_lane_queue", None)' in _SRC


def test_delegate_records_result_on_both_settle_paths():
    # success completion AND the crash path must settle the fan-out
    assert _SRC.count("record_subagent_result(") >= 2


def test_delegate_suppresses_legacy_terminal_events_when_consolidating():
    """Double-delivery guard: background_done/background_failed must only fire
    when NOT consolidating (the fan-out's synthetic turn delivers otherwise)."""
    # one guard in the success path, one in the crash path
    assert _SRC.count("if _fanout_group_id is None:") >= 2
    # the success background_done emit sits AFTER a None-guard (not unconditional)
    done_idx = _SRC.index('"background_done"')
    guard_idx = _SRC.rindex("if _fanout_group_id is None:", 0, done_idx)
    assert guard_idx != -1 and (done_idx - guard_idx) < 400
