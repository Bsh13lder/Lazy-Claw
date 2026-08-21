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

# Google MCP Toolbox for Databases (Apache-2.0). Single static Go binary — the
# bundled `db-toolbox` MCP (see lazyclaw/mcp/manager.py). No Python dep, so the
# license gate is untouched. Pinned; bump deliberately and re-verify the CLI
# flags in the BUNDLED_MCPS entry.
# Upstream ships linux/amd64 ONLY (no linux/arm64). db-toolbox is optional and
# stays inert unless its binary is on PATH, so on other build arches we skip the
# install rather than fail the whole image — apihunter etc. still build, and
# db-toolbox simply won't appear until built on amd64 (e.g. prod).
ARG TOOLBOX_VERSION=0.6.0
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    if [ "$arch" = "amd64" ]; then \
        curl -fsSL -o /usr/local/bin/toolbox \
            "https://storage.googleapis.com/genai-toolbox/v${TOOLBOX_VERSION}/linux/amd64/toolbox"; \
        chmod +x /usr/local/bin/toolbox; \
        /usr/local/bin/toolbox --version; \
    else \
        echo "toolbox: no linux/${arch} build upstream — skipping (db-toolbox MCP stays inert on this arch)"; \
    fi

# Runtime libs:
# - lsof: ram_monitor.py port checks
# - patch: workspace-mcp local patch step below
# - VNC stack (xvfb / x11vnc / websockify / novnc): share_browser_control remote takeover
# - tesseract-ocr (+eng): browser `ocr` action fallback — Apple Vision never
#   exists inside the Linux container, so without the binary the whole OCR
#   ladder is dead (2026-07-31 upwork task burned its budget partly on this)
RUN apt-get update && apt-get install -y --no-install-recommends \
        lsof \
        patch \
        xvfb \
        x11vnc \
        python3-websockify \
        novnc \
        tesseract-ocr \
        tesseract-ocr-eng \
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
# Belt-and-suspenders: the vendored browser-use subset excludes telemetry
# entirely, but if the full package is ever installed this keeps PostHog off.
ENV ANONYMIZED_TELEMETRY=False
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
COPY --chown=lazyclaw:lazyclaw mcp-upwork/ ./mcp-upwork/
COPY --chown=lazyclaw:lazyclaw mcp-apihunter/ ./mcp-apihunter/
RUN pip install --no-cache-dir \
        ./mcp-instagram \
        ./mcp-email \
        ./mcp-jobspy \
        ./mcp-scraper \
        ./mcp-upwork \
        ./mcp-apihunter \
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

# ---------------------------------------------------------------------------
# Deploy stamp — answers "what code is this container running?".
# `make rebuild` runs scripts/write-build-info.sh first, which writes
# BUILD_INFO.json (git sha + dirty flag + timestamp) to the build context.
# The gateway serves it at GET /api/health as the `build` object.
#
# The `[n]` in BUILD_INFO.jso[n] is the optional-COPY idiom: a bare
# `COPY BUILD_INFO.json` hard-fails a plain `docker compose build` run without
# the script, and a wildcard matching nothing is also an error — so pyproject
# .toml rides along as the anchor that guarantees ≥1 match. Deliberately the
# LAST layer: the stamp changes every build, so anything below it would never
# hit cache.
# ---------------------------------------------------------------------------
COPY --chown=lazyclaw:lazyclaw pyproject.toml BUILD_INFO.jso[n] ./
RUN [ -f /app/BUILD_INFO.json ] \
    || echo '{"sha":"unknown","dirty":null,"built_at":"unknown"}' > /app/BUILD_INFO.json \
    && chown lazyclaw:lazyclaw /app/BUILD_INFO.json

USER lazyclaw

EXPOSE 18789
ENTRYPOINT ["tini", "--", "/usr/local/bin/lazyclaw-entrypoint.sh"]
CMD ["lazyclaw", "start"]
