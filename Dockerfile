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
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        lsof \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lazyclaw/ ./lazyclaw/
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

# Optional bundled MCP server (Node.js)
COPY mcp-whatsapp/package.json mcp-whatsapp/package-lock.json ./mcp-whatsapp/
RUN cd mcp-whatsapp && npm ci --omit=dev && cd ..
COPY mcp-whatsapp/src/ ./mcp-whatsapp/src/

RUN pip install -e .
COPY .env.example .env.example
EXPOSE 18789
CMD ["lazyclaw", "start"]
