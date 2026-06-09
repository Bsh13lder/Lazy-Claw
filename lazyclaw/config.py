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
    # Bind address for the HTTP gateway. SECURITY: localhost by default so the
    # server is NOT reachable from the LAN/internet unless you opt in (set
    # LAZYCLAW_HOST=0.0.0.0). Auto-set to 0.0.0.0 inside Docker — the container
    # must bind all interfaces so the host's published port forwards in; real
    # host exposure is then controlled by docker-compose's `ports:` mapping.
    host: str = "127.0.0.1"
    brain_model: str = "gpt-5"       # Main agent, team lead, complex fallback
    worker_model: str = "gpt-5-mini"  # Specialists, background jobs, summaries
    cors_origin: str = "http://localhost:3000"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    minimax_api_key: str | None = None
    minimax_base_url: str = "https://api.minimax.io/anthropic"
    # Token Plan tier: starter / plus / max / ultra. Sets the rolling
    # 5-hour request cap (1500 / 4500 / 15000 / 30000). Plus is the
    # default because that's the user's actual subscription.
    minimax_token_plan_tier: str = "plus"
    telegram_bot_token: str | None = None
    browser_timeout: int = 300
    computer_timeout: int = 30
    heartbeat_interval: int = 60
    max_tool_iterations: int = 50
    log_level: str = "WARNING"
    tool_timeout: int = 60
    cdp_port: int = 9222
    browser_executable: str = ""  # Path to browser binary (Brave, Chrome, Chromium)
    # When True, save_memory writes ONLY to LazyBrain (legacy
    # personal_memory table is no longer written to). Reads continue
    # to merge both stores so existing rows stay accessible. Flip via
    # ``MEMORY_UNIFIED=1`` after running ``cli_migrate_lazybrain.py``.
    memory_unified: bool = False


def load_config() -> Config:
    root = get_project_root()
    env_path = root / ".env"
    load_dotenv(env_path, override=True)

    openai_key = os.getenv("OPENAI_API_KEY") or None
    anthropic_key = os.getenv("ANTHROPIC_API_KEY") or None
    minimax_key = os.getenv("MINIMAX_API_KEY") or None
    # MiniMax is reached via its Anthropic-compatible endpoint — MiniMax themselves
    # recommend it for full system/tool/thinking support. Override for China:
    # https://api.minimaxi.com/anthropic
    minimax_base_url = os.getenv(
        "MINIMAX_BASE_URL", "https://api.minimax.io/anthropic"
    ) or "https://api.minimax.io/anthropic"

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

    # Bind address. SECURITY: localhost by default (not reachable off-box).
    # Inside Docker the container must bind all interfaces so the host's
    # published port forwards in; the host-side exposure is controlled by
    # docker-compose's `ports:` mapping. An explicit LAZYCLAW_HOST always wins.
    explicit_host = os.getenv("LAZYCLAW_HOST")
    if explicit_host:
        host = explicit_host.strip()
    elif Path("/.dockerenv").exists():
        host = "0.0.0.0"  # noqa: S104 — intentional: container needs all-ifaces; host publish gates exposure
    else:
        host = "127.0.0.1"

    return Config(
        server_secret=os.getenv("SERVER_SECRET", ""),
        database_dir=Path(os.getenv("DATABASE_DIR", "./data")),
        port=int(os.getenv("PORT", "18789")),
        host=host,
        brain_model=brain_model,
        worker_model=worker_model,
        cors_origin=os.getenv("CORS_ORIGIN", "http://localhost:3000"),
        openai_api_key=openai_key,
        anthropic_api_key=anthropic_key,
        minimax_api_key=minimax_key,
        minimax_base_url=minimax_base_url,
        minimax_token_plan_tier=(os.getenv("MINIMAX_TIER", "plus") or "plus").lower().strip(),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        browser_timeout=int(os.getenv("BROWSER_TIMEOUT", "300")),
        computer_timeout=int(os.getenv("COMPUTER_TIMEOUT", "30")),
        heartbeat_interval=int(os.getenv("HEARTBEAT_INTERVAL", "60")),
        max_tool_iterations=int(os.getenv("MAX_TOOL_ITERATIONS", "50")),
        log_level=os.getenv("LOG_LEVEL", "WARNING"),
        tool_timeout=int(os.getenv("TOOL_TIMEOUT", "60")),
        cdp_port=int(os.getenv("CDP_PORT", "9222")),
        browser_executable=os.getenv("BROWSER_EXECUTABLE", "") or _detect_browser(),
        memory_unified=_env_bool("MEMORY_UNIFIED", default=False),
    )


def _env_bool(name: str, *, default: bool) -> bool:
    """Parse a boolean env var (1/true/yes/on are truthy)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _detect_browser() -> str:
    """Auto-detect best browser: Brave > Chrome > Chromium > Playwright bundle.

    Brave preferred because built-in ad/tracker blocking = cleaner pages for LLM.

    Inside our Docker image (`Dockerfile` line 6-8) Chromium is intentionally
    NOT installed at `/usr/bin/`; only Playwright's bundled Chromium ships,
    under ``$PLAYWRIGHT_BROWSERS_PATH/chromium-XXXX/chrome-linux/chrome``.
    Without probing that path the browser skill fast-fails inside the
    container even though a usable browser binary IS present (causing
    "show me visible" to silently no-op + noVNC canvas blank).
    """
    import glob
    import shutil

    candidates = [
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",  # macOS Brave
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",  # macOS Chrome
        "/usr/bin/chromium",         # Debian/Ubuntu/Docker
        "/usr/bin/chromium-browser", # Alpine/older Debian
        "/usr/bin/google-chrome",    # Google Chrome on Linux
    ]

    for path in candidates:
        if os.path.exists(path):
            return path

    # Fall back to system PATH
    for name in ("brave-browser", "brave", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found

    # Last resort: Playwright's bundled Chromium. Path is versioned
    # (`chromium-1234`), so glob and pick the newest by lexical sort —
    # Playwright versions are monotonic strings. Cross-platform: works
    # for Linux/Docker (`chrome-linux/chrome`) and macOS dev installs
    # (`chrome-mac/Chromium.app/...`).
    pw_root = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "")
    if not pw_root:
        # Default Playwright cache locations
        for default in (
            os.path.expanduser("~/.cache/ms-playwright"),
            "/ms-playwright",  # our Dockerfile sets this explicitly
        ):
            if os.path.isdir(default):
                pw_root = default
                break
    if pw_root and os.path.isdir(pw_root):
        for pattern in (
            os.path.join(pw_root, "chromium-*", "chrome-linux", "chrome"),
            os.path.join(pw_root, "chromium-*", "chrome-mac", "Chromium.app",
                         "Contents", "MacOS", "Chromium"),
        ):
            matches = sorted(glob.glob(pattern), reverse=True)
            for path in matches:
                if os.path.exists(path):
                    return path

    return ""  # No browser found — host bridge is the only remaining path


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
