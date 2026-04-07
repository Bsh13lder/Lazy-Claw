# Changelog

All notable changes to LazyClaw will be documented in this file.

## [0.1.0] - 2026-04-07

### Added
- E2E encrypted AI agent platform (AES-256-GCM on all user content)
- 14 core components: Gateway, Agent Runtime, Lane Queue, Skills, Channels, Browser, Computer, Memory, MCP, Crypto, Teams, Replay, Task Runner, TAOR Loop
- 101 registered skills with smart tool selection (4 base tools, dynamic discovery)
- 6 active MCP servers: TaskAI, LazyDoctor, WhatsApp, Instagram, Email, JobSpy
- 4 MCP servers disabled pending rebuild: Freeride, Healthcheck, APIHunter, VaultWhisper
- 3 ECO routing modes: HYBRID, FULL, CLAUDE
- Telegram channel adapter with admin chat lock
- Web UI: React 19 + Vite + Tailwind (8 pages: Chat, Overview, Skills, Jobs, MCP, Memory, Vault, Settings)
- WebSocket streaming for real-time agent responses
- Background task execution with Telegram push notifications
- TAOR loop (Think-Act-Observe-Reflect) with parallel tool execution
- 5-layer memory system: conversation, compressed summaries, daily logs, weekly rollups, encrypted facts
- Browser automation via CDP (Brave > Chrome > Chromium auto-detection)
- Encrypted credential vault
- n8n integration (6 management skills + workflow templates)
- CLI + TUI with live task progress widget
- BIP-39 recovery phrase for encryption key recovery
