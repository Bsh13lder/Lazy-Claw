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
    default_model: str = "gpt-4o-mini"
    cors_origin: str = "http://localhost:3000"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    telegram_bot_token: str | None = None


def load_config() -> Config:
    root = get_project_root()
    env_path = root / ".env"
    load_dotenv(env_path)

    return Config(
        server_secret=os.getenv("SERVER_SECRET", ""),
        database_dir=Path(os.getenv("DATABASE_DIR", "./data")),
        port=int(os.getenv("PORT", "18789")),
        default_model=os.getenv("DEFAULT_MODEL", "gpt-4o-mini"),
        cors_origin=os.getenv("CORS_ORIGIN", "http://localhost:3000"),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
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
