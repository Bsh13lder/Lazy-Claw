from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ApiHunterConfig:
    """Configuration for the API Hunter registry."""

    db_path: str = "./apihunter.db"
    validation_timeout: int = 15
    auto_validate: bool = True
    scan_on_startup: bool = True
    ollama_url: str = "http://localhost:11434"


def load_config() -> ApiHunterConfig:
    """Load configuration from environment variables."""
    auto_validate_raw = os.getenv("APIHUNTER_AUTO_VALIDATE", "true").strip().lower()

    return ApiHunterConfig(
        db_path=os.getenv("APIHUNTER_DB_PATH", "./apihunter.db"),
        validation_timeout=int(os.getenv("APIHUNTER_VALIDATION_TIMEOUT", "15")),
        auto_validate=auto_validate_raw != "false",
        scan_on_startup=os.getenv("APIHUNTER_SCAN_ON_STARTUP", "true").lower() == "true",
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
    )
