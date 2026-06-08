"""Phase 3a — user-facing relabel to Ask/Plan/Action/Execute.

Internal values (chat/ask/plan/auto) and behavior are UNCHANGED — this is a
display+input relabel only (no stored-value migration). Verifies the new
labels and the label/legacy → AgentMode input parser.
"""

from __future__ import annotations

from lazyclaw.runtime.agent_mode import (
    AgentMode,
    DEFAULT_MODE,
    MODE_LABELS,
    parse_mode,
    parse_mode_label,
)


def test_canonical_labels() -> None:
    assert MODE_LABELS[AgentMode.CHAT] == "Ask"
    assert MODE_LABELS[AgentMode.ASK] == "Action"
    assert MODE_LABELS[AgentMode.PLAN] == "Plan"
    assert MODE_LABELS[AgentMode.AUTO] == "Execute"


def test_internal_values_unchanged() -> None:
    # Stored values must NOT change (no migration).
    assert AgentMode.CHAT.value == "chat"
    assert AgentMode.ASK.value == "ask"
    assert AgentMode.PLAN.value == "plan"
    assert AgentMode.AUTO.value == "auto"
    assert DEFAULT_MODE is AgentMode.ASK  # default behavior unchanged


def test_parse_mode_label_canonical() -> None:
    # New canonical names map to the internal mode with matching behavior.
    assert parse_mode_label("ask") is AgentMode.CHAT       # Ask = answer-only
    assert parse_mode_label("Action") is AgentMode.ASK     # Action = gate
    assert parse_mode_label("plan") is AgentMode.PLAN
    assert parse_mode_label("EXECUTE") is AgentMode.AUTO


def test_parse_mode_label_legacy_and_unknown() -> None:
    # Legacy typed values still resolve sensibly.
    assert parse_mode_label("chat") is AgentMode.CHAT
    assert parse_mode_label("auto") is AgentMode.AUTO
    assert parse_mode_label("plan") is AgentMode.PLAN
    # Unknown → None
    assert parse_mode_label("nonsense") is None
    assert parse_mode_label("") is None
    assert parse_mode_label(None) is None


def test_parse_mode_value_roundtrip_unchanged() -> None:
    # parse_mode (value-based, used to read stored settings) is unchanged.
    assert parse_mode("ask") is AgentMode.ASK
    assert parse_mode("chat") is AgentMode.CHAT
    assert parse_mode(None) is DEFAULT_MODE
