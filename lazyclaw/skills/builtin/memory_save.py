from __future__ import annotations

import re

from lazyclaw.skills.base import BaseSkill


# Fingerprints for common credential formats. If content matches any of these,
# the skill refuses to save and points the LLM at vault_set instead.
_CREDENTIAL_PATTERNS = [
    (re.compile(r"GOCSPX-[A-Za-z0-9_-]{10,}"), "Google OAuth client_secret"),
    (re.compile(r"(?<![A-Za-z0-9])\d{6,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com"),
     "Google OAuth client_id"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "API secret key (sk-...)"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google API key"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
     "JWT token"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"), "GitHub personal access token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    # Raw hex >= 32 chars (unlabelled secret)
    (re.compile(r"\b[0-9a-f]{40,}\b"), "long hex string (looks like a secret)"),
]


def _looks_like_credential(content: str) -> str | None:
    """Return human-readable name of the credential type, or None."""
    for pattern, label in _CREDENTIAL_PATTERNS:
        if pattern.search(content):
            return label
    return None


class MemorySaveSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "memory"

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "Save a fact, preference, or piece of context about the user for "
            "future reference (e.g. 'User's name is Alex', 'Prefers Python', "
            "'Lives in Madrid'). "
            "⚠️ NEVER use this for API keys, tokens, passwords, OAuth client "
            "secrets/IDs, session cookies, or any credential — those MUST go "
            "to `vault_set`. This tool will REFUSE credential-shaped input."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "The fact or preference to remember. Plain language only — "
                        "no API keys, tokens, passwords, OAuth secrets. Use vault_set "
                        "for those."
                    ),
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["fact", "preference", "context"],
                    "description": "fact (personal info) / preference (likes-dislikes) / context (situational)",
                    "default": "fact",
                },
                "importance": {
                    "type": "integer",
                    "description": "Importance 1-10 (10=critical like name, 1=trivial)",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["content"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Memory system not configured"
        params = params or {}
        content = (params.get("content") or "").strip()
        if not content:
            return "Error: `content` is required."

        # Belt-and-braces credential guard — description warns the LLM, this
        # actually refuses. Catches the case where the LLM ignores the rule.
        leak = _looks_like_credential(content)
        if leak is not None:
            return (
                f"REFUSED: input looks like a {leak}. Credentials must not go "
                f"into memory. Call `vault_set(key='<name>', value='<secret>')` "
                f"instead — it's AES-256-GCM encrypted. "
                f"Pick a descriptive key like 'google_oauth_client_secret'."
            )

        from lazyclaw.memory.personal import save_memory
        memory_type = params.get("memory_type", "fact")
        importance = params.get("importance", 5)
        await save_memory(self._config, user_id, content, memory_type, importance)
        return f"Saved: {content}"
