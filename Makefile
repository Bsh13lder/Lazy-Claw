.PHONY: claude-login claude-status up down rebuild logs

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
