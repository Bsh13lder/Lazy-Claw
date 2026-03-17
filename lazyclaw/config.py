from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def get_project_root() -> Path:
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    return Path.cwd()


@dataclass
class Config:
    server_secret: str = ""
    database_dir: Path = field(default_factory=lambda: Path("./data"))
    port: int = 18789
    default_model: str = "gpt-5"
    worker_model: str = "gpt-5-mini"
    cors_origin: str = "http://localhost:3000"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    telegram_bot_token: str | None = None
    browser_model: str = "gpt-5-mini"
    browser_timeout: int = 300
    computer_timeout: int = 30
    heartbeat_interval: int = 1800
    max_tool_iterations: int = 25
    log_level: str = "WARNING"
    tool_timeout: int = 60
    cdp_port: int = 9222


def load_config() -> Config:
    root = get_project_root()
    env_path = root / ".env"
    load_dotenv(env_path)

    openai_key = os.getenv("OPENAI_API_KEY") or None
    anthropic_key = os.getenv("ANTHROPIC_API_KEY") or None

    # Pick default model based on which provider is configured
    explicit_model = os.getenv("DEFAULT_MODEL")
    if explicit_model:
        default_model = explicit_model
    elif anthropic_key and not openai_key:
        default_model = "claude-sonnet-4-20250514"
    else:
        default_model = "gpt-5"

    explicit_worker = os.getenv("WORKER_MODEL")
    if explicit_worker:
        worker_model = explicit_worker
    elif anthropic_key and not openai_key:
        worker_model = "claude-haiku-4-5-20251001"
    else:
        worker_model = "gpt-5-mini"

    explicit_browser = os.getenv("BROWSER_MODEL")
    if explicit_browser:
        browser_model = explicit_browser
    elif anthropic_key and not openai_key:
        browser_model = "claude-haiku-4-5-20251001"
    else:
        browser_model = "gpt-5-mini"

    return Config(
        server_secret=os.getenv("SERVER_SECRET", ""),
        database_dir=Path(os.getenv("DATABASE_DIR", "./data")),
        port=int(os.getenv("PORT", "18789")),
        default_model=default_model,
        worker_model=worker_model,
        cors_origin=os.getenv("CORS_ORIGIN", "http://localhost:3000"),
        openai_api_key=openai_key,
        anthropic_api_key=anthropic_key,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        browser_model=browser_model,
        browser_timeout=int(os.getenv("BROWSER_TIMEOUT", "300")),
        computer_timeout=int(os.getenv("COMPUTER_TIMEOUT", "30")),
        heartbeat_interval=int(os.getenv("HEARTBEAT_INTERVAL", "1800")),
        max_tool_iterations=int(os.getenv("MAX_TOOL_ITERATIONS", "25")),
        log_level=os.getenv("LOG_LEVEL", "WARNING"),
        tool_timeout=int(os.getenv("TOOL_TIMEOUT", "60")),
        cdp_port=int(os.getenv("CDP_PORT", "9222")),
    )


def save_env(key: str, value: str) -> None:
    root = get_project_root()
    env_path = root / ".env"

    lines: list[str] = []
    found = False

    if env_path.exists():
        lines = env_path.read_text().splitlines(keepends=True)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped == key:
                lines[i] = f"{key}={value}\n"
                found = True
                break

    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"{key}={value}\n")

    env_path.write_text("".join(lines))
