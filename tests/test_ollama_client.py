"""Lock the contract of ``get_ollama_base_url``.

Three call sites (``ollama_provider``, ``eco_management``,
``ollama_management``) plus a follow-up migration target (``embeddings``)
all depend on this single resolver — regressions here ripple silently.
"""
from __future__ import annotations

import pytest

from lazyclaw.llm.ollama_client import get_ollama_base_url


@pytest.mark.unit
def test_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert get_ollama_base_url() == "http://localhost:11434"


@pytest.mark.unit
def test_trailing_slash_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434/")
    assert get_ollama_base_url() == "http://localhost:11434"


@pytest.mark.unit
def test_multiple_trailing_slashes_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434///")
    assert get_ollama_base_url() == "http://localhost:11434"


@pytest.mark.unit
def test_missing_scheme_gets_http_prepended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "localhost:11434")
    assert get_ollama_base_url() == "http://localhost:11434"


@pytest.mark.unit
def test_host_docker_internal_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://host.docker.internal:11434")
    assert get_ollama_base_url() == "http://host.docker.internal:11434"


@pytest.mark.unit
def test_host_docker_internal_without_scheme_gets_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "host.docker.internal:11434")
    assert get_ollama_base_url() == "http://host.docker.internal:11434"


@pytest.mark.unit
def test_https_scheme_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "https://ollama.example.com")
    assert get_ollama_base_url() == "https://ollama.example.com"


@pytest.mark.unit
def test_empty_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "")
    assert get_ollama_base_url() == "http://localhost:11434"


@pytest.mark.unit
def test_whitespace_only_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "   ")
    assert get_ollama_base_url() == "http://localhost:11434"


@pytest.mark.unit
def test_env_value_is_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "  http://localhost:11434/  ")
    assert get_ollama_base_url() == "http://localhost:11434"
