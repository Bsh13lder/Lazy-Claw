# LazyClaw MCP Servers

Production bundle of all MCP (Model Context Protocol) servers for LazyClaw. Each server is a standalone process that exposes tools to the AI agent via stdio transport.

## What's Here

```
production/mcps/
  mcp-whatsapp/       WhatsApp messaging via Baileys WebSocket (Node.js)
  mcp-email/          Email via SMTP+IMAP (Python)
  mcp-instagram/      Instagram via private mobile API (Python)
  mcp-jobspy/         Job search across 5 platforms (Python)
  mcp-lazydoctor/     Python project linting, testing, auto-fix (Python)
  start_all.sh        Start all MCP servers
  README.md           This file
```

## Quick Start

```bash
# Install all dependencies
./start_all.sh install

# Start all servers (each runs in background)
./start_all.sh start

# Or start individually:
cd mcp-whatsapp && npm install && node src/index.js
cd mcp-email && pip install -e . && python -m mcp_email
cd mcp-instagram && pip install -e . && python -m mcp_instagram
cd mcp-jobspy && pip install -e . && python -m mcp_jobspy
cd mcp-lazydoctor && pip install -e . && python -m mcp_lazydoctor
```

## Server Overview

| Server | Runtime | Tools | Transport | Auth Required | API Keys |
|--------|---------|-------|-----------|---------------|----------|
| mcp-whatsapp | Node.js 18+ | 8 | stdio | QR code scan | None |
| mcp-email | Python 3.11+ | 11 | stdio | Email + App Password | None |
| mcp-instagram | Python 3.11+ | 20 | stdio | Username + Password | None |
| mcp-jobspy | Python 3.11+ | 1 | stdio | None | None |
| mcp-lazydoctor | Python 3.11+ | 5 | stdio | None | None |

## Registering with LazyClaw

Each MCP server connects to LazyClaw via the MCP manager. You can register them:

**Via the agent** (recommended):
```
"Add MCP server mcp-whatsapp with command node and args ['production/mcps/mcp-whatsapp/src/index.js']"
```

**Via the API**:
```bash
curl -X POST http://localhost:18789/api/mcp/servers \
  -H "Content-Type: application/json" \
  -d '{"name": "mcp-whatsapp", "command": "node", "args": ["production/mcps/mcp-whatsapp/src/index.js"], "transport": "stdio"}'
```

**Via config** (in LazyClaw's MCP manager):
```json
{
  "name": "mcp-email",
  "command": "python",
  "args": ["-m", "mcp_email"],
  "transport": "stdio"
}
```

---

## Decision Matrix

### Viral Potential

| Server | Viral Rating | Reasoning |
|--------|-------------|-----------|
| **mcp-whatsapp** | **HIGH** | 2B+ WhatsApp users. "My AI reads my WhatsApp" is instantly shareable. No other open-source agent has this. |
| **mcp-email** | **HIGH** | Universal use case. AI email triage/organization is a killer demo. 11 tools = feels complete. |
| **mcp-instagram** | **HIGH** | 2B+ users. 20 tools covering DMs, posting, stories, reels. Creator economy is massive. |
| **mcp-jobspy** | **MEDIUM** | Useful but seasonal. Multi-platform aggregation is a real time-saver but not daily use. |
| **mcp-lazydoctor** | **LOW** | Internal dev tool. Valuable infrastructure but invisible to end users. Not shareable. |

### Public vs Private Recommendation

| Server | Recommendation | Reasoning |
|--------|---------------|-----------|
| **mcp-whatsapp** | **PUBLIC** | Highest viral potential. Baileys is already MIT. Working code, not a stub. The #1 attraction for new users. |
| **mcp-email** | **PUBLIC** | Standard protocols (SMTP/IMAP), no proprietary risk. Table-stakes for any agent platform. Feature-rich. |
| **mcp-instagram** | **PUBLIC** | 20 tools, highest coverage. instagrapi is already public (10K+ GitHub stars). Creator economy demand is massive. Add disclaimers about ToS risk. |
| **mcp-jobspy** | **PUBLIC** | Wraps MIT library. Zero credentials. Low risk. Good showcase of MCP pattern. |
| **mcp-lazydoctor** | **PUBLIC** | Zero security concerns. Good developer utility. Educational value for MCP community. |

### Release Priority

| Priority | Server | Action Before Release |
|----------|--------|-----------------------|
| 1 | mcp-whatsapp | Encrypt session storage, add reconnect backoff, add .env.example |
| 2 | mcp-email | Encrypt credentials, remove unused aioimaplib dep, add IMAP connection test |
| 3 | mcp-jobspy | Add LinkedIn reliability note, add proxy support env var |
| 4 | mcp-lazydoctor | Remove dead git_ops code, clean up __init__.py import hook |
| 3 | mcp-instagram | Add ban risk disclaimers, encrypt credentials, recommend secondary account for testing |

### Known Issues Summary

| Server | Critical Issues | Medium Issues | Minor Issues |
|--------|----------------|---------------|--------------|
| mcp-whatsapp | Plaintext auth storage, no reconnect backoff | Stale lock file, LID mapping gaps | Hardcoded data paths, unbounded memory |
| mcp-email | Plaintext credential storage | No connection pooling, no attachments | Unused aioimaplib dep, simplistic IMAP search |
| mcp-instagram | Private API ban risk, no rate limiting | Session expiry handling, TOTP not persisted | No media download, basic challenge resolver |
| mcp-jobspy | Scraping fragility (sites change HTML) | LinkedIn blocks frequently, no caching | 500-char description truncation, no proxy |
| mcp-lazydoctor | None | Ruff-only lint, output truncation at 4K chars | Dead git_ops code, unusual __init__.py hook |

---

## Architecture Notes

All MCP servers follow the same pattern:
1. **stdio transport** -- Agent spawns the server as a child process, communicates via stdin/stdout
2. **Tool discovery** -- Server exposes `list_tools()` returning JSON schemas
3. **Tool execution** -- Server handles `call_tool(name, arguments)` and returns text results
4. **Lifecycle** -- LazyClaw's MCP manager handles spawning, idle timeouts, and cleanup

### How LazyClaw Manages These Servers

1. **Bundled MCP registry** -- `lazyclaw/mcp/manager.py` has a `BUNDLED_MCPS` dict defining all known servers
2. **Lazy loading** -- Non-favorite servers get stub tools registered (no subprocess). Real process spawns on first tool call
3. **Idle timeout** -- Servers disconnect after 300s of inactivity (favorites exempt)
4. **OAuth bridge** -- Remote MCP servers (Canva, GitHub, Slack) use OAuth 2.1 PKCE flow
5. **Skill integration** -- MCP tools appear as first-class skills in the agent's tool registry
