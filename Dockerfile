FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lazyclaw/ ./lazyclaw/
COPY pyproject.toml ./
COPY mcp-freeride/ ./mcp-freeride/
COPY mcp-healthcheck/ ./mcp-healthcheck/
COPY mcp-apihunter/ ./mcp-apihunter/
COPY mcp-vaultwhisper/ ./mcp-vaultwhisper/
COPY mcp-taskai/ ./mcp-taskai/
RUN pip install --no-cache-dir ./mcp-freeride ./mcp-healthcheck ./mcp-apihunter ./mcp-vaultwhisper ./mcp-taskai
RUN pip install -e .
COPY .env.example .env.example
EXPOSE 18789
CMD ["lazyclaw", "start"]
