.PHONY: claude-login claude-status up down rebuild logs \
        host-bridge host-bridge-uninstall host-bridge-status host-bridge-restart

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
claude-login:
	@docker compose ps --services --filter status=running | grep -qx lazyclaw \
	    || (echo "lazyclaw container is not running. Run 'make up' first." && exit 1)
	docker exec -it lazyclaw claude /login

# Quick check: is the CLI logged in inside the container?
claude-status:
	@docker exec lazyclaw sh -c '\
	    if [ -s /home/lazyclaw/.claude/.credentials.json ]; then \
	        echo "claude CLI: logged in (credential present in volume)"; \
	    else \
	        echo "claude CLI: NOT logged in. Run: make claude-login"; \
	        exit 1; \
	    fi'

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
