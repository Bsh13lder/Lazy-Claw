FROM python:3.11-slim

# Install Node.js (for npx/node MCP servers like claude-code, stripe, mcp-whatsapp)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lazyclaw/ ./lazyclaw/
COPY pyproject.toml ./

# Required bundled MCP servers (Python)
COPY mcp-freeride/ ./mcp-freeride/
COPY mcp-healthcheck/ ./mcp-healthcheck/
COPY mcp-apihunter/ ./mcp-apihunter/
COPY mcp-vaultwhisper/ ./mcp-vaultwhisper/
COPY mcp-taskai/ ./mcp-taskai/
COPY mcp-lazydoctor/ ./mcp-lazydoctor/
RUN pip install --no-cache-dir \
        ./mcp-freeride \
        ./mcp-healthcheck \
        ./mcp-apihunter \
        ./mcp-vaultwhisper \
        ./mcp-taskai \
        ./mcp-lazydoctor

# Optional bundled MCP servers (Python) — available for /mcp install
COPY mcp-instagram/ ./mcp-instagram/
COPY mcp-email/ ./mcp-email/
COPY mcp-jobspy/ ./mcp-jobspy/

# Optional bundled MCP server (Node.js) — npm install on demand
COPY mcp-whatsapp/ ./mcp-whatsapp/

RUN pip install -e .
COPY .env.example .env.example
EXPOSE 18789
CMD ["lazyclaw", "start"]
