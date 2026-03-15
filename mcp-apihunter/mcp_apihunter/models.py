from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegistryEntry:
    """An API endpoint in the registry."""

    id: int | None
    name: str
    base_url: str
    api_key_env: str | None
    models: tuple[str, ...]
    status: str  # pending | active | failed | removed
    last_validated: float | None
    latency_avg_ms: float | None
    added_by: str
    created_at: float


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating an API endpoint."""

    success: bool
    latency_ms: float
    error: str | None
    model_responded: str | None
    timestamp: float
