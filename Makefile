.PHONY: claude-login claude-status up down rebuild logs \
        host-bridge host-bridge-uninstall host-bridge-status host-bridge-restart \
        host-stt host-stt-uninstall host-stt-status host-stt-restart \
        awake-bridge awake-bridge-uninstall awake-bridge-status awake-bridge-restart \
        funnel-watchdog funnel-watchdog-uninstall funnel-watchdog-status

up:
	docker compose up -d

down:
	docker compose down

rebuild:
	docker compose build lazyclaw && docker compose up -d lazyclaw

logs:
	docker compose logs -f lazyclaw

# Open Claude Code's OAuth login flow inside the running container.
# Credential lands in the persistent `claude_creds` volume and survives
# `docker compose down` and image rebuilds. See scripts/docker-entrypoint.sh.
# `claude /login` was removed as a CLI argument — on 2.1.197 it answers
# "/login isn't available in this environment" and exits 0, so the old
# target looked like it worked while leaving the token expired. The
# subcommand is `claude auth login`. ANTHROPIC_API_KEY is blanked for the
# duration because a set API key outranks the claude.ai OAuth login.
claude-login:
	@docker compose ps --services --filter status=running | grep -qx lazyclaw \
	    || (echo "lazyclaw container is not running. Run 'make up' first." && exit 1)
	docker exec -it -e ANTHROPIC_API_KEY= -e ANTHROPIC_AUTH_TOKEN= lazyclaw claude auth login

# Quick check: is the CLI logged in inside the container?
# Checks the token's EXPIRY, not just that the file exists. The old
# file-presence check reported "logged in" for a token that had been
# dead for 37 days (2026-07-25 outage) — and `claude auth status` is no
# better, it also answers loggedIn:true on an expired token.
claude-status:
	@docker exec lazyclaw python3 -c 'import json,sys,time; \
	    p="/home/lazyclaw/.claude/.credentials.json"; \
	    d=json.load(open(p)); o=d.get("claudeAiOauth") or d; \
	    exp=int(o.get("expiresAt") or 0); now=int(time.time()*1000); \
	    ok=exp>now or bool(str(o.get("refreshToken") or "").strip()); \
	    print("claude CLI: logged in (token valid)" if ok \
	        else "claude CLI: TOKEN EXPIRED and not refreshable. Run: make claude-login"); \
	    sys.exit(0 if ok else 1)' 2>/dev/null \
	  || (echo "claude CLI: NOT logged in or credential unreadable. Run: make claude-login" && exit 1)

# ── Host Brave bridge — auto-start helper for Docker on macOS ────────────────
# `make host-bridge`  installs a launchd plist that auto-launches your real
# Brave with --remote-debugging-port=9222 every time you log in. Then the
# LazyClaw container can drive your real Brave (with all your cookies +
# logins) via host.docker.internal:9222 — Cloudflare doesn't see a bot,
# Upwork/Reddit/Gmail use your live session. Watching = your actual Brave
# window, no noVNC needed.
#
# After running this once, restart the container so it picks up the
# LAZYCLAW_HOST_CDP_TOKEN env var the installer added to .env.
host-bridge:
	@bash scripts/install-host-brave-bridge.sh
	@echo ""
	@echo "Now: make rebuild   # so the container reads the new .env token"

# Remove the launchd plist + clean .env. Brave keeps running until you
# Cmd+Q it; then it won't auto-restart anymore.
host-bridge-uninstall:
	@bash scripts/uninstall-host-brave-bridge.sh

# Show whether the helper is installed + reachable.
host-bridge-status:
	@if [ -f "$$HOME/Library/LaunchAgents/sh.lazyclaw.brave-bridge.plist" ]; then \
	    echo "launchd plist:  installed at ~/Library/LaunchAgents/sh.lazyclaw.brave-bridge.plist"; \
	else \
	    echo "launchd plist:  NOT installed (run: make host-bridge)"; \
	fi
	@if grep -qE '^LAZYCLAW_HOST_CDP_TOKEN=' .env 2>/dev/null; then \
	    echo "shared token:   set in .env"; \
	else \
	    echo "shared token:   NOT set in .env"; \
	fi
	@if curl -fsS --max-time 2 http://localhost:9222/json/version >/dev/null 2>&1; then \
	    echo "Brave CDP:      reachable on localhost:9222 ✓"; \
	else \
	    echo "Brave CDP:      not reachable (Brave may be quit; reopen it)"; \
	fi

# Force-restart the launchd-managed Brave (e.g. after editing the plist
# or to recover from a hung Brave).
host-bridge-restart:
	@launchctl kickstart -k "gui/$$(id -u)/sh.lazyclaw.brave-bridge" \
	    && echo "Brave restarted via launchd."

# ── Host STT bridge — Metal-accelerated whisper.cpp on the macOS host ────────
# Docker on macOS runs Linux containers in a VM that can't access Metal or
# Core ML, so pywhispercpp inside the container falls back to CPU (~5x
# slower than Metal). `make host-stt` installs a launchd-managed FastAPI
# service on the host that runs whisper.cpp natively with Metal+ANE; the
# container POSTs audio to it via host.docker.internal:18790. Falls back
# to CPU whisper transparently if the bridge is down.
host-stt:
	@bash scripts/install-host-stt-bridge.sh
	@echo ""
	@echo "Now: docker compose restart lazyclaw   # so the container reads the new .env token"

host-stt-uninstall:
	@bash scripts/uninstall-host-stt-bridge.sh

host-stt-status:
	@if [ -f "$$HOME/Library/LaunchAgents/sh.lazyclaw.stt-bridge.plist" ]; then \
	    echo "launchd plist:  installed at ~/Library/LaunchAgents/sh.lazyclaw.stt-bridge.plist"; \
	else \
	    echo "launchd plist:  NOT installed (run: make host-stt)"; \
	fi
	@if grep -qE '^LAZYCLAW_HOST_STT_TOKEN=' .env 2>/dev/null; then \
	    echo "shared token:   set in .env"; \
	else \
	    echo "shared token:   NOT set in .env"; \
	fi
	@if curl -fsS --max-time 2 http://localhost:18790/health >/dev/null 2>&1; then \
	    echo "STT service:    healthy on localhost:18790 ✓"; \
	    curl -s http://localhost:18790/health | python3 -m json.tool 2>/dev/null || true; \
	else \
	    echo "STT service:    not reachable (check ~/Library/Logs/LazyClaw/stt-bridge.err)"; \
	fi

host-stt-restart:
	@launchctl kickstart -k "gui/$$(id -u)/sh.lazyclaw.stt-bridge" \
	    && echo "Host STT bridge restarted via launchd."

# ── Host AWAKE bridge — lid-closed sleep prevention + scheduled wake ──────────
# `make awake-bridge` installs a root LaunchDaemon that lets the agent keep
# the Mac awake when the lid is closed (caffeinate) and schedule hardware
# wake-up alarms (pmset). Needs one-time sudo.
#
# After installing, restart the container so it picks up the new token:
#   docker compose restart lazyclaw
#
# Then say "stay awake" in Telegram or click the badge in the Web UI.
awake-bridge:
	@bash scripts/install-host-awake-bridge.sh
	@echo ""
	@echo "Now: docker compose restart lazyclaw   # so the container reads the new .env token"

awake-bridge-uninstall:
	@bash scripts/uninstall-host-awake-bridge.sh

awake-bridge-status:
	@if [ -f "/Library/LaunchDaemons/sh.lazyclaw.awake-bridge.plist" ]; then \
	    echo "launchd plist:  installed at /Library/LaunchDaemons/sh.lazyclaw.awake-bridge.plist"; \
	else \
	    echo "launchd plist:  NOT installed (run: make awake-bridge)"; \
	fi
	@if grep -qE '^LAZYCLAW_HOST_AWAKE_TOKEN=' .env 2>/dev/null; then \
	    echo "shared token:   set in .env"; \
	else \
	    echo "shared token:   NOT set in .env"; \
	fi
	@if curl -fsS --max-time 2 http://localhost:18791/health >/dev/null 2>&1; then \
	    echo "AWAKE service:  healthy on localhost:18791 ✓"; \
	    curl -s http://localhost:18791/health | python3 -m json.tool 2>/dev/null || true; \
	else \
	    echo "AWAKE service:  not reachable (check ~/Library/Logs/LazyClaw/awake-bridge.err)"; \
	fi

awake-bridge-restart:
	@sudo launchctl kickstart -k "system/sh.lazyclaw.awake-bridge" \
	    && echo "Awake bridge restarted via launchd."

# `make funnel-watchdog` installs a user LaunchAgent that curls the PUBLIC
# Tailscale Funnel URL every 3 min and re-registers the funnel when it has
# silently wedged (the LAN-IP-change failure no local tailscale signal
# reports — 2026-07-30 outage). Config: ~/.lazyclaw/funnel-watchdog.env.
funnel-watchdog:
	@bash scripts/install-funnel-watchdog.sh

funnel-watchdog-uninstall:
	@launchctl bootout "gui/$$(id -u)" "$$HOME/Library/LaunchAgents/sh.lazyclaw.funnel-watchdog.plist" 2>/dev/null || true
	@rm -f "$$HOME/Library/LaunchAgents/sh.lazyclaw.funnel-watchdog.plist" "$$HOME/.lazyclaw/funnel-watchdog.sh"
	@echo "Funnel watchdog removed (env + log kept in ~/.lazyclaw)."

funnel-watchdog-status:
	@if [ -f "$$HOME/Library/LaunchAgents/sh.lazyclaw.funnel-watchdog.plist" ]; then \
	    echo "launchd plist:  installed"; \
	    launchctl print "gui/$$(id -u)/sh.lazyclaw.funnel-watchdog" 2>/dev/null | grep -E "state|last exit" | sed 's/^/  /'; \
	else \
	    echo "launchd plist:  NOT installed (run: make funnel-watchdog)"; \
	fi
	@tail -n 5 "$$HOME/.lazyclaw/funnel-watchdog.log" 2>/dev/null || echo "(no log yet)"
