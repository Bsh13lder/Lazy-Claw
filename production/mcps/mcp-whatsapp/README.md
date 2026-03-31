# mcp-whatsapp

**WhatsApp MCP Server** -- Full WhatsApp access via Baileys WebSocket. No browser, no Puppeteer, no Chrome. Direct protocol-level connection.

## What It Does

Connects to WhatsApp's WebSocket protocol via the Baileys library, giving your AI agent full messaging capabilities:

- **Send & receive messages** (text + images)
- **Read conversations** from any chat (DMs + groups)
- **Search contacts** by name or phone number
- **List all chats** with message counts and previews
- **Mute/unmute chats** (time-based or forever)
- **QR code login** (scans via terminal or Telegram bot)
- **Group support** with name resolution

## Architecture

```
AI Agent <--stdio--> mcp-whatsapp <--WebSocket--> WhatsApp Servers
                          |
                    Baileys Library
                          |
                    Auth State (local)
```

- **Runtime**: Node.js (CommonJS)
- **Transport**: stdio (MCP standard)
- **Protocol**: Baileys WebSocket (WhatsApp Web multi-device)
- **Persistence**: JSON files for contacts, messages, muted chats, auth state

## Setup

### Prerequisites
- Node.js 18+
- A phone number with WhatsApp active

### Install
```bash
cd production/mcps/mcp-whatsapp
npm install
```

### First Run
```bash
node src/index.js
```

On first connection, a QR code is generated. Scan it with WhatsApp on your phone (Settings > Linked Devices > Link a Device).

### Optional: Telegram QR Delivery
Set these env vars to receive the QR code via Telegram instead of terminal:
```bash
export TELEGRAM_BOT_TOKEN=your_bot_token
export TELEGRAM_CHAT_ID=your_chat_id
```

### Register with LazyClaw
Add to your MCP config:
```json
{
  "name": "mcp-whatsapp",
  "command": "node",
  "args": ["production/mcps/mcp-whatsapp/src/index.js"],
  "transport": "stdio"
}
```

## Available Tools (8)

| Tool | Description |
|------|-------------|
| `whatsapp_setup` | Initialize/reconnect WhatsApp connection |
| `whatsapp_status` | Check connection status |
| `whatsapp_send` | Send text message to contact or group |
| `whatsapp_read` | Read recent messages from a chat |
| `whatsapp_list_chats` | List all chats with summaries |
| `whatsapp_search` | Search contacts by name or phone |
| `whatsapp_send_image` | Send image with optional caption |
| `whatsapp_mute` | Mute/unmute chats |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token for QR delivery |
| `TELEGRAM_CHAT_ID` | No | Telegram chat ID for QR delivery |

## Data Storage

All data stored in `data/whatsapp_sessions/`:
```
data/whatsapp_sessions/
  baileys_auth/     # WhatsApp auth credentials
  contacts.json     # Contact name cache
  messages.json     # Recent messages (max 20/chat persisted)
  muted.json        # Muted chat list
  whatsapp.lock     # Single-instance lock file
```

---

## Analysis

### Viral Potential: HIGH

**Reasoning**: WhatsApp has 2+ billion users. An AI that can read, reply, and manage WhatsApp messages is immediately useful to almost everyone. The "wow" moment is instant -- "my AI just replied to my WhatsApp." This is the kind of demo that gets shared on Twitter/X. No other open-source AI agent platform has working WhatsApp integration without a browser.

**Key viral moments**:
- AI reads your unread WhatsApp messages and summarizes them
- AI replies to messages on your behalf
- AI monitors WhatsApp for new messages and notifies you
- Group chat management via AI

### Known Bugs & Issues

1. **Passwords stored in plaintext JSON** -- `contacts.json`, `messages.json`, and auth state are not encrypted. If LazyClaw's encryption-everywhere promise matters, these files should use the crypto module.
2. **Lock file can go stale** -- If the process crashes without cleanup, `whatsapp.lock` may prevent reconnection. There's PID-based detection but edge cases exist (PID reuse).
3. **LID-to-phone mapping incomplete** -- WhatsApp's newer LID (Linked ID) format isn't always resolvable to phone numbers. Some contacts show as LID hashes instead of names.
4. **No reconnection backoff** -- If WhatsApp disconnects, the server logs it but doesn't auto-reconnect with exponential backoff. The agent must call `whatsapp_setup` again.
5. **Memory grows unbounded in-session** -- Messages cache is capped at 100/chat in memory but there's no global cap. Heavy group chats could consume significant RAM.
6. **Hardcoded paths** -- Data directory is relative to CWD (`data/whatsapp_sessions/`), not configurable via env var.

### Public vs Private Recommendation: PUBLIC (with caveats)

**Recommendation**: Open-source this one. It's the single highest-impact MCP for attracting users.

**Why public**:
- Highest viral potential of all MCPs
- WhatsApp integration is the #1 requested feature in AI agent communities
- Baileys is already open-source (MIT), so no IP concerns
- Working code that actually connects -- not a stub

**Caveats before release**:
- Add encrypted storage for session data (align with LazyClaw's encryption promise)
- Add a proper `.env.example` with all config options
- Add reconnection with backoff
- Document that WhatsApp may ban numbers that automate too aggressively
