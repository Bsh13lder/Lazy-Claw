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
    brain_model: str = "gpt-5"       # Main agent, team lead, complex fallback
    worker_model: str = "gpt-5-mini"  # Specialists, background jobs, summaries
    cors_origin: str = "http://localhost:3000"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    telegram_bot_token: str | None = None
    browser_timeout: int = 300
    computer_timeout: int = 30
    heartbeat_interval: int = 60
    max_tool_iterations: int = 50
    log_level: str = "WARNING"
    tool_timeout: int = 60
    cdp_port: int = 9222
    browser_executable: str = ""  # Path to browser binary (Brave, Chrome, Chromium)


def load_config() -> Config:
    root = get_project_root()
    env_path = root / ".env"
    load_dotenv(env_path, override=True)

    openai_key = os.getenv("OPENAI_API_KEY") or None
    anthropic_key = os.getenv("ANTHROPIC_API_KEY") or None

    # Brain model: main agent, team lead, complex fallback
    explicit_brain = os.getenv("BRAIN_MODEL") or os.getenv("DEFAULT_MODEL")
    if explicit_brain:
        brain_model = explicit_brain
    elif anthropic_key and not openai_key:
        brain_model = "claude-haiku-4-5-20251001"
    else:
        brain_model = "gpt-5"

    # Worker model: specialists, background jobs, summaries
    explicit_worker = os.getenv("WORKER_MODEL")
    if explicit_worker:
        worker_model = explicit_worker
    elif anthropic_key and not openai_key:
        worker_model = "claude-haiku-4-5-20251001"
    else:
        worker_model = "gpt-5-mini"

    return Config(
        server_secret=os.getenv("SERVER_SECRET", ""),
        database_dir=Path(os.getenv("DATABASE_DIR", "./data")),
        port=int(os.getenv("PORT", "18789")),
        brain_model=brain_model,
        worker_model=worker_model,
        cors_origin=os.getenv("CORS_ORIGIN", "http://localhost:3000"),
        openai_api_key=openai_key,
        anthropic_api_key=anthropic_key,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        browser_timeout=int(os.getenv("BROWSER_TIMEOUT", "300")),
        computer_timeout=int(os.getenv("COMPUTER_TIMEOUT", "30")),
        heartbeat_interval=int(os.getenv("HEARTBEAT_INTERVAL", "60")),
        max_tool_iterations=int(os.getenv("MAX_TOOL_ITERATIONS", "50")),
        log_level=os.getenv("LOG_LEVEL", "WARNING"),
        tool_timeout=int(os.getenv("TOOL_TIMEOUT", "60")),
        cdp_port=int(os.getenv("CDP_PORT", "9222")),
        browser_executable=os.getenv("BROWSER_EXECUTABLE", "") or _detect_browser(),
    )


def _detect_browser() -> str:
    """Auto-detect best browser: Brave > Chrome > Chromium.

    Brave preferred because built-in ad/tracker blocking = cleaner pages for LLM.
    """
    candidates = [
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",  # macOS Brave
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",  # macOS Chrome
        "/usr/bin/chromium",         # Debian/Ubuntu/Docker
        "/usr/bin/chromium-browser", # Alpine/older Debian
        "/usr/bin/google-chrome",    # Google Chrome on Linux
    ]
    import shutil

    for path in candidates:
        if os.path.exists(path):
            return path

    # Fall back to system PATH
    for name in ("brave-browser", "brave", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found

    return ""  # No browser found — Playwright will use bundled Chromium


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
