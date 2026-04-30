FROM python:3.11-slim

# ---------------------------------------------------------------------------
# Stable system layers (rarely change → stay cached).
# Order rule: each `apt install` block is independent; volatile COPYs come last.
# Note: in-container Chromium is intentionally NOT installed. crawl4ai uses
# Playwright's bundled Chromium (installed below); the host Brave bridge via
# `host.docker.internal` covers the user-browser path. Saves ~765 MB.
# ---------------------------------------------------------------------------

# tini as PID 1 — reaps zombie children (Chromium spawns many).
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Node.js 20 (for npx/node MCP servers — claude-code, mcp-whatsapp).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Runtime libs:
# - lsof: ram_monitor.py port checks
# - patch: workspace-mcp local patch step below
# - VNC stack (xvfb / x11vnc / websockify / novnc): share_browser_control remote takeover
RUN apt-get update && apt-get install -y --no-install-recommends \
        lsof \
        patch \
        xvfb \
        x11vnc \
        python3-websockify \
        novnc \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------------------------------------------------------------------------
# Heavy installs that DO NOT depend on MCP / lazyclaw source. These are pinned
# above the volatile COPYs so editing an MCP doesn't re-download Playwright,
# re-install Claude CLI, or re-install npm deps.
# ---------------------------------------------------------------------------

# Python deps from requirements.txt (busts only when requirements.txt changes).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright Python pkg + Chromium binary. Installed explicitly here so the
# 1 GB Chromium download is cached above the volatile MCP COPYs. crawl4ai
# (mcp-scraper) imports playwright; pip sees it already installed and skips.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium \
    && chmod -R a+rx /ms-playwright

# Claude Code CLI (for claude-code MCP — agent uses Claude as coding tool).
RUN npm install -g @anthropic-ai/claude-code

# mcp-whatsapp Node deps (split package.json from src/ so source edits don't
# bust npm ci).
COPY mcp-whatsapp/package.json mcp-whatsapp/package-lock.json ./mcp-whatsapp/
RUN cd mcp-whatsapp && npm ci --omit=dev && cd ..

# ---------------------------------------------------------------------------
# Patches (local fork of pip-installed packages).
# ---------------------------------------------------------------------------
COPY patches/ ./patches/
RUN SITE="$(python3 -c 'import auth, os; print(os.path.abspath(os.path.dirname(auth.__file__) + "/.."))')" \
    && echo "patching workspace-mcp in $SITE" \
    && patch -p1 -d "$SITE" < patches/workspace-mcp-login-hint.patch

# ---------------------------------------------------------------------------
# Non-root user — created BEFORE the volatile COPYs so we can use
# COPY --chown on each file individually. This avoids a 371 MB ghost layer
# from `RUN chown -R lazyclaw:lazyclaw /app` (which rewrites every file's
# metadata into a fresh layer).
# ---------------------------------------------------------------------------
RUN groupadd -r lazyclaw && useradd -r -g lazyclaw -m -d /home/lazyclaw lazyclaw \
    && mkdir -p /home/lazyclaw/.claude \
    && chown -R lazyclaw:lazyclaw /home/lazyclaw/.claude /app

# ---------------------------------------------------------------------------
# Volatile COPYs (in increasing volatility order so cache stays warm longer).
# Each MCP package is a small COPY (KB) followed by a small pip install — only
# the touched MCP busts its own layers.
# ---------------------------------------------------------------------------

# Bundled MCP servers (Python — active only).
COPY --chown=lazyclaw:lazyclaw mcp-taskai/ ./mcp-taskai/
COPY --chown=lazyclaw:lazyclaw mcp-lazydoctor/ ./mcp-lazydoctor/
RUN pip install --no-cache-dir \
        ./mcp-taskai \
        ./mcp-lazydoctor

# Optional bundled MCP servers (Python).
COPY --chown=lazyclaw:lazyclaw mcp-instagram/ ./mcp-instagram/
COPY --chown=lazyclaw:lazyclaw mcp-email/ ./mcp-email/
COPY --chown=lazyclaw:lazyclaw mcp-jobspy/ ./mcp-jobspy/
COPY --chown=lazyclaw:lazyclaw mcp-scraper/ ./mcp-scraper/
RUN pip install --no-cache-dir \
        ./mcp-instagram \
        ./mcp-email \
        ./mcp-jobspy \
        ./mcp-scraper \
        n8n-mcp-server

# mcp-whatsapp source (deps already installed above).
COPY --chown=lazyclaw:lazyclaw mcp-whatsapp/src/ ./mcp-whatsapp/src/

# Most volatile — main app source comes last.
COPY --chown=lazyclaw:lazyclaw personality/ ./personality/
COPY --chown=lazyclaw:lazyclaw pyproject.toml .env.example ./
COPY --chown=lazyclaw:lazyclaw lazyclaw/ ./lazyclaw/
RUN pip install -e .

# Entrypoint — runs as lazyclaw user, symlinks named-volume Claude creds.
COPY --chown=lazyclaw:lazyclaw scripts/docker-entrypoint.sh /usr/local/bin/lazyclaw-entrypoint.sh
RUN chmod +x /usr/local/bin/lazyclaw-entrypoint.sh

USER lazyclaw

EXPOSE 18789
ENTRYPOINT ["tini", "--", "/usr/local/bin/lazyclaw-entrypoint.sh"]
CMD ["lazyclaw", "start"]
