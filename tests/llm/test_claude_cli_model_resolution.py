"""CLAUDE-mode model validation and role resolution.

Guards the Settings → CLAUDE mode picker contract: every value the web
dropdown can write must survive `_is_valid_cli_model`, or the router
silently swaps it for the default and the user runs a model they did
not choose.

2026-07-25: adding Claude 5 to the picker exposed that `fable` was
missing from `_CLI_MODEL_FAMILIES`, so a Fable 5 selection resolved to
Sonnet with only a log line to show for it.
"""

from __future__ import annotations

import pytest

from lazyclaw.llm.eco_router import (
    ROLE_BRAIN,
    ROLE_WORKER,
    _is_valid_cli_model,
    _resolve_claude_cli_model,
)

# Mirrors CLAUDE_CLI_MODELS in web/src/pages/Settings.tsx. Keep in sync —
# an entry here that the validator rejects is a silent model swap.
PICKER_VALUES = [
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-haiku-4-5-20251001",
]

DEFAULT = "claude-sonnet-5"


class _Settings:
    def __init__(self, brain=None, worker=None):
        self.claude_brain_model = brain
        self.claude_worker_model = worker


class TestValidModelNames:
    @pytest.mark.parametrize("value", PICKER_VALUES)
    def test_every_picker_option_is_valid(self, value: str) -> None:
        assert _is_valid_cli_model(value) is True

    @pytest.mark.parametrize("alias", ["opus", "sonnet", "haiku", "fable"])
    def test_bare_cli_aliases_accepted(self, alias: str) -> None:
        """`claude --model fable` is documented on CLI 2.1.197."""
        assert _is_valid_cli_model(alias) is True

    def test_fable_family_accepted(self) -> None:
        """Regression: `fable` missing from the family tuple made every
        Fable selection resolve to Sonnet."""
        assert _is_valid_cli_model("claude-fable-5") is True

    @pytest.mark.parametrize(
        "value",
        [
            "claude-cli",  # internal registry alias, not a --model value
            "junk-model",
            "gpt-5",
            "",
        ],
    )
    def test_junk_rejected(self, value: str) -> None:
        assert _is_valid_cli_model(value) is False


class TestRoleResolution:
    def test_brain_uses_brain_model(self) -> None:
        s = _Settings(brain="claude-fable-5")
        assert _resolve_claude_cli_model(s, ROLE_BRAIN) == "claude-fable-5"

    def test_worker_prefers_worker_model(self) -> None:
        s = _Settings(brain="claude-opus-5", worker="claude-sonnet-5")
        assert _resolve_claude_cli_model(s, ROLE_WORKER) == "claude-sonnet-5"

    def test_worker_falls_back_to_brain(self) -> None:
        s = _Settings(brain="claude-opus-5", worker=None)
        assert _resolve_claude_cli_model(s, ROLE_WORKER) == "claude-opus-5"

    def test_junk_value_falls_back_to_default(self) -> None:
        s = _Settings(brain="claude-cli")
        assert _resolve_claude_cli_model(s, ROLE_BRAIN) == DEFAULT

    def test_no_settings_returns_default(self) -> None:
        assert _resolve_claude_cli_model(None, ROLE_BRAIN) == DEFAULT

    def test_retired_4x_value_still_resolves(self) -> None:
        """A stored 4.x pick predates the Claude 5 refresh. It is no
        longer in the picker, but the family is still valid — keep
        honoring it rather than silently moving the user to a
        different model behind their back."""
        s = _Settings(brain="claude-opus-4-8[1m]")
        assert _resolve_claude_cli_model(s, ROLE_BRAIN) == "claude-opus-4-8[1m]"


class TestPricingCoverage:
    @pytest.mark.parametrize(
        "model", ["claude-opus-5", "claude-sonnet-5", "claude-fable-5"]
    )
    def test_new_models_have_pricing(self, model: str) -> None:
        """A pickable model with no cost row reports $0 spend."""
        from lazyclaw.llm.pricing import MODEL_COSTS

        assert model in MODEL_COSTS
        assert MODEL_COSTS[model]["input"] > 0
        assert MODEL_COSTS[model]["output"] > 0

    def test_fable_priced_above_opus(self) -> None:
        from lazyclaw.llm.pricing import MODEL_COSTS

        assert (
            MODEL_COSTS["claude-fable-5"]["input"]
            > MODEL_COSTS["claude-opus-5"]["input"]
        )
