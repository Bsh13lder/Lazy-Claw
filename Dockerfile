FROM python:3.11-slim

# Install tini as PID 1 init — reaps zombie child processes (Chromium spawns many)
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Node.js (for npx/node MCP servers like claude-code, stripe, mcp-whatsapp)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Chromium for browser automation (site watchers, CDP)
# lsof needed by ram_monitor.py for port checks
# VNC stack: Xvfb (virtual display) + x11vnc + websockify + noVNC for remote takeover
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        lsof \
        xvfb \
        x11vnc \
        python3-websockify \
        novnc \
        patch \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Apply local patches to pip-installed packages. Right now: workspace-mcp
# login_hint fix so Google's consent screen pre-selects the correct
# account (see docs/adr/0003-direct-google-api-over-n8n.md and upstream
# PR taylorwilsdon/google_workspace_mcp#556).
COPY patches/ ./patches/
RUN SITE="$(python3 -c 'import auth, os; print(os.path.abspath(os.path.dirname(auth.__file__) + "/.."))')" \
    && echo "patching workspace-mcp in $SITE" \
    && patch -p1 -d "$SITE" < patches/workspace-mcp-login-hint.patch

COPY lazyclaw/ ./lazyclaw/
COPY personality/ ./personality/
COPY pyproject.toml ./

# Bundled MCP servers (Python — active only)
COPY mcp-taskai/ ./mcp-taskai/
COPY mcp-lazydoctor/ ./mcp-lazydoctor/
RUN pip install --no-cache-dir \
        ./mcp-taskai \
        ./mcp-lazydoctor

# Optional bundled MCP servers (Python)
COPY mcp-instagram/ ./mcp-instagram/
COPY mcp-email/ ./mcp-email/
COPY mcp-jobspy/ ./mcp-jobspy/
COPY mcp-scraper/ ./mcp-scraper/
RUN pip install --no-cache-dir \
        ./mcp-instagram \
        ./mcp-email \
        ./mcp-jobspy \
        ./mcp-scraper \
        n8n-mcp-server

# Playwright browser binaries for mcp-scraper (JS-rendered crawl).
# Chromium binary is already in /usr/bin/chromium (system pkg) but Playwright
# needs its own pinned build; --with-deps installs the X11/font shared libs.
# PLAYWRIGHT_BROWSERS_PATH sets a shared location readable by the non-root
# `lazyclaw` user that's added at the end of this Dockerfile (default would
# install into root's HOME and break for the runtime user).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium \
    && chmod -R a+rx /ms-playwright

# Claude Code CLI (for claude-code MCP — agent uses Claude as coding tool)
RUN npm install -g @anthropic-ai/claude-code

# Optional bundled MCP server (Node.js)
COPY mcp-whatsapp/package.json mcp-whatsapp/package-lock.json ./mcp-whatsapp/
RUN cd mcp-whatsapp && npm ci --omit=dev && cd ..
COPY mcp-whatsapp/src/ ./mcp-whatsapp/src/

RUN pip install -e .
COPY .env.example .env.example

# Create non-root user — required for Claude CLI
# (refuses --dangerously-skip-permissions when running as root)
RUN groupadd -r lazyclaw && useradd -r -g lazyclaw -m -d /home/lazyclaw lazyclaw \
    && chown -R lazyclaw:lazyclaw /app

# Pre-create the credential-volume mount point owned by lazyclaw so the
# Docker named volume inherits non-root ownership on first init.
# The entrypoint script symlinks ~/.claude/.credentials.json into here,
# letting `claude /login` inside the container persist across rebuilds.
RUN mkdir -p /home/lazyclaw/.claude-creds /home/lazyclaw/.claude \
    && chown -R lazyclaw:lazyclaw /home/lazyclaw/.claude-creds /home/lazyclaw/.claude

COPY --chown=lazyclaw:lazyclaw scripts/docker-entrypoint.sh /usr/local/bin/lazyclaw-entrypoint.sh
RUN chmod +x /usr/local/bin/lazyclaw-entrypoint.sh

USER lazyclaw

EXPOSE 18789
ENTRYPOINT ["tini", "--", "/usr/local/bin/lazyclaw-entrypoint.sh"]
CMD ["lazyclaw", "start"]
