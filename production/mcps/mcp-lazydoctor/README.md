# mcp-lazydoctor

**Self-Healing Doctor MCP Server** -- Diagnose, lint, test, and auto-fix Python projects. The AI's built-in code quality guardian.

## What It Does

Gives your AI agent the ability to run diagnostics on Python projects and automatically fix issues:

- **Diagnose** -- Run all checks (lint + tests) in parallel
- **Lint** -- Run ruff linter, parse errors and warnings
- **Test** -- Run pytest with short traceback
- **Fix** -- Auto-fix lint issues via `ruff check --fix`
- **Heal** -- Complete diagnose-fix-verify cycle (the magic button)

The `doctor_heal` tool is the star: it diagnoses problems, applies fixes, re-runs diagnostics to verify, and reports the result. One tool call to go from broken to healthy.

## Architecture

```
AI Agent <--stdio--> mcp-lazydoctor --> ruff (lint/fix)
                                   --> pytest (tests)
                                   --> git (status/diff)
```

- **Runtime**: Python 3.11+
- **Transport**: stdio (MCP standard)
- **Linter**: ruff (must be installed)
- **Test runner**: pytest (must be installed)
- **Git**: Optional, for status/diff info

## Setup

### Prerequisites
- Python 3.11+
- ruff (`pip install ruff`)
- pytest (`pip install pytest`)

### Install
```bash
cd production/mcps/mcp-lazydoctor
pip install -e .
```

### Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `LAZYDOCTOR_PROJECT_ROOT` | `.` | Path to the Python project to diagnose |
| `LAZYDOCTOR_AUTO_FIX` | `true` | Enable auto-fix in heal cycle |
| `LAZYDOCTOR_DRY_RUN` | `false` | Preview fixes without applying |

### Register with LazyClaw
```json
{
  "name": "mcp-lazydoctor",
  "command": "python",
  "args": ["-m", "mcp_lazydoctor"],
  "transport": "stdio",
  "env": {
    "LAZYDOCTOR_PROJECT_ROOT": "/path/to/your/project"
  }
}
```

## Available Tools (5)

| Tool | Description |
|------|-------------|
| `doctor_diagnose` | Run all diagnostics (lint + tests) in parallel |
| `doctor_lint` | Run ruff linter only |
| `doctor_test` | Run pytest only |
| `doctor_fix` | Auto-fix lint issues (supports dry_run) |
| `doctor_heal` | Full diagnose -> fix -> verify cycle |

### The Heal Cycle

`doctor_heal` runs this loop:
1. Diagnose (lint + tests in parallel)
2. If all pass -> report "healthy"
3. If failures -> apply auto-fixes
4. Re-diagnose to verify
5. Report: "healthy", "healed", or "needs_attention"

---

## Analysis

### Viral Potential: LOW

**Reasoning**: This is an internal developer tool, not a user-facing feature. End users don't care about linting. Developers might find it useful, but "AI runs ruff on my code" isn't shareable content. It's infrastructure -- valuable but invisible.

**Key use cases**:
- AI auto-fixes lint errors after writing code
- CI/CD integration for automated code quality
- Teaching tool for Python best practices

### Known Bugs & Issues

1. **Ruff-only** -- Only supports ruff for linting. Doesn't support flake8, pylint, mypy, or other common Python tools.
2. **Pytest-only** -- Only supports pytest. Doesn't support unittest or nose.
3. **120-second timeout** -- Commands timeout after 120s. Large test suites may fail silently.
4. **Output truncated to 4000 chars** -- Large test outputs are silently cut off. The agent won't see the full error if a test suite generates verbose output.
5. **No virtual env awareness** -- Runs ruff/pytest from system PATH. If the project uses a venv, the tools must be installed in the system Python or the MCP server must run inside the venv.
6. **Git ops unused** -- `git_ops.py` has stash/diff functions but they're not wired into any tool. Dead code.
7. **Custom `__init__.py` with .pyc loader** -- The import hook in `__init__.py` is non-standard and potentially fragile. Unclear why it's needed.
8. **Heal cycle only runs once** -- If auto-fix doesn't resolve all issues on the first pass, it reports "needs_attention" instead of retrying. Some issues need multiple fix passes.

### Public vs Private Recommendation: PUBLIC

**Recommendation**: Open-source. Good developer utility with zero security risk.

**Why public**:
- Zero security concerns -- runs linters on local code
- Good educational value -- shows how to wrap CLI tools as MCP
- Useful for any Python developer, not just LazyClaw users
- Demonstrates the "self-healing agent" concept

**Before release**:
- Remove dead code (`git_ops.py` functions that aren't wired up)
- Remove the unusual `__init__.py` import hook or document why it exists
- Add support for at least mypy alongside ruff
- Consider increasing output truncation limit or making it configurable
