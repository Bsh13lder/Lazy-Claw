"""THIN-ROUTER cap / SPECIALIST-FIRST anchor must trip only on a real mutation.

Regression guard for the 2026-06-23 "Locals" sheet-edit incident: a read-only
inspection (``read_sheet`` / ``list_sheets``) was counted as "the one inline
action", which narrowed the toolset to meta-only and suppressed the
``set_cells`` WRITE tool — making any read-then-write sheet edit structurally
impossible under ``LAZYCLAW_THIN_ROUTER=1``. The cap + stall anchor now key on
``_is_inline_mutation``, mirroring the AUTO-PROMOTE read-only exemption.
"""
import inspect
import re

from lazyclaw.runtime import agent as _agent
from lazyclaw.runtime.agent import _is_inline_mutation, _META_TOOLS


def _squash(text: str) -> str:
    """Drop ALL whitespace so the source probes below survive re-wrapping.

    The original probes matched a single-line spelling of the predicate and
    broke in 46c142d, which merely extracted the second site into a
    line-wrapped ``_cap_by_mutation = any(...)`` local. Behavior was
    identical; only the line breaks moved. Comparing whitespace-stripped
    source keeps the guard honest without re-breaking on formatting.
    """
    return re.sub(r"\s+", "", text)


def test_readonly_inspections_do_not_count_as_inline_mutation():
    for name in (
        "read_sheet", "list_sheets", "read_doc", "read_pdf",
        "get_project", "list_tasks", "find_contact",
        "upwork_last_conversation", "list_expenses",
    ):
        assert _is_inline_mutation(name) is False, f"{name} wrongly a mutation"


def test_real_mutations_count_as_inline_mutation():
    for name in (
        "set_cells", "set_formula", "create_sheet",
        "append_to_doc", "send_email", "fill_pdf_form",
    ):
        assert _is_inline_mutation(name) is True, f"{name} not counted as mutation"


def test_meta_tools_are_never_inline_mutations():
    for name in _META_TOOLS:
        assert _is_inline_mutation(name) is False, f"meta {name} wrongly counted"


def test_empty_or_none_is_not_a_mutation():
    assert _is_inline_mutation(None) is False
    assert _is_inline_mutation("") is False


def test_cap_and_anchor_use_the_mutation_predicate():
    """Both failsafe conditions must use ``_is_inline_mutation`` — not the old
    bare ``n not in _META_TOOLS`` form, which counted reads.

    Two call sites are required, and they are NOT redundant:
      1. the SPECIALIST-FIRST stall anchor (arms the failsafe), and
      2. ``_cap_by_mutation``, fed to ``_should_thin_router_cap`` (chooses
         meta-only vs dispatch-only narrowing).
    If either reverted to the bare form, a read-only inspection would again
    burn the single inline action and suppress the follow-up WRITE tool —
    the 2026-06-23 "Locals" sheet-edit incident.
    """
    src = _squash(inspect.getsource(_agent))
    assert _squash("any(n not in _META_TOOLS for n in _called_tool_names)") not in src
    needle = _squash("any(_is_inline_mutation(n) for n in _called_tool_names)")
    assert src.count(needle) >= 2, (
        "expected the mutation predicate at BOTH the specialist-first stall "
        "anchor and the thin-router cap"
    )


def test_cap_decision_is_routed_through_the_shared_helper():
    """The cap site must feed ``_should_thin_router_cap`` rather than inline
    its own boolean — that helper is what the budget tests exercise."""
    src = _squash(inspect.getsource(_agent))
    assert _squash("_cap_by_mutation = any(") in src
    assert _squash("cap_by_mutation=_cap_by_mutation") in src
