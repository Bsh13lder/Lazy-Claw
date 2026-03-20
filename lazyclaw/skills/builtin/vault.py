from __future__ import annotations

import json

from lazyclaw.skills.base import BaseSkill


class VaultSetSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "security"

    @property
    def name(self) -> str:
        return "vault_set"

    @property
    def description(self) -> str:
        return (
            "Securely store a credential (API key, token, password) in the encrypted vault. "
            "The value is encrypted with AES-256-GCM before storage. "
            "Common keys: openai_api_key, anthropic_api_key, github_token"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Credential name (e.g., 'openai_api_key', 'github_token')",
                },
                "value": {
                    "type": "string",
                    "description": "The secret value to store",
                },
            },
            "required": ["key", "value"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Vault not configured"
        from lazyclaw.crypto.vault import set_credential
        await set_credential(self._config, user_id, params["key"], params["value"])
        return f"Credential '{params['key']}' saved securely."


class VaultListSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "security"

    @property
    def name(self) -> str:
        return "vault_list"

    @property
    def description(self) -> str:
        return "List all stored credential names in the encrypted vault. Shows key names only, never values."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Vault not configured"
        from lazyclaw.crypto.vault import list_credentials
        keys = await list_credentials(self._config, user_id)
        if not keys:
            return "No credentials stored in vault."
        return "Stored credentials:\n" + "\n".join(f"- {k}" for k in keys)


class VaultDeleteSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "security"

    @property
    def name(self) -> str:
        return "vault_delete"

    @property
    def description(self) -> str:
        return "Delete a credential from the encrypted vault."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Credential name to delete",
                },
            },
            "required": ["key"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Vault not configured"
        from lazyclaw.crypto.vault import delete_credential
        deleted = await delete_credential(self._config, user_id, params["key"])
        if deleted:
            return f"Credential '{params['key']}' deleted."
        return f"No credential found with key '{params['key']}'."


class SaveSiteLoginSkill(BaseSkill):
    """Save website login credentials to the encrypted vault for auto-login."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "save_site_login"

    @property
    def description(self) -> str:
        return (
            "Save login credentials for a website. Stored encrypted in the vault. "
            "Used for automatic login when cookies expire — the browser will "
            "re-login automatically using these credentials. "
            "Example: save_site_login(domain='bank.com', username='me', password='secret')"
        )

    @property
    def category(self) -> str:
        return "browser"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Website domain (e.g., 'bank.com', 'gmail.com')",
                },
                "username": {
                    "type": "string",
                    "description": "Login username or email",
                },
                "password": {
                    "type": "string",
                    "description": "Login password",
                },
            },
            "required": ["domain", "username", "password"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.crypto.vault import set_credential

        if not self._config:
            return "Error: config not available"

        domain = params.get("domain", "").strip().lower()
        username = params.get("username", "")
        password = params.get("password", "")

        if not domain or not username or not password:
            return "Error: domain, username, and password are all required"

        domain = domain.replace("https://", "").replace("http://", "").rstrip("/")

        creds = json.dumps({"username": username, "password": password})
        await set_credential(self._config, user_id, f"site:{domain}", creds)

        return f"Login credentials saved for {domain}. Auto-login will be used when cookies expire."
