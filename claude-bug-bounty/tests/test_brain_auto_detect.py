"""Tests for LLMClient._auto_detect provider-priority reshuffling.

Users with only a cloud API key set (e.g. ANTHROPIC_API_KEY) should hit
the matching provider first. Without any key, the original ordering
(Ollama → Claude → OpenAI → Grok) still applies.

LazyClaw fork note: the auto-detect grew a `claude_cli` pre-check (prefers
$0 subscription path when both the `claude` binary and the `lazyclaw`
package are available). The original-behavior tests below disable that
pre-check via the `_disable_claude_cli` fixture; one new test exercises
the new path explicitly.
"""

import importlib
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)


@pytest.fixture
def brain_module(monkeypatch):
    for env in ("BRAIN_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    import brain
    importlib.reload(brain)
    return brain


@pytest.fixture
def _disable_claude_cli(monkeypatch):
    """Force the LazyClaw `claude_cli` pre-check to fail.

    Tests that assert on the legacy provider order need the pre-check
    skipped so the original priority reshuffling is observable.
    """
    import brain
    monkeypatch.setattr(brain.shutil, "which", lambda _binary: None)


class _Tracker:
    """Records _init_provider calls; marks a chosen provider available."""

    def __init__(self, available_provider: str | None = None):
        self.calls: list[str] = []
        self.available_provider = available_provider

    def bind(self, client):
        def _init(provider: str) -> None:
            self.calls.append(provider)
            client.available = provider == self.available_provider
        client._init_provider = _init


def test_anthropic_key_jumps_to_front(brain_module, monkeypatch, _disable_claude_cli):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    client = brain_module.LLMClient.__new__(brain_module.LLMClient)
    client.available = False
    tracker = _Tracker(available_provider="claude")
    tracker.bind(client)

    chosen = brain_module.LLMClient._auto_detect(client)

    assert chosen == "claude"
    assert tracker.calls[0] == "claude", "claude must be probed first when its key is set"


def test_openai_and_grok_keys_both_front_nothing_available(brain_module, monkeypatch, _disable_claude_cli):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    client = brain_module.LLMClient.__new__(brain_module.LLMClient)
    client.available = False
    tracker = _Tracker(available_provider=None)
    tracker.bind(client)

    chosen = brain_module.LLMClient._auto_detect(client)

    assert chosen == "ollama"
    assert set(tracker.calls[:2]) == {"openai", "grok"}, \
        "key-bearing providers must be probed before the rest"
    assert tracker.calls[2:] == ["ollama", "claude"], \
        "providers without keys keep their original relative order"


def test_no_keys_falls_back_to_default_priority(brain_module, _disable_claude_cli):
    client = brain_module.LLMClient.__new__(brain_module.LLMClient)
    client.available = False
    tracker = _Tracker(available_provider=None)
    tracker.bind(client)

    chosen = brain_module.LLMClient._auto_detect(client)

    assert chosen == "ollama"
    # claude_cli pre-check is disabled by the fixture, so the calls
    # exclude it. The fallthrough order skips claude_cli explicitly
    # (it has no env-key path; only the pre-check picks it).
    expected = [p for p in brain_module.LLMClient.PROVIDER_PRIORITY if p != "claude_cli"]
    assert tracker.calls == expected


def test_key_set_but_provider_unavailable_falls_through(brain_module, monkeypatch, _disable_claude_cli):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    client = brain_module.LLMClient.__new__(brain_module.LLMClient)
    client.available = False
    tracker = _Tracker(available_provider="ollama")
    tracker.bind(client)

    chosen = brain_module.LLMClient._auto_detect(client)

    assert chosen == "ollama"
    assert tracker.calls[0] == "claude"
    assert "ollama" in tracker.calls


def test_claude_cli_preferred_when_binary_and_lazyclaw_present(brain_module, monkeypatch):
    """LazyClaw $0 path: when `claude` binary is on PATH and lazyclaw is
    importable, claude_cli is probed first regardless of API keys set.
    """
    # Force shutil.which("claude") to look "found"
    monkeypatch.setattr(brain_module.shutil, "which",
                        lambda binary: "/usr/local/bin/claude" if binary == "claude" else None)
    # Force importlib.util.find_spec("lazyclaw") to look importable
    monkeypatch.setattr(brain_module.importlib.util, "find_spec",
                        lambda name: object() if name == "lazyclaw" else None)
    # And set ANTHROPIC_API_KEY — claude_cli must still win
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    client = brain_module.LLMClient.__new__(brain_module.LLMClient)
    client.available = False
    tracker = _Tracker(available_provider="claude_cli")
    tracker.bind(client)

    chosen = brain_module.LLMClient._auto_detect(client)

    assert chosen == "claude_cli", \
        "claude_cli must win when binary + lazyclaw are present, even over API keys"
    assert tracker.calls[0] == "claude_cli"
