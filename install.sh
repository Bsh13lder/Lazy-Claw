#!/usr/bin/env bash
#
# LazyClaw Installer
# Usage: ./install.sh        (from cloned repo)
#    or: curl -fsSL <raw-url> | bash
#
# Idempotent - safe to re-run.

set -euo pipefail

# ── Constants ─────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

REPO_URL="https://github.com/Bsh13lder/Lazy-Claw.git"
MIN_PYTHON_MINOR=11

PYTHON_CMD=""
PYTHON_VER=""
REPO_DIR=""

# ── Helpers ───────────────────────────────────────────────────────────────
info()  { printf "${CYAN}[INFO]${RESET}  %s\n" "$*"; }
ok()    { printf "${GREEN}[ OK ]${RESET}  %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${RESET}  %s\n" "$*"; }
fail()  { printf "${RED}[FAIL]${RESET}  %s\n" "$*"; exit 1; }

# ── Phase 1: Find Python 3.11+ ───────────────────────────────────────────
find_python() {
    for cmd in python3.13 python3.12 python3.11 python3; do
        if command -v "$cmd" &>/dev/null; then
            local minor
            minor=$("$cmd" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo "0")
            if [[ "$minor" -ge "$MIN_PYTHON_MINOR" ]]; then
                PYTHON_CMD="$cmd"
                PYTHON_VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
                return 0
            fi
        fi
    done
    return 1
}

install_python() {
    if [[ "$(uname)" != "Darwin" ]]; then
        fail "Python 3.11+ not found. Install it for your OS and re-run."
    fi

    info "Python 3.11+ not found. Installing via Homebrew..."

    if ! command -v brew &>/dev/null; then
        info "Homebrew not found. Installing..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Add brew to PATH for this session
        if [[ -f /opt/homebrew/bin/brew ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -f /usr/local/bin/brew ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
    fi

    brew install python@3.13
    ok "Python 3.13 installed"
}

# ── Phase 2: Ensure pipx ─────────────────────────────────────────────────
ensure_pipx() {
    if command -v pipx &>/dev/null; then
        ok "pipx found"
        return 0
    fi

    info "Installing pipx..."
    if [[ "$(uname)" == "Darwin" ]] && command -v brew &>/dev/null; then
        brew install pipx
    else
        "$PYTHON_CMD" -m pip install --user pipx
    fi
    pipx ensurepath 2>/dev/null || true
    export PATH="$HOME/.local/bin:$PATH"
    ok "pipx installed"
}

# ── Phase 3: Locate repo ─────────────────────────────────────────────────
locate_repo() {
    # Already inside cloned repo?
    if [[ -f "./pyproject.toml" ]] && grep -q 'name = "lazyclaw"' ./pyproject.toml 2>/dev/null; then
        REPO_DIR="$(pwd)"
        ok "Using repo at $REPO_DIR"
        return 0
    fi

    # Piped via curl — clone to ~/lazyclaw
    local target="$HOME/lazyclaw"
    if [[ -d "$target/.git" ]]; then
        info "Repo exists at $target, pulling latest..."
        git -C "$target" pull --ff-only 2>/dev/null || warn "Pull failed, using existing code"
    else
        info "Cloning LazyClaw..."
        git clone "$REPO_URL" "$target"
    fi
    REPO_DIR="$target"
    cd "$REPO_DIR"
    ok "Repo at $REPO_DIR"
}

# ── Phase 4: Install lazyclaw globally ────────────────────────────────────
install_lazyclaw() {
    # Remove old install if present
    if pipx list 2>/dev/null | grep -q "lazyclaw"; then
        info "Removing previous lazyclaw install..."
        pipx uninstall lazyclaw 2>/dev/null || true
    fi

    info "Installing lazyclaw globally (editable)..."
    pipx install --editable "$REPO_DIR" --python "$PYTHON_CMD"

    # Ensure PATH includes pipx bin dir
    export PATH="$HOME/.local/bin:$PATH"
    ok "lazyclaw installed globally"
}

# ── Phase 5: Run setup wizard ─────────────────────────────────────────────
run_setup() {
    if [[ -f "$REPO_DIR/.env" ]]; then
        local secret_val
        secret_val=$(grep "^SERVER_SECRET=" "$REPO_DIR/.env" 2>/dev/null | cut -d= -f2- || true)
        if [[ -n "$secret_val" && "$secret_val" != "change-me-to-a-random-string" ]]; then
            ok "Already configured (.env found)"
            printf "\n  Re-run setup wizard? [y/N] "
            read -r answer
            if [[ "${answer,,}" != "y" ]]; then
                return 0
            fi
        fi
    fi

    info "Running setup wizard..."
    lazyclaw setup
}

# ── Main ──────────────────────────────────────────────────────────────────
main() {
    printf "\n${BOLD}${CYAN}"
    cat << 'LOGO'
   _                     _____ _
  | |    __ _ _____   _ / ____| |
  | |   / _` |_  / | | | |    | | __ ___      __
  | |  | (_| |/ /| |_| | |    | |/ _` \ \ /\ / /
  | |___\__,_/___|\__, | |____| | (_| |\ V  V /
  |______\        |___/ \_____|_|\__,_| \_/\_/
LOGO
    printf "${RESET}\n"
    printf "  ${BOLD}One-command installer${RESET}\n\n"

    # Phase 1: Python
    if find_python; then
        ok "Python $PYTHON_VER ($PYTHON_CMD)"
    else
        install_python
        find_python || fail "Could not find Python 3.11+ after installation"
        ok "Python $PYTHON_VER ($PYTHON_CMD)"
    fi

    # Phase 2: pipx
    ensure_pipx

    # Phase 3: Repo
    locate_repo

    # Phase 4: Install
    install_lazyclaw

    # Phase 5: Setup
    run_setup

    # Done
    printf "\n${GREEN}${BOLD}  Installation complete!${RESET}\n\n"
    printf "  ${BOLD}lazyclaw${RESET}        Chat REPL\n"
    printf "  ${BOLD}lazyclaw start${RESET}  Full server (API + Telegram + TUI)\n"
    printf "  ${BOLD}lazyclaw setup${RESET}  Re-run setup wizard\n\n"

    if ! command -v lazyclaw &>/dev/null; then
        warn "Open a new terminal for the 'lazyclaw' command to be available."
    fi
}

main "$@"
