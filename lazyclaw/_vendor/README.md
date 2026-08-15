# Vendored packages

## browser_use (subset) — v0.13.7, MIT

Upstream: <https://github.com/browser-use/browser-use> (LICENSE preserved in
`browser_use/LICENSE`). Vendored 2026-08-15 because upstream pins its entire
dependency tree with `==` (pydantic, httpx, aiohttp + five LLM SDKs) —
installing the wheel would capture the app's core dependencies. Decision and
evidence: `docs/ARCHITECTURE_REVIEW_2026-08-15.md` §5 + plan of 2026-08-15.

**Included** (the CDP action layer only):

- `actor/` — Page / Element / Mouse (playground demos removed)
- `dom/` — DomService + serializer (playground removed)
- `llm/` — top-level message/type files only (no provider SDK subdirs)
- `config.py`, `utils.py`, `observability.py`, `logging_config.py`,
  `exceptions.py` — support modules the above import

**Excluded on purpose**: `Agent`, `Tools`, `BrowserSession`, watchdogs,
`bubus` event bus, telemetry (PostHog), MCP/CLI/sync/integrations, all LLM
provider adapters. LazyClaw drives the actor layer from its own TAOR loop
via `lazyclaw/browser/browser_use_backend.py`.

**Patched files** (keep this list current):

- `browser_use/__init__.py` — replaced: no `setup_logging()` side effects,
  exports only `logger` + `__version__`.

**Runtime deps** (declared in the main `pyproject.toml`, all loose-pinned):
`cdp-use` (MIT), `uuid7` (MIT), `psutil` (BSD-3); plus already-present
`pydantic`, `pydantic-settings`, `httpx`, `websockets`.

**Upgrade procedure**: install the target `browser-use==X.Y.Z` in a scratch
venv, re-copy the included paths, re-apply the patched files above, rerun
`tests/browser/` + the import-isolation test (asserts no LLM SDK / posthog /
bubus modules load).
