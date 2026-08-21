"""mcp-apihunter — admin-panel-to-API bridge.

Turns a web admin panel into durable, fast API tools. A one-time discovery
phase (driven by LazyClaw's browser specialist) records the panel's real HTTP
endpoints into an encrypted per-site manifest; thereafter `panel_call` replays
those endpoints directly — riding the user's live browser session for auth —
instead of clicking through the UI.
"""

__version__ = "0.2.0"
