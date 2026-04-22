FROM python:3.11-slim

# Install tini as PID 1 init — reaps zombie child processes (Chromium spawns many)
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["tini", "--"]

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
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
RUN pip install --no-cache-dir \
        ./mcp-instagram \
        ./mcp-email \
        ./mcp-jobspy \
        n8n-mcp-server

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
USER lazyclaw

EXPOSE 18789
CMD ["lazyclaw", "start"]
