"""Shared Ollama config — single source of truth for ``OLLAMA_HOST``.

Before this module existed, four call sites independently read ``OLLAMA_HOST``
with subtly different normalization (or none). That meant a deploy where the
operator set ``OLLAMA_HOST=http://host.docker.internal:11434/`` (trailing
slash) worked in the provider — which calls ``.rstrip("/")`` later — but
double-slashed every URL the management skills built with f-strings.

Scope intentionally tiny: just the URL resolver. Timeouts, retries, and
streaming behavior remain per-caller because the three consumers have very
different needs (300s chat vs 5s health vs 1800s model pull vs 30s embed).
A shared HTTP client factory would be premature abstraction.
"""
from __future__ import annotations

import os

_DEFAULT_BASE_URL = "http://localhost:11434"


def get_ollama_base_url() -> str:
    """Return the normalized Ollama base URL.

    Resolution order:
      1. ``OLLAMA_HOST`` environment variable (if set and non-empty).
      2. Default ``http://localhost:11434``.

    Normalization:
      - Trailing slashes are stripped (so ``f"{base}/api/tags"`` never
        produces a double slash).
      - If the value has no URI scheme (e.g. ``host.docker.internal:11434``
        or ``localhost:11434``) we prepend ``http://`` — Ollama is a local
        HTTP service and never serves TLS directly.
      - Existing ``http://`` / ``https://`` schemes are preserved verbatim,
        including ``host.docker.internal`` form used in Docker deployments.
    """
    raw = os.environ.get("OLLAMA_HOST")
    candidate = raw.strip() if raw else _DEFAULT_BASE_URL
    if not candidate:
        candidate = _DEFAULT_BASE_URL
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    return candidate.rstrip("/")


__all__ = ["get_ollama_base_url"]
