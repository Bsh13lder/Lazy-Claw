"""Unit tests for the per-kind ``is_empty_plan`` no-op detectors.

Each ``{docs,sheets,pdf}.ai_edit`` strategy exposes ``is_empty_plan(plan) ->
bool`` so the in-editor specialist can treat a structurally-valid-but-empty
plan as a non-plan (triggering a corrective retry) instead of letting it die in
``apply``. These are pure functions — no store, no LLM.
"""

from __future__ import annotations

import pytest

from lazyclaw.docs import ai_edit as docs_ai
from lazyclaw.sheets import ai_edit as sheets_ai
from lazyclaw.pdf import ai_edit as pdf_ai


# ── docs ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "plan",
    [
        {},
        {"blocks": []},
        {"paragraphs": []},
        {"mode": "append", "blocks": []},
        {"blocks": [{"foo": "bar"}]},  # no type/text/runs → dropped
        "not a dict",
    ],
)
def test_docs_empty_plans(plan):
    assert docs_ai.is_empty_plan(plan) is True


@pytest.mark.parametrize(
    "plan",
    [
        {"blocks": [{"type": "paragraph", "text": "hello"}]},
        {"paragraphs": ["hello"]},
        {"paragraphs": [{"runs": [{"text": "hi"}]}]},
        {"mode": "replace", "blocks": [{"type": "heading", "level": 1, "text": "T"}]},
    ],
)
def test_docs_populated_plans(plan):
    assert docs_ai.is_empty_plan(plan) is False


# ── sheets ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "plan",
    [
        {},
        {"edits": []},
        {"edits": [{"nonsense": 1}]},          # no cell/row+col → dropped
        {"edits": [{"row": "x", "col": "y"}]},  # bad coords → dropped
        "not a dict",
    ],
)
def test_sheets_empty_plans(plan):
    assert sheets_ai.is_empty_plan(plan) is True


@pytest.mark.parametrize(
    "plan",
    [
        {"edits": [{"cell": "A1", "value": 10}]},
        {"edits": [{"cell": "C1", "formula": "=SUM(A1:B1)"}]},
        {"edits": [{"row": 2, "col": 0, "value": "Total"}]},
    ],
)
def test_sheets_populated_plans(plan):
    assert sheets_ai.is_empty_plan(plan) is False


# ── pdf (op-dependent) ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "plan",
    [
        {},
        {"op": "add_text", "items": []},
        {"op": "fill_form", "values": {}},
        {"op": "generate", "text": "   "},
        {"op": "merge", "pdf_ids": []},
        {"op": "unknown_op"},
        "not a dict",
    ],
)
def test_pdf_empty_plans(plan):
    assert pdf_ai.is_empty_plan(plan) is True


@pytest.mark.parametrize(
    "plan",
    [
        {"op": "add_text", "items": [{"page": 1, "x": 1, "y": 1, "text": "S"}]},
        {"op": "fill_form", "values": {"Name": "Ada"}},
        {"op": "generate", "text": "hello"},
        {"op": "merge", "pdf_ids": ["abc"]},
        {"op": "rotate"},   # defaults to 90° whole-doc → never a no-op
        {"op": "split"},    # defaults to per-page → never a no-op
    ],
)
def test_pdf_populated_plans(plan):
    assert pdf_ai.is_empty_plan(plan) is False
