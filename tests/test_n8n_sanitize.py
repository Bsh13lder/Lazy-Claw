"""Pillar A — generic sanitize + lint for n8n workflow JSON.

Pins the type-agnostic interface hardening landed 2026-04-22 after
MiniMax-M2.7 loop-failed on `@n8n/n8n-nodes-base.webhook` (day 2 of
the n8n stall). The specific bugs this file guards against:

  * `_normalize_node_type` must strip the `@n8n/` scope from core
    nodes (`@n8n/n8n-nodes-base.X` is the npm package name, never
    a valid node-type id), and ADD it to langchain nodes
    (`n8n-nodes-langchain.X` → `@n8n/n8n-nodes-langchain.X`).
  * `_coerce_type_version` must turn stringy typeVersion into a
    number — n8n's JSON-schema validator rejects strings there.
  * `_validate_workflow_nodes` runs a generic pre-pass covering
    the 10 workflow-JSON footguns that break ANY node type, not
    just Google: type prefix, typeVersion numeric, position pair,
    id/name uniqueness, connections graph wiring.

Verified against n8n-io/n8n source (packages/core/src/constants.ts,
AI_NODES_PACKAGE_NAME) — see plan file async-bouncing-minsky.md.
"""

from __future__ import annotations

import pytest

from lazyclaw.skills.builtin import n8n_management as mod


# ── _normalize_node_type ────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        # Core nodes: strip the `@n8n/` scope.
        ("@n8n/n8n-nodes-base.webhook", "n8n-nodes-base.webhook"),
        ("@n8n/n8n-nodes-base.googleSheets", "n8n-nodes-base.googleSheets"),
        ("n8n/n8n-nodes-base.httpRequest", "n8n-nodes-base.httpRequest"),
        # LangChain nodes: ADD the `@n8n/` scope.
        ("n8n-nodes-langchain.agent", "@n8n/n8n-nodes-langchain.agent"),
        ("n8n/n8n-nodes-langchain.lmChatOpenAi", "@n8n/n8n-nodes-langchain.lmChatOpenAi"),
        # Already-correct strings pass through unchanged.
        ("n8n-nodes-base.webhook", "n8n-nodes-base.webhook"),
        ("@n8n/n8n-nodes-langchain.agent", "@n8n/n8n-nodes-langchain.agent"),
        # Whitespace is stripped.
        ("  n8n-nodes-base.webhook  ", "n8n-nodes-base.webhook"),
        # Empty / unknown types pass through (no surprising mutation).
        ("", ""),
        ("custom-vendor.myNode", "custom-vendor.myNode"),
    ],
)
def test_normalize_node_type_table(raw, expected):
    assert mod._normalize_node_type(raw) == expected


def test_normalize_node_type_non_string_returns_unchanged():
    assert mod._normalize_node_type(None) is None
    assert mod._normalize_node_type(42) == 42


# ── _coerce_type_version ────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        (1, 1),
        (4.5, 4.5),
        ("1", 1),
        ("4.5", 4.5),
        ("  3  ", 3),
        # Junk passes through unchanged — n8n will then catch it in
        # the generic lint pre-pass.
        ("abc", "abc"),
        ("", ""),
    ],
)
def test_coerce_type_version_table(raw, expected):
    assert mod._coerce_type_version(raw) == expected


# ── _sanitize_node wires both normalizer + coercer ─────────────────

def test_sanitize_node_normalizes_type_and_coerces_version():
    node = {
        "name": "Webhook",
        "type": "@n8n/n8n-nodes-base.webhook",  # wrong scope
        "typeVersion": "1.1",                   # stringy
        "position": [0, 0],
        "parameters": {},
    }
    out = mod._sanitize_node(node)
    assert out["type"] == "n8n-nodes-base.webhook"
    assert out["typeVersion"] == 1.1


def test_sanitize_node_still_strips_unknown_keys():
    # Regression: the allowlist behaviour from Pillar A (v1) must not
    # regress — `executable` and `color` would crash n8n's
    # "must NOT have additional properties" check.
    node = {
        "name": "X",
        "type": "n8n-nodes-base.webhook",
        "typeVersion": 1,
        "executable": True,
        "color": "#fff",
    }
    out = mod._sanitize_node(node)
    assert "executable" not in out
    assert "color" not in out


# ── _validate_workflow_nodes — generic pre-pass ────────────────────

def test_generic_lint_flags_base_node_with_scope_prefix():
    # This is literally the failing production case from the log.
    violations = mod._validate_workflow_nodes([
        {
            "name": "Webhook",
            "type": "@n8n/n8n-nodes-base.webhook",
            "typeVersion": 1,
            "position": [0, 0],
            "parameters": {"path": "x"},
        },
    ])
    assert any("invalid type" in v and "@n8n/n8n-nodes-base" in v for v in violations)


def test_generic_lint_flags_langchain_without_scope():
    violations = mod._validate_workflow_nodes([
        {
            "name": "Agent",
            "type": "n8n-nodes-langchain.agent",
            "typeVersion": 1,
            "position": [0, 0],
            "parameters": {},
        },
    ])
    assert any("prepend `@n8n/`" in v for v in violations)


def test_generic_lint_flags_stringy_type_version():
    violations = mod._validate_workflow_nodes([
        {
            "name": "Webhook",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": "1",  # string — must be number
            "position": [0, 0],
            "parameters": {"path": "x"},
        },
    ])
    assert any("typeVersion" in v for v in violations)


def test_generic_lint_flags_bad_position():
    violations = mod._validate_workflow_nodes([
        {
            "name": "Webhook",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 1,
            "position": "0,0",  # must be [x, y] pair
            "parameters": {"path": "x"},
        },
    ])
    assert any("position" in v for v in violations)


def test_generic_lint_flags_duplicate_node_names():
    # n8n keys connections by name — duplicates break the graph.
    violations = mod._validate_workflow_nodes([
        {"name": "X", "type": "n8n-nodes-base.webhook", "typeVersion": 1,
         "position": [0, 0], "parameters": {"path": "a"}},
        {"name": "X", "type": "n8n-nodes-base.set", "typeVersion": 1,
         "position": [200, 0], "parameters": {}},
    ])
    assert any("Duplicate node name" in v for v in violations)


def test_generic_lint_flags_connections_orphan_targets():
    violations = mod._validate_workflow_nodes(
        nodes=[
            {"name": "A", "type": "n8n-nodes-base.webhook", "typeVersion": 1,
             "position": [0, 0], "parameters": {"path": "a"}},
        ],
        connections={
            "A": {"main": [[{"node": "Ghost", "type": "main", "index": 0}]]},
        },
    )
    assert any("Ghost" in v for v in violations)


def test_generic_lint_flags_connections_orphan_source_key():
    violations = mod._validate_workflow_nodes(
        nodes=[
            {"name": "A", "type": "n8n-nodes-base.webhook", "typeVersion": 1,
             "position": [0, 0], "parameters": {"path": "a"}},
        ],
        connections={
            "Ghost": {"main": [[{"node": "A", "type": "main", "index": 0}]]},
        },
    )
    assert any("doesn't match any" in v and "Ghost" in v for v in violations)


def test_generic_lint_clean_workflow_has_no_violations():
    # Fully valid single-node workflow — no noise.
    violations = mod._validate_workflow_nodes(
        nodes=[
            {
                "id": "n1",
                "name": "Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 1,
                "position": [0, 0],
                "parameters": {"path": "hello"},
            },
        ],
        connections={},
    )
    assert violations == []
